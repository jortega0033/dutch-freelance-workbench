# AGENTS.md

The full instructions for coding agents are in [CLAUDE.md](CLAUDE.md): setup, commands,
architecture boundaries, Git workflow and the evidence required before a ticket is done.
This file keeps the short version for tools that read `AGENTS.md`.

## Test commands

```sh
python -m unittest discover -s tests -t .
```

Python 3.12 or newer, standard library only. There is no `npm test`, linter or build
step. The optional browser acceptance run is described in [CLAUDE.md](CLAUDE.md).

## Launch

```sh
python -m workbench --data-dir data/main
```

## Loop conventions

- The loop runs at L1, report-only. No scheduler, no automated source fix, no auto-merge.
- See [LOOP.md](LOOP.md) for the manual triage command and the human gates, and
  [loop-constraints.md](loop-constraints.md) for the binding rules.
