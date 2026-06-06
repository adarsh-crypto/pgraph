# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
