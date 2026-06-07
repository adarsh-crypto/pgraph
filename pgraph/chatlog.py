"""Import existing Claude Code and Codex chat transcripts into the graph.

Coding agents already keep a complete, append-only log of every session on disk —
Claude Code under ``~/.claude/projects/<dashed-cwd>/<uuid>.jsonl`` and Codex under
``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``. Those logs hold exactly the
"what / when / why" pgraph cares about: which files an agent touched, which
skills it used, and — most valuably — the *user's own prompts*, the intent a
diff can never show.

This module reads those transcripts and folds them into the existing node/edge
model (sessions, changes, files, skills, plus a new ``Prompt`` node). It is
**purely deterministic** — JSONL parsing and field extraction only, no model,
no inference, no network — honoring pgraph's non-LLM constraint. Importing is an
explicit, opt-in CLI action (never a hook): the transcripts may contain secrets
the user typed, and nothing here leaves the machine.

The two formats differ in how reliably they attribute a session to a project:

* **Claude Code** stamps every line with ``cwd`` and ``gitBranch``, so project
  attribution is exact and file edits come straight from ``tool_use`` blocks
  (``Edit``/``Write`` → ``file_path``).
* **Codex** records a session-level ``cwd`` that is often just the launch dir
  (e.g. ``$HOME``), so we attribute via the ``workdir`` field carried on each
  ``exec_command`` instead. Recent Codex shells out for edits rather than using
  a structured patch tool, so its file-change signal is best-effort; its solid
  signal is the session itself plus the user's prompts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .db import Graph
from . import capture

# Tool names whose invocation means "a file was edited", mapped to a Change kind.
# (Claude Code tool_use blocks; Write creates, Edit/NotebookEdit modify.)
_EDIT_TOOLS = {"Write": "create", "Edit": "edit", "NotebookEdit": "edit"}

# Prefixes that mark a "user" line as machine-injected rather than typed by the
# human: local-command stdout/caveats, slash-command wrappers, and the
# conversation-continuation preamble written after a /compact. These carry
# role=user but are NOT prompts — and (critically) a compaction summary can
# quote unrelated projects, so importing it would leak names into the graph.
_NON_PROMPT_PREFIXES = (
    "<local-command",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "Caveat:",
    "This session is being continued from a previous conversation",
)


def _is_genuine_user_prompt(line: dict[str, Any], text: str) -> bool:
    """True only for text a human actually typed — not injected/system content.

    Filters out meta lines (``isMeta``), post-compaction summaries
    (``isCompactSummary``), tool-result echoes, and the local-command / slash-
    command wrapper tags Claude Code records as user turns.
    """
    if not text:
        return False
    if line.get("isMeta") or line.get("isCompactSummary"):
        return False
    stripped = text.lstrip()
    return not stripped.startswith(_NON_PROMPT_PREFIXES)


@dataclass
class ImportedSession:
    """A transcript normalized into the shape pgraph stores, before any DB write.

    Kept DB-free so the parsers are trivially unit-testable on fixtures: a parser
    turns one ``.jsonl`` file into one of these, and :func:`import_chats` is the
    only thing that touches the graph.
    """

    source: str  # "claude" | "codex"
    source_id: str  # the agent's own session/rollout uuid (dedup key)
    agent: str  # "claude-code" | "codex"
    path: str  # the transcript file it came from
    cwd: str | None = None
    git_branch: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    prompts: list[dict[str, Any]] = field(default_factory=list)  # {text, ts}
    changes: list[dict[str, Any]] = field(default_factory=list)  # {kind, path, ts}
    skills: list[str] = field(default_factory=list)
    # Every working directory seen in the transcript — used for attribution when
    # the session-level cwd is unreliable (Codex).
    workdirs: set[str] = field(default_factory=set)

    @property
    def session_pk(self) -> str:
        """Deterministic, collision-free graph id — re-import maps to the same node."""
        return f"chat:{self.source}:{self.source_id}"


# -- discovery -------------------------------------------------------------
def claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def codex_sessions_dir() -> Path:
    return Path.home() / ".codex" / "sessions"


def _dashed(path: str | Path) -> str:
    """Claude's project-folder encoding: an absolute path with '/' and '_' -> '-'.

    Claude Code replaces both path separators *and* underscores with dashes, so
    ``/Users/me/Project_graph`` becomes ``-Users-me-Project-graph``. The encoding
    is lossy (``Project_graph`` and ``Project-graph`` collide), but we re-verify
    every matched transcript against its exact in-file ``cwd`` (see
    :func:`collect`), so a folder over-match never imports the wrong project.
    """
    return str(Path(path).resolve()).replace("/", "-").replace("_", "-")


def discover_claude(project_root: str | Path) -> list[Path]:
    """Claude transcript files whose project folder is at or under *project_root*.

    Claude names each project folder after the cwd (``/`` → ``-``), so a single
    project root that contains nested repos matches its own folder *and* any
    deeper one — mirroring pgraph's project-not-repo model.
    """
    base = claude_projects_dir()
    if not base.is_dir():
        return []
    prefix = _dashed(project_root)
    out: list[Path] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        # Match the root's own folder or any nested-repo folder beneath it.
        if child.name == prefix or child.name.startswith(prefix + "-"):
            out.extend(sorted(child.glob("*.jsonl")))
    return out


def discover_codex(project_root: str | Path) -> list[Path]:
    """All Codex rollout files (attribution happens later, per-session).

    Codex stores sessions by date, not by project, and its session-level ``cwd``
    is unreliable — so we can't filter by path here. We return every rollout and
    let :func:`parse_codex` + :func:`import_chats` keep only sessions whose work
    actually happened under *project_root*.
    """
    base = codex_sessions_dir()
    if not base.is_dir():
        return []
    return sorted(base.rglob("rollout-*.jsonl"))


# -- parsing: Claude Code --------------------------------------------------
def _text_of(content: Any) -> str:
    """Flatten a Claude message ``content`` (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def parse_claude(path: str | Path) -> ImportedSession | None:
    """Parse one Claude Code transcript into an :class:`ImportedSession`.

    Returns ``None`` for a transcript with no real conversation (e.g. only
    queue-operation bookkeeping lines).
    """
    path = Path(path)
    sess: ImportedSession | None = None
    first_ts: str | None = None
    last_ts: str | None = None

    for line in _iter_jsonl(path):
        sid = line.get("sessionId")
        ts = line.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        if sess is None and sid:
            sess = ImportedSession(
                source="claude", source_id=sid, agent="claude-code", path=str(path)
            )
        if sess is None:
            continue
        if line.get("cwd") and not sess.cwd:
            sess.cwd = line["cwd"]
        if line.get("cwd"):
            sess.workdirs.add(line["cwd"])
        if line.get("gitBranch") and not sess.git_branch:
            sess.git_branch = line["gitBranch"]

        msg = line.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user" and line.get("type") == "user":
            text = _text_of(msg.get("content")).strip()
            # Keep only genuine human prompts — skip tool_result echoes, meta /
            # compaction-summary lines, and local/slash-command wrappers.
            if _is_genuine_user_prompt(line, text):
                sess.prompts.append({"text": text, "ts": ts})
        elif role == "assistant":
            for blk in msg.get("content", []) or []:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") != "tool_use":
                    continue
                name = blk.get("name", "")
                inp = blk.get("input", {}) or {}
                if name in _EDIT_TOOLS and inp.get("file_path"):
                    sess.changes.append(
                        {"kind": _EDIT_TOOLS[name], "path": inp["file_path"], "ts": ts}
                    )
                elif name == "Skill" and inp.get("skill"):
                    sess.skills.append(str(inp["skill"]))
                elif name:
                    sess.skills.append(name)

    if sess is None:
        return None
    sess.started_at = first_ts
    sess.ended_at = last_ts
    # De-dup skills while keeping first-seen order.
    sess.skills = list(dict.fromkeys(sess.skills))
    return sess if (sess.prompts or sess.changes) else None


# -- parsing: Codex --------------------------------------------------------
def _codex_args(payload: dict[str, Any]) -> dict[str, Any]:
    """Best-effort parse of a Codex function_call ``arguments`` JSON string."""
    raw = payload.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return {}


def parse_codex(path: str | Path) -> ImportedSession | None:
    """Parse one Codex rollout file into an :class:`ImportedSession`.

    Codex file-edits aren't reliably structured in current versions, so the
    extracted signal is: the session, the user's prompts (intent), the skills/
    tools invoked, and every ``workdir`` seen (for project attribution).
    """
    path = Path(path)
    sess: ImportedSession | None = None
    first_ts: str | None = None
    last_ts: str | None = None

    for line in _iter_jsonl(path):
        ts = line.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        payload = line.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")

        if line.get("type") == "session_meta" or ("id" in payload and "cwd" in payload):
            if sess is None:
                sess = ImportedSession(
                    source="codex", source_id=str(payload.get("id", path.stem)),
                    agent="codex", path=str(path), cwd=payload.get("cwd"),
                )
                if payload.get("cwd"):
                    sess.workdirs.add(payload["cwd"])
            continue

        if sess is None:
            # A rollout without a session_meta line — synthesize an id from the file.
            sess = ImportedSession(
                source="codex", source_id=path.stem, agent="codex", path=str(path)
            )

        if ptype == "user_message":
            text = (payload.get("message") or payload.get("text") or "").strip()
            # Same guard as Claude: drop wrapper/continuation lines so injected
            # text (which can quote other projects) never becomes a "prompt".
            if _is_genuine_user_prompt(payload, text):
                sess.prompts.append({"text": text, "ts": ts})
        elif ptype == "function_call":
            name = payload.get("name", "")
            if name:
                sess.skills.append(name)
            args = _codex_args(payload)
            wd = args.get("workdir") or args.get("cwd")
            if wd:
                sess.workdirs.add(wd)

    if sess is None:
        return None
    sess.started_at = first_ts
    sess.ended_at = last_ts
    sess.skills = list(dict.fromkeys(sess.skills))
    return sess if (sess.prompts or sess.changes) else None


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Yield parsed JSON objects from a JSONL file, skipping unparseable lines."""
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


# -- attribution -----------------------------------------------------------
def _basename(path: str) -> str:
    """Final path component, tolerant of any OS's path syntax.

    The whole point of importing chats across machines is that the *same* project
    has a different absolute path on each OS — ``D:\\Projects\\app`` on Windows,
    ``/Users/me/app`` on macOS, ``/home/me/app`` on Linux — while the project
    folder name stays constant. So we extract that trailing name regardless of
    which OS recorded the transcript:

    * both separators are treated as boundaries (``\\`` and ``/``);
    * a Windows drive prefix (``D:``) is stripped;
    * trailing separators are ignored (``app/`` and ``app\\`` → ``app``).
    """
    s = path.replace("\\", "/").rstrip("/")
    tail = s.rsplit("/", 1)[-1]
    # Drop a bare drive prefix like "D:" if the path was just "D:\\".
    if len(tail) == 2 and tail[1] == ":" and tail[0].isalpha():
        return ""
    return tail


def _path_in_project(path: str, project_root: str | Path) -> bool:
    """True if an absolute *path* lies at or under *project_root*.

    Relative paths are accepted (they're project-relative by construction);
    absolute paths must be inside the root, so a file edited elsewhere on disk
    during the same session never lands in this project's graph. Foreign/Windows
    absolute paths (non-absolute on POSIX) are rejected rather than resolved.
    """
    p = Path(path)
    if not p.is_absolute():
        # A Windows-style path looks relative on POSIX — treat as foreign.
        if "\\" in path or (len(path) >= 2 and path[1] == ":"):
            return False
        return True
    root = Path(project_root).resolve()
    try:
        rp = p.resolve()
    except (OSError, ValueError):
        return False
    return rp == root or root in rp.parents


def belongs_to_project(
    sess: ImportedSession, project_root: str | Path, *, match_name: bool = True
) -> bool:
    """True if *sess* belongs to the project at *project_root*.

    Two ways to match, so a transcript still imports even when its recorded path
    differs from where the project lives now (a different machine, or a renamed
    parent directory):

    1. **By path** — any working dir is at or under *project_root* (the precise,
       same-machine case).
    2. **By project name** (when *match_name*) — any working dir's final
       component equals the project root's folder name. This is what lets
       "different paths, same project" still flow into the graph, while a
       *differently named* project (e.g. an unrelated ``Spokesfan``) stays out.

    Foreign/relative paths are never *resolved* (resolving a Windows ``D:\\proj``
    on POSIX would inject the current cwd and falsely match) — but their trailing
    folder name is still eligible for the name match.
    """
    root = Path(project_root).resolve()
    root_name = root.name
    for wd in sess.workdirs:
        wdp = Path(wd)
        if wdp.is_absolute():
            try:
                resolved = wdp.resolve()
            except (OSError, ValueError):
                resolved = None
            if resolved is not None and (resolved == root or root in resolved.parents):
                return True
        if match_name and root_name and _basename(wd) == root_name:
            return True
    return False


# -- import ----------------------------------------------------------------
def _truncate(text: str, mode: str) -> str | None:
    """Apply the prompt-storage policy. Returns None when prompts are dropped."""
    if mode == "none":
        return None
    if mode == "truncated":
        text = text.strip()
        return text[:200] + "…" if len(text) > 200 else text
    return text  # "full"


def import_chats(
    g: Graph,
    sessions: Iterable[ImportedSession],
    project_root: str | Path,
    *,
    prompts: str = "full",
    attribute: bool = True,
) -> dict[str, Any]:
    """Fold parsed transcripts into the graph. Idempotent on re-run.

    Each :class:`ImportedSession` becomes a ``Session`` node keyed by a
    deterministic id (:attr:`ImportedSession.session_pk`), so importing the same
    transcripts twice adds nothing new. User prompts become ``Prompt`` nodes
    (``PROMPTED_IN`` → Session), file edits become ``Change`` nodes, and tool/
    skill names become ``Skill`` nodes — reusing the existing capture helpers.

    *prompts* controls prompt-text storage: ``full`` | ``truncated`` | ``none``.
    When *attribute* is set, sessions whose work didn't happen under
    *project_root* are skipped (see :func:`belongs_to_project`).
    """
    stats = {"sessions": 0, "skipped_unrelated": 0, "skipped_existing": 0,
             "prompts": 0, "changes": 0, "skills": 0}

    for sess in sessions:
        if sess is None:
            continue
        if attribute and not belongs_to_project(sess, project_root):
            stats["skipped_unrelated"] += 1
            continue
        if g.get_node("Session", sess.session_pk) is not None:
            stats["skipped_existing"] += 1
            continue

        # Create the Session node directly (deterministic pk, preserving the
        # transcript's own timestamps), then link it to project + agent.
        summary = _session_summary(sess)
        g.add_node("Session", sess.session_pk, {
            "id": sess.session_pk, "agent_name": sess.agent,
            "started_at": sess.started_at, "ended_at": sess.ended_at,
            "summary": summary, "source": sess.source, "source_id": sess.source_id,
            "cwd": sess.cwd, "git_branch": sess.git_branch,
            "imported": True,
        })
        proj = g.match_nodes("Project", limit=1)
        if proj:
            g.add_edge("IN_PROJECT", "Session", sess.session_pk, "Project", proj[0]["id"])
        if g.get_node("Agent", sess.agent) is None:
            g.add_node("Agent", sess.agent, {"id": sess.agent, "name": sess.agent})
        g.add_edge("BY_AGENT", "Session", sess.session_pk, "Agent", sess.agent)
        stats["sessions"] += 1

        for i, p in enumerate(sess.prompts):
            text = _truncate(p.get("text", ""), prompts)
            if not text:
                continue
            pid = f"{sess.session_pk}:p{i}"
            g.add_node("Prompt", pid, {
                "id": pid, "text": text, "ts": p.get("ts"),
                "source": sess.source,
            })
            g.add_edge("PROMPTED_IN", "Prompt", pid, "Session", sess.session_pk)
            stats["prompts"] += 1

        for ch in sess.changes:
            # A session can edit files anywhere on disk, but only edits *within
            # this project* belong in its graph. An absolute path outside the
            # root is another project's file (e.g. an unrelated repo touched in
            # the same session) — skip it so it can't leak in.
            if attribute and not _path_in_project(ch["path"], project_root):
                stats["skipped_foreign_changes"] = stats.get("skipped_foreign_changes", 0) + 1
                continue
            capture.log_change(
                g, ch.get("kind", "edit"), ch["path"],
                summary="(imported from chat)", session_id=sess.session_pk,
                ts=ch.get("ts"),
            )
            stats["changes"] += 1

        for skill in sess.skills:
            capture.record_skill_use(g, sess.session_pk, skill)
            stats["skills"] += 1

    return stats


def _session_summary(sess: ImportedSession) -> str:
    """Derive a one-line summary deterministically — first prompt + a tally.

    No model is used: the "summary" is the user's opening ask (trimmed) plus a
    count of prompts and file edits, which is enough for the brief to be useful.
    """
    head = ""
    if sess.prompts:
        head = re.sub(r"\s+", " ", sess.prompts[0]["text"]).strip()
        if len(head) > 120:
            head = head[:117] + "…"
    tally = f"{len(sess.prompts)} prompt(s), {len(sess.changes)} edit(s)"
    return f"{head} [{tally}]" if head else f"(imported) [{tally}]"


def collect(
    project_root: str | Path, source: str = "both"
) -> list[ImportedSession]:
    """Discover + parse all transcripts for a project. Pure, no DB writes.

    *source* ∈ ``claude`` | ``codex`` | ``both``. Codex sessions are pre-filtered
    to those whose work touched *project_root* (since discovery can't filter by
    path); Claude sessions are already folder-scoped but re-checked for safety.
    """
    out: list[ImportedSession] = []
    if source in ("claude", "both"):
        for p in discover_claude(project_root):
            s = parse_claude(p)
            # Re-verify against the transcript's real cwd: the folder-name match
            # is lossy (underscores become dashes), so confirm by actual path.
            if s is not None and belongs_to_project(s, project_root):
                out.append(s)
    if source in ("codex", "both"):
        for p in discover_codex(project_root):
            s = parse_codex(p)
            if s is not None and belongs_to_project(s, project_root):
                out.append(s)
    return out
