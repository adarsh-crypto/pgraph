# Development guide

How to set up a dev environment, run the tests, and understand the layout if
you want to extend `pgraph`.

## Environment

Kùzu has wheels for **Python 3.11–3.13** only (not 3.14 yet). Use conda:

```bash
conda create -y -n pgraph python=3.13
conda activate pgraph
pip install -e .
pip install pytest
```

> If you ever see `ERROR: Failed building wheel for kuzu`, you're almost
> certainly on Python 3.14. Recreate the env on 3.13.

## Running the tests

```bash
pytest -q
```

The suite (12 tests) covers:

| File | What it verifies |
|------|------------------|
| `tests/test_schema.py` | `init()` creates the schema and a single Project node. |
| `tests/test_capture.py` | Sessions, changes, decisions, skills, and their edges. |
| `tests/test_query.py` | Ordering, path filtering, `file_history`, `context_pack` budgeting. |
| `tests/test_export_roundtrip.py` | export → wipe → import → identical. |

Fixtures live in `tests/conftest.py` (`graph` and `root`).

## Project layout

```
pgraph/
  __init__.py        version + path constants
  db.py              Graph handle, project-root discovery
  schema.py          DDL, init(), now(), new_id()
  capture.py         write side (sessions/changes/decisions/skills)
  query.py           read side (recent_changes, file_history, context_pack, …)
  scan.py            folder walk + git ingest
  export.py          JSONL export/import
  mcp_server.py      MCP stdio server
  hook.py            Claude Code hook handlers
  install_hooks.py   merge hooks into a project's settings
  cli.py             Click command group
tests/               pytest suite
docs/                this documentation
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for how these fit together.

## Extending the graph

To add a new node or relationship type:

1. Add the `CREATE ... TABLE IF NOT EXISTS` DDL to `NODE_TABLES` /`REL_TABLES`
   in [`schema.py`](../pgraph/schema.py). Because `init()` is idempotent,
   existing graphs pick it up on the next `init` with no migration script.
2. Add write helpers in [`capture.py`](../pgraph/capture.py) and read helpers
   in [`query.py`](../pgraph/query.py).
3. Register the node/rel in the `_NODE_SPECS` / `_PK` / `_REL_SPECS` tables in
   [`export.py`](../pgraph/export.py) so it survives export/import.
4. If agents should reach it, add an `@mcp.tool()` in
   [`mcp_server.py`](../pgraph/mcp_server.py) and a Click command in
   [`cli.py`](../pgraph/cli.py).
5. Add a test.

The export step (3) is the one that's easy to forget — a node type missing from
`_NODE_SPECS` simply won't be exported, and the round-trip test won't catch a
type it doesn't know about.

## Conventions

- All Cypher is **parameterized** — never string-format user values into a
  query. Kùzu also rejects parameters that the statement doesn't reference, so
  pass only what you use (see the import path in `export.py`).
- Read helpers return plain JSON-serializable dicts/lists; datetimes are
  rendered to ISO strings at the boundary (`query._iso`).
- Hook handlers must never raise. Wrap everything and return `0`.
- Timestamps come from `schema.now()` (naive UTC, microsecond precision).

## Conda env reuse

This repo was built in a conda env named `pgraph`
(`/Users/adarshsinha/anaconda3/envs/pgraph`, Python 3.13). Activate it with:

```bash
source /Users/adarshsinha/anaconda3/etc/profile.d/conda.sh && conda activate pgraph
```
