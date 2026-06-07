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
| `decision-status` | Set a decision's lifecycle status. |
| `skill` | Record that a skill/tool was used in a session. |
| `history` | Recent changes, or one file's full history. |
| `context` | Build a compact, budgeted context bundle (the token-saver). |
| `search` | Full-text search decisions/changes/files (BM25-ranked). |
| `eval` | Measure token savings vs. a flat-log baseline. |
| `scan` | Walk the folder, recording File + nested Repo nodes. |
| `ingest-git` | Backfill commit history (project + cloned repos). |
| `import-chats` | Import Claude Code / Codex chat transcripts (sessions, prompts, edits). |
| `export` | Dump the graph to git-diffable JSONL. |
| `import` | Rebuild a graph from a JSONL export. |
| `status` | Health summary: node/edge counts + any open session. |
| `doctor` | Diagnose graph health: durability, integrity, search index, export freshness. |
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
              --about src/auth.py \
              --supersedes "$OLD_DECISION_ID"
```
`--motivates` (a change id), `--about` (a file path), and `--supersedes` (a
prior decision id) are all repeatable. A new decision starts with status
`accepted`; each `--supersedes` adds a `SUPERSEDES` edge to the older decision
and marks it `superseded`, so its stale rationale stops surfacing in the brief.

### `decision-status`
```bash
pgraph decision-status <decision_id> rejected
```
Set a decision's lifecycle `status` — one of `accepted | superseded |
rejected`. Superseded and rejected decisions are filtered out of the session
brief.

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

### `eval`
```bash
pgraph eval                                  # project-wide
pgraph eval src/auth.py --budget 4000        # for the files you'd touch
```
Measures the token savings pgraph buys. It reconstructs the flat markdown log an
agent would otherwise re-read every turn (the baseline) and compares its token
cost against `session_brief` and `context_pack`. Output is JSON: the token
`method` (exact via `tiktoken` if installed, otherwise a labelled `~4
chars/token` estimate), graph totals, `flat_log_tokens`, `session_brief_tokens`,
`context_pack_tokens`, and the `saved_pct` for each.

### `scan` / `ingest-git`
```bash
pgraph scan                  # record File + nested Repo/doc nodes
pgraph ingest-git --limit 200
```
`scan` walks the tree (skipping `node_modules`, `.venv`, build dirs, hidden
dirs, etc.) and detects nested git repos as `Repo` nodes. `ingest-git`
backfills commit history for the project root and every detected repo.

### `import-chats`
```bash
pgraph import-chats --dry-run                 # preview — no writes
pgraph import-chats                            # import both agents' transcripts
pgraph import-chats --source codex             # just Codex
pgraph import-chats --prompts truncated        # cap stored prompt text at ~200 chars
pgraph import-chats --prompts none             # store sessions/edits but no prompt text
pgraph import-chats --no-attribute             # skip the project-scope safety filter
```
Reads the agents' own session logs — Claude Code (`~/.claude/projects/<dashed-
cwd>/*.jsonl`) and Codex (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`) — and
folds them into the graph as `Session`, `Prompt`, `Change` and `Skill` nodes.
The user's **prompts** are the headline signal: the intent behind past work that
a diff can't reconstruct.

- **Deterministic, no LLM**: pure JSONL parsing and field extraction. Honors
  pgraph's non-LLM design.
- **Idempotent**: each transcript becomes a `Session` keyed by the agent's own
  session id, so re-running imports nothing new.
- **Project-scoped**: only sessions whose work happened in *this* project are
  imported, matched by path **or project-folder name**. The name match is what
  lets the same project's chats import after you move between machines or
  operating systems (Windows ↔ macOS ↔ Linux); a *differently named* project is
  still excluded. Foreign relative/Windows paths are never resolved against the
  local working directory (which could falsely match).
- **Privacy**: transcripts can contain secrets you typed. Use `--dry-run` to see
  exactly what would import first; `--prompts` controls how much text is stored;
  nothing ever leaves your machine. Importing is an explicit CLI action — it is
  never run from a hook.

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

### `doctor`
```bash
pgraph doctor              # readable ✓/!/✗ report + overall verdict
pgraph doctor --json       # raw JSON for scripts/CI
```
Runs health diagnostics and prints an overall verdict (`ok | warn | error`)
plus per-check detail: **durability** (`journal_mode=WAL`, `busy_timeout`),
**integrity** (orphaned edges — endpoints with no node), **search index** (FTS
health), and **export freshness** (the JSONL export's node count vs. the live
graph). The readable report marks each check `✓`/`!`/`✗`; `--json` emits the raw
report instead. The command **exits non-zero** when any check is at error level
(e.g. orphaned edges), so it doubles as a CI gate.

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
