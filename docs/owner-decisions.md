# Owner decisions

Decisions the owner made for this repository, on the date in the Date column. Each entry names the file that implements it. Entry 14 also holds items recommended by the orchestrator and adopted under the owner's delegation of 2026-11-23; the list below gives the source of each. Changing an entry needs the owner; an agent that finds a decision blocking a ticket stops and says so.

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
| 13 | Visual system: Pipenzo Flow look tokens (docs/design-tokens.md). SDLC kit version: jortega0033/sdlc-kit v1.0.0. | 2026-11-23 | `workbench/static/tokens.css`, `workbench/static/styles.css`, `docs/design-tokens.md` |
| 14 | Token rules, 2026-11-23: D1 to D4. Only the yellow rule of D1 was decided by the owner; the How of D1 and D2 to D4 were recommended by the orchestrator and adopted under delegation (sources in Entry 14). | 2026-11-23 | `docs/design-tokens.md`, `docs/ui-quality.md` |

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

## Entry 14: token rules

Source of each item is stated as given. Only D1's yellow rule was decided by the owner.

- D1 Current selection: the yellow accent is used for the primary action, the current-step marker and the current selection (UI rule 8). Decided by the owner on 2026-11-23. How (recommended by the orchestrator, adopted under the owner's delegation of the same day): a selection uses a solid `--primary` fill with `--on-primary` text (not only a yellow border, which has about 1.5:1 against white on light), plus a second sign that is not colour (weight, icon or text) and the existing aria attribute. Never a tint with a same-hue border and never a thick one-sided border.
- D2 Focus ring: `--focus` is a focus indicator, exempt from the accent limit, including where it is yellow in dark. A ticket that says the focus style stays unchanged means the rule on main after the token pull request. Recommended by the orchestrator, adopted under the owner's delegation of 2026-11-23.
- D3 Themes: contrast and no-colour-alone checks cover both the light and the dark block of `tokens.css`. Recommended by the orchestrator, adopted under the owner's delegation of 2026-11-23.
- D4 Linking: a ticket that creates a page or its static allowlist links `tokens.css` before any other stylesheet and serves it (allowlist and its test). Recommended by the orchestrator, adopted under the owner's delegation of 2026-11-23.
