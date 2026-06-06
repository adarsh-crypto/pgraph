from pgraph import capture, query


def _seed(graph):
    sid = capture.start_session(graph, "claude-code", "seed")
    c1 = capture.log_change(graph, "edit", "a.py", "first", sid)
    c2 = capture.log_change(graph, "edit", "a.py", "second", sid)
    capture.log_change(graph, "create", "b.py", "new file", sid)
    capture.log_decision(graph, "why a", "because", motivates_change_ids=[c1], about_paths=["a.py"])
    return sid, c1, c2


def test_recent_changes_orders_newest_first(graph):
    _seed(graph)
    rows = query.recent_changes(graph, limit=10)
    assert [r["summary"] for r in rows][:3] == ["new file", "second", "first"]


def test_recent_changes_filter_by_path(graph):
    _seed(graph)
    rows = query.recent_changes(graph, path="a.py")
    assert {r["path"] for r in rows} == {"a.py"}
    assert len(rows) == 2


def test_file_history_includes_changes_and_decisions(graph):
    _seed(graph)
    hist = query.file_history(graph, "a.py")
    assert len(hist["changes"]) == 2
    assert hist["decisions"][0]["title"] == "why a"


def test_context_pack_respects_budget(graph):
    _seed(graph)
    pack = query.context_pack(graph, paths=["a.py"], budget=100000)
    assert pack["files"][0]["path"] == "a.py"
    assert pack["_budget"]["chars_used"] <= pack["_budget"]["budget"]
    # latest session intent is surfaced
    assert pack["latest_session"]["agent"] == "claude-code"


def test_context_pack_no_paths_uses_recent(graph):
    _seed(graph)
    pack = query.context_pack(graph, paths=None, budget=100000)
    assert len(pack["recent"]) == 3
