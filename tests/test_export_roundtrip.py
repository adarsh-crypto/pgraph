import shutil

from pgraph import capture, export as export_mod, query
from pgraph.db import graph_path
from pgraph.schema import init


def _wipe_graph(tmp_path):
    """Kùzu may store the DB as a single file or a directory — remove either."""
    p = graph_path(tmp_path)
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    # Remove the wal/shadow sidecar files Kùzu leaves next to the DB.
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
    assert g2.one(
        "MATCH (d:Decision)-[:MOTIVATES]->(c:Change) RETURN d.title AS t"
    )["t"] == "JWT"
    assert g2.one(
        "MATCH (:Session)-[:USED_SKILL]->(sk:Skill) RETURN sk.name AS n"
    )["n"] == "deep-research"
    g2.close()
