"""Drive the MCP stdio server end-to-end to verify tools + resources.

These spawn the real `pgraph-mcp` process over stdio with PGRAPH_ROOT pointed at
a seeded temp graph, so they exercise the actual MCP wiring (not just the
underlying query functions).
"""

import json
import os
import subprocess
import sys

from pgraph import capture
from pgraph.schema import init


def _seed(root):
    g = init(root)
    sid = capture.start_session(g, "claude-code", "resource test session")
    capture.log_change(g, "edit", "src/auth.py", "add JWT", sid)
    capture.log_decision(g, "Use JWT", "stateless tokens", about_paths=["src/auth.py"])
    g.close()


def _rpc(root, *messages):
    """Send JSON-RPC messages to pgraph-mcp and return parsed responses by id."""
    pybin = sys.executable
    # Run the server module with the same interpreter running the tests.
    proc = subprocess.run(
        [pybin, "-m", "pgraph.mcp_server"],
        input="\n".join(json.dumps(m) for m in messages) + "\n",
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PGRAPH_ROOT": str(root)},
    )
    out = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if "id" in m:
            out[m["id"]] = m
    return out, proc


_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "t", "version": "1"}},
}
_INITED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_resources_listed(tmp_path):
    _seed(tmp_path)
    resp, proc = _rpc(
        tmp_path, _INIT, _INITED,
        {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
    )
    assert 2 in resp, proc.stderr
    uris = {r["uri"] for r in resp[2]["result"]["resources"]}
    assert "pgraph://brief" in uris
    assert "pgraph://status" in uris


def test_brief_resource_read(tmp_path):
    _seed(tmp_path)
    resp, proc = _rpc(
        tmp_path, _INIT, _INITED,
        {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
         "params": {"uri": "pgraph://brief"}},
    )
    assert 3 in resp, proc.stderr
    contents = resp[3]["result"]["contents"]
    text = contents[0]["text"]
    assert "pgraph project memory" in text
    assert "Use JWT" in text


def test_search_tool_over_mcp(tmp_path):
    _seed(tmp_path)
    resp, proc = _rpc(
        tmp_path, _INIT, _INITED,
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "search", "arguments": {"term": "JWT"}}},
    )
    assert 4 in resp, proc.stderr
    # Structured content or text content — just confirm JWT surfaces.
    blob = json.dumps(resp[4]["result"])
    assert "JWT" in blob
