from pgraph.db import graph_path
from pgraph.schema import init


def test_init_creates_graph_and_project(tmp_path):
    g = init(tmp_path)
    assert graph_path(tmp_path).exists()
    proj = g.one("MATCH (p:Project) RETURN p.name AS name")
    assert proj["name"] == tmp_path.name
    g.close()


def test_init_is_idempotent(tmp_path):
    init(tmp_path).close()
    g = init(tmp_path)  # second call must not error or duplicate the project
    n = g.one("MATCH (p:Project) RETURN count(p) AS c")
    assert n["c"] == 1
    g.close()
