# Development guide

How to set up a dev environment, run the tests, and understand the layout if
you want to extend `pgraph`.

## Environment

`pgraph` has **zero compiled dependencies** (it uses the stdlib `sqlite3`), so it
runs on any **Python >= 3.11**, including 3.14+. A plain venv or a conda env both
work:

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
```

## Running the tests

```bash
pytest -q
```

The suite (36 tests) covers:

| File | What it verifies |
|------|------------------|
| `tests/test_schema.py` | `init()` creates the schema and a single Project node. |
| `tests/test_capture.py` | Sessions, changes, decisions, skills, and their edges. |
| `tests/test_query.py` | Ordering, path filtering, `file_history`, `context_pack` budgeting. |
| `tests/test_export_roundtrip.py` | export → wipe → import → identical. |
| `tests/test_status_and_guard.py` | `status()` and the read-only `sql` guard. |
| `tests/test_session_brief.py` | The `SessionStart` brief, `scan --exclude`, and `SessionStart` hook install. |
| `tests/test_search.py` | FTS5 search, BM25 ranking, label filter, fallback, `busy_timeout`. |

Fixtures live in `tests/conftest.py` (`graph` and `root`).

## Project layout

```
pgraph/
  __init__.py        version + path constants
  db.py              Graph handle, project-root discovery, sqlite node/edge store
  schema.py          label registry, init(), now(), new_id()
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

1. Add the new label to `NODE_LABELS` (or the `(rel, FROM, TO)` tuple to
   `REL_SPECS`) in [`schema.py`](../pgraph/schema.py). Because `init()` is
   idempotent, existing graphs pick it up on the next `init` with no migration
   script.
2. Add write helpers in [`capture.py`](../pgraph/capture.py) and read helpers
   in [`query.py`](../pgraph/query.py).
3. Export derives nodes from `schema.NODE_LABELS` and edges from
   `schema.REL_SPECS`, and the primary-key map is `PK` in
   [`db.py`](../pgraph/db.py) — so adding to the schema registry plus `PK` is
   what makes the new type survive export/import.
4. If agents should reach it, add an `@mcp.tool()` in
   [`mcp_server.py`](../pgraph/mcp_server.py) and a Click command in
   [`cli.py`](../pgraph/cli.py).
5. Add a test.

The export step (3) is the one that's easy to forget — a node type missing from
`NODE_LABELS` (or `PK`) simply won't be exported, and the round-trip test won't
catch a type it doesn't know about.

## Conventions

- All SQL goes through the typed `Graph` API (parameterized queries via
  `sqlite3`) — never string-format user values into a query. The read-only `sql`
  passthrough runs on a read-only connection and rejects write keywords.
- Read helpers return plain JSON-serializable dicts/lists; datetimes are
  rendered to ISO strings at the boundary (`query._iso`).
- Hook handlers must never raise. Wrap everything and return `0`.
- Timestamps come from `schema.now()` (naive UTC, microsecond precision).

## Conda env reuse

This is optional now — there are no compiled deps, so any venv or env works. But
the conda env named `pgraph` (`/Users/adarshsinha/anaconda3/envs/pgraph`, Python
3.13) this repo was built in still works. Activate it with:

```bash
source /Users/adarshsinha/anaconda3/etc/profile.d/conda.sh && conda activate pgraph
```
