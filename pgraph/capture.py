"""Write side: sessions, changes, decisions, skills and the edges between them."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .db import Graph
from .schema import new_id, now

VALID_CHANGE_KINDS = {"edit", "create", "delete", "commit"}

# Minimal extension -> language map; used only as a hint on File nodes.
_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".md": "markdown", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".sh": "shell", ".sql": "sql", ".html": "html", ".css": "css",
}


def lang_for(path: str) -> str:
    return _LANG_BY_EXT.get(Path(path).suffix.lower(), "")


# -- files -----------------------------------------------------------------
def upsert_file(g: Graph, path: str) -> None:
    """Ensure a File node exists for *path*, refreshing its last_seen_at."""
    g.run(
        """MERGE (f:File {path:$path})
           ON CREATE SET f.lang=$lang, f.last_seen_at=$ts
           ON MATCH SET f.last_seen_at=$ts""",
        {"path": path, "lang": lang_for(path), "ts": now()},
    )


# -- sessions --------------------------------------------------------------
def start_session(g: Graph, agent: str, summary: str = "") -> str:
    """Open a work session, link it to the project and agent, return its id."""
    sid = new_id()
    g.run(
        "CREATE (s:Session {id:$id, agent_name:$agent, started_at:$ts, ended_at:NULL, summary:$summary})",
        {"id": sid, "agent": agent, "ts": now(), "summary": summary},
    )
    proj = g.one("MATCH (p:Project) RETURN p.id AS id LIMIT 1")
    if proj:
        g.run(
            "MATCH (s:Session {id:$sid}),(p:Project {id:$pid}) CREATE (s)-[:IN_PROJECT]->(p)",
            {"sid": sid, "pid": proj["id"]},
        )
    g.run("MERGE (a:Agent {id:$id, name:$name})", {"id": agent, "name": agent})
    g.run(
        "MATCH (s:Session {id:$sid}),(a:Agent {id:$aid}) CREATE (s)-[:BY_AGENT]->(a)",
        {"sid": sid, "aid": agent},
    )
    return sid


def end_session(g: Graph, session_id: str, summary: str = "") -> None:
    """Close a session, stamping ended_at and (optionally) a summary."""
    if summary:
        g.run(
            "MATCH (s:Session {id:$id}) SET s.ended_at=$ts, s.summary=$summary",
            {"id": session_id, "ts": now(), "summary": summary},
        )
    else:
        g.run(
            "MATCH (s:Session {id:$id}) SET s.ended_at=$ts",
            {"id": session_id, "ts": now()},
        )


def latest_open_session(g: Graph) -> str | None:
    """Most recently started session that hasn't been ended yet (for hooks)."""
    row = g.one(
        "MATCH (s:Session) WHERE s.ended_at IS NULL RETURN s.id AS id ORDER BY s.started_at DESC LIMIT 1"
    )
    return row["id"] if row else None


# -- changes ---------------------------------------------------------------
def log_change(
    g: Graph,
    kind: str,
    path: str,
    summary: str = "",
    session_id: str | None = None,
    diff_stat: str = "",
    ts=None,
) -> str:
    """Record a Change, link it to its File and (if any) its Session."""
    if kind not in VALID_CHANGE_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_CHANGE_KINDS)}, got {kind!r}")
    cid = new_id()
    g.run(
        """CREATE (c:Change {id:$id, kind:$kind, path:$path, summary:$summary,
                             diff_stat:$diff, ts:$ts})""",
        {"id": cid, "kind": kind, "path": path, "summary": summary,
         "diff": diff_stat, "ts": ts or now()},
    )
    upsert_file(g, path)
    g.run(
        "MATCH (c:Change {id:$cid}),(f:File {path:$path}) CREATE (c)-[:AFFECTS]->(f)",
        {"cid": cid, "path": path},
    )
    if session_id:
        g.run(
            "MATCH (c:Change {id:$cid}),(s:Session {id:$sid}) CREATE (c)-[:IN_SESSION]->(s)",
            {"cid": cid, "sid": session_id},
        )
    return cid


# -- decisions -------------------------------------------------------------
def log_decision(
    g: Graph,
    title: str,
    body: str = "",
    motivates_change_ids: Iterable[str] | None = None,
    about_paths: Iterable[str] | None = None,
) -> str:
    """Record a manual 'why' note, optionally linking the changes/files it explains."""
    did = new_id()
    g.run(
        "CREATE (d:Decision {id:$id, title:$title, body:$body, ts:$ts})",
        {"id": did, "title": title, "body": body, "ts": now()},
    )
    for cid in motivates_change_ids or []:
        g.run(
            "MATCH (d:Decision {id:$did}),(c:Change {id:$cid}) CREATE (d)-[:MOTIVATES]->(c)",
            {"did": did, "cid": cid},
        )
    for path in about_paths or []:
        upsert_file(g, path)
        g.run(
            "MATCH (d:Decision {id:$did}),(f:File {path:$path}) CREATE (d)-[:ABOUT]->(f)",
            {"did": did, "path": path},
        )
    return did


# -- skills ----------------------------------------------------------------
def record_skill_use(g: Graph, session_id: str, skill: str) -> None:
    g.run("MERGE (sk:Skill {name:$name})", {"name": skill})
    # Avoid duplicate edges for repeated use within a session.
    existing = g.one(
        "MATCH (s:Session {id:$sid})-[:USED_SKILL]->(sk:Skill {name:$name}) RETURN sk.name AS n",
        {"sid": session_id, "name": skill},
    )
    if not existing:
        g.run(
            "MATCH (s:Session {id:$sid}),(sk:Skill {name:$name}) CREATE (s)-[:USED_SKILL]->(sk)",
            {"sid": session_id, "name": skill},
        )
