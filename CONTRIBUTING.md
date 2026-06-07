# Contributing to pgraph

Thanks for your interest! pgraph is small and the contribution loop is short.

## Setup

Kùzu publishes wheels for **Python 3.11–3.13** only (not 3.14 yet).

```bash
conda create -y -n pgraph python=3.13   # or a venv on 3.11–3.13
conda activate pgraph
pip install -e ".[dev]"
```

## Before you open a PR

1. **Run the tests** — `pytest -q` must be green (CI runs them on 3.11, 3.12, 3.13).
2. **Add a test** for any behaviour you change or add.
3. **Keep Cypher parameterized** — never string-format values into a query, and
   pass only parameters the statement references (Kùzu rejects unused ones).
4. **Hook handlers must never raise** — wrap everything and return `0`.

## Extending the graph

Adding a node or relationship type touches a predictable set of files —
`schema.py`, `capture.py`/`query.py`, `export.py` (so it survives the JSONL
round-trip), and optionally `cli.py`/`mcp_server.py`. The full checklist is in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#extending-the-graph).

## Commit style

- Keep commits focused; write a clear subject line.
- Reference an issue number when one exists.

## Reporting bugs / requesting features

Open an issue using the templates. For bugs, include your Python version, OS,
and the smallest steps that reproduce it.
