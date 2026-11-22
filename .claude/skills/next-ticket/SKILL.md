---
name: next-ticket
description: Owner-started driver that takes exactly one ready ticket of epic #18 up to a ready pull request. Never merges.
argument-hint: "[issue number]"
disable-model-invocation: true
---

# Next ticket

Only the owner starts this skill (`/next-ticket` or `/next-ticket <issue number>`). Starting it is the go signal: for this run it counts as the owner's request to push the ticket branch and open the pull request. It runs one ticket, never merges, never enables auto-merge and never starts another run by itself.

Epic: [#18](https://github.com/jortega0033/dutch-freelance-workbench/issues/18). Labels: `status:ready`, `status:blocked`, `status:in-progress`, `needs-decision`.

## Steps

1. **Refresh the queue.** `gh issue list --state open --limit 100 --json number,title,labels` and read the epic's dependency order. A `status:blocked` ticket whose blockers are all closed becomes `status:ready` (`gh issue edit <n> --add-label status:ready --remove-label status:blocked`).
2. **Pick the ticket.** With an issue number, use it, but stop and say so if it is `status:in-progress`, `needs-decision` or has an open blocker. Without one, take the first `status:ready` ticket in the epic's dependency order that is neither `status:in-progress` nor `needs-decision`. If none qualifies, report the queue and stop.
3. **Claim it.** Add the label `status:in-progress` and remove `status:ready`.
4. **Branch in a fresh worktree** from an up-to-date `main`, named `feature/<issue-number>-<short-slug>` as `CLAUDE.md` says: `git fetch origin`, then `git worktree add ../dutch-freelance-workbench-<issue-number> -b feature/<issue-number>-<short-slug> origin/main`. Then move the session into that worktree with the `EnterWorktree` tool (pass the existing worktree path), so the session's working directory, the permission rules, the review gate hook and its ledger all see the ticket worktree. A `cd` inside a Bash command is not enough: the hook reads the session's working directory, and in the original checkout it would see a clean `main` and let `gh pr create` through unchecked. Work only there; if the session cannot be moved into the worktree, stop and say so.
5. **Run the `implement-ticket` playbook** ([.claude/skills/implement-ticket/SKILL.md](../implement-ticket/SKILL.md)) from step 1 to its hand-off, including both gates, CI and the pull request template.
6. **Finish.** When Code Reviewer and Reality Checker have passed on the current diff and CI is green on the pushed commit, mark the pull request ready (`gh pr ready`) and comment on the issue with the pull request link and the evidence summary. Run `gh pr create`, `gh pr ready` and every subagent from the session inside the ticket worktree. The review gate hook blocks `gh pr create` and `gh pr ready` until both gates have run after the last code edit; do not switch it off.
7. **Stop.** Leave the label `status:in-progress` on the issue until the owner merges. Do not merge, do not close the issue, do not pick another ticket.

If a decision named in the ticket is open, take its stated default; where there is none, take the conservative option and say so in an issue comment. If a decision in `docs/owner-decisions.md` blocks the ticket, stop and say so.
