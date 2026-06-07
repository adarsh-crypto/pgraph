# Architecture

This document explains how `pgraph` is put together — the modules, the data
flow, and the design decisions behind them. If you want the user-facing
quickstart, see the [README](../README.md); if you want the data model, see
[SCHEMA.md](SCHEMA.md).

## The problem it solves

When you drive a project with a coding agent, the "what changed / when / why"
record usually accretes in a markdown log. That log has two failure modes:

1. **It costs tokens.** The agent must re-read the whole file to find anything,
   and the cost grows linearly with history.
2. **It loses structure.** A flat list can't cheaply answer "what decisions
   touched `auth.py`?" without scanning every line.

`pgraph` replaces the flat log with an **embedded property graph**. The agent
asks a precise question and gets back only the matching nodes. The graph lives
in a single SQLite file as two tables — `nodes(label, pk, props)` and
`edges(rel, src_label, src_pk, dst_label, dst_pk)` — the "simple-graph"
pattern. Multi-hop questions ("change → session → decision") stay cheap because
the edge table is indexed on both `(rel, src_label, src_pk)` and
`(rel, dst_label, dst_pk)`: the 1-2 hop lookups pgraph actually needs are
indexed joins, which SQLite handles trivially.

## Layered design

Everything funnels through one storage layer, and both entry points (the CLI
and the MCP server) are thin wrappers over the same capture/query functions.
That symmetry is deliberate: a hook, a human, and an agent all exercise
identical code paths, so there is exactly one place a behaviour can be wrong.

```
            ┌─────────────┐     ┌──────────────────┐     ┌───────────────┐
            │   cli.py    │     │  mcp_server.py   │     │    hook.py    │
            │ (humans +   │     │ (MCP-capable     │     │ (Claude Code  │
            │  hooks)     │     │  agents)         │     │  PostToolUse/ │
            └──────┬──────┘     └────────┬─────────┘     │  Stop)        │
                   │                     │               └───────┬───────┘
                   └──────────┬──────────┴───────────────────────┘
                              │  all call the same functions
                ┌─────────────┴─────────────┐
                │   capture.py / query.py    │   write side / read side
                │   scan.py / export.py      │   ingest / portability
                └─────────────┬─────────────┘
                              │  typed node/edge API
                       ┌──────┴──────┐
                       │   db.py     │   Graph: add_node / match_nodes / add_edge / neighbors / sql
                       └──────┬──────┘
                              │
                       ┌──────┴──────┐
                       │  schema.py  │   label registry + idempotent init
                       └──────┬──────┘
                              │
                  ┌───────────┴───────────┐
                  │ SQLite (stdlib sqlite3)│   <project>/.pgraph/graph
                  └───────────────────────┘
```

## Module responsibilities

| Module | Responsibility |
|--------|----------------|
| [`__init__.py`](../pgraph/__init__.py) | Version + the `.pgraph/`, `graph`, `export` path constants. |
| [`db.py`](../pgraph/db.py) | Locate the project root, open the SQLite file (WAL mode + busy-timeout for concurrent agents), and expose a typed `Graph` handle over the `nodes`/`edges` tables: `add_node`/`get_node`/`set_node_props`/`match_nodes`/`count_nodes`, `add_edge`/`has_edge`/`count_edges`/`out_neighbors`/`in_neighbors`/`all_edges`, FTS5-backed `search`/`reindex`, plus a read-only `sql()` passthrough. Props are stored as a JSON blob and returned as plain dicts. |
| [`schema.py`](../pgraph/schema.py) | The **label registry**: `NODE_LABELS`, `REL_SPECS` (`(rel, FROM, TO)` tuples), and `REL_TYPES`. `init()` calls `Graph.init_schema()` (creates the two tables + edge indexes) then ensures one `Project` node; re-running it is safe and idempotent. Also exposes `now()` and `new_id()`. |
| [`capture.py`](../pgraph/capture.py) | The **write side**: sessions, changes, decisions, skills, files, and the edges between them. |
| [`query.py`](../pgraph/query.py) | The **read side**: `recent_changes`, `file_history`, `decisions_for`, `session_summary`, full-text `search`, and the headline `context_pack`. |
| [`scan.py`](../pgraph/scan.py) | Populate `File`/`Repo` nodes from the folder tree and backfill git commit history. |
| [`export.py`](../pgraph/export.py) | Dump the graph to git-diffable JSONL and re-import it — the portability guarantee. |
| [`mcp_server.py`](../pgraph/mcp_server.py) | Expose the capture/query functions as MCP tools over stdio. |
| [`hook.py`](../pgraph/hook.py) | Translate Claude Code hook stdin JSON into graph writes; never raises into the host agent. |
| [`install_hooks.py`](../pgraph/install_hooks.py) | Merge the two hooks into a target project's `.claude/settings.json`, idempotently. |
| [`cli.py`](../pgraph/cli.py) | Click command group binding all of the above to a terminal interface. |

## Data flow: a single edit

When an agent edits `src/auth.py` in a project with hooks installed:

1. Claude Code fires the **PostToolUse** hook → `pgraph hook-post-tool-use`.
2. [`hook.post_tool_use`](../pgraph/hook.py) reads the stdin JSON, maps the tool
   (`Write`→`create`, `Edit`/`NotebookEdit`→`edit`), and relativizes the path
   against the project root.
3. It finds the latest open session (or auto-opens one so no edit is dropped),
   then calls [`capture.log_change`](../pgraph/capture.py).
4. `log_change` does `add_node` for the `Change`, upserts the `File` node
   (`get_node` then `add_node` if missing), and links them with
   `add_edge("AFFECTS", ...)` and `add_edge("IN_SESSION", ...)`.
5. On **Stop**, `pgraph hook-stop` ends the session and writes a fresh JSONL
   export snapshot.

Later, the agent asks `context_pack(["src/auth.py"])` and gets only the recent
changes + decisions for that file, trimmed to a character budget — instead of
re-reading the whole history.

## Key design decisions

- **One write path, three callers.** CLI, MCP, and hooks all reduce to
  `capture.py`/`query.py`. No behaviour is duplicated.
- **App-minted IDs.** UUID4 hex from `schema.new_id()`. Git commits get a
  *deterministic* id (`commit:<repo>:<sha>`) so re-ingesting is idempotent.
- **Microsecond timestamps.** `schema.now()` keeps microseconds so several
  edits in the same second still order deterministically.
- **Hooks never raise.** Every hook handler swallows all exceptions — a memory
  hiccup must never block the user's real work.
- **JSONL is the insurance.** The SQLite file is the primary store, but the
  diffable JSONL export is what you commit and what survives an engine swap.

## Why SQLite (stdlib)

The graph is stored in a single SQLite file via Python's standard-library
`sqlite3` module — two tables (`nodes` and `edges`), indexed for the simple
1-2 hop lookups pgraph does. The escape hatch is a read-only `sql` tool/command
that queries those tables directly (props come out via
`json_extract(props, '$.field')`); write keywords are rejected by
`assert_read_only`, and reads run on a read-only connection as a hard backstop.

This deliberately replaced Kùzu after Kùzu's repository was archived in October
2025. The rationale:

- **Zero storage dependencies.** Storage is the standard library, so the only
  remaining runtime deps are `mcp` and `click`. The engine can never be
  "archived out from under us" — it ships with Python itself.
- **Future-proof across Python versions.** Works on all Python versions,
  including 3.14+. Kùzu was wheel-locked to 3.11–3.13.
- **No compiled artifact.** Nothing to build or pin per platform.
- **Durable and crash-safe.** WAL journal mode is enabled, so writes survive a
  crash mid-edit.
- **Single-file and portable.** Still a memory you can copy as a file — and
  much smaller: one real project's graph went from 13 MB to 2.0 MB (~6.5x).
- **Simple-graph node/edge pattern.** Index-free adjacency is gone, but pgraph
  only ever does 1-2 hop lookups, which indexed SQLite joins handle trivially.
