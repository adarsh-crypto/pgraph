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


def test_context_pack_dedupes_repeated_changes(graph):
    sid = capture.start_session(graph, "claude-code", "s")
    for _ in range(6):
        capture.log_change(graph, "edit", "loop.py", "(auto) file write", sid)
    capture.log_change(graph, "edit", "other.py", "real work", sid)
    pack = query.context_pack(graph, paths=None, budget=100000)
    loop_entries = [c for c in pack["recent"] if c["path"] == "loop.py"]
    # Six identical writes collapse to one.
    assert len(loop_entries) == 1
    assert any(c["path"] == "other.py" for c in pack["recent"])


def test_context_pack_ranks_commits_above_auto_edits(graph):
    sid = capture.start_session(graph, "claude-code", "s")
    # An auto edit first, then a (more important) commit at the same instant-ish.
    capture.log_change(graph, "edit", "a.py", "(auto) file write", sid)
    capture.log_change(graph, "commit", "repo", "important release", sid)
    pack = query.context_pack(graph, paths=None, budget=100000)
    kinds = [c["kind"] for c in pack["recent"]]
    # The commit should outrank the auto edit despite similar recency.
    assert kinds.index("commit") < kinds.index("edit")


def test_dedupe_keeps_distinct_summaries(graph):
    sid = capture.start_session(graph, "claude-code", "s")
    capture.log_change(graph, "edit", "x.py", "add A", sid)
    capture.log_change(graph, "edit", "x.py", "add B", sid)
    pack = query.context_pack(graph, paths=["x.py"], budget=100000)
    summaries = {c["summary"] for c in pack["files"][0]["changes"]}
    assert summaries == {"add A", "add B"}
