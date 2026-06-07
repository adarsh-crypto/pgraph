"""Portability: dump the graph to git-diffable JSONL and load it back.

The SQLite file is the primary store (copy it and you've moved the memory), but
JSONL export is the insurance policy: human-readable, diffable in git, and
re-importable on a fresh machine. The on-disk node/edge model already matches the
JSONL record shape, so export/import are thin passthroughs over the storage API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import PK, Graph, export_dir
from .schema import NODE_LABELS, REL_SPECS


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
        for label in NODE_LABELS:
            for props in g.match_nodes(label, order_by=PK[label]):
                rec = {"label": label, "props": {k: _ser(v) for k, v in props.items()}}
                fh.write(json.dumps(rec, default=str) + "\n")
                n_nodes += 1

    n_edges = 0
    with edges_path.open("w") as fh:
        for rel, src, dst in REL_SPECS:
            for e in g.all_edges(rel):
                rec = {"rel": rel, "from": src, "to": dst, "src": e["src"], "dst": e["dst"]}
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
        props = rec["props"]
        pk = PK[label]
        g.add_node(label, props[pk], props)
        n_nodes += 1

    n_edges = 0
    if edges_path.exists():
        for line in edges_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            g.add_edge(rec["rel"], rec["from"], rec["src"], rec["to"], rec["dst"])
            n_edges += 1

    return {"nodes": n_nodes, "edges": n_edges, "dir": str(out_dir)}
