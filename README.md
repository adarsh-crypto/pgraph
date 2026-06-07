# pgraph — local, portable, graph-based project memory for coding agents

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/adarsh-crypto/pgraph)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

When you work on a project with a coding agent (Claude Code, Codex, …), the
"what changed / when / why" context usually lives in a growing markdown file.
Re-reading that whole file on every turn burns tokens and scales badly.

`pgraph` replaces the flat log with a **per-project embedded graph** stored in a
single, dependency-free [SQLite](https://sqlite.org) file (Python's stdlib
`sqlite3` — a node table + an edge table). An agent queries a small, targeted
slice — *"what changed in `auth.py` recently and why?"* — instead of re-reading
everything. The graph is just files under `.pgraph/`, so you can zip it, copy it
between machines, or commit a diffable JSONL export to git.

> **A project, not a repo.** Memory lives at a *project root*, which can contain
> several repos, docs, and loose files. Initialize `pgraph` once at that root;
> every command, hook, and the MCP server resolves the graph by walking
> **upward** from wherever the agent opened — so working inside any nested repo
> still reads and writes the one shared project memory.

## Why a graph saves tokens

A flat markdown log forces "read everything to find anything." A graph lets the
agent ask precise questions and get back only the relevant nodes. The store is
two indexed SQLite tables — a `nodes` table (label + JSON props) and an `edges`
table — so the *change → session → decision* lookups pgraph needs are cheap
indexed joins, and stay cheap as history grows.

### Measured savings

On this repo's own memory (135 nodes) and the Spokesfan project (4,557 nodes),
versus an agent re-reading the equivalent flat markdown log each turn
(token counts estimated at ~4 chars/token):

| What the agent loads | pgraph (this repo) | Spokesfan |
|----------------------|-------------------:|----------:|
| Flat log — re-read everything | ~1,593 tok | ~1,902 tok |
| `session_brief` — injected automatically on open | **~244 tok** | **~292 tok** |
| `context_pack` — one targeted query | ~904 tok | ~1,181 tok |
| **Reduction (brief vs flat)** | **~85%** | **~85%** |

The real win is **scaling**: a flat log grows linearly and unbounded, while the
auto-injected brief stays budget-bounded (~constant). The gap widens with every
session:

| Log entries | Flat log (re-read all) | pgraph brief | Saved |
|------------:|-----------------------:|-------------:|------:|
| 40          | ~1,700 tok             | ~250 tok     | ~85%  |
| 200         | ~8,600 tok             | ~250 tok     | ~97%  |
| 1,000       | ~43,000 tok            | ~250 tok     | ~99%  |
| 5,000       | ~215,000 tok           | ~250 tok     | ~99.9% |

A 1,000-edit project would burn ~43k tokens *per turn* just re-reading its log;
pgraph keeps that bounded to a few hundred, querying for more only when needed.

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

No compiled dependencies — storage is the Python standard library (`sqlite3`).
Runs on **Python 3.11+** (including 3.14 and later). A plain venv is enough:

```bash
python -m venv .venv
source .venv/bin/activate
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
pgraph search "JWT" --label Decision          # full-text search the "why" notes
pgraph doctor                                 # health check: durability, integrity, search, export
pgraph eval                                   # measure token savings vs a flat log
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
`set_decision_status`, `record_skill_use`, `recent_changes`, `file_history`,
`context_pack`, `search`, `status`, `doctor`, `sql`.
It also exposes two MCP resources — `pgraph://brief` and `pgraph://status` — so
a client can pull project memory as context without a tool call.
The headliner is **`context_pack(paths, budget)`** — hand it the files you're
about to touch and it returns only the relevant recent changes + decisions,
trimmed to a character budget, instead of a whole log.

## Automatic capture (Claude Code hooks)

Wire auto-capture into a project's `.claude/settings.json`:

```bash
pgraph install-hooks --pgraph-bin "$(command -v pgraph)"
```

This adds (merging, never clobbering existing hooks):

- a **SessionStart** hook → injects a compact brief of the project's memory
  (last session, recent changes, recent decisions) so the agent opens already
  knowing what's been done — in the project root *or any nested repo*;
- a **PostToolUse** hook on `Write|Edit|NotebookEdit` → records a `Change`
  (auto-opening a session if none is active);
- a **Stop** hook → ends the open session and writes a fresh JSONL export.

Hooks are best-effort and never raise into Claude Code — a memory hiccup can't
block your work.

## Portability model

- **Primary store:** `.pgraph/graph` — a single SQLite file; copy it and you've
  moved the whole memory.
- **Insurance / git:** `.pgraph/export/*.jsonl` — human-readable, diffable, and
  re-importable on any machine or Python version. Commit the export; gitignore
  the live DB (see `.gitignore`).

## Tests

```bash
pip install -e ".[dev]"
pytest -q          # 26 tests, runs in well under a second
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

- Storage is **stdlib `sqlite3`** — no compiled dependency, durable (WAL), and a
  single portable file. pgraph only does simple 1–2 hop lookups, which indexed
  SQLite joins handle easily, so no native-graph engine is needed.
- Earlier versions (≤ 0.1.x) used [Kùzu](https://kuzudb.com); it was dropped in
  0.2.0 after its repo was archived (Oct 2025). The JSONL export was the
  migration path — see [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE).
