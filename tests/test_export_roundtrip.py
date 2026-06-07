from pgraph import capture, export as export_mod, query
from pgraph.db import graph_path
from pgraph.schema import init


def _wipe_graph(tmp_path):
    """Remove the SQLite graph file and its WAL/SHM sidecars."""
    p = graph_path(tmp_path)
    for sidecar in p.parent.glob(p.name + "*"):
        if sidecar.is_file():
            sidecar.unlink()


def test_export_import_roundtrip(tmp_path):
    g = init(tmp_path)
    sid = capture.start_session(g, "claude-code", "s")
    cid = capture.log_change(g, "edit", "src/auth.py", "jwt", sid)
    capture.log_decision(g, "JWT", "stateless", motivates_change_ids=[cid], about_paths=["src/auth.py"])
    capture.record_skill_use(g, sid, "deep-research")
    capture.end_session(g, sid, "done")

    before = export_mod.export_graph(g, tmp_path)
    g.close()

    # Wipe the live graph, keep the export, reimport into a fresh DB.
    _wipe_graph(tmp_path)
    g2 = init(tmp_path)
    after = export_mod.import_graph(g2, tmp_path)

    assert after["nodes"] == before["nodes"]
    assert after["edges"] == before["edges"]

    # Relationships survived the round-trip.
    hist = query.file_history(g2, "src/auth.py")
    assert len(hist["changes"]) == 1
    assert hist["decisions"][0]["title"] == "JWT"
    assert query.decisions_for(g2, cid)[0]["title"] == "JWT"
    assert g2.count_edges("USED_SKILL") == 1
    assert g2.get_node("Skill", "deep-research") is not None
    g2.close()
