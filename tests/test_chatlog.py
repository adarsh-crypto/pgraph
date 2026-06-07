"""Import of Claude Code / Codex chat transcripts.

Uses small synthetic JSONL fixtures shaped like the real on-disk formats, so the
parsers and the project-attribution guard are exercised without touching the
user's actual ~/.claude or ~/.codex.
"""

import json

from pgraph import chatlog, query


# -- fixtures: write tiny transcripts in the real layouts ------------------
def _claude_transcript(project_dir, cwd, sid="sess-abc"):
    """Mimic ~/.claude/projects/<dashed-cwd>/<uuid>.jsonl."""
    folder = project_dir / ".claude" / "projects" / chatlog._dashed(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "queue-operation", "sessionId": sid, "timestamp": "2026-06-07T10:00:00Z"},
        {"type": "user", "sessionId": sid, "cwd": cwd, "gitBranch": "main",
         "timestamp": "2026-06-07T10:00:01Z",
         "message": {"role": "user", "content": "add rate limiting to login"}},
        {"type": "assistant", "sessionId": sid, "cwd": cwd,
         "timestamp": "2026-06-07T10:00:05Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "sure"},
             {"type": "tool_use", "name": "Edit",
              "input": {"file_path": "accounts/views.py"}},
             {"type": "tool_use", "name": "Skill", "input": {"skill": "deep-research"}},
         ]}},
        {"type": "user", "sessionId": sid, "cwd": cwd,
         "timestamp": "2026-06-07T10:01:00Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "content": "ok"}]}},  # should be skipped
    ]
    path = folder / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def _codex_rollout(base_dir, workdir, sid="codex-xyz", session_cwd=None):
    """Mimic ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl."""
    folder = base_dir / "2026" / "06" / "07"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "session_meta", "timestamp": "2026-06-07T11:00:00Z",
         "payload": {"id": sid, "cwd": session_cwd or str(base_dir), "originator": "codex-tui"}},
        {"type": "event_msg", "timestamp": "2026-06-07T11:00:01Z",
         "payload": {"type": "user_message", "message": "refactor the auth module"}},
        {"type": "response_item", "timestamp": "2026-06-07T11:00:05Z",
         "payload": {"type": "function_call", "name": "exec_command",
                     "arguments": json.dumps({"cmd": "ls", "workdir": workdir})}},
    ]
    path = folder / f"rollout-2026-06-07T11-00-00-{sid}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


# -- parsing ---------------------------------------------------------------
def test_parse_claude_extracts_prompts_changes_skills(tmp_path):
    cwd = str(tmp_path / "proj")
    path = _claude_transcript(tmp_path, cwd)
    s = chatlog.parse_claude(path)
    assert s is not None
    assert s.source == "claude" and s.agent == "claude-code"
    assert s.cwd == cwd and s.git_branch == "main"
    # One real user prompt; the tool_result turn is ignored.
    assert [p["text"] for p in s.prompts] == ["add rate limiting to login"]
    assert {(c["kind"], c["path"]) for c in s.changes} == {("edit", "accounts/views.py")}
    assert "deep-research" in s.skills
    assert s.started_at and s.ended_at


def test_parse_claude_skips_injected_user_lines(tmp_path):
    # role=user lines that aren't genuine prompts must be dropped: tool_result
    # echoes, meta/compaction-summary lines, and command/local-command wrappers.
    cwd = str(tmp_path / "proj")
    folder = tmp_path / ".claude" / "projects" / chatlog._dashed(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    sid = "filt"
    lines = [
        {"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": "2026-06-07T10:00:00Z",
         "message": {"role": "user", "content": "real question here"}},
        {"type": "user", "sessionId": sid, "cwd": cwd, "isMeta": True,
         "timestamp": "2026-06-07T10:00:01Z",
         "message": {"role": "user", "content": "<system meta>"}},
        {"type": "user", "sessionId": sid, "cwd": cwd, "isCompactSummary": True,
         "timestamp": "2026-06-07T10:00:02Z",
         "message": {"role": "user", "content": "This session is being continued from a previous conversation. Spokesfan stuff."}},
        {"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": "2026-06-07T10:00:03Z",
         "message": {"role": "user", "content": "<local-command-stdout>Compacted</local-command-stdout>"}},
        {"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": "2026-06-07T10:00:04Z",
         "message": {"role": "user", "content": "<command-name>/compact</command-name>"}},
    ]
    path = folder / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    s = chatlog.parse_claude(path)
    assert [p["text"] for p in s.prompts] == ["real question here"]
    # The compaction summary (which mentioned another project) was NOT captured.
    assert all("Spokesfan" not in p["text"] for p in s.prompts)


def test_parse_codex_extracts_prompt_and_workdir(tmp_path):
    workdir = str(tmp_path / "proj")
    path = _codex_rollout(tmp_path, workdir, session_cwd=str(tmp_path))
    s = chatlog.parse_codex(path)
    assert s is not None
    assert s.source == "codex" and s.agent == "codex"
    assert [p["text"] for p in s.prompts] == ["refactor the auth module"]
    # workdir from exec_command is captured for attribution.
    assert workdir in s.workdirs
    assert "exec_command" in s.skills


# -- attribution -----------------------------------------------------------
def test_belongs_to_project_matches_nested_workdir(tmp_path):
    root = tmp_path / "proj"
    s = chatlog.ImportedSession(source="codex", source_id="x", agent="codex", path="f")
    s.workdirs.add(str(root / "nested" / "repo"))
    assert chatlog.belongs_to_project(s, root) is True


def test_belongs_to_project_rejects_outside_workdir(tmp_path):
    root = tmp_path / "proj"
    other = tmp_path / "other-project"
    s = chatlog.ImportedSession(source="codex", source_id="x", agent="codex", path="f")
    s.workdirs.add(str(other))
    assert chatlog.belongs_to_project(s, root) is False


def test_belongs_to_project_matches_by_name_when_path_differs(tmp_path):
    # A transcript recorded on another machine: its absolute path is nowhere near
    # our root, but the project *folder name* matches — so it should still import.
    root = tmp_path / "Project_graph"
    s = chatlog.ImportedSession(source="claude", source_id="x", agent="claude-code", path="f")
    s.workdirs.add("/some/other/machine/Project_graph")
    assert chatlog.belongs_to_project(s, root) is True
    # ...unless name-matching is explicitly disabled (strict same-machine mode).
    assert chatlog.belongs_to_project(s, root, match_name=False) is False


def test_belongs_to_project_name_match_excludes_different_project(tmp_path):
    # Different project name (a foreign Windows path) must never match by name.
    root = tmp_path / "Project_graph"
    s = chatlog.ImportedSession(source="codex", source_id="x", agent="codex", path="f")
    s.workdirs.add("D:\\Spokesfan\\spokesfan_shopify")
    assert chatlog.belongs_to_project(s, root) is False


def test_basename_is_cross_os():
    # Every combination a transcript might have been recorded on.
    cases = {
        "/Users/me/Project_graph": "Project_graph",      # macOS
        "/home/me/Project_graph": "Project_graph",        # Linux
        "/home/me/Project_graph/": "Project_graph",       # trailing slash
        "D:\\Projects\\Project_graph": "Project_graph",   # Windows
        "D:\\Projects\\Project_graph\\": "Project_graph",  # Windows trailing
        "C:\\Project_graph": "Project_graph",             # Windows drive root
        "\\\\server\\share\\Project_graph": "Project_graph",  # UNC path
        "Project_graph": "Project_graph",                  # bare name
        "D:\\": "",                                        # bare drive → no name
    }
    for raw, expected in cases.items():
        assert chatlog._basename(raw) == expected, raw


def test_belongs_to_project_matches_same_name_across_os(tmp_path):
    # The same project, as recorded on three different operating systems, all
    # resolve to the same project when imported on this machine.
    root = tmp_path / "Project_graph"
    for foreign in ("/Users/alice/Project_graph",
                    "/home/bob/code/Project_graph",
                    "D:\\Projects\\Project_graph",
                    "\\\\nas\\team\\Project_graph"):
        s = chatlog.ImportedSession(source="codex", source_id=foreign, agent="codex", path="f")
        s.workdirs.add(foreign)
        assert chatlog.belongs_to_project(s, root) is True, foreign


def test_belongs_to_project_rejects_foreign_relative_path(tmp_path, monkeypatch):
    # A Windows path from a remote Codex session is non-absolute on POSIX;
    # resolving it would inject the current cwd and falsely match. Guard against
    # that: even with cwd == root, a "D:\\Other" workdir must NOT match.
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.chdir(root)
    s = chatlog.ImportedSession(source="codex", source_id="win", agent="codex", path="f")
    s.workdirs.add("D:\\SomeOtherProject\\sub")
    assert chatlog.belongs_to_project(s, root) is False


# -- import (idempotency, prompt policy, attribution) ----------------------
def test_import_creates_nodes_and_is_idempotent(graph):
    root = graph.root
    s = chatlog.parse_claude(_claude_transcript(root, str(root)))
    first = chatlog.import_chats(graph, [s], root)
    assert first["sessions"] == 1
    assert first["prompts"] == 1
    assert first["changes"] == 1
    assert graph.count_nodes("Prompt") == 1

    # Re-importing the same parsed session adds nothing.
    again = chatlog.import_chats(graph, [chatlog.parse_claude(
        _claude_transcript(root, str(root)))], root)
    assert again["sessions"] == 0
    assert again["skipped_existing"] == 1
    assert graph.count_nodes("Prompt") == 1


def test_import_prompts_none_stores_no_text(graph):
    root = graph.root
    s = chatlog.parse_claude(_claude_transcript(root, str(root)))
    stats = chatlog.import_chats(graph, [s], root, prompts="none")
    assert stats["prompts"] == 0
    assert graph.count_nodes("Prompt") == 0
    # The session itself is still recorded.
    assert stats["sessions"] == 1


def test_import_truncated_caps_prompt_length(graph):
    root = graph.root
    s = chatlog.ImportedSession(source="claude", source_id="t", agent="claude-code", path="f")
    s.prompts.append({"text": "x" * 500, "ts": "2026-06-07T10:00:00Z"})
    s.workdirs.add(str(root))
    chatlog.import_chats(graph, [s], root, prompts="truncated")
    prompts = query.recent_prompts(graph)
    assert len(prompts) == 1
    assert len(prompts[0]["text"]) <= 201  # 200 chars + ellipsis


def test_import_skips_foreign_file_changes(graph, tmp_path):
    # A session that ran in this project but edited a file in ANOTHER project's
    # absolute path: the foreign edit must not be imported into this graph.
    root = graph.root
    s = chatlog.ImportedSession(source="claude", source_id="mix", agent="claude-code", path="f")
    s.workdirs.add(str(root))
    s.changes.append({"kind": "edit", "path": str(root / "in_project.py"), "ts": "2026-06-07T10:00:00Z"})
    s.changes.append({"kind": "edit", "path": str(root.parent / "Other" / "secret.py"), "ts": "2026-06-07T10:00:01Z"})
    stats = chatlog.import_chats(graph, [s], root)
    assert stats["changes"] == 1
    assert stats["skipped_foreign_changes"] == 1
    paths = {c["path"] for c in query.recent_changes(graph, limit=50)}
    assert str(root / "in_project.py") in paths
    assert not any("Other" in p for p in paths)


def test_path_in_project_rejects_windows_path(tmp_path):
    assert chatlog._path_in_project("D:\\Other\\f.py", tmp_path) is False
    assert chatlog._path_in_project("relative/ok.py", tmp_path) is True


def test_import_skips_unrelated_project(graph, tmp_path):
    root = graph.root
    s = chatlog.ImportedSession(source="codex", source_id="u", agent="codex", path="f")
    # A sibling of the project root (the graph fixture roots at tmp_path itself).
    s.workdirs.add(str(root.parent / "somewhere-else-entirely"))
    s.prompts.append({"text": "unrelated work", "ts": "2026-06-07T10:00:00Z"})
    stats = chatlog.import_chats(graph, [s], root)
    assert stats["sessions"] == 0
    assert stats["skipped_unrelated"] == 1


def test_imported_prompt_is_searchable(graph):
    root = graph.root
    s = chatlog.ImportedSession(source="claude", source_id="srch", agent="claude-code", path="f")
    s.prompts.append({"text": "add rate limiting to the login endpoint", "ts": "2026-06-07T10:00:00Z"})
    s.workdirs.add(str(root))
    chatlog.import_chats(graph, [s], root)
    hits = query.search(graph, "rate limiting", labels=["Prompt"])
    assert any("rate limiting" in h.get("text", "") for h in hits)


# -- discovery scoping -----------------------------------------------------
def test_discover_claude_scopes_to_project(tmp_path, monkeypatch):
    # Two Claude project folders: one under our root, one elsewhere.
    home = tmp_path / "home"
    monkeypatch.setattr(chatlog, "claude_projects_dir",
                        lambda: home / ".claude" / "projects")
    root = tmp_path / "proj"
    _claude_transcript(home, str(root), sid="mine")
    _claude_transcript(home, str(tmp_path / "other"), sid="theirs")
    found = chatlog.discover_claude(root)
    names = {p.stem for p in found}
    assert "mine" in names
    assert "theirs" not in names
