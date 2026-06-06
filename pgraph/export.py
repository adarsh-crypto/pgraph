"""Portability: dump the graph to git-diffable JSONL and load it back.

The Kùzu folder is the primary store (copy it and you've moved the memory),
but JSONL export is the insurance policy: human-readable, diffable in git, and
re-importable even across Kùzu versions or onto a fresh machine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Graph, export_dir

# (label, list of property names) for each node table.
_NODE_SPECS = [
    ("Project", ["id", "name", "root_path", "created_at"]),
    ("Agent", ["id", "name"]),
    ("Session", ["id", "agent_name", "started_at", "ended_at", "summary"]),
    ("Change", ["id", "kind", "path", "summary", "diff_stat", "ts"]),
    ("File", ["path", "lang", "last_seen_at"]),
    ("Decision", ["id", "title", "body", "ts"]),
    ("Repo", ["id", "name", "url", "path", "kind"]),
    ("Skill", ["name"]),
]

# Primary-key property per label (used to match endpoints on import).
_PK = {
    "Project": "id", "Agent": "id", "Session": "id", "Change": "id",
    "File": "path", "Decision": "id", "Repo": "id", "Skill": "name",
}

# (rel type, FROM label, TO label) for each relationship table.
_REL_SPECS = [
    ("IN_PROJECT", "Session", "Project"),
    ("BY_AGENT", "Session", "Agent"),
    ("IN_SESSION", "Change", "Session"),
    ("AFFECTS", "Change", "File"),
    ("MOTIVATES", "Decision", "Change"),
    ("ABOUT", "Decision", "File"),
    ("USED_SKILL", "Session", "Skill"),
    ("IN_REPO", "File", "Repo"),
]


def _ser(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def export_graph(g: Graph, root: str | Path) -> dict:
    """Write nodes.jsonl and edges.jsonl; return counts and the output path."""
    out_dir = export_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes_path = out_dir / "nodes.jsonl"
    edges_path = out_dir / "edges.jsonl"

    n_nodes = 0
    with nodes_path.open("w") as fh:
        for label, props in _NODE_SPECS:
            ret = ", ".join(f"n.{p} AS {p}" for p in props)
            for row in g.all(f"MATCH (n:{label}) RETURN {ret}"):
                rec = {"label": label, "props": {p: _ser(row[p]) for p in props}}
                fh.write(json.dumps(rec, default=str) + "\n")
                n_nodes += 1

    n_edges = 0
    with edges_path.open("w") as fh:
        for rel, src, dst in _REL_SPECS:
            spk, dpk = _PK[src], _PK[dst]
            rows = g.all(
                f"""MATCH (a:{src})-[:{rel}]->(b:{dst})
                    RETURN a.{spk} AS src, b.{dpk} AS dst"""
            )
            for row in rows:
                rec = {"rel": rel, "from": src, "to": dst,
                       "src": _ser(row["src"]), "dst": _ser(row["dst"])}
                fh.write(json.dumps(rec, default=str) + "\n")
                n_edges += 1

    return {"nodes": n_nodes, "edges": n_edges, "dir": str(out_dir)}


def import_graph(g: Graph, root: str | Path) -> dict:
    """Load nodes.jsonl + edges.jsonl into *g* (assumes schema already created)."""
    out_dir = export_dir(root)
    nodes_path = out_dir / "nodes.jsonl"
    edges_path = out_dir / "edges.jsonl"
    if not nodes_path.exists():
        raise FileNotFoundError(f"No export found at {nodes_path}")

    n_nodes = 0
    for line in nodes_path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        label = rec["label"]
        props = {k: _deser(v) for k, v in rec["props"].items()}
        pk = _PK[label]
        # MERGE on the PK, then set the remaining props. Only pass parameters
        # the statement actually references — Kùzu rejects unused parameters.
        set_keys = [k for k in props if k != pk]
        cypher = f"MERGE (n:{label} {{{pk}:$_pk}})"
        params = {"_pk": props[pk]}
        if set_keys:
            cypher += " SET " + ", ".join(f"n.{k}=${k}" for k in set_keys)
            params.update({k: props[k] for k in set_keys})
        g.run(cypher, params)
        n_nodes += 1

    n_edges = 0
    if edges_path.exists():
        for line in edges_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            spk, dpk = _PK[rec["from"]], _PK[rec["to"]]
            g.run(
                f"""MATCH (a:{rec['from']} {{{spk}:$src}}),(b:{rec['to']} {{{dpk}:$dst}})
                    CREATE (a)-[:{rec['rel']}]->(b)""",
                {"src": _deser(rec["src"]), "dst": _deser(rec["dst"])},
            )
            n_edges += 1

    return {"nodes": n_nodes, "edges": n_edges, "dir": str(out_dir)}


def _deser(v: Any) -> Any:
    """Turn ISO strings back into datetimes so TIMESTAMP columns load correctly."""
    if isinstance(v, str) and len(v) >= 19 and v[4] == "-" and v[7] == "-" and "T" in v:
        import datetime as _dt
        try:
            dt = _dt.datetime.fromisoformat(v)
            if dt.tzinfo is not None:
                dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            return v
    return v
