# Branching Models

Code Cannon supports three branching models. Set `BRANCH_DEV` and `BRANCH_TEST` in `.codecannon.yaml` to match your workflow.

## Trunk-based development

**Config:** Leave `BRANCH_DEV` and `BRANCH_TEST` empty (or unset).

```
feature/<name>  →  main
    /start           /submit-for-review merges here
                     /deploy runs here
```

The simplest model. Feature branches are created from and merged directly into `BRANCH_PROD` (default: `main`). `/submit-for-review` opens a PR targeting `main` with `Closes #N` — issues auto-close on merge.

**When to use:** Solo developers, small teams, projects where every merge is production-ready. Fast iteration with low ceremony.

**Skill behavior in trunk mode:**
- `/submit-for-review` targets `BRANCH_PROD` directly
- `/deploy` runs from `BRANCH_PROD` — bumps version and creates a GitHub Release (no promotion PR needed)
- QA labels are not applied automatically

## Two-branch development

**Config:** Set `BRANCH_DEV` to your integration branch (e.g. `dev`, `development`, `staging`).

```
feature/<name>  →  BRANCH_DEV  →  BRANCH_PROD
    /start           /submit-for-review           /deploy
```

Feature PRs target `BRANCH_DEV`. Issues deliberately stay open through the feature merge — `Closes #N` is not used on feature PRs because they don't land in the default branch. Issues only auto-close when `/deploy` promotes `BRANCH_DEV` to `BRANCH_PROD`.

This supports a QA gate between merging code and shipping to production. After `/submit-for-review` merges a feature, you deploy the integration branch to a preview environment, test it, then run `/deploy` when satisfied.

**When to use:** Teams that want a review/QA gate before production. The most common model for teams with a staging or preview environment.

**Skill behavior in two-branch mode:**
- `/submit-for-review` targets `BRANCH_DEV` and uses `Closes #N` (inert until promotion; `Related to #N` for context-only references)
- `/submit-for-review` applies `QA_READY_LABEL` to the linked issue (if configured)
- `/deploy` runs from `BRANCH_DEV` — bumps version, opens a promotion PR into `BRANCH_PROD` (from a throwaway `release/vX.Y.Z` branch cut at the head of `BRANCH_DEV`) with `Closes #N` to auto-close issues

## Three-branch development

**Config:** Set both `BRANCH_DEV` and `BRANCH_TEST`.

```
feature/<name>  →  BRANCH_DEV  →  BRANCH_TEST  →  BRANCH_PROD
    /start           /submit-for-review        manual/future       /deploy
                                   /promote
```

Adds a dedicated test/staging branch between integration and production. Feature PRs still target `BRANCH_DEV`. The promotion from `BRANCH_DEV` to `BRANCH_TEST` is a manual PR or a future `/promote` skill.

**When to use:** Teams with a formal QA or staging environment that is separate from the integration environment. Common in regulated industries or teams with dedicated QA staff.

**Skill behavior in three-branch mode:**
- `/submit-for-review` targets `BRANCH_DEV` (same as two-branch)
- `/deploy` runs from `BRANCH_TEST` — bumps version and promotes `BRANCH_TEST` to `BRANCH_PROD`
- The `BRANCH_DEV` to `BRANCH_TEST` step is outside Code Cannon's current scope

## Choosing your model

| Profile | Branch model | AI review | QA flow | Good for |
|---|---|---|---|---|
| **Lightweight** | Trunk | Advisory (posts but doesn't block) | Off | Fast iteration, low ceremony |
| **Standard** | Two-branch | Blocks merge | Optional | Review-gated development |
| **Governed** | Two or three-branch | Blocks merge | On | Full traceability and QA handoff |

These aren't rigid modes — they're starting points. Every setting is independently configurable. Run `/setup` for a guided walkthrough that helps you pick the right profile and configure it.

## Branch discipline

Code Cannon enforces branch rules at the skill level:

- `/submit-for-review` aborts if run from any protected branch (`BRANCH_PROD`, `BRANCH_DEV`, or `BRANCH_TEST` when set). It must be run from a `feature/*` branch.
- `/deploy` aborts if not on the required pre-production branch (determined by mode).
- `/start` always creates `feature/*` branches via `gh issue develop`, ensuring every branch is linked to an issue.

The agent will not proceed past these checks. There is no override flag — if you're on the wrong branch, switch first.

## Working on parallel branches

When working on multiple features simultaneously, **sequence tasks that touch overlapping files — don't parallelize them.** Submit and merge the first branch before starting the second.

This is especially important for tasks that involve renaming, restructuring, or updating cross-references across skills. These changes tend to be broad in scope (touching many files) even though the individual edits are small. Running `sync.py` amplifies the problem further: a one-line edit in a source skill becomes changes across every adapter directory (`.claude/`, `.cursor/`, `.agents/`, `.gemini/`), multiplying the conflict surface.

If you do end up with parallel branches that conflict, the cleanest resolution is usually to re-apply the smaller change on a fresh branch off the updated integration branch, rather than resolving conflicts file-by-file. The mechanical nature of most cross-cutting changes (find-and-replace + `sync.py`) makes this fast and reliable.

## Cleaning up merged branches

Branch cleanup is optional housekeeping. Some teams keep every branch forever for history, others prune after each sprint. Code Cannon has no opinion and enforces nothing — do whatever fits your workflow.

**Easiest baseline:** enable GitHub's *Settings → General → "Automatically delete head branches"*. Every merged PR then deletes its remote branch automatically. This handles the remote side with zero ongoing effort.

The setting deletes the head branch of *any* merged PR, long-lived branches included. `/deploy` is safe: it promotes from a throwaway `release/vX.Y.Z` branch, so the integration or staging branch is never a PR head. A PR you open yourself with a long-lived branch as its head is not — in three-branch mode that includes the `make promote` PR (integration → staging). Before merging one with the setting on, protect the head branch against deletion (a branch protection rule or ruleset), or restore it afterwards with the "Restore branch" button on the merged PR.

**Manual cleanup:** the following one-liners are safe to re-run any time. They assume the default branch names `main`, `dev`, and `test` — if yours differ, substitute your own names everywhere they appear below (both in the `git branch --merged` argument and in the `grep` exclusion list).

Prune local refs to branches that no longer exist on the remote:

```bash
git fetch --prune
```

Delete local branches already merged into the integration branch (excludes `main`, `dev`, `test`, and the current branch; uses `-d` so git refuses to delete anything unmerged):

```bash
git branch --merged dev | grep -vE '^\*|^\s*(main|dev|test)$' | xargs -n1 git branch -d
```

Delete remote branches already merged (for users who don't enable GitHub's auto-delete):

```bash
git branch -r --merged origin/dev | grep -vE 'origin/(HEAD|main|dev|test)' | sed 's|origin/||' | xargs -n1 git push origin --delete
```

A natural time to run cleanup is right after `/deploy`, once the integration branch has been promoted to production — but that's personal preference, not a Code Cannon rule. Run it whenever it feels useful.

**Only delete branches that have been merged into the integration branch.** Never prune by age, staleness, or "looks abandoned" heuristics — an old branch may be someone's in-progress work, and `git branch -D` (or a force-push delete) will destroy it silently. The `--merged` filter is what makes the commands above safe; don't swap it out for a time-based one.
