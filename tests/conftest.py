import pytest

from pgraph.schema import init


@pytest.fixture
def graph(tmp_path):
    """A freshly initialized pgraph graph rooted at a temp dir."""
    g = init(tmp_path)
    yield g
    g.close()


@pytest.fixture
def root(tmp_path):
    return tmp_path
