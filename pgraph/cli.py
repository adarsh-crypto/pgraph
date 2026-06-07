"""``pgraph`` command-line interface.

Thin wrapper over capture/query/scan/gitlog/export so that humans and the
Claude Code hooks invoke exactly the same logic the MCP server exposes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import capture, export as export_mod, query, scan as scan_mod
from .db import Graph, WriteQueryRejected, assert_read_only, find_project_root
from .schema import init as schema_init


def _resolve_root(explicit: str | None) -> Path:
    """Pick the project root: an explicit flag, else the nearest initialized graph, else cwd."""
    if explicit:
        return Path(explicit).resolve()
    found = find_project_root()
    return found if found else Path.cwd()


def _open(root: Path) -> Graph:
    if not (root / ".pgraph" / "graph").exists():
        raise click.ClickException(
            f"No pgraph database under {root}. Run `pgraph init` first."
        )
    return Graph(root)


def _emit(obj) -> None:
    click.echo(json.dumps(obj, indent=2, default=str))


@click.group()
@click.option("--root", default=None, help="Project root (defaults to nearest .pgraph or cwd).")
@click.pass_context
def main(ctx: click.Context, root: str | None) -> None:
    """Local, portable, graph-based project memory for coding agents."""
    ctx.ensure_object(dict)
    ctx.obj["root"] = _resolve_root(root)


@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Create the graph database and schema for this project."""
    root = ctx.obj["root"]
    g = schema_init(root)
    g.close()
    click.echo(f"Initialized pgraph at {root / '.pgraph' / 'graph'}")


# -- sessions --------------------------------------------------------------
@main.group()
def session() -> None:
    """Manage work sessions."""


@session.command("start")
@click.option("--agent", required=True, help="Agent name, e.g. claude-code, codex.")
@click.option("--summary", default="", help="Optional initial summary.")
@click.pass_context
def session_start(ctx: click.Context, agent: str, summary: str) -> None:
    with _open(ctx.obj["root"]) as g:
        sid = capture.start_session(g, agent, summary)
    click.echo(sid)


@session.command("end")
@click.option("--id", "session_id", default=None, help="Session id (defaults to latest open).")
@click.option("--summary", default="", help="Closing summary.")
@click.pass_context
def session_end(ctx: click.Context, session_id: str | None, summary: str) -> None:
    with _open(ctx.obj["root"]) as g:
        sid = session_id or capture.latest_open_session(g)
        if not sid:
            raise click.ClickException("No open session to end.")
        capture.end_session(g, sid, summary)
    click.echo(f"Ended session {sid}")


@session.command("show")
@click.option("--id", "session_id", default=None)
@click.pass_context
def session_show(ctx: click.Context, session_id: str | None) -> None:
    with _open(ctx.obj["root"]) as g:
        _emit(query.session_summary(g, session_id))


# -- capture ---------------------------------------------------------------
@main.command()
@click.option("--kind", required=True, type=click.Choice(sorted(capture.VALID_CHANGE_KINDS)))
@click.option("--path", required=True)
@click.option("--summary", default="")
@click.option("--session", "session_id", default=None, help="Session id (defaults to latest open).")
@click.option("--diff-stat", default="")
@click.pass_context
def log(ctx: click.Context, kind: str, path: str, summary: str, session_id: str | None, diff_stat: str) -> None:
    """Record a change to a file."""
    with _open(ctx.obj["root"]) as g:
        sid = session_id or capture.latest_open_session(g)
        cid = capture.log_change(g, kind, path, summary, sid, diff_stat)
    click.echo(cid)


@main.command()
@click.option("--title", required=True)
@click.option("--body", default="")
@click.option("--motivates", multiple=True, help="Change id this decision motivates (repeatable).")
@click.option("--about", multiple=True, help="File path this decision is about (repeatable).")
@click.pass_context
def decide(ctx: click.Context, title: str, body: str, motivates: tuple[str, ...], about: tuple[str, ...]) -> None:
    """Record a manual 'why' note (a decision)."""
    with _open(ctx.obj["root"]) as g:
        did = capture.log_decision(g, title, body, list(motivates), list(about))
    click.echo(did)


@main.command("skill")
@click.argument("name")
@click.option("--session", "session_id", default=None)
@click.pass_context
def skill(ctx: click.Context, name: str, session_id: str | None) -> None:
    """Record that a skill/tool was used in a session."""
    with _open(ctx.obj["root"]) as g:
        sid = session_id or capture.latest_open_session(g)
        if not sid:
            raise click.ClickException("No open session; start one first.")
        capture.record_skill_use(g, sid, name)
    click.echo(f"Recorded skill {name}")


# -- query -----------------------------------------------------------------
@main.command()
@click.option("--path", default=None, help="Only this file's history.")
@click.option("--limit", default=20)
@click.option("--since", default=None, help="ISO timestamp lower bound.")
@click.pass_context
def history(ctx: click.Context, path: str | None, limit: int, since: str | None) -> None:
    """Show recent changes (optionally for one file)."""
    with _open(ctx.obj["root"]) as g:
        if path:
            _emit(query.file_history(g, path))
        else:
            _emit(query.recent_changes(g, limit=limit, since=since))


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show a health summary: node/edge counts and any open session."""
    with _open(ctx.obj["root"]) as g:
        _emit(query.status(g))


@main.command()
@click.argument("paths", nargs=-1)
@click.option("--budget", default=4000, help="Approximate character budget for the bundle.")
@click.pass_context
def context(ctx: click.Context, paths: tuple[str, ...], budget: int) -> None:
    """Build a compact context bundle for the given files (the token-saving query)."""
    with _open(ctx.obj["root"]) as g:
        _emit(query.context_pack(g, list(paths) or None, budget))


# -- ingest ----------------------------------------------------------------
@main.command()
@click.pass_context
def scan(ctx: click.Context) -> None:
    """Walk the project folder, recording File and Repo nodes."""
    with _open(ctx.obj["root"]) as g:
        stats = scan_mod.scan_project(g, ctx.obj["root"])
    _emit(stats)


@main.command("ingest-git")
@click.option("--limit", default=200, help="Max commits to ingest per repo.")
@click.pass_context
def ingest_git(ctx: click.Context, limit: int) -> None:
    """Backfill commit history (project + cloned repos) as Change nodes."""
    with _open(ctx.obj["root"]) as g:
        stats = scan_mod.ingest_all_git(g, ctx.obj["root"], limit=limit)
    _emit(stats)


# -- portability -----------------------------------------------------------
@main.command("export")
@click.pass_context
def export_cmd(ctx: click.Context) -> None:
    """Dump the graph to git-diffable JSONL under .pgraph/export/."""
    with _open(ctx.obj["root"]) as g:
        out = export_mod.export_graph(g, ctx.obj["root"])
    _emit(out)


@main.command("import")
@click.pass_context
def import_cmd(ctx: click.Context) -> None:
    """Load a JSONL export into a freshly initialized graph."""
    root = ctx.obj["root"]
    g = schema_init(root)
    try:
        out = export_mod.import_graph(g, root)
    finally:
        g.close()
    _emit(out)


@main.command("install-hooks")
@click.option("--pgraph-bin", default="pgraph", help="Path/name of the pgraph executable the hooks call.")
@click.pass_context
def install_hooks(ctx: click.Context, pgraph_bin: str) -> None:
    """Wire auto-capture hooks into this project's .claude/settings.json."""
    from .install_hooks import install

    _emit(install(ctx.obj["root"], pgraph_bin))


# -- hooks (invoked by Claude Code, read JSON from stdin) ------------------
@main.command("hook-post-tool-use", hidden=True)
def hook_post_tool_use() -> None:
    """PostToolUse hook entry: record a Change from stdin JSON. Never errors out."""
    from .hook import post_tool_use

    sys.exit(post_tool_use())


@main.command("hook-stop", hidden=True)
def hook_stop() -> None:
    """Stop hook entry: end the open session and export a snapshot."""
    from .hook import stop

    sys.exit(stop())


@main.command()
@click.argument("query_text")
@click.pass_context
def cypher(ctx: click.Context, query_text: str) -> None:
    """Run an arbitrary read Cypher query (escape hatch)."""
    try:
        assert_read_only(query_text)
    except WriteQueryRejected as exc:
        raise click.ClickException(str(exc))
    with _open(ctx.obj["root"]) as g:
        _emit([{k: str(v) for k, v in r.items()} for r in g.all(query_text)])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
