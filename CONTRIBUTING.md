# Contributing

Please open a focused issue or pull request with a reproducible case and a
clear benefit. Keep this tool small, dependency-free at runtime and usable
with Python 3.10 or newer on Linux.

Preserve the read-only boundary: no delete, repair, chmod, network, credential
handling or automatic cleanup commands. A manifest is not a certificate that
another copy exists or that deleting the original is safe.

Use only temporary, synthetic fixtures in tests. Never include personal paths,
real customer data, keys, tokens, private snapshots or repository history.
Preserve the hidden-file policy, strict input validation before root access,
no-follow descriptor handling, stability checks and bounded streaming. Do not
replace a failing security test with a weaker assertion.

Run the public tests:

```sh
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

For the isolated test path, install `bwrap` yourself through your normal
system administration process, then run:

```sh
python -B tests/run_offline.py
```

The helper reads the selected project source and Python runtime, stages a
small temporary copy, and never installs tools. Its test child has no home or
network access; its writable tree contains only generated fixtures. Packaging
checks can additionally use an explicitly staged install with `--installed DIR`;
the helper does not run pip for you.

Document changes to schema, path policy, resource bounds or CLI exit codes.
Avoid incidental changes, empty commits or splitting one useful change merely
to increase activity. Contributions are made under the project's MIT license.
