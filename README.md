# pgraph — local, portable, graph-based project memory for coding agents

[![tests](https://github.com/adarsh-crypto/pgraph/actions/workflows/test.yml/badge.svg)](https://github.com/adarsh-crypto/pgraph/actions/workflows/test.yml)
[![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue)](https://github.com/adarsh-crypto/pgraph)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

When you work on a project with a coding agent (Claude Code, Codex, …), the
"what changed / when / why" context usually lives in a growing markdown file.
Re-reading that whole file on every turn burns tokens and scales badly.

`pgraph` replaces the flat log with a **per-project embedded graph database**
(powered by [Kùzu](https://kuzudb.com)). An agent queries a small, targeted
slice — *"what changed in `auth.py` recently and why?"* — instead of re-reading
everything. The graph is just files under `.pgraph/`, so you can zip it, copy it
between machines, or commit a diffable JSONL export to git.

## Why a graph saves tokens

A flat markdown log forces "read everything to find anything." A graph lets the
agent ask precise questions and get back only the relevant nodes. Kùzu uses
**index-free adjacency** (each node points directly at its neighbours), so
multi-hop *change → session → decision* traversals stay cheap as history grows.

## What it records

| Node | Meaning |
|------|---------|
| `Project` | the project root |
| `Session` | one work session by an agent (timestamps + summary) |
| `Change` | a file edit/create/delete, or a git `commit` |
| `File` | a tracked file (with language hint) |
| `Decision` | a manual "why" note — the intent a diff can't show |
| `Repo` | a cloned dependency/doc repo found inside the project |
| `Skill` | a skill/tool used in a session |

Edges link them: `Change-[:IN_SESSION]->Session`, `Change-[:AFFECTS]->File`,
`Decision-[:MOTIVATES]->Change`, `Decision-[:ABOUT]->File`,
`Session-[:USED_SKILL]->Skill`, `File-[:IN_REPO]->Repo`, and so on.

## Install

Kùzu has prebuilt wheels for **Python 3.11–3.13** (not yet 3.14). Use a conda
env or venv on a supported version:

```bash
conda create -y -n pgraph python=3.13
conda activate pgraph
pip install -e .
```

## Quickstart (CLI)

```bash
cd /path/to/your/project
pgraph init                                   # creates .pgraph/graph

SID=$(pgraph session start --agent claude-code --summary "wiring auth")
CID=$(pgraph log --kind edit --path src/auth.py --summary "add JWT verify")
pgraph decide --title "Use JWT not sessions" \
              --body "stateless, scales horizontally" \
              --motivates "$CID" --about src/auth.py

pgraph history --path src/auth.py             # changes + decisions for one file
pgraph context src/auth.py --budget 4000      # compact, token-budgeted bundle
pgraph session end --summary "auth done"
```

Populate from what's already on disk:

```bash
pgraph scan          # record File nodes + nested cloned repos/docs
pgraph ingest-git    # backfill commit history (project + cloned repos)
```

Move the memory between machines:

```bash
pgraph export        # writes .pgraph/export/{nodes,edges}.jsonl (git-diffable)
# ... on another machine, in the project:
pgraph import        # rebuilds the graph from the JSONL export
```

## Use it from an agent (MCP)

`pgraph` ships an MCP server so any MCP-capable agent can read/write the graph.
Register it with Claude Code (run from inside the project, or set `PGRAPH_ROOT`):

```bash
claude mcp add pgraph -- pgraph-mcp
```

Exposed tools: `session_start`, `session_end`, `log_change`, `log_decision`,
`record_skill_use`, `recent_changes`, `file_history`, `context_pack`, `cypher`.
The headliner is **`context_pack(paths, budget)`** — hand it the files you're
about to touch and it returns only the relevant recent changes + decisions,
trimmed to a character budget, instead of a whole log.

## Automatic capture (Claude Code hooks)

Wire auto-capture into a project's `.claude/settings.json`:

```bash
pgraph install-hooks --pgraph-bin "$(command -v pgraph)"
```

This adds (merging, never clobbering existing hooks):

- a **PostToolUse** hook on `Write|Edit|NotebookEdit` → records a `Change`
  (auto-opening a session if none is active);
- a **Stop** hook → ends the open session and writes a fresh JSONL export.

Hooks are best-effort and never raise into Claude Code — a memory hiccup can't
block your work.

## Portability model

- **Primary store:** `.pgraph/graph` — copy it and you've moved the whole memory.
- **Insurance / git:** `.pgraph/export/*.jsonl` — human-readable, diffable, and
  re-importable even across Kùzu versions. Commit the export; gitignore the live
  DB (see `.gitignore`).

## Tests

```bash
pip install pytest
pytest -q
```

## This repo dogfoods pgraph

`pgraph` tracks its own development. The committed
[`.pgraph/export/`](.pgraph/export/) is this repo's own project memory — a
git-diffable JSONL snapshot of the sessions, changes and decisions behind it.
Rebuild the live graph from it with `pgraph import`, then explore with
`pgraph status`, `pgraph history`, or `pgraph context <file>`.

## Documentation

Deep-dive docs live in [`docs/`](docs/README.md):

- [Architecture](docs/ARCHITECTURE.md) — modules, layers, data flow.
- [Schema](docs/SCHEMA.md) — the graph data model.
- [CLI reference](docs/CLI.md) — every command.
- [MCP server](docs/MCP.md) — tools agents call.
- [`context_pack`](docs/CONTEXT_PACK.md) — the token-saving query, in depth.
- [Hooks](docs/HOOKS.md) — automatic capture.
- [Portability](docs/PORTABILITY.md) — the JSONL export & git split.
- [Development](docs/DEVELOPMENT.md) — setup, tests, extending.

## Notes & limits

- Kùzu is pinned at **0.11.3** (its repo was archived Oct 2025). It's used as a
  stable embedded library; the JSONL export is the migration path if the engine
  is ever swapped.
- Python **3.14 is not yet supported** by Kùzu wheels — use 3.11–3.13.

## License

[MIT](LICENSE).
