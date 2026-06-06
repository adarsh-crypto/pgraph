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
asks a precise question and gets back only the matching nodes. Multi-hop
questions ("change → session → decision") stay cheap because Kùzu uses
index-free adjacency: each node holds direct pointers to its neighbours, so a
traversal is a pointer-chase, not a join.

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
                              │  parameterized Cypher
                       ┌──────┴──────┐
                       │   db.py     │   Graph: open / run / one / all / close
                       └──────┬──────┘
                              │
                       ┌──────┴──────┐
                       │  schema.py  │   DDL + idempotent init
                       └──────┬──────┘
                              │
                    ┌─────────┴─────────┐
                    │  Kùzu (embedded)  │   <project>/.pgraph/graph
                    └───────────────────┘
```

## Module responsibilities

| Module | Responsibility |
|--------|----------------|
| [`__init__.py`](../pgraph/__init__.py) | Version + the `.pgraph/`, `graph`, `export` path constants. |
| [`db.py`](../pgraph/db.py) | Locate the project root, open the Kùzu DB, and expose a `Graph` handle with `run`/`one`/`all`/`close`. Materializes rows into plain dicts so no caller ever touches a Kùzu cursor. |
| [`schema.py`](../pgraph/schema.py) | All `CREATE NODE/REL TABLE IF NOT EXISTS` DDL, `init()`, plus `now()` and `new_id()`. Re-running `init()` is a lightweight migration. |
| [`capture.py`](../pgraph/capture.py) | The **write side**: sessions, changes, decisions, skills, files, and the edges between them. |
| [`query.py`](../pgraph/query.py) | The **read side**: `recent_changes`, `file_history`, `decisions_for`, `session_summary`, and the headline `context_pack`. |
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
4. `log_change` creates a `Change` node, `MERGE`s the `File` node, and links
   `Change-[:AFFECTS]->File` and `Change-[:IN_SESSION]->Session`.
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
- **JSONL is the insurance.** The Kùzu file is the primary store, but the
  diffable JSONL export is what you commit and what survives an engine swap.

## Why Kùzu

Kùzu is an embedded (in-process, no server) native graph database with
index-free adjacency, Cypher support, and single-file on-disk storage. That
combination matches the requirements exactly: zero-ops, fast multi-hop
traversal, and a memory you can copy as a file. See
[SCHEMA.md](SCHEMA.md#why-kùzu-and-the-version-pin) for the version-pin caveat.
