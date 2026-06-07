"""Storage: an embedded, zero-dependency graph store over stdlib ``sqlite3``.

The whole graph lives in a single SQLite file at ``<project>/.pgraph/graph``.
Two tables hold everything — a generic *nodes* table (label + primary key +
a JSON property blob) and an *edges* table (typed, endpoint-keyed). Every
traversal pgraph needs is a 1–2 hop lookup, so simple indexed joins cover the
query layer with no external engine and no compiled artifact.

This mirrors the well-known "simple-graph" SQLite pattern, kept in-tree so the
project depends only on the Python standard library. The JSON property shape is
identical to the JSONL export, making export/import trivial and lossless.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import EXPORT_SUBDIR, GRAPH_SUBDIR, PGRAPH_DIR

# Primary-key property name per node label. The pk is also stored inside the
# JSON props so a node round-trips to/from the export unchanged.
PK = {
    "Project": "id", "Agent": "id", "Session": "id", "Change": "id",
    "File": "path", "Decision": "id", "Repo": "id", "Skill": "name",
}


# SQL statements that mutate data or schema. The `sql` escape hatch is read-only,
# so a query containing any of these (as a whole word) is rejected — and we also
# run it on a read-only connection as a hard backstop.
_WRITE_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "REPLACE", "DROP", "CREATE", "ALTER",
    "TRUNCATE", "ATTACH", "DETACH", "VACUUM", "REINDEX", "PRAGMA",
})


class WriteQueryRejected(ValueError):
    """Raised when a write statement is passed to a read-only query path."""


def assert_read_only(sql: str) -> None:
    """Raise :class:`WriteQueryRejected` if *sql* looks like it mutates data.

    A deliberately conservative, token-level check guarding the ``sql`` escape
    hatch (CLI + MCP). Reads still also run on a read-only connection, so this
    is the friendly first line of defence, not the only one.
    """
    import re

    tokens = {t.upper() for t in re.findall(r"[A-Za-z_]+", sql)}
    hit = tokens & _WRITE_KEYWORDS
    if hit:
        raise WriteQueryRejected(
            "sql is read-only; refusing statement containing "
            + ", ".join(sorted(hit))
        )


def find_project_root(start: str | os.PathLike[str] | None = None) -> Path | None:
    """Walk upward from *start* (default cwd) looking for an existing ``.pgraph`` graph.

    Returns the project root (the dir *containing* ``.pgraph``) or ``None`` if no
    initialized graph is found between *start* and the filesystem root. Walking
    upward is what lets a single project root serve every repo nested inside it.
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


def _ser(v: Any) -> Any:
    """Make a property value JSON-friendly (datetimes -> ISO strings)."""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _ser_props(props: dict[str, Any]) -> dict[str, Any]:
    return {k: _ser(v) for k, v in props.items()}


class Graph:
    """A handle on the SQLite-backed graph for one project.

    Exposes a small typed API — node upserts/lookups and edge create/traverse —
    plus a read-only :meth:`sql` passthrough. Capture and query layers call these
    helpers instead of any query language, so there is no engine to depend on.
    """

    def __init__(self, root: str | os.PathLike[str], read_only: bool = False):
        self.root = Path(root).resolve()
        self.path = graph_path(self.root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if read_only:
            uri = f"file:{self.path}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True)
        else:
            self._conn = sqlite3.connect(str(self.path))
            # WAL gives durable, crash-safe writes without blocking readers.
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = sqlite3.Row

    # -- schema ------------------------------------------------------------
    def init_schema(self) -> None:
        cur = self._conn
        cur.execute(
            """CREATE TABLE IF NOT EXISTS nodes (
                   label TEXT NOT NULL,
                   pk    TEXT NOT NULL,
                   props TEXT NOT NULL,
                   PRIMARY KEY (label, pk)
               )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS edges (
                   rel       TEXT NOT NULL,
                   src_label TEXT NOT NULL,
                   src_pk    TEXT NOT NULL,
                   dst_label TEXT NOT NULL,
                   dst_pk    TEXT NOT NULL
               )"""
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS edges_src ON edges(rel, src_label, src_pk)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS edges_dst ON edges(rel, dst_label, dst_pk)"
        )
        cur.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS edges_uniq
               ON edges(rel, src_label, src_pk, dst_label, dst_pk)"""
        )
        self._conn.commit()

    # -- nodes -------------------------------------------------------------
    def add_node(self, label: str, pk: str, props: dict[str, Any]) -> None:
        """Insert a node, or no-op if one with this (label, pk) already exists."""
        self._conn.execute(
            "INSERT OR IGNORE INTO nodes(label, pk, props) VALUES (?,?,?)",
            (label, str(pk), json.dumps(_ser_props(props))),
        )
        self._conn.commit()

    def get_node(self, label: str, pk: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT props FROM nodes WHERE label=? AND pk=?", (label, str(pk))
        ).fetchone()
        return json.loads(row["props"]) if row else None

    def set_node_props(self, label: str, pk: str, updates: dict[str, Any]) -> None:
        """Merge *updates* into an existing node's props (no-op if missing)."""
        cur = self.get_node(label, pk)
        if cur is None:
            return
        cur.update(_ser_props(updates))
        self._conn.execute(
            "UPDATE nodes SET props=? WHERE label=? AND pk=?",
            (json.dumps(cur), label, str(pk)),
        )
        self._conn.commit()

    def match_nodes(
        self,
        label: str,
        where: list[tuple[str, str, Any]] | None = None,
        order_by: str | None = None,
        desc: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return node props for *label*, optionally filtered/ordered/limited.

        *where* is a list of ``(prop, op, value)`` triples ANDed together, where
        ``op`` is a literal SQL comparison (``=``, ``>=``, ``IS``). Property
        access goes through ``json_extract`` so callers never write SQL.
        """
        sql = "SELECT props FROM nodes WHERE label=?"
        params: list[Any] = [label]
        for prop, op, value in where or []:
            if op.upper() == "IS" and value is None:
                sql += f" AND json_extract(props, '$.{prop}') IS NULL"
            else:
                sql += f" AND json_extract(props, '$.{prop}') {op} ?"
                params.append(_ser(value))
        if order_by:
            sql += f" ORDER BY json_extract(props, '$.{order_by}') {'DESC' if desc else 'ASC'}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [json.loads(r["props"]) for r in self._conn.execute(sql, params)]

    def count_nodes(self, label: str) -> int:
        row = self._conn.execute(
            "SELECT count(*) AS c FROM nodes WHERE label=?", (label,)
        ).fetchone()
        return int(row["c"]) if row else 0

    # -- edges -------------------------------------------------------------
    def add_edge(
        self, rel: str, src_label: str, src_pk: str, dst_label: str, dst_pk: str
    ) -> None:
        """Create an edge, deduped on the full (rel, endpoints) tuple."""
        self._conn.execute(
            """INSERT OR IGNORE INTO edges(rel, src_label, src_pk, dst_label, dst_pk)
               VALUES (?,?,?,?,?)""",
            (rel, src_label, str(src_pk), dst_label, str(dst_pk)),
        )
        self._conn.commit()

    def has_edge(
        self, rel: str, src_label: str, src_pk: str, dst_label: str, dst_pk: str
    ) -> bool:
        row = self._conn.execute(
            """SELECT 1 FROM edges
               WHERE rel=? AND src_label=? AND src_pk=? AND dst_label=? AND dst_pk=?""",
            (rel, src_label, str(src_pk), dst_label, str(dst_pk)),
        ).fetchone()
        return row is not None

    def count_edges(self, rel: str) -> int:
        row = self._conn.execute(
            "SELECT count(*) AS c FROM edges WHERE rel=?", (rel,)
        ).fetchone()
        return int(row["c"]) if row else 0

    def out_neighbors(
        self,
        rel: str,
        src_label: str,
        src_pk: str,
        dst_label: str,
        order_by: str | None = None,
        desc: bool = False,
    ) -> list[dict[str, Any]]:
        """Props of nodes reachable via ``(src)-[rel]->(dst)``."""
        sql = """SELECT n.props AS props FROM edges e
                 JOIN nodes n ON n.label=e.dst_label AND n.pk=e.dst_pk
                 WHERE e.rel=? AND e.src_label=? AND e.src_pk=? AND e.dst_label=?"""
        params: list[Any] = [rel, src_label, str(src_pk), dst_label]
        if order_by:
            sql += f" ORDER BY json_extract(n.props, '$.{order_by}') {'DESC' if desc else 'ASC'}"
        return [json.loads(r["props"]) for r in self._conn.execute(sql, params)]

    def in_neighbors(
        self,
        rel: str,
        dst_label: str,
        dst_pk: str,
        src_label: str,
        order_by: str | None = None,
        desc: bool = False,
    ) -> list[dict[str, Any]]:
        """Props of nodes that point in via ``(src)-[rel]->(dst)``."""
        sql = """SELECT n.props AS props FROM edges e
                 JOIN nodes n ON n.label=e.src_label AND n.pk=e.src_pk
                 WHERE e.rel=? AND e.dst_label=? AND e.dst_pk=? AND e.src_label=?"""
        params: list[Any] = [rel, dst_label, str(dst_pk), src_label]
        if order_by:
            sql += f" ORDER BY json_extract(n.props, '$.{order_by}') {'DESC' if desc else 'ASC'}"
        return [json.loads(r["props"]) for r in self._conn.execute(sql, params)]

    def all_edges(self, rel: str) -> list[dict[str, str]]:
        """Raw (src_pk, dst_pk) pairs for one rel type — used by export."""
        rows = self._conn.execute(
            "SELECT src_pk, dst_pk FROM edges WHERE rel=?", (rel,)
        ).fetchall()
        return [{"src": r["src_pk"], "dst": r["dst_pk"]} for r in rows]

    # -- read-only escape hatch -------------------------------------------
    def sql(self, query: str, params: tuple | None = None) -> list[dict[str, Any]]:
        """Run a read-only SQL query against a fresh read-only connection.

        The store is two tables — ``nodes(label, pk, props)`` and
        ``edges(rel, src_label, src_pk, dst_label, dst_pk)`` — with ``props`` a
        JSON blob queryable via ``json_extract(props, '$.field')``.
        """
        assert_read_only(query)
        uri = f"file:{self.path}?mode=ro"
        ro = sqlite3.connect(uri, uri=True)
        ro.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in ro.execute(query, params or ())]
        finally:
            ro.close()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Graph":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
