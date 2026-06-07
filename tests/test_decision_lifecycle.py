import pytest

from pgraph import capture, query


def test_new_decision_is_accepted(graph):
    did = capture.log_decision(graph, "Use JWT", "stateless")
    node = graph.get_node("Decision", did)
    assert node["status"] == "accepted"


def test_supersede_marks_old_and_links(graph):
    old = capture.log_decision(graph, "Use sessions", "stateful")
    new = capture.log_decision(graph, "Use JWT", "stateless", supersedes=[old])
    assert graph.get_node("Decision", old)["status"] == "superseded"
    assert graph.get_node("Decision", new)["status"] == "accepted"
    assert graph.has_edge("SUPERSEDES", "Decision", new, "Decision", old)


def test_supersede_via_helper(graph):
    old = capture.log_decision(graph, "A", "x")
    new = capture.log_decision(graph, "B", "y")
    capture.supersede_decision(graph, new, old)
    assert graph.get_node("Decision", old)["status"] == "superseded"
    assert graph.has_edge("SUPERSEDES", "Decision", new, "Decision", old)


def test_set_status_validates(graph):
    did = capture.log_decision(graph, "A", "x")
    capture.set_decision_status(graph, did, "rejected")
    assert graph.get_node("Decision", did)["status"] == "rejected"
    with pytest.raises(ValueError):
        capture.set_decision_status(graph, did, "bogus")


def test_session_brief_hides_superseded(graph):
    sid = capture.start_session(graph, "claude-code", "s")
    capture.log_change(graph, "edit", "a.py", "x", sid)
    old = capture.log_decision(graph, "Old approach", "do it the old way")
    capture.log_decision(graph, "New approach", "do it the new way", supersedes=[old])
    brief = query.session_brief(graph)
    assert "New approach" in brief
    assert "Old approach" not in brief


def test_file_history_exposes_status(graph):
    did = capture.log_decision(graph, "About x", "why", about_paths=["x.py"])
    capture.set_decision_status(graph, did, "rejected")
    hist = query.file_history(graph, "x.py")
    assert hist["decisions"][0]["status"] == "rejected"


def test_superseded_survives_export_roundtrip(graph, tmp_path):
    from pgraph import export as export_mod
    from pgraph.schema import init

    old = capture.log_decision(graph, "Old", "x")
    capture.log_decision(graph, "New", "y", supersedes=[old])
    export_mod.export_graph(graph, graph.root)

    # Reimport into a fresh graph (different root) and confirm status + edge.
    g2 = init(tmp_path / "fresh")
    # point export dir at the original by copying the files
    import shutil
    (tmp_path / "fresh" / ".pgraph").mkdir(parents=True, exist_ok=True)
    shutil.copytree(graph.root / ".pgraph" / "export",
                    tmp_path / "fresh" / ".pgraph" / "export")
    export_mod.import_graph(g2, tmp_path / "fresh")
    assert g2.get_node("Decision", old)["status"] == "superseded"
    assert g2.count_edges("SUPERSEDES") == 1
    g2.close()
