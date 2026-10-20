"""Read-only Linux content manifests. No backup, atomic snapshot or deletion claim."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from contextlib import contextmanager

MAX_JSON = 2 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_DEPTH = 64
MAX_PATH = 4096
CHUNK = 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
_EXIT = {"INVALID_MANIFEST": 2, "INVALID_ROOT": 2, "INVALID_PATH": 2,
         "LIMIT_EXCEEDED": 2, "UNSAFE_TREE": 3, "TREE_CHANGED": 3, "IO_ERROR": 3}


class ManifestError(ValueError):
    """A fixed, path-free error code. Operating-system messages are not exposed."""
    def __init__(self, code):
        if code not in _EXIT:
            raise ValueError("Unknown manifest error code")
        self.code = code
        super().__init__(code)


def _bad(code):
    raise ManifestError(code)


def _text(value):
    return isinstance(value, str) and not any(unicodedata.category(c) in ("Cc", "Cs") for c in value)


def _path(value, code):
    if (not _text(value) or not value or "\\" in value or
            any(not c or c in (".", "..") or c.startswith(".") for c in value.split("/"))):
        _bad(code)
    if len(value.encode("utf-8")) > MAX_PATH or len(value.split("/")) > MAX_DEPTH:
        _bad("LIMIT_EXCEEDED")
    return value


def _encoded(value):
    try:
        raw = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _bad("INVALID_MANIFEST")
    if len(raw) > MAX_JSON:
        _bad("LIMIT_EXCEEDED")
    return raw


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _bad("INVALID_MANIFEST")
        result[key] = value
    return result


def _validate(manifest):
    if type(manifest) in (str, bytes):
        try:
            raw = manifest.encode("utf-8") if type(manifest) is str else manifest
            if len(raw) > MAX_JSON:
                _bad("LIMIT_EXCEEDED")
            manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                                  parse_constant=lambda _: _bad("INVALID_MANIFEST"))
        except (UnicodeError, ValueError, RecursionError) as error:
            if isinstance(error, ManifestError):
                raise
            _bad("INVALID_MANIFEST")
    if (type(manifest) is not dict or set(manifest) != {"schema_version", "include_hidden", "files"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["include_hidden"] is not False or type(manifest["files"]) is not list):
        _bad("INVALID_MANIFEST")
    if len(manifest["files"]) > MAX_ENTRIES:
        _bad("LIMIT_EXCEEDED")
    previous = None
    for row in manifest["files"]:
        if (type(row) is not dict or set(row) != {"path", "bytes", "sha256"}
                or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= 2**63 - 1
                or type(row["sha256"]) is not str or _HEX.fullmatch(row["sha256"]) is None):
            _bad("INVALID_MANIFEST")
        path = _path(row["path"], "INVALID_MANIFEST")
        if previous is not None and path <= previous:
            _bad("INVALID_MANIFEST")
        previous = path
    _encoded(manifest)
    return manifest


def _same(left, right, *, identity_only=False):
    fields = ("st_dev", "st_ino", "st_mode") if identity_only else _FIELDS
    return all(getattr(left, key) == getattr(right, key) for key in fields)


def _entry(parent, name, *, changed=False):
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        _bad("TREE_CHANGED" if changed else "IO_ERROR")


def _safe_type(info, *, directory=False):
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            _bad("UNSAFE_TREE")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _bad("UNSAFE_TREE")


def _open_child(parent, name, before, *, directory):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if directory:
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(name, flags, dir_fd=parent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR):
            _bad("TREE_CHANGED")
        raise
    try:
        actual = os.fstat(fd)
        _safe_type(actual, directory=directory)
        if not _same(before, actual):
            _bad("TREE_CHANGED")
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _root(root):
    try:
        value = os.fspath(root)
    except (TypeError, ValueError):
        _bad("INVALID_ROOT")
    if not _text(value) or not value or "\\" in value:
        _bad("INVALID_ROOT")
    # Traverse every component, including relative '..', without resolving a
    # symlink first. abspath/realpath would hide link/.. traversal violations.
    parts = value.split("/") if value.startswith("/") else os.getcwd().split("/") + value.split("/")
    fds = []
    edges = []
    try:
        fds.append(os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
        for name in parts:
            if not name or name == ".":
                continue
            before = _entry(fds[-1], name)
            if stat.S_ISLNK(before.st_mode):
                _bad("UNSAFE_TREE")
            if not stat.S_ISDIR(before.st_mode):
                _bad("IO_ERROR")
            child = _open_child(fds[-1], name, before, directory=True)
            edges.append((fds[-1], name, child, before))
            fds.append(child)
        yield fds[-1]
        for parent, name, child, before in edges:
            if not _same(before, os.fstat(child), identity_only=True) or not _same(before, _entry(parent, name, changed=True), identity_only=True):
                _bad("TREE_CHANGED")
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _hash_file(parent, name, before):
    fd = _open_child(parent, name, before, directory=False)
    try:
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(fd, min(CHUNK, before.st_size - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > before.st_size:
                _bad("TREE_CHANGED")
            digest.update(chunk)
        after = os.fstat(fd)
        bound = _entry(parent, name, changed=True)
        if size != before.st_size or not _same(before, after) or not _same(before, bound):
            _bad("TREE_CHANGED")
        return {"path": name, "bytes": size, "sha256": digest.hexdigest()}
    finally:
        os.close(fd)


def _scan(fd, prefix, rows, count):
    before = os.fstat(fd)
    names = []
    # scandir is consumed incrementally: a large directory cannot allocate an
    # unbounded list before the entry limit is enforced. Never use cached stat.
    with os.scandir(fd) as entries:
        for entry in entries:
            count[0] += 1
            if count[0] > MAX_ENTRIES:
                _bad("LIMIT_EXCEEDED")
            if entry.name.startswith("."):
                continue
            relative = _path(prefix + entry.name, "INVALID_PATH")
            names.append((entry.name, relative))
    for name, relative in sorted(names):
        info = _entry(fd, name, changed=True)
        if stat.S_ISDIR(info.st_mode):
            child = _open_child(fd, name, info, directory=True)
            try:
                _scan(child, relative + "/", rows, count)
                if not _same(info, os.fstat(child)) or not _same(info, _entry(fd, name, changed=True)):
                    _bad("TREE_CHANGED")
            finally:
                os.close(child)
        else:
            _safe_type(info)
            row = _hash_file(fd, name, info)
            row["path"] = relative
            rows.append(row)
    if not _same(before, os.fstat(fd)):
        _bad("TREE_CHANGED")


def create_manifest(root):
    """Read visible regular files; reject unsafe or changing tree entries."""
    rows = []
    try:
        with _root(root) as fd:
            _scan(fd, "", rows, [0])
    except OSError:
        _bad("IO_ERROR")
    result = {"schema_version": 1, "include_hidden": False, "files": sorted(rows, key=lambda r: r["path"])}
    _encoded(result)
    return result


def verify_manifest(root, manifest):
    """Validate the complete snapshot before converting or opening root."""
    expected = _validate(manifest)
    current = create_manifest(root)
    old = {row["path"]: (row["bytes"], row["sha256"]) for row in expected["files"]}
    new = {row["path"]: (row["bytes"], row["sha256"]) for row in current["files"]}
    missing = sorted(old.keys() - new.keys())
    extra = sorted(new.keys() - old.keys())
    changed = sorted(path for path in old.keys() & new.keys() if old[path] != new[path])
    result = {"schema_version": 1, "ok": not (missing or extra or changed),
              "missing": missing, "extra": extra, "changed": changed}
    _encoded(result)
    return result


def _write(fd, raw):
    view = memoryview(raw)
    while view:
        sent = os.write(fd, view)
        if sent <= 0:
            raise OSError("Output transport failed")
        view = view[sent:]


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["--help"]:
            _write(1, b"Usage: file-manifest-check create ROOT | verify ROOT < manifest.json\nRead-only content comparison; does not certify backups or authorize deletion.\n")
            return 0
        if len(args) != 2 or args[0] not in ("create", "verify"):
            _write(2, b"USAGE_ERROR\n")
            return 2
        if args[0] == "create":
            result = create_manifest(args[1])
            status = 0
        else:
            stream = getattr(sys.stdin, "buffer", None)
            if stream is None:
                _bad("IO_ERROR")
            try:
                raw = stream.read(MAX_JSON + 1)
            except (OSError, ValueError):
                _bad("IO_ERROR")
            result = verify_manifest(args[1], raw)
            status = 0 if result["ok"] else 1
        _write(1, _encoded(result))
        return status
    except (ManifestError, OSError) as error:
        code = error.code if isinstance(error, ManifestError) else "IO_ERROR"
        try:
            _write(2, (code + "\n").encode("ascii"))
        except OSError:
            pass
        return _EXIT[code]


if __name__ == "__main__":
    raise SystemExit(main())
