# MCP server

`pgraph` ships an [MCP](https://modelcontextprotocol.io) server so any
MCP-capable agent can read and write the project graph directly — no shelling
out to the CLI. It is built on `mcp.server.fastmcp.FastMCP` and runs over
stdio via the `pgraph-mcp` entry point.

## Registering with Claude Code

Run from inside the project (or set `PGRAPH_ROOT`):

```bash
claude mcp add pgraph -- pgraph-mcp
```

The server resolves the project root in this order:

1. `$PGRAPH_ROOT` if set,
2. else the nearest `.pgraph/graph` walking up from its working directory,
3. else the current directory.

If no graph exists yet, the server **auto-initializes** one — an agent never
hits a "run init first" wall.

## Exposed tools

| Tool | Signature | Purpose |
|------|-----------|---------|
| `session_start` | `(agent, summary="")` → id | Open a work session. |
| `session_end` | `(session_id="", summary="")` | Close a session (defaults to latest open). |
| `log_change` | `(kind, path, summary="", session_id="", diff_stat="")` → id | Record a change. `kind ∈ edit\|create\|delete\|commit`. |
| `log_decision` | `(title, body="", motivates_change_ids=[], about_paths=[])` → id | Record a "why" note. |
| `record_skill_use` | `(session_id, skill)` | Track a skill/tool used. |
| `recent_changes` | `(limit=20, since="", path="")` → list | Targeted recent history. |
| `file_history` | `(path)` → dict | All changes + decisions for one file. |
| `context_pack` | `(paths=None, budget=4000)` → dict | **The token-saver** — compact bundle for the files you're about to touch. |
| `cypher` | `(query_text)` → list | Arbitrary read query. |

## The headline: `context_pack`

This is the reason the project exists. Instead of an agent re-reading a growing
log on every turn, it calls:

```jsonc
context_pack({ "paths": ["src/auth.py"], "budget": 4000 })
```

and gets back only the most recent relevant changes and decisions for those
files, trimmed to a character budget (a cheap proxy for tokens). The full
output shape and budgeting rules are documented in
[CONTEXT_PACK.md](CONTEXT_PACK.md).

## Recommended agent workflow

1. `session_start("claude-code", "implementing X")` at the start of work.
2. `context_pack([files you're about to edit])` to load just-enough history.
3. As you work, `log_change(...)` per edit and `log_decision(...)` for the
   non-obvious "why" — or let the [hooks](HOOKS.md) capture changes
   automatically.
4. `session_end(summary="...")` to close out with intent for next time.

## Relationship to the CLI

The MCP tools and the CLI commands are two faces of the same
`capture`/`query` functions. Anything an agent does over MCP, a human can do
with `pgraph` in the terminal, and vice versa — the graph doesn't care who
wrote to it.
