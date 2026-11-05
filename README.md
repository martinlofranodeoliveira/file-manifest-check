<!-- public-docs-pt:v1 -->
# Manifestos e comparação de arquivos

Inventaria conteúdos de uma pasta e compara snapshots no Linux. Não apaga, corrige, envia arquivos ou substitui um backup.

**Estado:** edição pública de desenvolvimento. Recursos e limites detalhados estão na referência técnica preservada abaixo.

## Começar em português

**Requisitos:** Python 3.10+; Linux.

```sh
git clone https://github.com/martinlofranodeoliveira/file-manifest-check.git
cd file-manifest-check
python -m file_manifest_check --help
```

Use um ambiente de desenvolvimento separado. Não envie `.env`, chaves, bancos,
uploads ou dados pessoais ao Git. Nas demonstrações, use somente dados fictícios.

## Documentação e validação

[Contribuições](CONTRIBUTING.md) · [Licença](LICENSE) · [Catálogo do laboratório](https://github.com/martinlofranodeoliveira#projetos-abertos)

Os comandos acima foram conferidos nos manifests/guia público; não significam que todas as integrações, plataformas ou serviços reais foram exercitados nesta revisão documental.

A referência técnica original abaixo preserva APIs, exemplos, limites e histórico
de funcionalidades. Auxiliares em `community/` são módulos independentes: sua
existência não comprova integração na interface ou no fluxo principal. Ao avaliar
uma funcionalidade, consulte também o teste e o código do módulo correspondente.

## Contribuir

Abra uma issue com objetivo, comportamento observado, versão e um exemplo mínimo
sintético. Para código, inclua testes relevantes, instruções de reprodução e
atualização da documentação. Não envie segredos ou informações de terceiros em
issues, exemplos, capturas ou pull requests. Mudanças úteis e completas têm
preferência sobre alterações feitas apenas para aumentar atividade.

## Licença e autoria

Licença declarada no repositório: **MIT**. Consulte o arquivo de licença e mantenha as atribuições de terceiros.

Nome técnico e URL preservados para manter imports, links e automações existentes.
Título, descrição e tópicos em português facilitam localizar a finalidade do projeto.

<!-- public-docs-pt:end -->

---

## Referência técnica e documentação original

# file-manifest-check

A small, standard-library Python utility for **Linux** that inventories file
contents in a selected directory and compares them with a saved manifest.
It can help review artifacts before a separate backup or cleanup procedure.

It never deletes, repairs, chmods, uploads or downloads files. A matching
manifest is **not** a backup certificate, signature, atomic filesystem snapshot
or permission to delete anything.

## Use the source directly

Requires Python 3.10 or newer on Linux. No third-party runtime packages:

```sh
python -m file_manifest_check create ./artifacts > ./snapshot.json
python -m file_manifest_check verify ./artifacts < ./snapshot.json
```

Keep the snapshot **outside** the directory being inventoried. A visible
snapshot inside that directory is another input file; there is no special
exception for its name. The shell owns the redirection above; the utility
itself opens artifact files only for reading.

Optional installation exposes the same command as `file-manifest-check`:

```sh
python -m pip install .
file-manifest-check --help
```

Installation may obtain the packaging backend. Running the source directly
does not install or fetch anything.

## Manifest and comparison

For a file `notes.txt` containing exactly the three bytes `abc`:

```json
{"schema_version":1,"include_hidden":false,"files":[{"path":"notes.txt","bytes":3,"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}]}
```

Paths are relative, ordered, case-sensitive and kept exactly as written.
Unicode and spaces are supported, with no Unicode normalization. Manifests
contain no absolute root, file content, timestamps, owner or permission data.
Empty directories are not represented.

Verification reports only content differences:

```json
{"schema_version":1,"ok":false,"missing":["old.txt"],"extra":["new.txt"],"changed":["notes.txt"]}
```

`missing`, `extra` and `changed` are independently sorted. A same-size content
change is still a change. Hidden-only changes and empty-directory changes do
not change the comparison, provided the scan remains within its resource limits.

```python
from file_manifest_check import create_manifest, verify_manifest

snapshot = create_manifest("./artifacts")
report = verify_manifest("./artifacts", snapshot)
print(report["ok"])
```

`verify_manifest` also accepts a UTF-8 JSON string or bytes. It validates the
complete snapshot before converting or accessing the root, so malformed input
does not trigger a directory scan. Library errors are `ManifestError` instances
with a fixed `.code`; their messages do not echo paths or OS error details.

## Hidden files and unsafe trees

- A name beginning with `.` is omitted, and hidden directories are not entered.
  There is no `include-hidden` override. Hidden symlinks are omitted without
  following them.
- Visible symlinks, special files and regular files with more than one hardlink
  are refused. Root and ancestor symlinks are refused as well.
- Relative manifest paths cannot be absolute, contain backslashes, empty/dot/
  parent components, hidden components or Unicode control/surrogate characters.
- Inputs with duplicate JSON keys, invalid UTF-8, non-finite numbers, invalid
  schemas or unordered/duplicate file paths are rejected.

The utility uses read-only, no-follow descriptors and checks identity and
metadata around each streamed hash. A detected replacement or change fails
closed instead of returning a partial scan. Operating-system reads can update
access times.

These checks do **not** make the entire directory tree atomic or attest to the
filesystem, backup destination, ownership or permissions. A manifest can reveal
filenames and content hashes; store it according to the sensitivity of the
artifacts you explicitly selected.

## Limits and exit codes

A scan observes at most 10,000 entries: files, directories and hidden names in
visited directories. Hidden subtree contents are not visited or counted.
Visible paths have at most 64 components and 4,096 UTF-8 bytes. Input/output
JSON is limited to 2 MiB, and files are streamed in chunks of at most 1 MiB.

| Exit | Meaning |
| --- | --- |
| `0` | Manifest created, or verification found no differences |
| `1` | Verification found missing, extra or changed files |
| `2` | Invalid usage, snapshot, root selector, path or resource limit |
| `3` | Unsafe tree, concurrent change or I/O failure |

Errors emit no JSON and one fixed code on stderr: `USAGE_ERROR`,
`INVALID_MANIFEST`, `INVALID_ROOT`, `INVALID_PATH`, `LIMIT_EXCEEDED`,
`UNSAFE_TREE`, `TREE_CHANGED` or `IO_ERROR`. A failure while writing stdout
may already have emitted a prefix; it is an error, with no rollback guarantee.

## Development

```sh
python -B -m unittest discover -s tests -p 'test_*.py' -v
python -B tests/run_offline.py
```

The offline helper requires `bwrap` already installed. It runs the public tests
with source mounted read-only, a synthetic temporary directory, no home or
network, and process/resource checks. It never installs packages or requests
sudo. CI installs its tools and stages a package outside that test namespace
on an ephemeral GitHub runner, then checks Python 3.10, 3.12 and 3.14.

See [CONTRIBUTING.md](CONTRIBUTING.md). MIT licensed; this is a focused content
comparison tool, not a backup or file-removal product.
