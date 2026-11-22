# Owner decisions

Decisions the owner made for this repository, all on 2026-11-22. Each entry names the file that implements it. Changing an entry needs the owner; an agent that finds a decision blocking a ticket stops and says so.

| # | Decision | Date | Implemented in |
| --- | --- | --- | --- |
| 1 | The instructions, tooling and playbook live on the default branch (`main`). | 2026-11-22 | `CLAUDE.md`, `.claude/`, `docs/` |
| 2 | Persona agents run automatically during ticket work and carry instructions tailored to this project. | 2026-11-22 | `.claude/agents/`, `.claude/skills/implement-ticket/SKILL.md`, `docs/agent-roles.md` |
| 3 | Every open ticket is about app implementation; tooling is installed, not ticketed. | 2026-11-22 | `CLAUDE.md` ("Delivery workflow"), `.github/ISSUE_TEMPLATE/implementation-ticket.md` |
| 4 | Agents may take a ticket up to a ready pull request. Only the owner merges and releases. | 2026-11-22 | `CLAUDE.md`, `.claude/skills/next-ticket/SKILL.md`, `.claude/settings.json` (deny rules), `docs/RELEASING.md` |
| 5 | No scheduler. Ticket work starts only on the owner's go signal (`/next-ticket`), because the repository is used to train AI models. | 2026-11-22 | `.claude/skills/next-ticket/SKILL.md` (`disable-model-invocation: true`), `LOOP.md` |
| 6 | A review gate hook blocks opening or readying a pull request until the gate roles have run after the last code edit. | 2026-11-22 | `.claude/hooks/role_gate.py`, `.claude/settings.json` |
| 7 | Loop Engineering stays at L1, report-only and unscheduled. | 2026-11-22 | `LOOP.md`, `loop-constraints.md`, `loop-budget.md` |
| 8 | Continuous integration runs on Linux and Windows; the default branch requires a pull request and green checks. | 2026-11-22 | `.github/workflows/ci.yml` (branch protection is set by the orchestrator after the merge) |
| 9 | Permission rules: documented commands are allowed; force pushes, pushes to the default branch, merges, releases and reads of secrets are denied; edits to CI, agent configuration and Loop files ask first. | 2026-11-22 | `.claude/settings.json` |
| 10 | Open decisions on tickets are settled with the ticket's stated default; where a ticket states none, the conservative option is taken and flagged in the issue. | 2026-11-22 | Settled so far: see the list below |
| 11 | Rust is verified in CI only. Not applicable: this repository has no Rust. | 2026-11-22 | None |
| 12 | The ten UI rules in `docs/ui-quality.md` are binding; the `ui-ux-pro-max` and `unslop` skills are installed and used for UI work and for copy. | 2026-11-22 | `docs/ui-quality.md`, `.claude/skills/ui-ux-pro-max/`, `.claude/skills/unslop/`, `.claude/skills/implement-ticket/SKILL.md` |

## Entry 10: open decisions settled by a ticket default

Each line: issue, decision, value taken. "Flag in issue" means the ticket states no default; the conservative option is taken and the agent says so in the issue.

- #2, payment term in the revision form: 14 calendar days (the ticket's "proposed UI default 14").
- #2, how the stage and follow-up rules move past the table `CHECK` constraints: rebuild the `lead` table in the single upgrade transaction and relax both constraints so #8 needs no second rebuild. Flag in issue.
- #2, how a second path id reaches `Handler._pipeline`: none needed, the revision number is allocated on the server. Flag in issue.
- #5, boundary of validation with #17: #5 ships the validation step, one registration point and the structural and non-financial checks; #17 adds the project and billing validators through the same registration point (the ticket's proposed reading).
- #17, same boundary as #5.
- #8, new stage identifiers: `conditional_interest`, `won`, `closed` (the ticket's proposal).
- #18, W4 restore boundary: restore into a fresh directory only (the epic's proposal).
- #19, how milestone and deliverable ids reach a write command: nested paths under `/api/v1/projects/<id>/...`, each id validated to belong to the project. Flag in issue.
- #13, timer segments feeding billable minutes: not started (outside the W1-W4 gates); when taken up, segments only propose rows saved once into the existing minutes. Flag in issue.
- #15, shared annual numbering for credits: no, keep internal references. Flag in issue.
