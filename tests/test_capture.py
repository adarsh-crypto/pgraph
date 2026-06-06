import pytest

from pgraph import capture


def test_session_change_decision_links(graph):
    sid = capture.start_session(graph, "claude-code", "wiring auth")
    cid = capture.log_change(graph, "edit", "src/auth.py", "add jwt", sid)
    did = capture.log_decision(
        graph, "Use JWT", "stateless", motivates_change_ids=[cid], about_paths=["src/auth.py"]
    )

    # Change -> Session
    assert graph.one(
        "MATCH (c:Change {id:$c})-[:IN_SESSION]->(s:Session {id:$s}) RETURN c.id AS id",
        {"c": cid, "s": sid},
    )
    # Change -> File
    assert graph.one(
        "MATCH (c:Change {id:$c})-[:AFFECTS]->(f:File {path:'src/auth.py'}) RETURN c.id AS id",
        {"c": cid},
    )
    # Decision -> Change and Decision -> File
    assert graph.one(
        "MATCH (d:Decision {id:$d})-[:MOTIVATES]->(c:Change {id:$c}) RETURN d.id AS id",
        {"d": did, "c": cid},
    )
    assert graph.one(
        "MATCH (d:Decision {id:$d})-[:ABOUT]->(f:File {path:'src/auth.py'}) RETURN d.id AS id",
        {"d": did},
    )


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
    n = graph.one(
        "MATCH (s:Session {id:$s})-[r:USED_SKILL]->(:Skill) RETURN count(r) AS c", {"s": sid}
    )
    assert n["c"] == 1
