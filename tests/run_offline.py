#!/usr/bin/env python3
"""Run public tests in a small read-only, network-free Linux namespace.
No installation or privilege changes: bwrap must already be available.
Only the GitHub runner may request its fixed staged-package smoke check.
"""
import argparse
import hashlib
import os
from pathlib import Path
import resource
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


FIXED_FILES=('file_manifest_check.py','pyproject.toml','README.md','CONTRIBUTING.md','LICENSE','tests/__init__.py','tests/run_offline.py','.github/workflows/ci.yml')


def checked_sources(root):
    paths=[root/name for name in FIXED_FILES]
    paths.extend(sorted((root/'tests').glob('test_*.py')))
    result={}
    for path in paths:
        relative=path.relative_to(root)
        for parent in [path,*path.parents]:
            if parent==root.parent:break
            if parent.is_symlink():raise RuntimeError('SOURCE_SYMLINK_REFUSED')
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>100*1024:
            raise RuntimeError('UNSAFE_OR_OVERSIZED_SOURCE')
        data=path.read_bytes()
        result[str(relative)]=(data,hashlib.sha256(data).hexdigest())
    if sum(len(v[0]) for v in result.values())>100*1024:
        raise RuntimeError('SOURCE_BUDGET_EXCEEDED')
    return result


def child(installed):
    status=Path('/proc/self/status').read_text()
    if os.geteuid()==0 or 'CapEff:\t0000000000000000' not in status or 'NoNewPrivs:\t1' not in status:
        raise RuntimeError('ISOLATION_NOT_PROVED')
    if Path('/home').exists() or Path('/src/.git').exists() or not os.statvfs('/src').f_flag&os.ST_RDONLY:
        raise RuntimeError('SOURCE_OR_HOME_ISOLATION_FAILED')
    if len(Path('/proc/net/route').read_text().splitlines())>1:
        raise RuntimeError('NETWORK_NAMESPACE_NOT_EMPTY')
    resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(30,30))
    resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,64*1024*1024))
    print('Isolated tests: sourceRO, no home/auth/network, CapEff0, NoNewPrivs1.',flush=True)
    suite=unittest.defaultTestLoader.discover('/src/tests',pattern='test_*.py')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():return 1
    if installed:
        # Fixed public package installed outside this namespace by CI, never pip here.
        env={'PATH':'/usr/bin:/bin','PYTHONPATH':'/installed','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','PYTHONIOENCODING':'utf-8','LD_LIBRARY_PATH':str(Path(sys.base_prefix)/'lib')}
        command=Path('/installed/bin/file-manifest-check')
        if not command.is_file() or command.is_symlink():raise RuntimeError('STAGED_CONSOLE_COMMAND_MISSING')
        subprocess.run([str(command),'--help'],cwd='/tmp',env=env,check=True)
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed',type=Path,help='CI-only fixed RUNNER_TEMP/file-manifest-installed directory')
    parser.add_argument('--namespace-child',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.namespace_child:return child(args.installed is not None)
    if sys.platform!='linux' or not shutil.which('bwrap'):
        parser.error('Linux with bwrap already installed is required; this helper installs nothing')
    installed=None
    if args.installed:
        runner=os.environ.get('RUNNER_TEMP')
        if os.environ.get('GITHUB_ACTIONS')!='true' or not runner:
            parser.error('--installed is only for the fixed ephemeral CI staging directory')
        expected=Path(runner).resolve()/'file-manifest-installed'
        if args.installed.is_symlink() or args.installed.resolve()!=expected or not expected.is_dir():
            parser.error('unexpected staged package directory')
        installed=expected
    root=Path(__file__).resolve().parent.parent
    before=checked_sources(root)
    with tempfile.TemporaryDirectory(prefix='file-manifest-test-') as temporary:
        stage=Path(temporary)/'source';stage.mkdir()
        for name,(data,_) in before.items():
            dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        python=Path(sys.executable).resolve();prefix=Path(sys.base_prefix).resolve()
        command=['bwrap','--die-with-parent','--new-session','--unshare-net','--unshare-pid','--cap-drop','ALL',
                 '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64',
                 '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/no-home','--ro-bind',str(stage),'/src']
        if prefix!=Path('/usr') and not str(prefix).startswith('/usr/'):
            command+=['--ro-bind',str(prefix),str(prefix)]
        if not str(python).startswith('/usr/') and not python.is_relative_to(prefix):
            parser.error('interpreter must belong to its standard-library prefix')
        if installed:command+=['--ro-bind',str(installed),'/installed']
        command+=['--clearenv','--setenv','HOME','/no-home','--setenv','PATH','/usr/bin:/bin',
                  '--setenv','PYTHONPATH','/src','--setenv','PYTHONDONTWRITEBYTECODE','1',
                  '--setenv','PYTHONNOUSERSITE','1','--setenv','PYTHONIOENCODING','utf-8',
                  '--setenv','LD_LIBRARY_PATH',str(prefix/'lib'),'--chdir','/src',str(python),'-B','/src/tests/run_offline.py','--namespace-child']
        if installed:command+=['--installed','/installed']
        result=subprocess.run(command,check=False)
        # Hash the exact staged/read-only input and original public source afterwards.
        after=checked_sources(root)
        if before!=after:raise RuntimeError('SOURCE_CHANGED_DURING_TESTS')
        for name,(_,digest) in before.items():
            if hashlib.sha256((stage/name).read_bytes()).hexdigest()!=digest:
                raise RuntimeError('STAGED_SOURCE_CHANGED_DURING_TESTS')
        return result.returncode


if __name__=='__main__':raise SystemExit(main())
