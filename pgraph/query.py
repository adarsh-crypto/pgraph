"""Read side: token-efficient retrieval over the project graph.

Every function returns plain dicts/lists so the CLI can print them and the MCP
server can hand them straight back to an agent. The design goal is that an agent
fetches a *small, targeted* slice (recent changes for a file, the decisions that
explain them) instead of re-reading a whole markdown log.
"""

from __future__ import annotations

from typing import Any

from .db import Graph


def _iso(v: Any) -> Any:
    """Render datetimes as ISO strings so results are JSON-serializable."""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _iso(v) for k, v in row.items()}


def recent_changes(
    g: Graph, limit: int = 20, since: str | None = None, path: str | None = None
) -> list[dict[str, Any]]:
    """Most recent changes, newest first, optionally filtered by file or time."""
    where = []
    params: dict[str, Any] = {"limit": limit}
    if path:
        where.append("c.path = $path")
        params["path"] = path
    if since:
        where.append("c.ts >= timestamp($since)")
        params["since"] = since
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = g.all(
        f"""MATCH (c:Change) {clause}
            RETURN c.id AS id, c.kind AS kind, c.path AS path,
                   c.summary AS summary, c.diff_stat AS diff_stat, c.ts AS ts
            ORDER BY c.ts DESC LIMIT $limit""",
        params,
    )
    return [_clean(r) for r in rows]


def file_history(g: Graph, path: str) -> dict[str, Any]:
    """All changes touching *path* plus the decisions about it — the full story of one file."""
    changes = g.all(
        """MATCH (c:Change)-[:AFFECTS]->(f:File {path:$path})
           RETURN c.id AS id, c.kind AS kind, c.summary AS summary,
                  c.diff_stat AS diff_stat, c.ts AS ts
           ORDER BY c.ts DESC""",
        {"path": path},
    )
    decisions = g.all(
        """MATCH (d:Decision)-[:ABOUT]->(f:File {path:$path})
           RETURN d.id AS id, d.title AS title, d.body AS body, d.ts AS ts
           ORDER BY d.ts DESC""",
        {"path": path},
    )
    return {
        "path": path,
        "changes": [_clean(r) for r in changes],
        "decisions": [_clean(r) for r in decisions],
    }


def decisions_for(g: Graph, change_id: str) -> list[dict[str, Any]]:
    """Decisions that motivated a given change."""
    rows = g.all(
        """MATCH (d:Decision)-[:MOTIVATES]->(c:Change {id:$cid})
           RETURN d.id AS id, d.title AS title, d.body AS body, d.ts AS ts
           ORDER BY d.ts DESC""",
        {"cid": change_id},
    )
    return [_clean(r) for r in rows]


def session_summary(g: Graph, session_id: str | None = None) -> dict[str, Any]:
    """Summary of one session (or the latest): metadata, its changes and skills."""
    if session_id:
        sess = g.one(
            """MATCH (s:Session {id:$id})
               RETURN s.id AS id, s.agent_name AS agent, s.started_at AS started_at,
                      s.ended_at AS ended_at, s.summary AS summary""",
            {"id": session_id},
        )
    else:
        sess = g.one(
            """MATCH (s:Session)
               RETURN s.id AS id, s.agent_name AS agent, s.started_at AS started_at,
                      s.ended_at AS ended_at, s.summary AS summary
               ORDER BY s.started_at DESC LIMIT 1"""
        )
    if not sess:
        return {}
    sid = sess["id"]
    changes = g.all(
        """MATCH (c:Change)-[:IN_SESSION]->(s:Session {id:$sid})
           RETURN c.id AS id, c.kind AS kind, c.path AS path, c.summary AS summary, c.ts AS ts
           ORDER BY c.ts DESC""",
        {"sid": sid},
    )
    skills = g.all(
        "MATCH (s:Session {id:$sid})-[:USED_SKILL]->(sk:Skill) RETURN sk.name AS name",
        {"sid": sid},
    )
    out = _clean(sess)
    out["changes"] = [_clean(r) for r in changes]
    out["skills"] = [r["name"] for r in skills]
    return out


def context_pack(
    g: Graph, paths: list[str] | None = None, budget: int = 4000
) -> dict[str, Any]:
    """Build a compact, ranked context bundle for the files about to be worked on.

    This is the headline retrieval the whole project exists for: instead of an
    agent re-reading a growing log, it asks for context on a handful of paths
    (or, with no paths, the project's recent activity) and gets back only the
    most recent, relevant changes and decisions — trimmed to a rough character
    *budget* (a cheap proxy for tokens).
    """
    pack: dict[str, Any] = {"files": [], "recent": [], "open_questions": []}
    used = 0

    def room(obj: dict) -> bool:
        nonlocal used
        cost = sum(len(str(v)) for v in obj.values())
        if used + cost > budget:
            return False
        used += cost
        return True

    if paths:
        for p in paths:
            hist = file_history(g, p)
            entry = {
                "path": p,
                "changes": hist["changes"][:5],
                "decisions": hist["decisions"][:3],
            }
            if room({"_": entry}):
                pack["files"].append(entry)
    else:
        for c in recent_changes(g, limit=15):
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
