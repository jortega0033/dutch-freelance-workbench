# Loop Budget — Dutch Freelance Workbench

> Primary loop: **Daily Triage** (scaffolded by loop-init)

## Daily limits

| Loop | Max runs/day | Max tokens/day | Max sub-agent spawns/run |
|------|--------------|----------------|--------------------------|
| Daily Triage | 1 (manual) | 100k | 0 (L1, current level) |

## On budget exceed

1. Stop the manual run (no scheduler is configured; do not add one)
2. Append event to `loop-run-log.md`
3. Notify the human with one line under High Priority in `STATE.md` (no Slack message, no GitHub issue or comment)

## Kill switch

- Command or issue label: `loop-pause-all`
- Resume only after human clears the flag in STATE.md

## Estimate spend

```bash
npx --yes @cobusgreyling/loop@0.2.0 cost --pattern daily-triage --level L1
```
