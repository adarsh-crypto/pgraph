"""Merge pgraph's auto-capture hooks into a target project's .claude/settings.json.

Reads the existing settings (never clobbers other hooks), adds a PostToolUse
hook on Write|Edit|NotebookEdit and a Stop hook, both calling the ``pgraph``
CLI. Idempotent: re-running won't add duplicate entries.

Invoked via ``pgraph install-hooks [--root PATH]``.
"""

from __future__ import annotations

import json
from pathlib import Path

# The commands the hooks run. `pgraph` must be on PATH (e.g. the conda env that
# installed it, or a full path written via --pgraph-bin).
_POST_MATCHER = "Write|Edit|NotebookEdit"
_POST_CMD = "pgraph hook-post-tool-use"
_STOP_CMD = "pgraph hook-stop"


def _hook_entry(command: str) -> dict:
    return {"type": "command", "command": command}


def _has_command(group_list: list, matcher: str | None, command: str) -> bool:
    for group in group_list:
        if matcher is not None and group.get("matcher") != matcher:
            continue
        for h in group.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def install(root: str | Path, pgraph_bin: str = "pgraph") -> dict:
    """Add the hooks to ``<root>/.claude/settings.json``; return what changed."""
    root = Path(root).resolve()
    settings_dir = root / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.json"

    if settings_path.exists():
        data = json.loads(settings_path.read_text() or "{}")
    else:
        data = {}

    post_cmd = _POST_CMD.replace("pgraph", pgraph_bin, 1)
    stop_cmd = _STOP_CMD.replace("pgraph", pgraph_bin, 1)

    hooks = data.setdefault("hooks", {})
    post = hooks.setdefault("PostToolUse", [])
    stop = hooks.setdefault("Stop", [])

    added = []
    if not _has_command(post, _POST_MATCHER, post_cmd):
        post.append({"matcher": _POST_MATCHER, "hooks": [_hook_entry(post_cmd)]})
        added.append("PostToolUse")
    if not _has_command(stop, None, stop_cmd):
        stop.append({"hooks": [_hook_entry(stop_cmd)]})
        added.append("Stop")

    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return {"settings": str(settings_path), "added": added, "post_cmd": post_cmd, "stop_cmd": stop_cmd}
