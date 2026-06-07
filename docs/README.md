# pgraph documentation

Welcome to the `pgraph` docs. Start with the top-level
[README](../README.md) for the quickstart, then dive into whichever area you
need.

| Doc | What's inside |
|-----|---------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The big picture: modules, layers, data flow, design decisions. |
| [SCHEMA.md](SCHEMA.md) | The graph data model — node/relationship types, IDs, timestamps, example SQL. |
| [CLI.md](CLI.md) | Every `pgraph` command with examples. |
| [MCP.md](MCP.md) | The MCP server: registration and the tools agents call. |
| [CONTEXT_PACK.md](CONTEXT_PACK.md) | The headline token-saving query, in depth. |
| [HOOKS.md](HOOKS.md) | Automatic capture via Claude Code hooks. |
| [PORTABILITY.md](PORTABILITY.md) | The JSONL export, the git split, moving memory between machines. |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Setup, tests, layout, and how to extend the graph. |

## The one-paragraph version

`pgraph` gives a coding agent a per-project memory backed by an embedded graph
in a single, dependency-free SQLite file instead of a growing markdown log.
Changes, decisions,
sessions, files, repos and skills are recorded as timestamped nodes; an agent
queries a small, targeted slice — chiefly via
[`context_pack`](CONTEXT_PACK.md) — instead of re-reading everything, which
saves tokens. The whole memory is portable: it's files under `.pgraph/`, with a
git-diffable JSONL export as the cross-machine, cross-version insurance.
