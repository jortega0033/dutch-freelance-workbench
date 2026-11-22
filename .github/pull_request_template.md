## Ticket

Closes #

## What changed

<!-- What changed and why, in plain sentences. Only what the issue asks for. -->

## Evidence

<!-- Each command you ran and its result (last lines). Commit tested and Python version. See CLAUDE.md, "Evidence before calling a ticket done". -->

| Command | Result |
| --- | --- |
|  |  |

Not run or not verified:

## Agent roles run

<!-- Role, what it was asked, verdict. Also list any step done without a role. -->

| Role | Asked | Verdict |
| --- | --- | --- |
|  |  |  |

## Risks and rollback

<!-- What could go wrong, and how to undo it (schema upgrades cannot be undone: say so). -->

## Checklist

- [ ] One issue, one branch; diff limited to the issue's scope
- [ ] Full test suite green locally and CI green on this commit
- [ ] New tests fail without the change
- [ ] README and `docs/architecture.md` updated where behaviour or an invariant changed
- [ ] UI changes checked against `docs/ui-quality.md`; copy, docs and this text passed through `unslop`
- [ ] Line added under `Unreleased` in `CHANGELOG.md`
- [ ] Code Reviewer and Reality Checker ran on the current diff
- [ ] Fictional data only
