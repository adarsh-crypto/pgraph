# Portability & the JSONL export

A core requirement of `pgraph` is that project memory is **just files you can
transfer** — zip it, copy it between machines, or commit a diffable snapshot to
git. There are two layers to this, with different jobs.

## Two layers

### 1. The primary store — `.pgraph/graph`
The live Kùzu database. Copy this single file and you've moved the entire
memory, instantly and losslessly. It's the fast path the CLI/MCP/hooks read and
write. But it's a binary, engine-specific format — not something you want to
diff in git, and not guaranteed to load on a different Kùzu version.

### 2. The insurance — `.pgraph/export/{nodes,edges}.jsonl`
A human-readable, line-oriented dump of every node and edge. This is:

- **git-diffable** — each node/edge is one JSON line, so a commit shows exactly
  what changed in your project memory;
- **engine-independent** — it can be re-imported even across Kùzu versions, and
  is the migration path if the engine is ever swapped;
- **the thing you commit** — the live DB is gitignored; the export is tracked.

## The git split (see [`.gitignore`](../.gitignore))

```gitignore
.pgraph/graph/      # live DB — machine-local, gitignored
.pgraph/graph*      # and its wal/shadow sidecars
# .pgraph/export/   # NOT ignored — commit this
```

Commit the export; ignore the live DB. A teammate clones the repo and runs
`pgraph import` to rebuild a working graph from the JSONL.

## Export / import

```bash
pgraph export        # writes .pgraph/export/{nodes,edges}.jsonl
pgraph import        # rebuilds the graph from that export
```

The [Stop hook](HOOKS.md) also runs `export` automatically at the end of every
session, so a fresh snapshot always exists without you remembering to dump it.

## File format

**`nodes.jsonl`** — one node per line:
```jsonc
{ "label": "Change", "props": { "id": "ab12…", "kind": "edit", "path": "src/auth.py", "summary": "add JWT", "diff_stat": "", "ts": "2026-06-07T12:00:00" } }
```

**`edges.jsonl`** — one edge per line, endpoints referenced by primary key:
```jsonc
{ "rel": "AFFECTS", "from": "Change", "to": "File", "src": "ab12…", "dst": "src/auth.py" }
```

Timestamps are serialized as ISO-8601 strings and parsed back into `TIMESTAMP`
columns on import (`export._deser`). Import is **idempotent for nodes**: it
`MERGE`s each node on its primary key, then sets the remaining properties — so
re-importing the same export won't create duplicates.

## Moving memory between machines

Either approach works:

- **Fast / exact:** copy the whole `.pgraph/` folder (live DB + export).
- **Portable / reviewable:** commit `.pgraph/export/` to git; on the other
  machine, `pgraph import` after cloning.

## Round-trip guarantee

The behaviour is covered by
[`tests/test_export_roundtrip.py`](../tests/test_export_roundtrip.py): it seeds
a graph, exports it, **wipes the live DB**, re-imports, and asserts that node
counts, edge counts, and relationship-traversal queries all come back
identical. Export → wipe → import → identical is a tested invariant, not an
aspiration.
