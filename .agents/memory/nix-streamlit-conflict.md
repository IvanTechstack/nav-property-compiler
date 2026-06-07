---
name: Nix streamlit binary conflict
description: Why the workflow must use the full .pythonlibs/bin/streamlit path and python3.11 site-packages
---

## The rule
Always run streamlit with the full absolute path:
`/home/runner/workspace/.pythonlibs/bin/streamlit run ...`

Always set PYTHONPATH to `python3.11/site-packages` (NOT `python3.12`):
`PYTHONPATH = "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages"`

## Why
Nix ships a system streamlit 0.50.2 binary (Python 3.9) that shadows `streamlit` on PATH.
When that old binary runs, it tries to import our installed streamlit 1.57.0 from PYTHONPATH,
which requires `TypeAlias` (Python 3.10+), causing an immediate ImportError.

Separately, `.pythonlibs/` contains TWO site-packages trees:
- `python3.11/` — Pillow 10.2.0 with cpython-311 C extensions ✓
- `python3.12/` — Pillow 12.2.0 with cpython-312 C extensions ✗

The `.pythonlibs/bin/streamlit` shebang points to a Nix-wrapped Python 3.11.14.
If PYTHONPATH is set to `python3.12/site-packages`, Pillow's cpython-312 .so files
cannot be loaded by Python 3.11, causing: `ImportError: cannot import name '_imaging' from 'PIL'`.

## How to apply
- artifact.toml `[services.env]` must use `python3.11/site-packages`
- Both `[services.development]` and `[services.production]` run commands must use the full binary path
- Do NOT change PYTHONPATH to python3.12 even if newer Pillow/boto3 versions are there
