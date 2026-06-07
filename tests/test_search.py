from pgraph import capture, query


def _seed(g):
    sid = capture.start_session(g, "claude-code", "auth work")
    cid = capture.log_change(g, "edit", "src/auth.py", "add JWT verification", sid)
    capture.log_decision(
        g, "Use JWT not sessions", "stateless tokens scale horizontally",
        motivates_change_ids=[cid], about_paths=["src/auth.py"],
    )
    capture.log_change(g, "edit", "src/db.py", "tune connection pool", sid)
    return sid, cid


def test_search_finds_decision_by_body(graph):
    _seed(graph)
    hits = query.search(graph, "stateless")
    assert any(h.get("title") == "Use JWT not sessions" for h in hits)


def test_search_finds_change_by_summary(graph):
    _seed(graph)
    hits = query.search(graph, "JWT")
    # The change summary and the decision both mention JWT.
    labels = {h["_label"] for h in hits}
    assert "Change" in labels or "Decision" in labels
    assert any("JWT" in (h.get("summary", "") + h.get("title", "")) for h in hits)


def test_search_label_filter(graph):
    _seed(graph)
    hits = query.search(graph, "JWT", labels=["Decision"])
    assert hits
    assert all(h["_label"] == "Decision" for h in hits)


def test_search_empty_term_returns_nothing(graph):
    _seed(graph)
    assert query.search(graph, "") == []
    assert query.search(graph, "   ") == []


def test_search_no_match(graph):
    _seed(graph)
    assert query.search(graph, "kubernetes") == []


def test_search_hit_includes_label_marker(graph):
    _seed(graph)
    hits = query.search(graph, "connection pool")
    assert hits
    assert hits[0]["_label"] == "Change"
    assert hits[0]["path"] == "src/db.py"


def test_fts_enabled_in_this_env(graph):
    # The CI/dev environments ship FTS5; assert we detected it so a regression
    # (silent fallback) is visible.
    assert graph.fts_enabled is True


def test_reindex_rebuilds(graph):
    _seed(graph)
    n = graph.reindex()
    assert n > 0
    # Still searchable after a rebuild.
    assert query.search(graph, "stateless")


def test_busy_timeout_is_set(graph):
    row = graph._conn.execute("PRAGMA busy_timeout").fetchone()
    assert int(row[0]) >= 5000


def test_search_quoted_term_does_not_crash(graph):
    _seed(graph)
    # A stray quote is a malformed FTS query; must degrade, not raise.
    result = query.search(graph, 'JWT"')
    assert isinstance(result, list)
