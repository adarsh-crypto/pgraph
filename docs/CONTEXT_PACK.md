# `context_pack` — the token-saving query

`context_pack` is the single most important read in `pgraph`. Everything else
exists to feed it. It answers: *"For the files I'm about to work on, what's the
least context I need that still captures the relevant recent changes and the
decisions behind them?"* — and it returns exactly that, trimmed to a budget.

Defined in [`query.py`](../pgraph/query.py); exposed as `pgraph context` on the
CLI and the `context_pack` MCP tool.

## Signature

```python
context_pack(g, paths: list[str] | None = None, budget: int = 4000) -> dict
```

- **`paths`** — the files you're about to touch. With paths, the pack is
  *file-centric*. With `paths=None`, it falls back to project-wide activity,
  **ranked by importance × recency** (see below).
- **`budget`** — an approximate **character** budget (a cheap proxy for
  tokens). Entries are added until the budget would be exceeded, then trimming
  stops.

## Ranking and dedupe

With `paths=None`, the pack doesn't just take the newest changes — it pulls a
wider candidate set and ranks each change by **importance × recency**:

- *importance* — a per-kind weight, so a `commit` or `create` outranks a routine
  `edit` of similar age;
- *recency* — exponential decay with a 30-day half-life, measured relative to
  the newest change in the set (so the ranking is deterministic, not
  wall-clock-dependent).

Repeated `(path, summary)` changes are **deduped** (keeping the newest), because
auto-capture hooks emit many identical `(auto) file write` entries that would
otherwise crowd out everything else. The top entries that fit the budget are
returned. The file-centric (`paths`) branch likewise dedupes per file.

## Output shape

```jsonc
{
  "files": [                       // present when paths were given
    {
      "path": "src/auth.py",
      "changes":  [ /* up to 5 most recent changes for this file */ ],
      "decisions":[ /* up to 3 most recent decisions about this file */ ]
    }
  ],
  "recent": [ /* present when paths was None: top-ranked project-wide changes */ ],
  "open_questions": [],            // reserved for future use
  "latest_session": {              // included if budget remains
    "agent": "claude-code",
    "summary": "wiring auth",
    "started_at": "2026-06-07T12:00:00"
  },
  "_budget": { "chars_used": 812, "budget": 4000 }
}
```

`_budget` always reports actual usage so the caller can see how much of the
budget the pack consumed, and `chars_used <= budget` is guaranteed.

## How budgeting works

A running `used` counter tracks the character cost of everything added so far.
Each candidate entry's cost is `sum(len(str(v)) for v in entry.values())`. An
entry is included only if it fits under `budget`; otherwise it's skipped and
trimming stops. After the main content, the latest session's intent is added
**if any budget remains** — so even a tight budget usually surfaces "what was
the agent last doing here."

This means a pack degrades gracefully: shrink the budget and you keep the most
recent, most relevant entries and drop the tail, rather than failing.

## Why this saves tokens

A flat markdown log forces *read-everything-to-find-anything*, and its cost
grows with total history. `context_pack` cost is bounded by the budget and
scoped to the handful of files in play — it's effectively **constant** in the
size of project history. The graph's index-free adjacency makes the underlying
`Change → File` and `Decision → File` lookups cheap multi-hop traversals rather
than scans.

## Usage

CLI:
```bash
pgraph context src/auth.py src/db.py --budget 4000
pgraph context --budget 2000            # no paths → recent activity
```

MCP:
```jsonc
context_pack({ "paths": ["src/auth.py"], "budget": 4000 })
```

## Tuning the budget

- **Small (1–2k chars):** a quick "what changed here lately" glance.
- **Default (4k):** enough for several files' recent changes + their decisions.
- **Large (8k+):** broad context when starting work on an unfamiliar area.

Because `_budget.chars_used` is reported back, an agent can start small and
re-request a larger budget only if the pack came back fuller than expected.
