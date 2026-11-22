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

1. **Refresh the queue.** `gh issue list --state open --limit 100 --json number,title,labels` and read the epic's dependency order. A `status:blocked` ticket whose blockers are all closed becomes `status:ready` (`gh issue edit <n> --add-label status:ready --remove-label status:blocked`). A parent whose sub-issues are all closed is closed with a comment. Sub-issues: `gh api repos/jortega0033/dutch-freelance-workbench/issues/<n>/sub_issues`.
2. **Pick the ticket.** Only leaf tickets are work: a ticket labelled `tracking` or with open sub-issues is a container and is never picked. With an issue number, use it, but stop and say so if it is `status:in-progress`, `needs-decision` or has an open blocker; a `tracking` number or a parent with open sub-issues is refused, listing its open sub-issues. Without one, take the first `status:ready` leaf ticket (order: the parents in the epic's table, then the sub-issues of each parent in order) that is neither `status:in-progress` nor `needs-decision`. If none qualifies, report the queue and stop.
3. **Claim it.** Add the label `status:in-progress` and remove `status:ready`.
4. **Branch in a fresh worktree** from an up-to-date `main`, named `feature/<issue-number>-<short-slug>` as `CLAUDE.md` says: `git fetch origin`, then `git worktree add ../dutch-freelance-workbench-<issue-number> -b feature/<issue-number>-<short-slug> origin/main`. Then move the session into that worktree with the `EnterWorktree` tool (pass the existing worktree path), so the session's working directory, the permission rules, the review gate hook and its ledger all see the ticket worktree. A `cd` inside a Bash command is not enough: the hook reads the session's working directory, and in the original checkout it would see a clean `main` and let `gh pr create` through unchecked. Work only there; if the session cannot be moved into the worktree, stop and say so.
5. **Run the `implement-ticket` playbook** ([.claude/skills/implement-ticket/SKILL.md](../implement-ticket/SKILL.md)) from step 1 to its hand-off, including both gates, CI and the pull request template. If the ticket is the last open sub-issue of a parent, its pull request says `Closes #<sub-issue>` and `Closes #<parent>`.
6. **Finish.** The order is fixed: local checks, Code Reviewer, Reality Checker, then the draft pull request, then CI. Code Reviewer and Reality Checker have no shell: before calling them, write the diff and the local evidence to `.claude/state/review/` in the ticket worktree (`git diff --stat origin/main...HEAD`, `git diff origin/main...HEAD`, the check outputs) and name those files in the request. Never open the pull request with a gate not run. After `gh pr create --draft` and `git push`, bind the pull request to the session when the app offers it and end the turn with the line "Draft pull request #N is waiting for CI". Do not loop, sleep or watch. When the session is woken (a CI event, or the owner says "continue"), read `gh pr checks <n>` once: green, mark it ready (`gh pr ready`) and comment on the issue with the pull request link, the CI run link and the evidence summary; red, fix, re-run the local checks and both gates, push and end the turn again; still running, end the turn again with the same line. You check the CI result, not Reality Checker. Run `gh pr create`, `gh pr ready` and every subagent from the session inside the ticket worktree. The review gate hook blocks `gh pr create` and `gh pr ready` until both gates have run after the last code edit; do not switch it off.
7. **Stop.** Leave the label `status:in-progress` on the issue until the owner merges. Do not merge, do not close the issue, do not pick another ticket.

If a decision named in the ticket is open, take its stated default; where there is none, take the conservative option and say so in an issue comment. If a decision in `docs/owner-decisions.md` blocks the ticket, stop and say so.
