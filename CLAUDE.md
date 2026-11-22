# Dutch Freelance Workbench: instructions for coding agents

A local-only lead tracker for one freelancer: a Python standard-library HTTP server, one
SQLite file and a browser client made of native JavaScript modules. No dependencies, no
build step. Read this file first, then the documents it links to. Do not copy
their content here.

## Source of truth

| Question | Where to look |
| --- | --- |
| What the product does today, how to launch it, the sample workflow | [README.md](README.md) |
| How the code is layered, invariants, one traced workflow, plan-versus-code drift | [docs/architecture.md](docs/architecture.md) |
| What to build next, in which order, with estimates | Epic [#18](https://github.com/jortega0033/dutch-freelance-workbench/issues/18) and its child issues |
| How a ticket is implemented, step by step, and which agent role runs when | [.claude/skills/implement-ticket/SKILL.md](.claude/skills/implement-ticket/SKILL.md) |
| Project agent roles, their pin, licence and local changes | [docs/agent-roles.md](docs/agent-roles.md) |
| The report-only triage loop | [LOOP.md](LOOP.md), [loop-constraints.md](loop-constraints.md) |
| Delivery workflow, UI rules, recorded owner decisions | [Delivery workflow](#delivery-workflow), [docs/ui-quality.md](docs/ui-quality.md), [docs/owner-decisions.md](docs/owner-decisions.md) |

There is no PRD file. The README is the requirement for shipped behaviour and the GitHub
issues are the requirement for planned behaviour. If they disagree with the code, the
code describes what exists; record the difference in `docs/architecture.md` under
"Requirements baseline and drift" and fix whichever side is wrong in its own change.

## Setup and commands

Python 3.12 or newer is the only requirement (`workbench/__main__.py` refuses older
versions). Nothing is installed. Run every command from the repository root.

```sh
# launch (the server prints its URL and database path; stop with Ctrl+C)
python -m workbench --data-dir data/main
python -m workbench --data-dir data/demo --port 8001   # separate demo workspace

# full automated suite (standard library only, about 45 seconds)
python -m unittest discover -s tests -t .

# one file, one class or one test
python -m unittest tests.test_catalog
python -m unittest tests.test_workflow.T03FirstOutreach -v

# health check of a running server
curl http://127.0.0.1:8000/api/v1/health
```

Use `python3` on Linux and macOS. On Windows use a `python` that is 3.12 or newer.

Baseline recorded on 2026-11-21 at commit `223da9a` (Python 3.13.14, Windows 11):
`Ran 204 tests ... OK (skipped=1)`. The skip is the zoneinfo cross-check in
`tests/test_dates.py`, which needs a tz database; Windows has none unless the `tzdata`
package is installed, and the app then uses its built-in EU daylight-saving rule.

Optional browser acceptance run (needs Node.js 18+, a globally installed `playwright`
package and Google Chrome; it uses ports 18781 to 18783 and temporary folders):

```sh
NODE_PATH="$(npm root -g)" WORKBENCH_PYTHON=python node tests/browser/acceptance.mjs
```

There is no linter, formatter or type checker. Do not invent one inside a feature
change. The one CI workflow is `.github/workflows/ci.yml` (see "Delivery workflow"). Match the surrounding style: small functions, docstrings that state the
rule, no third-party imports.

## Architecture boundaries

Details and the reasons are in [docs/architecture.md](docs/architecture.md). The rules
that a change must not break:

- **Dependency direction.** `__main__` -> `server` -> command modules (`leads`) ->
  `discovery`, `queries`, `validation`, `db` -> `dates`, `errors`. `db.py` and
  `queries.py` never import a command module.
- **One save is one transaction.** Every write goes through `leads.run_mutation` and
  `db.write_transaction`: idempotency lookup, command, audit row, stored response, commit.
  A failed save leaves no row behind. Do not open a second transaction or write outside it.
- **Every write is versioned, idempotent and audited.** A command that changes an
  existing record checks the expected version and bumps it once; a create starts at
  version 1. Each state change writes one audit row with ids, versions and stage names
  only (never labels, free text, keys or bodies). A save that changes nothing, such as a
  repeated catalog save, writes no audit row.
- **Schema changes are upgrades.** Add `db.UPGRADES[n]` and bump `db.SCHEMA_VERSION`;
  never edit `db.SCHEMA_STATEMENTS` (it is the v1 schema and the migration tests build
  their fixture from it). An upgrade runs in one transaction, keeps every existing row
  and id, and must be covered by a test that upgrades a database from the previous version.
- **Dates.** Business dates are Amsterdam dates from `dates.business_date`; derived
  values such as dashboard buckets are computed at read time and never stored.
- **Local only.** Bind to `127.0.0.1`, keep the Host, Origin and session-token checks and
  the Content Security Policy, add no CORS headers, make no external request, load no
  remote asset. Render user text as text (`static/js/dom.js`), never as HTML.
- **Test seams stay out of the command line.** The clock and the fault hook are
  constructor arguments of `server.create_server`; `python -m workbench` always uses the
  system clock and `NoFaults`. Test-only code lives in `tests/support/`.
- **Standard library only.** No new runtime dependency, package manifest or build step
  without an explicit decision recorded in the epic.
- **Fictional data only** in fixtures, demos, screenshots and issue text.

## Git workflow

- `main` is the default branch and is not protected by a GitHub rule; treat it as
  protected anyway. Never commit to `main` directly and never force-push it.
- One issue per branch, named `feature/<issue-number>-<short-slug>` (the convention used
  by `feature/1-discover-catalog`). Preparation or documentation work uses its own branch.
- Open a pull request against `main`, link the issue (`Closes #n`), and keep the diff to
  what the issue asks for. The owner reviews and merges; agents never merge and never
  enable auto-merge.
- Commit messages are one imperative sentence in sentence case, as in the existing
  history (`Add Discover screen backed by a bundled fictional catalog`).
- Do not commit databases, logs, browser profiles or anything under `data/`
  (`.gitignore` covers them and `tests/test_regressions.py` checks that it does).

## Evidence before calling a ticket done

Put this in the pull request description. Report only what was actually run.

1. The exact commit tested and the Python version.
2. The full-suite command and its last lines (test count, failures, skips). The count
   must not drop below the baseline without an explanation.
3. New tests that fail without the change, covering each acceptance criterion of the
   issue: the main path, an exact retry after a lost response, a stale version from a
   second tab, and an injected failure before commit that leaves no partial row
   (`ScriptedFaults.arm("before_commit")`).
4. For a schema change: a test that upgrades a database created by the previous schema
   version and shows ids, rows and stored retry outcomes unchanged, plus a restart check.
5. For a user-visible change: the browser acceptance run (or a stated reason it was not
   run), with keyboard-only operation of the new screens and the "no external requests"
   check passing.
6. README and `docs/architecture.md` updated in the same pull request when behaviour,
   a boundary or an invariant changed.
7. CI green on the pushed commit (both operating systems, both Python versions).
8. Anything not run or not verified, stated plainly.

## Project agents and the loop

### Agent roles

- The project subagents are the files in `.claude/agents/`: eight pinned copies from
  Agency Agents and `loop-verifier`. They shadow same-named user-level agents inside this
  repository. Pin, licence, local changes and the load check are in
  [docs/agent-roles.md](docs/agent-roles.md).
- Each Agency Agents role file starts with an "In this repository" section tailored to
  this project: the files the role owns or reviews, its commands, the invariants it
  checks, what it must hand back, and which parts of the generic upstream text do not
  apply. That section and this file override the generic text below it.
- Reviewer roles are read-only (`Read`, `Grep`, `Glob`) and cannot run commands. Give
  them the diff, the acceptance criteria and the recorded command output.

### Ticket work

When asked to implement, continue or finish a GitHub issue, follow the playbook
[`.claude/skills/implement-ticket/SKILL.md`](.claude/skills/implement-ticket/SKILL.md).
Claude Code loads it on its own for ticket work; `/implement-ticket <issue number>`
starts it by hand. Delegate to the roles without waiting to be asked; each role's
`description` ends with a "Use proactively" sentence so that Claude Code does.

| When | Roles |
| --- | --- |
| Before coding | Codebase Onboarding Engineer (unfamiliar area); Software Architect (a module boundary, data model, storage schema, public API or dependency changes) |
| While coding | The implementing role that matches the files: Backend Architect, Database Reliability Engineer or Frontend Developer |
| After coding | Accessibility Auditor, when UI, keyboard handling, focus order or markup changed |
| Gates on every ticket | Code Reviewer on the full diff, then Reality Checker on the acceptance criteria and the evidence listed above |

- The two gates run on every ticket, however small the change. The other roles run when
  their trigger matches.
- The session or role that wrote a change does not count as its reviewer.
- In the pull request description, under "Agent roles run", list each role that ran,
  what it was asked and its verdict, and any step done without a role.

### Loop

The Loop Engineering setup is level L1, report-only: it may read the repository and
GitHub and update `STATE.md` and `loop-run-log.md`. It does not edit source, write to
GitHub, schedule itself or merge. See [LOOP.md](LOOP.md).

## Delivery workflow

How a ticket gets from the queue to a ready pull request. The pieces are described where they live; this part only links them.

- **Driver and playbook.** The owner starts a run with `/next-ticket` ([.claude/skills/next-ticket/SKILL.md](.claude/skills/next-ticket/SKILL.md)); it follows the playbook [.claude/skills/implement-ticket/SKILL.md](.claude/skills/implement-ticket/SKILL.md). Where a rule says not to push or open a pull request unless the owner asked in the current session, a `/next-ticket` run counts as that request. No scheduler starts ticket work.
- **Review gate hook.** [.claude/hooks/role_gate.py](.claude/hooks/role_gate.py) blocks `gh pr create` and `gh pr ready` until Code Reviewer and Reality Checker ran after the last code edit. `ROLE_GATE=off` in the environment Claude Code was started from switches it off for that session; only the owner does that.
- **Permissions.** [.claude/settings.json](.claude/settings.json): documented commands are allowed, merges, force pushes, pushes to `main`, releases and reads of secrets are denied, edits to CI, agent configuration and Loop files ask first.
- **CI.** [.github/workflows/ci.yml](.github/workflows/ci.yml) runs the full suite and the gate self-test on Linux and Windows with Python 3.12 and 3.13. A pull request is marked ready only when it is green.
- **Pull request.** Use [.github/pull_request_template.md](.github/pull_request_template.md). Add one line under `Unreleased` in [CHANGELOG.md](CHANGELOG.md).
- **Labels.** `status:ready`, `status:blocked`, `status:in-progress`, `needs-decision` on the epic's children.
- **UI and copy.** The ten rules in [docs/ui-quality.md](docs/ui-quality.md) are binding; the `ui-ux-pro-max` and `unslop` skills are in `.claude/skills/`.
- **Decisions.** [docs/owner-decisions.md](docs/owner-decisions.md) records the owner's decisions; an agent that finds one blocking a ticket stops and says so.
- **Merging and releasing.** Only the owner merges and releases ([docs/RELEASING.md](docs/RELEASING.md)). Agents never merge, never enable auto-merge and never push to `main`.
