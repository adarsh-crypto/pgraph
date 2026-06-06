"""Graph schema: node/relationship table DDL and idempotent initialization."""

from __future__ import annotations

import datetime as _dt
import uuid
from pathlib import Path

from .db import Graph

# Each statement is created with IF NOT EXISTS so init() is safe to re-run
# (acts as a lightweight migration: adding a new table here is picked up on
# the next init without disturbing existing data).
NODE_TABLES = [
    """CREATE NODE TABLE IF NOT EXISTS Project(
        id STRING, name STRING, root_path STRING, created_at TIMESTAMP,
        PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Agent(
        id STRING, name STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Session(
        id STRING, agent_name STRING, started_at TIMESTAMP,
        ended_at TIMESTAMP, summary STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Change(
        id STRING, kind STRING, path STRING, summary STRING,
        diff_stat STRING, ts TIMESTAMP, PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS File(
        path STRING, lang STRING, last_seen_at TIMESTAMP, PRIMARY KEY(path))""",
    """CREATE NODE TABLE IF NOT EXISTS Decision(
        id STRING, title STRING, body STRING, ts TIMESTAMP, PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Repo(
        id STRING, name STRING, url STRING, path STRING, kind STRING,
        PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Skill(
        name STRING, PRIMARY KEY(name))""",
]

REL_TABLES = [
    "CREATE REL TABLE IF NOT EXISTS IN_PROJECT(FROM Session TO Project)",
    "CREATE REL TABLE IF NOT EXISTS BY_AGENT(FROM Session TO Agent)",
    "CREATE REL TABLE IF NOT EXISTS IN_SESSION(FROM Change TO Session)",
    "CREATE REL TABLE IF NOT EXISTS AFFECTS(FROM Change TO File)",
    "CREATE REL TABLE IF NOT EXISTS MOTIVATES(FROM Decision TO Change)",
    "CREATE REL TABLE IF NOT EXISTS ABOUT(FROM Decision TO File)",
    "CREATE REL TABLE IF NOT EXISTS USED_SKILL(FROM Session TO Skill)",
    "CREATE REL TABLE IF NOT EXISTS IN_REPO(FROM File TO Repo)",
]


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
    """Create the graph database and schema if missing; return an open handle.

    Also ensures a single ``Project`` node exists for *root*.
    """
    g = Graph(root)
    for ddl in NODE_TABLES:
        g.run(ddl)
    for ddl in REL_TABLES:
        g.run(ddl)
    _ensure_project(g)
    return g


def _ensure_project(g: Graph) -> None:
    existing = g.one("MATCH (p:Project) RETURN p.id AS id LIMIT 1")
    if existing:
        return
    root = Path(g.root)
    g.run(
        "CREATE (p:Project {id:$id, name:$name, root_path:$root, created_at:$ts})",
        {"id": new_id(), "name": root.name, "root": str(root), "ts": now()},
    )
