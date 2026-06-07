from pgraph import capture, eval as eval_mod


def _seed_big(graph, n=40):
    sid = capture.start_session(graph, "claude-code", "lots of work")
    for i in range(n):
        capture.log_change(graph, "edit", f"file_{i}.py", f"change number {i} with some detail", sid)
    capture.log_decision(graph, "Big decision", "a fairly long rationale " * 10)
    return sid


def test_evaluate_reports_savings(graph):
    _seed_big(graph, 40)
    r = eval_mod.evaluate(graph)
    assert r["flat_log_tokens"] > 0
    assert r["session_brief_tokens"] > 0
    # Brief is much smaller than the full log.
    assert r["session_brief_tokens"] < r["flat_log_tokens"]
    assert r["saved_pct"]["session_brief_vs_flat"] > 0
    assert "method" in r


def test_flat_log_grows_with_history(graph):
    sid = capture.start_session(graph, "claude-code", "s")
    capture.log_change(graph, "edit", "a.py", "x", sid)
    small = len(eval_mod.flat_log(graph))
    for i in range(50):
        capture.log_change(graph, "edit", f"f{i}.py", "y" * 50, sid)
    big = len(eval_mod.flat_log(graph))
    assert big > small * 5  # linear growth


def test_evaluate_empty_graph_is_safe(graph):
    r = eval_mod.evaluate(graph)
    # Empty graph: brief is empty and nothing crashes / divides by zero.
    assert r["session_brief_tokens"] == 0
    assert 0.0 <= r["saved_pct"]["session_brief_vs_flat"] <= 100.0


def test_method_label_present(graph):
    _seed_big(graph, 5)
    r = eval_mod.evaluate(graph)
    assert r["method"].startswith("tiktoken") or r["method"].startswith("estimate")
