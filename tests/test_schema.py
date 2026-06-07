from pgraph.db import graph_path
from pgraph.schema import init


def test_init_creates_graph_and_project(tmp_path):
    g = init(tmp_path)
    assert graph_path(tmp_path).exists()
    proj = g.match_nodes("Project")
    assert len(proj) == 1
    assert proj[0]["name"] == tmp_path.name
    g.close()


def test_init_is_idempotent(tmp_path):
    init(tmp_path).close()
    g = init(tmp_path)  # second call must not error or duplicate the project
    assert g.count_nodes("Project") == 1
    g.close()
