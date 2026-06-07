# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-06-07

### Changed — storage engine: Kùzu → stdlib SQLite (BREAKING for the escape hatch)
- **Replaced Kùzu with Python's standard-library `sqlite3`.** The graph is now a
  single SQLite file (`<project>/.pgraph/graph`) backed by two indexed tables —
  `nodes(label, pk, props)` (props is a JSON blob) and
  `edges(rel, src_label, src_pk, dst_label, dst_pk)` — the well-known
  "simple-graph" pattern, kept in-tree. Motivation: Kùzu's repo was archived in
  Oct 2025 and its wheels were locked to Python 3.11–3.13.
- **Zero compiled dependencies.** Only `mcp` and `click` remain. pgraph now runs
  on **any Python 3.11+**, including 3.14 and later; no conda env required.
- **~6.5× smaller on disk** (a real example: a dogfooded graph shrank from 13 MB
  to 2.0 MB) and dramatically faster tests (suite runs in well under a second).
- Durable writes via SQLite **WAL** journal mode.
- `db.py` now exposes a small typed node/edge API (`add_node`, `get_node`,
  `match_nodes`, `add_edge`, `out_neighbors`/`in_neighbors`, counts, …) instead
  of a Kùzu cursor; `schema.py` is now a label registry rather than DDL.
- **The `cypher` escape hatch is now `sql`** (CLI command + MCP tool): a
  read-only SQL query over the nodes/edges tables (`json_extract(props, '$.x')`).
  Write statements are rejected *and* the query runs on a read-only connection.
- Existing graphs migrate losslessly via the JSONL export: `pgraph import`
  rebuilds the SQLite store from `.pgraph/export/*.jsonl` (the live Project_graph
  and Spokesfan graphs were migrated this way with zero data loss).
- Docs updated throughout (ARCHITECTURE, SCHEMA, PORTABILITY, DEVELOPMENT,
  README, CLI, MCP) to reflect the SQLite design.

### Added
- **SessionStart auto-context**: a `hook-session-start` command (wired by
  `install-hooks`) injects a compact brief of the project's memory — last
  session, recent changes, recent decisions — when an agent opens, in the
  project root *or any nested repo*. Backed by `query.session_brief`. This is
  what makes "open the agent → it already knows what's been done" automatic.
- `scan --exclude` (repeatable): prune named dirs / path prefixes from the walk
  (e.g. a nested repo you don't want tracked).
- `pgraph status` command + `status` MCP tool: node/edge counts and any open
  session — a quick health check.
- Read-only guard on the escape hatch (CLI + MCP) so the graph can only be
  mutated through the validated capture path.
- `CONTRIBUTING.md`, issue/PR templates, and a `[dev]` extra for `pytest`.
- This repo now dogfoods pgraph: its own development memory is committed as a
  JSONL export under `.pgraph/export/`.

### Changed
- `scan` docstrings corrected: the walk prunes a fixed noise-dir set and
  dotfiles; it does not parse `.gitignore` (clarified as future work).

## [0.1.0] — 2026-06-07

Initial release.

### Added
- Embedded Kùzu property-graph store for per-project agent memory
  (`Project`, `Agent`, `Session`, `Change`, `File`, `Decision`, `Repo`,
  `Skill` nodes and the edges between them).
- Write side (`capture.py`): sessions, changes, decisions, skills, file upserts.
- Read side (`query.py`): `recent_changes`, `file_history`, `decisions_for`,
  `session_summary`, and the headline `context_pack` (budgeted retrieval).
- Folder scan + git history ingest (`scan.py`), including detection of nested
  cloned repos/docs and idempotent commit ingestion.
- Git-diffable JSONL export/import for portability (`export.py`), with a tested
  export → wipe → import round-trip.
- MCP stdio server (`mcp_server.py`) exposing nine tools to agents.
- Claude Code hooks (`hook.py`, `install_hooks.py`): PostToolUse auto-capture
  and a Stop snapshot, installed non-destructively into a project's settings.
- `pgraph` CLI mirroring all of the above.
- Full documentation under `docs/`.

### Notes
- Kùzu pinned at 0.11.3 (upstream archived Oct 2025); Python 3.11–3.13 only.
