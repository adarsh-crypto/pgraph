from pgraph import capture, export as export_mod, query


def _levels(report):
    return {c["check"]: c["level"] for c in report["checks"]}


def test_doctor_healthy_graph(graph):
    sid = capture.start_session(graph, "claude-code", "s")
    capture.log_change(graph, "edit", "a.py", "x", sid)
    export_mod.export_graph(graph, graph.root)
    report = query.diagnose(graph)
    lv = _levels(report)
    assert lv["journal_mode"] == "ok"        # WAL
    assert lv["busy_timeout"] == "ok"        # >= 1000ms
    assert lv["orphan_edges"] == "ok"
    assert lv["fts_index"] == "ok"
    assert lv["export"] == "ok"
    assert report["overall"] == "ok"


def test_doctor_warns_on_missing_export(graph):
    capture.log_decision(graph, "A", "x")
    report = query.diagnose(graph)
    lv = _levels(report)
    assert lv["export"] == "warn"            # never exported
    assert report["overall"] in ("warn", "error")


def test_doctor_warns_on_stale_export(graph):
    sid = capture.start_session(graph, "claude-code", "s")
    capture.log_change(graph, "edit", "a.py", "x", sid)
    export_mod.export_graph(graph, graph.root)
    # Add more after exporting -> export is now stale.
    capture.log_change(graph, "edit", "b.py", "y", sid)
    report = query.diagnose(graph)
    assert _levels(report)["export"] == "warn"


def test_doctor_detects_orphan_edge(graph):
    # Manually inject an edge to a non-existent node (bypass capture).
    graph._conn.execute(
        "INSERT INTO edges(rel, src_label, src_pk, dst_label, dst_pk) VALUES (?,?,?,?,?)",
        ("AFFECTS", "Change", "ghost", "File", "nowhere.py"),
    )
    graph._conn.commit()
    report = query.diagnose(graph)
    assert _levels(report)["orphan_edges"] == "error"
    assert report["overall"] == "error"


def test_orphan_edges_count_zero_when_clean(graph):
    sid = capture.start_session(graph, "claude-code", "s")
    capture.log_change(graph, "edit", "a.py", "x", sid)
    assert graph.orphan_edges() == 0
