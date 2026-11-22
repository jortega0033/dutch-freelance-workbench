# Loop Configuration: Daily Triage (Claude Code)

Scaffolded with `npx --yes @cobusgreyling/loop@0.2.0 init . --pattern daily-triage --tool claude`
on 2026-11-21 ([Loop Engineering](https://github.com/cobusgreyling/loop-engineering), MIT,
upstream `main` at `959fad23efa52456d3b478bd78eca1b1b4d9e88a`). The generated text was
then adapted to this repository; the changes are listed at the end of this file.

## Active loops

| Pattern | Operating level | Cadence | Scheduler | How it runs |
|---------|-----------------|---------|-----------|-------------|
| Daily Triage | **L1 report-only** | On demand | **Disabled** | A person starts one run by hand (command below) |

`loop doctor` reports a readiness score of "100/100 L2". That score describes the files
that exist, not permission to act. This repository operates at L1 until the owner changes
this table in a reviewed pull request.

## Ticket work is not a loop run

Implementing a ticket is not a loop run. The owner starts it with `/next-ticket`, and it is governed by `CLAUDE.md`, the `implement-ticket` playbook, the review gate hook, CI and branch protection. The loop stays report-only and unscheduled at L1.

## Manual triage command

From the repository root, in an interactive Claude Code session:

```
Run the loop-constraints skill, then the loop-budget skill, then the loop-triage skill once, in report-only mode. Inputs: `gh issue list --state open --limit 50`, `gh pr list --state open`, `git log --oneline -15 origin/main`, and the result of `python -m unittest discover -s tests -t .`. Update STATE.md and append one entry to loop-run-log.md. Do not edit any other file and do not write to GitHub.
```

Do not wrap it in `/loop`, a cron entry, a scheduled task or a GitHub workflow. Check the
setup at any time with:

```sh
npx --yes @cobusgreyling/loop@0.2.0 doctor .
npx --yes @cobusgreyling/loop@0.2.0 status .
```

## What a run may and may not do at L1

- May read the repository, read GitHub through `gh`, and run the documented test command.
- May write only `STATE.md` and `loop-run-log.md`.
- May not edit `workbench/`, `tests/`, documentation or configuration.
- May not create, edit, label, close or comment on issues or pull requests.
- May not push, merge, enable auto-merge or spawn sub-agents (budget: 0 per run).

The binding rules are in [loop-constraints.md](loop-constraints.md); the budget and kill
switch are in [loop-budget.md](loop-budget.md).

## Human gates

- Moving to L2 (proposing fixes) needs an owner decision recorded in this file, the
  upstream [loop design checklist](https://github.com/cobusgreyling/loop-engineering/blob/959fad23efa52456d3b478bd78eca1b1b4d9e88a/docs/loop-design-checklist.md)
  completed, and at least one reviewed L1 run in `loop-run-log.md`.
- At any level: no merge to `main` without the owner, and every change follows the Git
  workflow and evidence list in [CLAUDE.md](CLAUDE.md).

## Worktrees and connectors (L2 and later, not active)

- Use `isolation: worktree` for implementer sub-agents; one worktree per attempt.
- No MCP connector is needed at L1. `gh` read access is enough.

## Recorded checks

| Date | Command | Result |
|------|---------|--------|
| 2026-11-21 | `loop init . --pattern daily-triage --tool claude --dry-run` | Listed 9 files under `.claude/`, `STATE.md`, `LOOP.md`, budget, run log, constraints; no `.github` workflow |
| 2026-11-21 | `loop init . --pattern daily-triage --tool claude` | Exit 0; wrote those files plus `AGENTS.md`; no scheduled workflow was generated, so there was nothing to disable |
| 2026-11-21 | `loop doctor .` (fresh scaffold) | Exit 0, `HEALTHY`, `Ready: 100/100 L2`, `Files: state=STATE.md LOOP.md=yes budget=yes run-log=yes gate=no`, note `Could not parse loop-sync JSON output` |
| 2026-11-21 | `loop status .` (fresh scaffold) | Exit 0, `Pattern: daily-triage`, `State: STATE.md · never`, `Recent runs: (none in loop-run-log.md)` |

| 2026-11-22 | `loop doctor .` (after the local changes listed below) | Exit 0, `HEALTHY`, `Ready: 100/100 L2`, same `Files:` line |
| 2026-11-22 | `loop status .` (after the local changes) | Exit 0, `Pattern: daily-triage`, `State: STATE.md · never (L1 report-only; ...)`, `Budget: loop-budget.md · gate=no · constraints=yes`, no runs |
| 2026-11-21 | `loop cost --pattern daily-triage --level L1` | Printed an estimate (`Suggested daily cap: 100k tokens`); its figures assume a scheduled cadence that is not used here |

Doctor's suggested next actions (`docs/safety.md`, issue and PR templates, workflows,
`patterns/registry.yaml`) are optional for L1 and were not added: this repository has no
CI and keeps its safety rules in `loop-constraints.md` and `CLAUDE.md`.

## Why this pattern

The package offers a second low-risk L1 starter aimed at issue queues. In version 0.2.0
that starter has no Claude variant: its dry run says so and would also write `.grok/` and
`.codex/` folders. The daily triage starter scaffolds Claude files only, and its triage
skill already covers open issues, recent commits and test failures.

## Local changes to the generated scaffold

- `AGENTS.md`: replaced `npm test` and `npm run lint` with this repository's commands.
- `LOOP.md` (this file): replaced the scheduled `/loop 1d` command with a manual one,
  replaced broken relative links to upstream files, added the recorded checks.
- `STATE.md`, `loop-budget.md`, `loop-run-log.md`: project name filled in; budget set to
  one manual run per day and zero sub-agents.
- `loop-constraints.md`: project rules added under the marker at the end.
- `.claude/agents/loop-verifier.md`: `tools: Read, Grep, Glob, Bash` added so the checker
  can run tests and has no editing tools (`Bash` can still write files, so "never
  implement fixes" remains an instruction, not a technical limit).
- `.claude/skills/loop-*/SKILL.md`: unchanged. Where their generic text mentions Linear,
  Slack, issues, `loop-budget.md` alerts or sub-agents, the project rules in
  `loop-constraints.md` take precedence.
