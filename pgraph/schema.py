"""Graph schema: the label registry and idempotent initialization.

Storage is two SQLite tables (see :mod:`pgraph.db`); the "schema" here is the
set of node labels and relationship types pgraph uses, plus a tiny bootstrap
that guarantees a single ``Project`` node exists.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from pathlib import Path

from .db import Graph

# Node labels and the property each one carries. Adding a label here is all it
# takes for export/status/import to pick it up.
NODE_LABELS = ["Project", "Agent", "Session", "Change", "File", "Decision", "Repo", "Skill"]

# Relationship types as (rel, FROM label, TO label).
REL_SPECS = [
    ("IN_PROJECT", "Session", "Project"),
    ("BY_AGENT", "Session", "Agent"),
    ("IN_SESSION", "Change", "Session"),
    ("AFFECTS", "Change", "File"),
    ("MOTIVATES", "Decision", "Change"),
    ("ABOUT", "Decision", "File"),
    ("USED_SKILL", "Session", "Skill"),
    ("IN_REPO", "File", "Repo"),
    # A newer decision that replaces an older one: (new)-[:SUPERSEDES]->(old).
    ("SUPERSEDES", "Decision", "Decision"),
]
REL_TYPES = [r[0] for r in REL_SPECS]


def now() -> _dt.datetime:
    """UTC timestamp for node creation.

    Microsecond precision is kept so changes made in rapid succession (e.g. an
    agent editing several files in the same second) still order deterministically
    by time.
    """
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


def init(root: str | Path) -> Graph:
    """Create the graph store and schema if missing; return an open handle.

    Also ensures a single ``Project`` node exists for *root*. Safe to re-run.
    """
    g = Graph(root)
    g.init_schema()
    _ensure_project(g)
    return g


def _ensure_project(g: Graph) -> None:
    if g.count_nodes("Project") > 0:
        return
    root = Path(g.root)
    pid = new_id()
    g.add_node(
        "Project",
        pid,
        {"id": pid, "name": root.name, "root_path": str(root), "created_at": now()},
    )
