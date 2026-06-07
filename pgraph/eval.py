"""Reproducible token-savings evaluation.

The thesis pgraph rests on is "query a small slice instead of re-reading the
whole log." This module measures that on a real graph: it reconstructs the flat
markdown log an agent would otherwise re-read each turn, then compares its size
to what ``session_brief`` and ``context_pack`` actually return.

Token counts are *estimated* (chars / `_CHARS_PER_TOKEN`) when no real tokenizer
is installed; if ``tiktoken`` is importable it's used for exact counts. The
estimate is labelled in the output so numbers are never passed off as exact.
"""

from __future__ import annotations

from typing import Any

from . import query
from .db import Graph

# Rough average for English/code with BPE tokenizers; used only as a fallback.
_CHARS_PER_TOKEN = 4.0


def _counter():
    """Return (count_fn, method_label). Prefer a real tokenizer if available."""
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return (lambda s: len(enc.encode(s)), "tiktoken/cl100k_base")
    except Exception:
        return (lambda s: round(len(s) / _CHARS_PER_TOKEN), f"estimate(~{_CHARS_PER_TOKEN:g} chars/token)")


def flat_log(g: Graph) -> str:
    """Reconstruct the flat markdown log pgraph replaces — the re-read-everything
    baseline: every change (newest first) plus every decision body."""
    lines = ["# Project memory log", ""]
    for c in query.recent_changes(g, limit=1_000_000):
        lines.append(
            f"- [{c.get('ts', '')}] {c.get('kind', '')} {c.get('path', '')}: {c.get('summary', '')}"
        )
    decisions = g.match_nodes("Decision", order_by="ts", desc=True)
    for d in decisions:
        lines.append("")
        lines.append(f"## Decision: {d.get('title', '')}")
        lines.append(str(d.get("body", "")))
    return "\n".join(lines)


def evaluate(g: Graph, paths: list[str] | None = None, budget: int = 4000) -> dict[str, Any]:
    """Measure token cost of the flat log vs. pgraph's targeted retrieval.

    Returns the token method, the baseline (flat-log) cost, the cost of
    ``session_brief`` and ``context_pack``, and the percentage saved by each.
    """
    count, method = _counter()

    baseline_text = flat_log(g)
    baseline = count(baseline_text)

    brief_text = query.session_brief(g)
    brief = count(brief_text)

    import json as _json

    pack = query.context_pack(g, paths=paths, budget=budget)
    pack_tokens = count(_json.dumps(pack, default=str))

    def saved(part: int) -> float:
        if baseline <= 0:
            return 0.0
        return round(100.0 * (1.0 - part / baseline), 1)

    st = query.status(g)
    return {
        "method": method,
        "graph": st["totals"],
        "flat_log_tokens": baseline,
        "session_brief_tokens": brief,
        "context_pack_tokens": pack_tokens,
        "saved_pct": {
            "session_brief_vs_flat": saved(brief),
            "context_pack_vs_flat": saved(pack_tokens),
        },
    }
