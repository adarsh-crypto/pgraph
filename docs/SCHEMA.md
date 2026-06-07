# Graph schema

`pgraph` stores project memory in a single **SQLite** file (Python stdlib
`sqlite3`) as two physical tables: a generic `nodes` table with a JSON `props`
blob, plus an `edges` table. The 9 node types and 10 edge types below are the
**logical** model — they're enforced by the application ([`capture.py`](../pgraph/capture.py))
and registered in [`schema.py`](../pgraph/schema.py)'s label registry
(`NODE_LABELS` / `REL_SPECS`), not as separate physical tables. `init()` creates
the two tables (via `Graph.init_schema()`) and ensures exactly one `Project`
node; it is still idempotent, so calling it again is safe.

The physical tables are:

- `nodes(label TEXT, pk TEXT, props TEXT, PRIMARY KEY(label, pk))` — `props` is
  a JSON blob holding all of the node's properties.
- `edges(rel, src_label, src_pk, dst_label, dst_pk)` — with indexes for fast
  src/dst lookups.

The node and relationship tables below are logical types; every node row lives
in the generic `nodes` table (keyed by `label` + primary key) and every edge in
the `edges` table.

## Node tables

| Label | Primary key | Properties | Meaning |
|-------|-------------|------------|---------|
| `Project` | `id` | `name`, `root_path`, `created_at` | The project root. Exactly one per graph. |
| `Agent` | `id` | `name` | A coding agent, e.g. `claude-code`, `codex`. |
| `Session` | `id` | `agent_name`, `started_at`, `ended_at`, `summary` | One work session. `ended_at` is `NULL` while open. |
| `Change` | `id` | `kind`, `path`, `summary`, `diff_stat`, `ts` | A file edit/create/delete, or a git `commit`. |
| `File` | `path` | `lang`, `last_seen_at` | A tracked file. Keyed by project-relative path. |
| `Decision` | `id` | `title`, `body`, `status`, `ts` | A manual "why" note — intent a diff can't show. `status ∈ accepted\|superseded\|rejected` (new decisions start `accepted`). |
| `Repo` | `id` | `name`, `url`, `path`, `kind` | A cloned dependency/doc repo found inside the project. `kind ∈ repo\|doc`. |
| `Skill` | `name` | — | A skill/tool used in a session. |
| `Prompt` | `id` | `text`, `ts`, `source` | A user prompt imported from a chat transcript (`pgraph import-chats`) — captured intent. `source ∈ claude\|codex`. |

`Change.kind` is constrained at the application layer to
`edit | create | delete | commit` (see `capture.VALID_CHANGE_KINDS`), and
`Decision.status` to `accepted | superseded | rejected` (see
`capture.VALID_DECISION_STATUSES`). Only `accepted` decisions appear in the
session brief — a superseded or rejected "why" is stale and is filtered out.

## Relationship tables

| Relationship | From → To | Meaning |
|--------------|-----------|---------|
| `IN_PROJECT` | `Session` → `Project` | the session belongs to this project |
| `BY_AGENT` | `Session` → `Agent` | which agent ran the session |
| `IN_SESSION` | `Change` → `Session` | the change happened in this session |
| `AFFECTS` | `Change` → `File` | the change touched this file |
| `MOTIVATES` | `Decision` → `Change` | this decision explains that change |
| `ABOUT` | `Decision` → `File` | this decision concerns that file |
| `SUPERSEDES` | `Decision` → `Decision` | a newer decision replaces an older one (the old one is marked `superseded`) |
| `USED_SKILL` | `Session` → `Skill` | this skill was used in the session |
| `IN_REPO` | `File` → `Repo` | the file lives in a nested cloned repo |
| `PROMPTED_IN` | `Prompt` → `Session` | this user prompt was made in that session |

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

- Timestamps are stored as **ISO-8601 strings** inside the JSON `props` (no
  native timestamp column). Fixed-format ISO strings sort lexicographically in
  chronological order, so `ORDER BY` and `>=` comparisons still work as
  expected. `schema.now()` returns a naive UTC datetime **with microseconds**
  (serialized to ISO on write) — so rapid same-second edits still sort
  correctly.
- Node IDs are minted in Python: `schema.new_id()` returns a UUID4 hex.
- Git commits are the exception: their `Change.id` is
  `commit:<repo-name>:<sha>`, which makes `pgraph ingest-git` idempotent — a
  re-run skips commits already present.

## Full-text search index

Alongside `nodes`/`edges`, an FTS5 virtual table `node_fts(label, pk, text)`
indexes the human-meaningful text of each node (decision title + body, change
summary, file path, repo name). It's kept in sync as nodes are written and
powers `pgraph search` / the `search` MCP tool with BM25 relevance ranking. FTS5
is feature-detected at init; if a SQLite build lacks it, search degrades to a
substring scan and the table is simply absent. The FTS index is derived data —
it is **not** part of the JSONL export and is rebuilt on `import`.

## Example SQL

These run via `pgraph sql '<query>'` or the MCP `sql` tool, and are
**read-only**. Joins go through the `edges` table; node properties are read out
of the JSON `props` with `json_extract(props, '$.field')`.

```sql
-- Every change to one file, newest first
SELECT json_extract(n.props, '$.ts')      AS ts,
       json_extract(n.props, '$.kind')    AS kind,
       json_extract(n.props, '$.summary') AS summary
FROM edges e
JOIN nodes n ON n.label = e.src_label AND n.pk = e.src_pk
WHERE e.rel = 'AFFECTS'
  AND e.dst_label = 'File'
  AND e.dst_pk = 'src/auth.py'
ORDER BY ts DESC;

-- Decisions and the changes they motivated
SELECT json_extract(d.props, '$.title')   AS title,
       json_extract(c.props, '$.path')    AS path,
       json_extract(c.props, '$.summary') AS summary
FROM edges e
JOIN nodes d ON d.label = e.src_label AND d.pk = e.src_pk
JOIN nodes c ON c.label = e.dst_label AND c.pk = e.dst_pk
WHERE e.rel = 'MOTIVATES';

-- All decisions
SELECT json_extract(props, '$.title') AS title
FROM nodes
WHERE label = 'Decision';
```

## Why SQLite (stdlib)

SQLite via the stdlib `sqlite3` module is chosen because it is **embedded**
(in-process, no server to run), **zero-dependency** (ships with Python, no
compiled artifacts to build or pin), **durable** (WAL mode), and stores the
whole database as a **single portable file** you can copy. It works on **every**
Python version, including 3.14+.

pgraph migrated here **from Kùzu** (an archived embedded graph DB that spoke
Cypher): Kùzu's wheels were locked to Python 3.11–3.13 and its repository was
archived in **October 2025**, which prompted the move. pgraph only ever does
simple 1–2 hop lookups, which indexed SQLite joins over the `edges` table handle
easily, so a native-graph engine wasn't needed.

**The JSONL export remains the portability format.** The human-readable
`nodes.jsonl`/`edges.jsonl` export is what carries your memory across machines
or storage engines. See [PORTABILITY.md](PORTABILITY.md).
