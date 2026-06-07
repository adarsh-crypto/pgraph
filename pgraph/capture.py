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
    if g.get_node("File", path) is None:
        g.add_node("File", path, {"path": path, "lang": lang_for(path), "last_seen_at": now()})
    else:
        g.set_node_props("File", path, {"last_seen_at": now()})


# -- sessions --------------------------------------------------------------
def start_session(g: Graph, agent: str, summary: str = "") -> str:
    """Open a work session, link it to the project and agent, return its id."""
    sid = new_id()
    g.add_node(
        "Session",
        sid,
        {"id": sid, "agent_name": agent, "started_at": now(), "ended_at": None, "summary": summary},
    )
    proj = g.match_nodes("Project", limit=1)
    if proj:
        g.add_edge("IN_PROJECT", "Session", sid, "Project", proj[0]["id"])
    if g.get_node("Agent", agent) is None:
        g.add_node("Agent", agent, {"id": agent, "name": agent})
    g.add_edge("BY_AGENT", "Session", sid, "Agent", agent)
    return sid


def end_session(g: Graph, session_id: str, summary: str = "") -> None:
    """Close a session, stamping ended_at and (optionally) a summary."""
    updates = {"ended_at": now()}
    if summary:
        updates["summary"] = summary
    g.set_node_props("Session", session_id, updates)


def latest_open_session(g: Graph) -> str | None:
    """Most recently started session that hasn't been ended yet (for hooks)."""
    rows = g.match_nodes(
        "Session", where=[("ended_at", "IS", None)],
        order_by="started_at", desc=True, limit=1,
    )
    return rows[0]["id"] if rows else None


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
    g.add_node(
        "Change",
        cid,
        {"id": cid, "kind": kind, "path": path, "summary": summary,
         "diff_stat": diff_stat, "ts": ts or now()},
    )
    upsert_file(g, path)
    g.add_edge("AFFECTS", "Change", cid, "File", path)
    if session_id:
        g.add_edge("IN_SESSION", "Change", cid, "Session", session_id)
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
    g.add_node("Decision", did, {"id": did, "title": title, "body": body, "ts": now()})
    for cid in motivates_change_ids or []:
        g.add_edge("MOTIVATES", "Decision", did, "Change", cid)
    for path in about_paths or []:
        upsert_file(g, path)
        g.add_edge("ABOUT", "Decision", did, "File", path)
    return did


# -- skills ----------------------------------------------------------------
def record_skill_use(g: Graph, session_id: str, skill: str) -> None:
    if g.get_node("Skill", skill) is None:
        g.add_node("Skill", skill, {"name": skill})
    # add_edge is deduped, so repeated use within a session adds one edge.
    g.add_edge("USED_SKILL", "Session", session_id, "Skill", skill)
