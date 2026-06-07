# CLI reference

The `pgraph` command is the human (and hook) entry point. Every command is a
thin wrapper over the same `capture`/`query`/`scan`/`export` functions the MCP
server exposes, so the terminal and an agent always behave identically.

All commands accept a global `--root PATH`. When omitted, `pgraph` walks upward
from the current directory looking for an existing `.pgraph/graph`; if none is
found it falls back to the current directory. Output is JSON (pretty-printed)
for query commands, and a bare id/line for write commands so results compose in
shell pipelines.

```
pgraph [--root PATH] COMMAND [ARGS]
```

## Commands at a glance

| Command | Purpose |
|---------|---------|
| `init` | Create the graph DB + schema for this project. |
| `session start` | Open a work session; prints the new session id. |
| `session end` | Close a session (defaults to the latest open one). |
| `session show` | Print a session summary (metadata, changes, skills). |
| `log` | Record a file change. |
| `decide` | Record a manual "why" note (a decision). |
| `skill` | Record that a skill/tool was used in a session. |
| `history` | Recent changes, or one file's full history. |
| `context` | Build a compact, budgeted context bundle (the token-saver). |
| `search` | Full-text search decisions/changes/files (BM25-ranked). |
| `scan` | Walk the folder, recording File + nested Repo nodes. |
| `ingest-git` | Backfill commit history (project + cloned repos). |
| `export` | Dump the graph to git-diffable JSONL. |
| `import` | Rebuild a graph from a JSONL export. |
| `status` | Health summary: node/edge counts + any open session. |
| `sql` | Run an arbitrary **read-only** SQL query (escape hatch). |
| `install-hooks` | Wire auto-capture into `.claude/settings.json`. |

There are also three hidden commands — `hook-session-start`,
`hook-post-tool-use`, and `hook-stop` — that Claude Code invokes with JSON on
stdin; you never run these by hand.

## Detailed usage

### `init`
```bash
pgraph init
```
Creates `<root>/.pgraph/graph` and the schema, and ensures a single `Project`
node exists. Safe to re-run (acts as a migration).

### `session start` / `end` / `show`
```bash
SID=$(pgraph session start --agent claude-code --summary "wiring auth")
pgraph session show --id "$SID"
pgraph session end --id "$SID" --summary "auth done"
```
`--id` defaults to the latest open session for both `end` and `show`, so the
common case is just `pgraph session end --summary "..."`.

### `log`
```bash
CID=$(pgraph log --kind edit --path src/auth.py --summary "add JWT verify")
```
`--kind` must be one of `edit | create | delete | commit`. `--session` defaults
to the latest open session. Prints the new change id.

### `decide`
```bash
pgraph decide --title "Use JWT not sessions" \
              --body "stateless, scales horizontally" \
              --motivates "$CID" \
              --about src/auth.py
```
`--motivates` (a change id) and `--about` (a file path) are both repeatable.

### `skill`
```bash
pgraph skill deep-research --session "$SID"
```
Deduped per session — recording the same skill twice adds one edge.

### `history`
```bash
pgraph history                       # recent changes across the project
pgraph history --path src/auth.py    # one file's changes + decisions
pgraph history --limit 50 --since 2026-01-01T00:00:00
```

### `context`
```bash
pgraph context src/auth.py src/db.py --budget 4000
```
The headline retrieval. Pass the files you're about to touch; get back only the
recent changes + decisions for them, trimmed to a rough character budget. With
no paths it returns recent project-wide activity. See
[CONTEXT_PACK.md](CONTEXT_PACK.md) for the output shape and budgeting rules.

### `search`
```bash
pgraph search "connection pool"            # across all node types
pgraph search "JWT" --label Decision       # restrict to decisions (repeatable)
pgraph search "auth" --limit 5
```
Full-text search over the human-meaningful text of nodes — decision titles +
bodies, change summaries, file paths, repo names. Uses SQLite **FTS5/BM25** for
relevance ranking when available (each hit carries a `_score`; lower is better),
and degrades to a recency-ordered substring scan if the SQLite build lacks
FTS5. Each result is the matched node's properties plus its `_label`. This is
how an agent finds *why* something was done when it doesn't know the file path.

### `scan` / `ingest-git`
```bash
pgraph scan                  # record File + nested Repo/doc nodes
pgraph ingest-git --limit 200
```
`scan` walks the tree (skipping `node_modules`, `.venv`, build dirs, hidden
dirs, etc.) and detects nested git repos as `Repo` nodes. `ingest-git`
backfills commit history for the project root and every detected repo.

### `export` / `import`
```bash
pgraph export                # writes .pgraph/export/{nodes,edges}.jsonl
# ... elsewhere, in the project:
pgraph import                # rebuilds the graph from the JSONL
```
See [PORTABILITY.md](PORTABILITY.md).

### `status`
```bash
pgraph status
```
Prints per-label node counts, per-type edge counts, totals, and the currently
open session (if any). A cheap sanity check after `init`/`scan`/`ingest-git`.

### `sql`
```bash
pgraph sql "SELECT json_extract(props,'\$.path') AS path,
                   json_extract(props,'\$.summary') AS summary
            FROM nodes WHERE label='Change'
            ORDER BY json_extract(props,'\$.ts') DESC LIMIT 5"
```
Read-only escape hatch over the two storage tables — `nodes(label, pk, props)`
(props is JSON; use `json_extract(props, '$.field')`) and
`edges(rel, src_label, src_pk, dst_label, dst_pk)`. Write statements (`INSERT`,
`UPDATE`, `DELETE`, `DROP`, …) are **rejected**, and the query runs on a
read-only connection as a hard backstop. Values are stringified for safe JSON
output.

### `install-hooks`
```bash
pgraph install-hooks --pgraph-bin "$(command -v pgraph)"
```
Merges the auto-capture hooks (SessionStart context injection, PostToolUse
change capture, Stop snapshot) into `<root>/.claude/settings.json` without
clobbering existing hooks. See [HOOKS.md](HOOKS.md).

## Exit codes

Query and write commands raise a Click error (non-zero exit) on misuse — for
example, running a command before `pgraph init`, or `session end` with no open
session. The two hidden hook commands always exit `0`: they must never break
the host agent.
