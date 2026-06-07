import pytest

from pgraph import capture


def test_session_change_decision_links(graph):
    sid = capture.start_session(graph, "claude-code", "wiring auth")
    cid = capture.log_change(graph, "edit", "src/auth.py", "add jwt", sid)
    did = capture.log_decision(
        graph, "Use JWT", "stateless", motivates_change_ids=[cid], about_paths=["src/auth.py"]
    )

    # Change -> Session
    assert graph.has_edge("IN_SESSION", "Change", cid, "Session", sid)
    # Change -> File
    assert graph.has_edge("AFFECTS", "Change", cid, "File", "src/auth.py")
    # Decision -> Change and Decision -> File
    assert graph.has_edge("MOTIVATES", "Decision", did, "Change", cid)
    assert graph.has_edge("ABOUT", "Decision", did, "File", "src/auth.py")


def test_invalid_change_kind_rejected(graph):
    with pytest.raises(ValueError):
        capture.log_change(graph, "bogus", "x.py", "nope")


def test_latest_open_session(graph):
    assert capture.latest_open_session(graph) is None
    sid = capture.start_session(graph, "codex")
    assert capture.latest_open_session(graph) == sid
    capture.end_session(graph, sid, "done")
    assert capture.latest_open_session(graph) is None


def test_skill_use_is_deduped(graph):
    sid = capture.start_session(graph, "claude-code")
    capture.record_skill_use(graph, sid, "deep-research")
    capture.record_skill_use(graph, sid, "deep-research")
    assert graph.count_edges("USED_SKILL") == 1
