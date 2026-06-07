import json

from pgraph import capture, query
from pgraph.install_hooks import install


def test_session_brief_empty_when_no_activity(graph):
    # A freshly initialized graph has only the Project node; nothing to brief.
    assert query.session_brief(graph) == ""


def test_session_brief_summarizes_recent_work(graph):
    sid = capture.start_session(graph, "claude-code", "wiring auth")
    cid = capture.log_change(graph, "edit", "src/auth.py", "add JWT verify", sid)
    capture.log_decision(
        graph,
        "Use JWT not sessions",
        "stateless, scales horizontally",
        motivates_change_ids=[cid],
        about_paths=["src/auth.py"],
    )

    brief = query.session_brief(graph)
    assert "pgraph project memory" in brief
    assert "wiring auth" in brief
    assert "src/auth.py" in brief
    assert "Use JWT not sessions" in brief
    # Open session is flagged as such.
    assert "Open session" in brief


def test_session_brief_respects_budget(graph):
    sid = capture.start_session(graph, "claude-code", "x" * 500)
    for i in range(30):
        capture.log_change(graph, "edit", f"f{i}.py", "y" * 100, sid)
    brief = query.session_brief(graph, budget=300)
    assert len(brief) <= 320  # budget + the truncation marker
    assert "truncated" in brief


def test_install_adds_session_start_hook(root):
    out = install(root, pgraph_bin="pgraph")
    assert "SessionStart" in out["added"]

    settings = json.loads((root / ".claude" / "settings.json").read_text())
    cmds = [
        h["command"]
        for group in settings["hooks"]["SessionStart"]
        for h in group["hooks"]
    ]
    assert "pgraph hook-session-start" in cmds

    # Idempotent: a second install adds nothing.
    out2 = install(root, pgraph_bin="pgraph")
    assert "SessionStart" not in out2["added"]
