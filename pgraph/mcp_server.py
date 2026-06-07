"""MCP stdio server exposing the project-memory graph to any MCP-capable agent.

Run via the ``pgraph-mcp`` entry point. Register with Claude Code:

    claude mcp add pgraph -- pgraph-mcp

The server operates on the project graph found by walking up from its working
directory (or ``$PGRAPH_ROOT`` if set), so launch it from inside the project.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import capture, query
from .db import Graph, assert_read_only, find_project_root
from .schema import init as schema_init

mcp = FastMCP("pgraph")


def _root() -> Path:
    env = os.environ.get("PGRAPH_ROOT")
    if env:
        return Path(env).resolve()
    found = find_project_root()
    return found if found else Path.cwd()


def _open() -> Graph:
    root = _root()
    if not (root / ".pgraph" / "graph").exists():
        # Auto-initialize so the agent never hits a "run init first" wall.
        return schema_init(root)
    return Graph(root)


@mcp.tool()
def session_start(agent: str, summary: str = "") -> str:
    """Open a work session for an agent (e.g. 'claude-code'). Returns the session id."""
    with _open() as g:
        return capture.start_session(g, agent, summary)


@mcp.tool()
def session_end(session_id: str = "", summary: str = "") -> str:
    """Close a session (defaults to the latest open one) with an optional closing summary."""
    with _open() as g:
        sid = session_id or capture.latest_open_session(g)
        if not sid:
            return "no open session"
        capture.end_session(g, sid, summary)
        return f"ended {sid}"


@mcp.tool()
def log_change(kind: str, path: str, summary: str = "", session_id: str = "", diff_stat: str = "") -> str:
    """Record a change to a file. kind ∈ edit|create|delete|commit. Returns the change id."""
    with _open() as g:
        sid = session_id or capture.latest_open_session(g)
        return capture.log_change(g, kind, path, summary, sid, diff_stat)


@mcp.tool()
def log_decision(title: str, body: str = "", motivates_change_ids: list[str] | None = None,
                 about_paths: list[str] | None = None, supersedes: list[str] | None = None) -> str:
    """Record a 'why' note (a decision), optionally linking the changes/files it explains.

    Pass `supersedes` (prior decision ids) when this decision replaces older ones;
    those get marked 'superseded' so stale rationale stops surfacing.
    """
    with _open() as g:
        return capture.log_decision(g, title, body, motivates_change_ids, about_paths,
                                    supersedes=supersedes)


@mcp.tool()
def set_decision_status(decision_id: str, status: str) -> str:
    """Set a decision's lifecycle status: accepted | superseded | rejected."""
    with _open() as g:
        capture.set_decision_status(g, decision_id, status)
        return f"{decision_id} -> {status}"


@mcp.tool()
def record_skill_use(session_id: str, skill: str) -> str:
    """Record that a skill/tool was used during a session."""
    with _open() as g:
        capture.record_skill_use(g, session_id, skill)
        return f"recorded {skill}"


@mcp.tool()
def recent_changes(limit: int = 20, since: str = "", path: str = "") -> list[dict[str, Any]]:
    """Most recent changes (newest first), optionally filtered by file path or ISO timestamp."""
    with _open() as g:
        return query.recent_changes(g, limit=limit, since=since or None, path=path or None)


@mcp.tool()
def file_history(path: str) -> dict[str, Any]:
    """All changes and decisions touching one file — the full story of that file."""
    with _open() as g:
        return query.file_history(g, path)


@mcp.tool()
def context_pack(paths: list[str] | None = None, budget: int = 4000) -> dict[str, Any]:
    """Compact, ranked context bundle for the files you're about to work on.

    This is the token-saving query: pass the paths you're editing and get back
    only the most recent relevant changes + decisions, trimmed to a budget,
    instead of re-reading a whole project log.
    """
    with _open() as g:
        return query.context_pack(g, paths or None, budget)


@mcp.tool()
def search(term: str, labels: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Full-text search project memory (decisions, changes, files) by keyword.

    Relevance-ranked (BM25) when available. Optionally restrict to node labels
    like ["Decision"] or ["Change"]. Returns each hit's props plus its _label.
    Use this to find *why* something was done when you don't know the file path.
    """
    with _open() as g:
        return query.search(g, term, labels=labels, limit=limit)


@mcp.tool()
def status() -> dict[str, Any]:
    """Health summary of the project graph: node/edge counts and any open session."""
    with _open() as g:
        return query.status(g)


@mcp.tool()
def sql(query_text: str) -> list[dict[str, Any]]:
    """Run an arbitrary read-only SQL query against the project graph.

    The store is two tables: nodes(label, pk, props) and
    edges(rel, src_label, src_pk, dst_label, dst_pk), where props is a JSON blob
    (query fields via json_extract(props, '$.field')). Write statements
    (INSERT, UPDATE, DELETE, …) are rejected — use the dedicated tools
    (log_change, log_decision, …) to mutate the graph.
    """
    assert_read_only(query_text)
    with _open() as g:
        return [{k: str(v) for k, v in r.items()} for r in g.sql(query_text)]


def main() -> None:
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
