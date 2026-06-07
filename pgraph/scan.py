"""Populate File/Repo nodes from the project folder and ingest git history.

`scan_project` walks the tree and records File nodes plus any nested cloned
repos/docs as Repo nodes. `ingest_all_git` backfills commit history for the
project root and each detected repo as Change nodes of kind ``commit``.

Note: the walk does not parse ``.gitignore``. It prunes a fixed set of common
noise directories (``_SKIP_DIRS``) and all dotfile/dot-directories. This keeps
the scan dependency-free; honouring real ``.gitignore`` rules is future work.
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from pathlib import Path

from .capture import lang_for, upsert_file
from .db import Graph
from .schema import new_id, now

# Directories we never descend into when scanning project files.
_SKIP_DIRS = {
    ".git", ".pgraph", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".idea", ".vscode",
    "target", ".next", ".cache",
}
_DOC_DIRS = {"docs", "doc", "documentation"}


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def scan_project(
    g: Graph,
    root: str | Path,
    max_files: int = 5000,
    exclude: list[str] | None = None,
) -> dict:
    """Record File nodes for tracked-looking files and Repo nodes for nested clones.

    *exclude* is a list of directory names or project-relative path prefixes that
    are pruned from the walk (e.g. a nested repo you don't want tracked).
    """
    root = Path(root).resolve()
    exclude = exclude or []
    files = 0
    repos = 0
    seen_repo_roots: set[Path] = set()

    for dirpath, dirnames, filenames in _walk(root, exclude):
        d = Path(dirpath)
        # Detect a nested cloned repo (not the project root itself).
        if d != root and _is_git_repo(d) and d not in seen_repo_roots:
            kind = "doc" if d.name.lower() in _DOC_DIRS else "repo"
            _upsert_repo(g, d, kind)
            seen_repo_roots.add(d)
            repos += 1
        for name in filenames:
            if files >= max_files:
                break
            fpath = d / name
            rel = str(fpath.relative_to(root))
            upsert_file(g, rel)
            # Link file to the nearest enclosing detected repo, if any.
            repo_root = _enclosing_repo(d, seen_repo_roots)
            if repo_root is not None:
                _link_file_repo(g, rel, repo_root)
            files += 1

    return {"files": files, "repos": repos, "root": str(root)}


def _walk(root: Path, exclude: list[str] | None = None):
    """os.walk-like generator that prunes ``_SKIP_DIRS``, hidden dirs and *exclude*.

    *exclude* entries match either a bare directory name (anywhere in the tree)
    or a project-relative path prefix. This is a heuristic, not a ``.gitignore``
    parser — see the module docstring.
    """
    import os

    exclude = exclude or []
    for dirpath, dirnames, filenames in os.walk(root):
        kept = []
        for dn in dirnames:
            if dn in _SKIP_DIRS or dn.startswith("."):
                continue
            rel = str((Path(dirpath) / dn).relative_to(root))
            if dn in exclude or rel in exclude or any(rel.startswith(e + "/") or rel == e for e in exclude):
                continue
            kept.append(dn)
        dirnames[:] = kept
        yield dirpath, dirnames, filenames


def _enclosing_repo(d: Path, repo_roots: set[Path]) -> Path | None:
    for r in repo_roots:
        try:
            d.relative_to(r)
            return r
        except ValueError:
            continue
    return None


def _repo_by_path(g: Graph, path: Path) -> dict | None:
    matches = g.match_nodes("Repo", where=[("path", "=", str(path))], limit=1)
    return matches[0] if matches else None


def _upsert_repo(g: Graph, path: Path, kind: str) -> str:
    existing = _repo_by_path(g, path)
    if existing:
        return existing["id"]
    rid = new_id()
    url = _git_remote(path)
    g.add_node(
        "Repo",
        rid,
        {"id": rid, "name": path.name, "url": url, "path": str(path), "kind": kind},
    )
    return rid


def _link_file_repo(g: Graph, rel_path: str, repo_root: Path) -> None:
    repo = _repo_by_path(g, repo_root)
    if not repo:
        return
    g.add_edge("IN_REPO", "File", rel_path, "Repo", repo["id"])


# -- git -------------------------------------------------------------------
def _git_remote(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def ingest_all_git(g: Graph, root: str | Path, limit: int = 200) -> dict:
    """Ingest commit history for the project root and every detected Repo node."""
    root = Path(root).resolve()
    repos = [root] if _is_git_repo(root) else []
    for r in g.match_nodes("Repo"):
        p = Path(r["path"])
        if p not in repos:
            repos.append(p)
    total = 0
    for repo in repos:
        total += _ingest_git_repo(g, repo, limit)
    return {"repos_ingested": len(repos), "commits": total}


def _ingest_git_repo(g: Graph, repo: Path, limit: int) -> int:
    if not _is_git_repo(repo):
        return 0
    fmt = "%H%x1f%aI%x1f%s"  # hash, author date ISO, subject — unit-separated
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", f"-{limit}", f"--pretty=format:{fmt}"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if out.returncode != 0:
        return 0
    count = 0
    for line in out.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        sha, iso, subject = parts
        # Idempotent: a commit's Change id is derived from its sha + repo.
        cid = f"commit:{repo.name}:{sha}"
        if g.get_node("Change", cid) is not None:
            continue
        ts = _parse_iso(iso)
        g.add_node(
            "Change",
            cid,
            {"id": cid, "kind": "commit", "path": str(repo.name),
             "summary": subject, "diff_stat": sha[:12], "ts": ts},
        )
        count += 1
    return count


def _parse_iso(iso: str) -> _dt.datetime:
    try:
        dt = _dt.datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return dt.replace(microsecond=0)
    except ValueError:
        return now()
