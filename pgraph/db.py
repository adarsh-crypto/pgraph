"""Database location, connection management and thin query helpers for Kùzu."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import kuzu

from . import EXPORT_SUBDIR, GRAPH_SUBDIR, PGRAPH_DIR


# Cypher keywords that mutate the graph. The `cypher` escape hatch is meant for
# reads only, so a query containing any of these (as a whole word) is rejected.
_WRITE_KEYWORDS = frozenset({
    "CREATE", "MERGE", "SET", "DELETE", "REMOVE", "DETACH", "DROP",
    "ALTER", "COPY", "INSTALL", "LOAD", "ATTACH", "USE",
})


class WriteQueryRejected(ValueError):
    """Raised when a write statement is passed to a read-only query path."""


def assert_read_only(cypher: str) -> None:
    """Raise :class:`WriteQueryRejected` if *cypher* looks like it mutates data.

    A deliberately conservative, token-level check: it tokenizes on word
    boundaries and rejects the query if any write keyword appears. This guards
    the ``cypher`` escape hatch (CLI + MCP) from being used to mutate the graph.
    """
    import re

    tokens = {t.upper() for t in re.findall(r"[A-Za-z_]+", cypher)}
    hit = tokens & _WRITE_KEYWORDS
    if hit:
        raise WriteQueryRejected(
            "cypher is read-only; refusing statement containing "
            + ", ".join(sorted(hit))
        )


def find_project_root(start: str | os.PathLike[str] | None = None) -> Path | None:
    """Walk upward from *start* (default cwd) looking for an existing ``.pgraph`` dir.

    Returns the project root (the dir *containing* ``.pgraph``) or ``None`` if no
    initialized graph is found between *start* and the filesystem root.
    """
    cur = Path(start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / PGRAPH_DIR / GRAPH_SUBDIR).exists():
            return candidate
    return None


def pgraph_dir(root: str | os.PathLike[str]) -> Path:
    return Path(root).resolve() / PGRAPH_DIR


def graph_path(root: str | os.PathLike[str]) -> Path:
    return pgraph_dir(root) / GRAPH_SUBDIR


def export_dir(root: str | os.PathLike[str]) -> Path:
    return pgraph_dir(root) / EXPORT_SUBDIR


class Graph:
    """A handle on the Kùzu database for one project.

    Wraps a :class:`kuzu.Database`/:class:`kuzu.Connection` pair and exposes
    parameterized ``run``/``one``/``all`` helpers that return plain Python
    dicts so callers never touch the QueryResult cursor directly.
    """

    def __init__(self, root: str | os.PathLike[str], read_only: bool = False):
        self.root = Path(root).resolve()
        self.path = graph_path(self.root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(self.path), read_only=read_only)
        self._conn = kuzu.Connection(self._db)

    # -- low level ---------------------------------------------------------
    def run(self, cypher: str, params: dict[str, Any] | None = None):
        """Execute a statement, returning the raw Kùzu QueryResult."""
        if params:
            return self._conn.execute(cypher, parameters=params)
        return self._conn.execute(cypher)

    def all(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute and materialize every row as a dict keyed by return column."""
        res = self.run(cypher, params)
        cols = res.get_column_names()
        rows: list[dict[str, Any]] = []
        while res.has_next():
            rows.append(dict(zip(cols, res.get_next())))
        return rows

    def one(self, cypher: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.all(cypher, params)
        return rows[0] if rows else None

    def close(self) -> None:
        # Kùzu releases the DB lock when the objects are dropped.
        self._conn = None
        self._db = None

    def __enter__(self) -> "Graph":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
