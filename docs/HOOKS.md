# Automatic capture via Claude Code hooks

Manual logging is precise but easy to forget. The hooks make capture
automatic: every file write becomes a `Change` node, and every session end
writes a fresh portable snapshot — with zero effort once installed.

## Installing

From inside a project:

```bash
pgraph install-hooks --pgraph-bin "$(command -v pgraph)"
```

This merges two entries into `<project>/.claude/settings.json`
([`install_hooks.py`](../pgraph/install_hooks.py)):

- a **PostToolUse** hook matching `Write|Edit|NotebookEdit`, running
  `pgraph hook-post-tool-use`;
- a **Stop** hook running `pgraph hook-stop`.

The merge is **idempotent** and **non-destructive**: it reads the existing
settings, checks whether each command is already present (by matcher +
command string), and only appends what's missing. Your other hooks are never
touched.

> Pass `--pgraph-bin "$(command -v pgraph)"` so the hook calls the exact
> executable from the env where you installed `pgraph` (e.g. your conda env),
> rather than relying on `pgraph` being on Claude Code's PATH.

## What each hook does

### PostToolUse → `hook-post-tool-use`
([`hook.post_tool_use`](../pgraph/hook.py))

1. Reads the hook payload from stdin.
2. Maps the tool to a change kind: `Write → create`, `Edit`/`NotebookEdit → edit`. Other tools are ignored.
3. Extracts the file path and rewrites it relative to the project root (so it
   matches `scan` and manual logging).
4. Finds the latest open session — or **auto-opens one** if none exists, so an
   edit is never dropped.
5. Records a `Change` with summary `(auto) file write`.

### Stop → `hook-stop`
([`hook.stop`](../pgraph/hook.py))

1. Ends the latest open session (stamps `ended_at`).
2. Writes a fresh JSONL export under `.pgraph/export/` — so a portable,
   git-diffable snapshot exists at the end of every session.

## Safety: hooks never break your work

Both handlers wrap their entire body in a catch-all and always exit `0`. If the
database is locked, missing, or anything else goes wrong, the hook silently
does nothing. A project-memory hiccup can **never** block or fail the host
agent's actual task. This is a deliberate, load-bearing design choice.

## Resulting settings shape

After install, `.claude/settings.json` contains (merged with whatever was
already there):

```jsonc
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|NotebookEdit",
        "hooks": [{ "type": "command", "command": "/path/to/pgraph hook-post-tool-use" }]
      }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "/path/to/pgraph hook-stop" }] }
    ]
  }
}
```

## Verifying

Make an edit through Claude Code, then:

```bash
pgraph history --limit 5
```

You should see a `(auto) file write` change for the file you just edited. If
you don't, confirm the hook is registered by opening `/hooks` in Claude Code,
and that `--pgraph-bin` pointed at a `pgraph` that actually exists.

## Manual + automatic together

The hooks capture *what* changed. They can't capture *why* — that's still a
human/agent judgement call. Pair the automatic `Change` capture with manual
`pgraph decide` / `log_decision` notes (and a meaningful `session end
--summary`) to record the intent a diff can never show.
