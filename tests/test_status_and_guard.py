import pytest

from pgraph import capture, query
from pgraph.db import WriteQueryRejected, assert_read_only


def test_status_counts_nodes_edges_and_open_session(graph):
    sid = capture.start_session(graph, "claude-code", "s")
    cid = capture.log_change(graph, "edit", "a.py", "x", sid)
    capture.log_decision(graph, "why", "because", motivates_change_ids=[cid], about_paths=["a.py"])

    st = query.status(graph)
    assert st["nodes"]["Project"] == 1
    assert st["nodes"]["Session"] == 1
    assert st["nodes"]["Change"] == 1
    assert st["nodes"]["File"] == 1
    assert st["nodes"]["Decision"] == 1
    assert st["edges"]["AFFECTS"] == 1
    assert st["edges"]["MOTIVATES"] == 1
    assert st["totals"]["nodes"] >= 4
    # Session is still open.
    assert st["open_session"]["agent"] == "claude-code"

    capture.end_session(graph, sid, "done")
    assert query.status(graph)["open_session"] is None


@pytest.mark.parametrize(
    "bad",
    [
        "MATCH (n) DETACH DELETE n",
        "CREATE (n:Change {id:'x'})",
        "MATCH (n:File) SET n.lang='python'",
        "MERGE (a:Agent {id:'x'})",
        "match (n) remove n.lang",
    ],
)
def test_assert_read_only_rejects_writes(bad):
    with pytest.raises(WriteQueryRejected):
        assert_read_only(bad)


@pytest.mark.parametrize(
    "ok",
    [
        "MATCH (c:Change) RETURN c.path",
        "MATCH (d:Decision)-[:MOTIVATES]->(c:Change) RETURN d.title, c.summary",
        "MATCH (n:File) WHERE n.lang = 'python' RETURN n.path ORDER BY n.path",
    ],
)
def test_assert_read_only_allows_reads(ok):
    assert_read_only(ok)  # should not raise
