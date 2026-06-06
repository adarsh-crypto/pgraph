# Graph schema

`pgraph` stores project memory as a Kùzu **property graph**: typed node tables
and typed relationship tables. The DDL lives in
[`schema.py`](../pgraph/schema.py) and every statement uses
`CREATE ... IF NOT EXISTS`, so calling `init()` again is a safe, lightweight
migration — add a table to the list and the next `init` picks it up without
touching existing data.

## Node tables

| Label | Primary key | Properties | Meaning |
|-------|-------------|------------|---------|
| `Project` | `id` | `name`, `root_path`, `created_at` | The project root. Exactly one per graph. |
| `Agent` | `id` | `name` | A coding agent, e.g. `claude-code`, `codex`. |
| `Session` | `id` | `agent_name`, `started_at`, `ended_at`, `summary` | One work session. `ended_at` is `NULL` while open. |
| `Change` | `id` | `kind`, `path`, `summary`, `diff_stat`, `ts` | A file edit/create/delete, or a git `commit`. |
| `File` | `path` | `lang`, `last_seen_at` | A tracked file. Keyed by project-relative path. |
| `Decision` | `id` | `title`, `body`, `ts` | A manual "why" note — intent a diff can't show. |
| `Repo` | `id` | `name`, `url`, `path`, `kind` | A cloned dependency/doc repo found inside the project. `kind ∈ repo\|doc`. |
| `Skill` | `name` | — | A skill/tool used in a session. |

`Change.kind` is constrained at the application layer to
`edit | create | delete | commit` (see `capture.VALID_CHANGE_KINDS`).

## Relationship tables

| Relationship | From → To | Meaning |
|--------------|-----------|---------|
| `IN_PROJECT` | `Session` → `Project` | the session belongs to this project |
| `BY_AGENT` | `Session` → `Agent` | which agent ran the session |
| `IN_SESSION` | `Change` → `Session` | the change happened in this session |
| `AFFECTS` | `Change` → `File` | the change touched this file |
| `MOTIVATES` | `Decision` → `Change` | this decision explains that change |
| `ABOUT` | `Decision` → `File` | this decision concerns that file |
| `USED_SKILL` | `Session` → `Skill` | this skill was used in the session |
| `IN_REPO` | `File` → `Repo` | the file lives in a nested cloned repo |

## Diagram

```
        ┌─────────┐  IN_PROJECT   ┌─────────┐
        │ Session │──────────────▶│ Project │
        └────┬────┘               └─────────┘
             │ BY_AGENT ┌───────┐
             ├─────────▶│ Agent │
             │          └───────┘
             │ USED_SKILL ┌───────┐
             └───────────▶│ Skill │
                          └───────┘
   IN_SESSION ▲
              │
        ┌─────┴────┐  AFFECTS   ┌──────┐  IN_REPO   ┌──────┐
        │  Change  │───────────▶│ File │───────────▶│ Repo │
        └──────────┘            └──────┘            └──────┘
              ▲                    ▲
     MOTIVATES │           ABOUT   │
              ┌┴──────────┐        │
              │ Decision  │────────┘
              └───────────┘
```

## Timestamps and IDs

- All time columns are Kùzu `TIMESTAMP`. `schema.now()` returns a naive UTC
  datetime **with microseconds** — rapid same-second edits still sort
  correctly.
- Node IDs are minted in Python: `schema.new_id()` returns a UUID4 hex.
- Git commits are the exception: their `Change.id` is
  `commit:<repo-name>:<sha>`, which makes `pgraph ingest-git` idempotent — a
  re-run skips commits already present.

## Example Cypher

These run via `pgraph cypher '<query>'` or the MCP `cypher` tool.

```cypher
-- Every change to one file, newest first
MATCH (c:Change)-[:AFFECTS]->(f:File {path:'src/auth.py'})
RETURN c.ts, c.kind, c.summary ORDER BY c.ts DESC;

-- Decisions and the changes they motivated
MATCH (d:Decision)-[:MOTIVATES]->(c:Change)
RETURN d.title, c.path, c.summary;

-- What did the last session do?
MATCH (c:Change)-[:IN_SESSION]->(s:Session)
WHERE s.ended_at IS NULL
RETURN s.agent_name, c.path, c.summary ORDER BY c.ts DESC;

-- Files belonging to a cloned doc repo
MATCH (f:File)-[:IN_REPO]->(r:Repo {kind:'doc'})
RETURN r.name, f.path;
```

## Why Kùzu, and the version pin

Kùzu is chosen because it is **embedded** (in-process, no server to run),
**native graph** (index-free adjacency for cheap multi-hop traversal), speaks
**Cypher**, and stores the whole database as a single on-disk file you can copy.

It is pinned at **0.11.3**: the upstream repo was archived in October 2025, so
this is the last stable release. Two practical consequences:

- **Python 3.11–3.13 only.** There is no Kùzu wheel for Python 3.14 yet. Use a
  conda env or venv on a supported version.
- **The JSONL export is the migration path.** If the engine ever needs
  swapping (for a Kùzu successor, or SQLite + recursive CTEs), the
  human-readable `nodes.jsonl`/`edges.jsonl` export is the format that carries
  your memory across. See [PORTABILITY.md](PORTABILITY.md).
