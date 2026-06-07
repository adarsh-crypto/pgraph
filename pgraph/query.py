"""Read side: token-efficient retrieval over the project graph.

Every function returns plain dicts/lists so the CLI can print them and the MCP
server can hand them straight back to an agent. The design goal is that an agent
fetches a *small, targeted* slice (recent changes for a file, the decisions that
explain them) instead of re-reading a whole markdown log.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from .db import Graph
from .schema import NODE_LABELS, REL_TYPES

# How much each change kind is worth when ranking what to surface. Decisions are
# scored separately (they're the scarce, high-value "why"); among changes, a
# human-authored commit/create outranks an auto-captured edit.
_KIND_WEIGHT = {"commit": 1.6, "create": 1.3, "delete": 1.2, "edit": 1.0}

# Recency half-life in days: a change this many days older than the newest one
# in the candidate set is worth half as much. Decay is computed relative to the
# newest timestamp in the set (not wall-clock), so it's deterministic.
_HALF_LIFE_DAYS = 30.0


def _parse_ts(v: Any) -> _dt.datetime | None:
    if isinstance(v, _dt.datetime):
        return v
    if isinstance(v, str):
        try:
            return _dt.datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


def _recency_factor(ts: Any, newest: _dt.datetime | None) -> float:
    """Exponential-decay weight in (0, 1], 1.0 for the newest item."""
    t = _parse_ts(ts)
    if t is None or newest is None:
        return 1.0
    age_days = max(0.0, (newest - t).total_seconds() / 86400.0)
    return 0.5 ** (age_days / _HALF_LIFE_DAYS)


def _cost(obj: Any) -> int:
    """Character cost of an object once serialized — a cheap token proxy."""
    return len(json.dumps(obj, default=str))


def _iso(v: Any) -> Any:
    """Render datetimes as ISO strings so results are JSON-serializable.

    Timestamps are already stored as ISO strings, so this is a safety net for
    any stray datetime; it's a no-op on the common path.
    """
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _iso(v) for k, v in row.items()}


def _pick(node: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Project selected props out of a node, ISO-cleaning values."""
    return {k: _iso(node.get(k)) for k in keys}


def recent_changes(
    g: Graph, limit: int = 20, since: str | None = None, path: str | None = None
) -> list[dict[str, Any]]:
    """Most recent changes, newest first, optionally filtered by file or time."""
    where: list[tuple[str, str, Any]] = []
    if path:
        where.append(("path", "=", path))
    if since:
        where.append(("ts", ">=", since))
    rows = g.match_nodes("Change", where=where, order_by="ts", desc=True, limit=limit)
    return [_pick(r, "id", "kind", "path", "summary", "diff_stat", "ts") for r in rows]


def file_history(g: Graph, path: str) -> dict[str, Any]:
    """All changes touching *path* plus the decisions about it — the full story of one file."""
    change_nodes = g.in_neighbors("AFFECTS", "File", path, "Change", order_by="ts", desc=True)
    changes = [_pick(c, "id", "kind", "summary", "diff_stat", "ts") for c in change_nodes]
    decision_nodes = g.in_neighbors("ABOUT", "File", path, "Decision", order_by="ts", desc=True)
    decisions = [_pick(d, "id", "title", "body", "status", "ts") for d in decision_nodes]
    return {"path": path, "changes": changes, "decisions": decisions}


def search(g: Graph, term: str, labels: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Full-text search across decisions, changes, files, … best matches first.

    Relevance-ranked (BM25) when the SQLite build has FTS5; otherwise a
    recency-ordered substring fallback. Returns each hit's props (ISO-cleaned)
    with its node label under ``_label``.
    """
    return [_clean(hit) for hit in g.search(term, labels=labels, limit=limit)]


def decisions_for(g: Graph, change_id: str) -> list[dict[str, Any]]:
    """Decisions that motivated a given change."""
    nodes = g.in_neighbors("MOTIVATES", "Change", change_id, "Decision", order_by="ts", desc=True)
    return [_pick(d, "id", "title", "body", "ts") for d in nodes]


def session_summary(g: Graph, session_id: str | None = None) -> dict[str, Any]:
    """Summary of one session (or the latest): metadata, its changes and skills."""
    if session_id:
        sess = g.get_node("Session", session_id)
    else:
        rows = g.match_nodes("Session", order_by="started_at", desc=True, limit=1)
        sess = rows[0] if rows else None
    if not sess:
        return {}
    sid = sess["id"]
    change_nodes = g.in_neighbors("IN_SESSION", "Session", sid, "Change", order_by="ts", desc=True)
    changes = [_pick(c, "id", "kind", "path", "summary", "ts") for c in change_nodes]
    skills = g.out_neighbors("USED_SKILL", "Session", sid, "Skill")
    out = _pick(sess, "id", "agent_name", "started_at", "ended_at", "summary")
    # Preserve the historical 'agent' alias used by callers/tests.
    out["agent"] = out.pop("agent_name")
    out["changes"] = changes
    out["skills"] = [s["name"] for s in skills]
    return out


def status(g: Graph) -> dict[str, Any]:
    """A quick health check: per-label node counts, edge counts, open session.

    Cheap to run and handy as a sanity check after init/scan/ingest or to see
    whether a session is currently open.
    """
    nodes: dict[str, int] = {}
    total_nodes = 0
    for label in NODE_LABELS:
        n = g.count_nodes(label)
        nodes[label] = n
        total_nodes += n

    edges: dict[str, int] = {}
    total_edges = 0
    for rel in REL_TYPES:
        n = g.count_edges(rel)
        edges[rel] = n
        total_edges += n

    open_rows = g.match_nodes(
        "Session", where=[("ended_at", "IS", None)],
        order_by="started_at", desc=True, limit=1,
    )
    open_sess = None
    if open_rows:
        open_sess = _pick(open_rows[0], "id", "agent_name", "started_at")
        open_sess["agent"] = open_sess.pop("agent_name")
    return {
        "nodes": nodes,
        "edges": edges,
        "totals": {"nodes": total_nodes, "edges": total_edges},
        "open_session": open_sess,
    }


def session_brief(g: Graph, limit: int = 8, budget: int = 2000) -> str:
    """A compact markdown brief of where the project stands — for session start.

    Built to be injected verbatim into an agent's context when it opens in the
    project (or any nested repo): the last session's intent, the most recent
    changes, and any open decisions. Trimmed to a rough character *budget* so it
    stays cheap. Returns ``""`` when there's nothing worth saying yet.
    """
    st = status(g)
    # Nothing worth briefing until there's actual recorded work — a bare graph
    # holds only the bootstrap Project node.
    if st["nodes"]["Session"] + st["nodes"]["Change"] + st["nodes"]["Decision"] == 0:
        return ""

    lines: list[str] = ["# pgraph project memory", ""]

    sess = session_summary(g)
    if sess:
        when = sess.get("ended_at") or sess.get("started_at") or ""
        state = "open" if not sess.get("ended_at") else "last"
        summary = sess.get("summary") or "(no summary recorded)"
        lines.append(f"**{state.capitalize()} session** ({sess.get('agent', '?')}, {when}): {summary}")
        lines.append("")

    recent = recent_changes(g, limit=limit)
    if recent:
        lines.append("**Recent changes:**")
        for c in recent:
            note = c.get("summary") or ""
            lines.append(f"- `{c.get('path', '?')}` — {c.get('kind', '?')}{(': ' + note) if note else ''}")
        lines.append("")

    # Only surface live decisions — a superseded/rejected "why" is noise. Nodes
    # written before the status field existed have no status and count as live.
    all_decisions = g.match_nodes("Decision", order_by="ts", desc=True, limit=20)
    decision_nodes = [
        d for d in all_decisions if d.get("status", "accepted") == "accepted"
    ][:5]
    if decision_nodes:
        lines.append("**Recent decisions (the *why*):**")
        for d in decision_nodes:
            body = (d.get("body") or "").strip().replace("\n", " ")
            if len(body) > 160:
                body = body[:157] + "..."
            lines.append(f"- **{d.get('title', '?')}**{(' — ' + body) if body else ''}")
        lines.append("")

    lines.append(
        f"_{st['totals']['nodes']} nodes, {st['totals']['edges']} edges. "
        "Query more with the `pgraph` MCP tools (`context_pack`, `file_history`, `recent_changes`)._"
    )

    brief = "\n".join(lines).rstrip() + "\n"
    if len(brief) > budget:
        brief = brief[:budget].rstrip() + "\n…(truncated)\n"
    return brief


def context_pack(
    g: Graph, paths: list[str] | None = None, budget: int = 4000
) -> dict[str, Any]:
    """Build a compact, ranked context bundle for the files about to be worked on.

    This is the headline retrieval the whole project exists for: instead of an
    agent re-reading a growing log, it asks for context on a handful of paths
    (or, with no paths, the project's recent activity) and gets back only the
    most relevant changes and decisions — trimmed to a rough character *budget*
    (a cheap proxy for tokens).

    Ranking blends **recency** (exponential decay) with **importance** (change
    kind; decisions weighted highest), and duplicate edits to the same file with
    the same summary are collapsed so the budget isn't spent on repetition.
    """
    pack: dict[str, Any] = {"files": [], "recent": [], "open_questions": []}
    used = 0

    def room(obj: Any) -> bool:
        nonlocal used
        cost = _cost(obj)
        if used + cost > budget:
            return False
        used += cost
        return True

    if paths:
        for p in paths:
            hist = file_history(g, p)
            entry = {
                "path": p,
                "changes": _dedupe_changes(hist["changes"])[:5],
                "decisions": hist["decisions"][:3],
            }
            if room(entry):
                pack["files"].append(entry)
    else:
        ranked = _rank_changes(_dedupe_changes(recent_changes(g, limit=50)))
        for c in ranked:
            if len(pack["recent"]) >= 15:
                break
            if room(c):
                pack["recent"].append(c)

    # Always surface the latest session's intent if there's budget left.
    sess = session_summary(g)
    if sess and used < budget:
        pack["latest_session"] = {
            "agent": sess.get("agent"),
            "summary": sess.get("summary"),
            "started_at": sess.get("started_at"),
        }
    pack["_budget"] = {"chars_used": used, "budget": budget}
    return pack


def _dedupe_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated (path, summary) changes, keeping the newest occurrence.

    Auto-capture hooks fire on every save, so the same file gets many identical
    "(auto) file write" entries — without this they'd crowd out everything else.
    Input is assumed newest-first; output preserves that order.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for c in changes:
        key = (c.get("path", ""), c.get("summary", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _rank_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort changes by importance × recency-decay, highest first (stable)."""
    timestamps = [_parse_ts(c.get("ts")) for c in changes]
    newest = max((t for t in timestamps if t is not None), default=None)

    def score(c: dict[str, Any]) -> float:
        kind_w = _KIND_WEIGHT.get(c.get("kind", ""), 1.0)
        return kind_w * _recency_factor(c.get("ts"), newest)

    return sorted(changes, key=score, reverse=True)
