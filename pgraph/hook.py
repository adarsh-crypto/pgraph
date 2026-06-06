"""Claude Code hook handlers: turn hook stdin JSON into graph writes.

These are invoked by the shell wrappers in ``hooks/`` and wired into a target
project's ``.claude/settings.json`` by ``hooks/install.py``. They read the hook
payload from stdin and never raise into Claude Code — any failure is swallowed
so a memory hiccup can never block the user's actual work.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import capture
from .db import Graph, find_project_root
from .schema import init as schema_init

# Tool name -> change kind for the PostToolUse hook.
_TOOL_KIND = {"Write": "create", "Edit": "edit", "NotebookEdit": "edit"}


def _open_for_cwd() -> Graph | None:
    root = find_project_root()
    if root is None:
        # No graph initialized for this project; stay silent.
        return None
    return Graph(root)


def _read_stdin() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def post_tool_use() -> int:
    """On Write|Edit: record a Change for the edited file in the open session."""
    try:
        payload = _read_stdin()
        tool = payload.get("tool_name", "")
        kind = _TOOL_KIND.get(tool)
        if kind is None:
            return 0
        tin = payload.get("tool_input", {}) or {}
        path = tin.get("file_path") or tin.get("notebook_path") or ""
        if not path:
            return 0
        # Store project-relative paths to match `scan`/manual logging.
        g = _open_for_cwd()
        if g is None:
            return 0
        rel = _relativize(path, g.root)
        with g:
            sid = capture.latest_open_session(g)
            if sid is None:
                # Auto-open a session so edits are never dropped on the floor.
                sid = capture.start_session(g, payload.get("agent", "claude-code"))
            capture.log_change(g, kind, rel, summary="(auto) file write", session_id=sid)
    except Exception:
        # Hooks must never break the host agent.
        return 0
    return 0


def stop() -> int:
    """On Stop: close the open session and export a fresh JSONL snapshot."""
    try:
        from . import export as export_mod

        g = _open_for_cwd()
        if g is None:
            return 0
        with g:
            sid = capture.latest_open_session(g)
            if sid is not None:
                capture.end_session(g, sid)
            export_mod.export_graph(g, g.root)
    except Exception:
        return 0
    return 0


def _relativize(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return path
