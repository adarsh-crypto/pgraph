"""pgraph — local, portable, graph-based project memory for coding agents.

The graph lives in an embedded Kùzu database under ``<project>/.pgraph/graph``.
It records sessions, changes, decisions, files, repos and skills — each
timestamped — so an agent can query a small, targeted slice of project history
instead of re-reading a growing markdown log on every turn.
"""

__version__ = "0.1.0"

# Directory layout inside a project that uses pgraph.
PGRAPH_DIR = ".pgraph"
GRAPH_SUBDIR = "graph"
EXPORT_SUBDIR = "export"
