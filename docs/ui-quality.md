# UI quality rules

Ten rules for every screen and every user-facing sentence in this repository. The owner stated rules 1 and 2 and approved rules 3 to 10 on 2026-11-22; all ten are binding. The goal is an interface that does not carry the usual marks of generated design.

1. No thick accent border on one side of a card, callout, alert, list item or quote (for example `border-left: 4px solid <accent>`). Use spacing, type weight, an icon or a full hairline border instead.
2. No soft tinted background with a thin border of the same hue as the default way to mark a status, tag, callout or card. One neutral surface and one clear signal (text, icon or a solid chip) is enough.
3. No gradient text and no purple-to-blue gradient as a default accent.
4. No blur or glass panels, and no drop shadow plus large radius on every card.
5. No emoji used as icons or bullets, and no sparkle marks.
6. No pill or badge on things that are not states. No all-caps, letter-spaced micro labels as section decoration.
7. No centred hero with a slogan in an application screen. Screens open on the user's task.
8. One accent colour, used for the primary action and the current selection. Status colours only for status.
9. Dense, aligned layouts on a consistent spacing scale. No card grid where a table or a list is the right form.
10. User-facing copy is plain and specific: no filler, no exclamation marks, and the same word for the same thing everywhere.

## How the rules are applied

- A UI ticket starts with the `ui-ux-pro-max` skill (`.claude/skills/ui-ux-pro-max/`): run a design-system search for the product type before choosing layout, colour and type, then keep to these rules where the skill's output differs.
- A UI ticket ends with a check of every changed screen against this file. Record the result in the pull request.
- User-facing copy, documentation and pull request text go through the `unslop` skill (`.claude/skills/unslop/`) before the pull request is opened.
- Enforcement is by the review gate: Code Reviewer checks every ticket diff against these rules, and Frontend Developer and Accessibility Auditor name this file in their checklists. There is no script. Rule 1 can be found with `grep -n "border-left\|border-right" workbench/static/styles.css`; rule 2 cannot be detected reliably from CSS.
- Existing breaches are collected in one ticket in epic [#18](https://github.com/jortega0033/dutch-freelance-workbench/issues/18); do not mix their repair into an unrelated ticket.
- The stack still applies: native HTML, one app stylesheet plus the shared `tokens.css`, no remote asset, no framework (`CLAUDE.md`, "Architecture boundaries").
- Colour values come from the shared tokens in [design-tokens.md](design-tokens.md). The rule 8 accent is `--primary`, used only on the primary action and the current-step marker; the current selection uses text weight, underline or `--state-selected`, not yellow.
