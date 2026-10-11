---
name: status
description: "Code Cannon: Summarize in-progress and recently completed work from GitHub and git"
---

## What `/status` does

`/status` prints a read-only, standup-ready snapshot of in-progress and recently completed work, then a single "what's next" suggestion. It never writes to GitHub or the working tree — it only reads and reports.

Because it is read-only, the *shape* of its output does not matter: a differently-formatted-but-accurate summary is a fine result. Derive a clear, scannable layout yourself. What this skill pins down is the data to fetch, how to classify it, and the one piece of real opinion — the "what's next" ordering.

---

## Step 1 — Determine mode

Three mutually exclusive modes, selected from `$ARGUMENTS`:

- **Milestone mode** — `--milestone` or `--sprint` is present. Everything after the flag is the milestone name (a name or a number; trim outer whitespace, preserve internal spaces). Ignore other arguments. Run Steps M1–M2.
- **Team mode** — `--team` is present. Run Steps T1–T2. `--team` is mutually exclusive with `--milestone`/`--sprint` and with a username; if combined, report the conflict and stop.
- **Personal mode** — no mode flag. **Subject** defaults to `@me`; a `@name` or a non-numeric word is a username (strip the leading `@` for `gh` flags that reject it, keep it for display). **Lookback** defaults to `7`; a bare number is the lookback in days.

---

## Step 2 — Fetch GitHub data (personal mode)

Run these concurrently. If any `gh` command exits non-zero (including auth errors), report the message and stop — do not retry.

**Open PRs authored by subject** — request enough fields to derive health (draft, CI, review decision, merge conflict) and staleness:
```bash
gh pr list --author <subject> --state open \
  --json number,title,url,labels,milestone,baseRefName,body,reviewDecision,statusCheckRollup,updatedAt,mergeable,isDraft
```

**Recently merged PRs**, filtered to those merged within `<lookback>` days:
```bash
gh pr list --author <subject> --state merged --limit 20 \
  --json number,title,url,mergedAt,labels,baseRefName
```

**Open issues assigned to subject:**
```bash
gh issue list --assignee <subject> --state open \
  --json number,title,url,labels,milestone,updatedAt
```

**PRs requesting your review** — only when subject is `@me`; skip for other users:
```bash
gh pr list --search "review-requested:@me" --state open \
  --json number,title,url,author,updatedAt
```

Also fetch local git context (skip and note if not in a git repo — `git rev-parse --is-inside-work-tree`):
```bash
git log --oneline --since="<lookback> days ago"
```

---

## Step 3 — Classify and report (personal mode)

Sort items into these buckets:

- **In progress** — open PRs. Identify a linked issue from the PR body (`#N`, `closes #N`, `fixes #N`, `issue #N`) and cross-reference open issues.
- **Done** — PRs merged within the lookback window.
- **Up next** — open issues whose number is **not** referenced by any open PR body. (An issue linked from an open PR belongs under *In progress* with that PR, not here.)
- **Needs your review** — the review-requested query (only when subject is `@me`).

For each open PR, derive health from the JSON — draft state, CI status from `statusCheckRollup`, review state from `reviewDecision`, merge conflict from `mergeable`. Present each as a compact badge; omit a badge when it does not apply or is not configured.

**Staleness:** flag any open PR or issue not updated within `14` days (a threshold of `0` disables staleness entirely). Note the last-updated date and age. This is a real config-driven rule — honor the threshold exactly.

Report the buckets as a scannable summary: a heading naming the subject and lookback, a one-line count roll-up, then a section per non-empty bucket, then the local commits (or a note that git was skipped). Show labels/milestone only when present; dates as `YYYY-MM-DD`. If every GitHub bucket is empty, say so plainly for the subject and window.

**Do not post, comment, write files, or take any action. Output only.**

---

## Step 4 — What's next (personal mode)

After the summary, append **one** actionable suggestion. Gather the extra local state you need (current branch, `git status --porcelain`, latest tag via `git describe --tags --abbrev=0`, unreleased commit count via `git rev-list <tag>..HEAD --count`, and the current branch's PR review/check state via `gh pr view` — treat a non-zero exit as "no PR for this branch"). Skip git lookups when not in a repo.

Evaluate these conditions **in order** and use the **first** match. This ordering is the workflow's opinion about what the operator should do next — it is load-bearing, not formatting:

| Priority | Condition | Suggestion |
|----------|-----------|------------|
| 1 | On a `feature/*` branch with uncommitted changes | You have uncommitted changes on `<branch>`. When ready, run `/submit-for-review`. |
| 2 | On a `feature/*` branch, open PR is `APPROVED` and all checks `COMPLETED` | PR #<number> is approved and checks pass. Consider running `/deploy`. |
| 3 | On a `feature/*` branch with an open PR in any other state | PR #<number> (<title>) is open and awaiting review. |
| 3.5 | Subject is `@me` and PRs request your review | Append to the current suggestion (or stand alone if nothing higher matched): You also have <N> PR(s) awaiting your review. |
| 4 | On a `feature/*` branch, no open PR, clean tree | No open PR for `<branch>`. Run `/submit-for-review` to open one. |
| 5 | On the integration branch with unreleased commits since the last tag | <N> commit(s) on `<branch>` since `<tag>`. Run `/deploy` when ready to release. |
| 6 | No open PRs and no open issues assigned to subject | Nothing in progress. Run `/start` to begin new work. |
| 7 | Open issues exist in "Up next" | Next up is #<number> (<title>). Run `/start <number>` to pick it up. |

If none match, omit the "what's next" section. Omit it entirely in milestone mode.

---

## Milestone mode (Steps M1–M2)

### M1 — Fetch
```bash
gh issue list --milestone "<name>" --state all --limit 200 \
  --json number,title,state,labels,assignees,url
gh pr list --state open \
  --json number,title,body,baseRefName,reviewDecision,statusCheckRollup,mergeable,isDraft
```
If the issue query fails (milestone not found, auth error), report and stop.

### M2 — Classify and report

Group the milestone's issues into three buckets:
- **Done** — `state: closed`.
- **In progress** — open, and the issue number is referenced by some open PR body (`#N`, `closes #N`, `fixes #N`, `related to #N`, `issue #N`). This "referenced by an open PR" definition is the real rule — apply it exactly.
- **Not started** — open, and no open PR references it.

Derive health badges for in-progress issues from their linked PR (same fields as personal mode). Report as a scannable summary titled with the milestone name and a closed/in-progress/not-started roll-up. Show assignees and URLs where they add value; omit empty buckets. **Do not post, comment, write files, or take any action. Output only.**

---

## Team mode (Steps T1–T2)

### T1 — Fetch (run both concurrently)
```bash
gh pr list --state open --limit 100 \
  --json number,title,url,author,labels,milestone,baseRefName,body,reviewDecision,statusCheckRollup,updatedAt,mergeable,isDraft
gh issue list --state open --limit 200 \
  --json number,title,url,assignees,labels,milestone,updatedAt
```
If either fails, report and stop.

### T2 — Group and report

Group by person: PRs by `author.login`; issues by first assignee (unassigned issues into an "Unassigned" group). Within each person, classify as personal mode does — **in progress** (open PRs and their linked issues) and **up next** (open issues not referenced by any open PR) — and derive health badges plus staleness. Report per-person sections with in-progress items first, an "Unassigned" section if any, and a consolidated stale section. **Do not post, comment, write files, or take any action. Output only.**

---

## Hard rules

- Never write to GitHub (no comments, labels, issue updates, or PR changes) and never touch the working tree. Output only.
- If `gh` is unauthenticated or any fetch fails, report the error and stop immediately. Do not retry.
- Strip the leading `@` from the subject when passing to `gh` flags that reject it.
- `--team` is mutually exclusive with `--milestone`/`--sprint` and with a username.
- The `14` threshold is config-driven; `0` disables staleness. The "what's next" priority ordering is fixed — evaluate top to bottom, first match wins.
<!-- generated by CodeCannon/sync.py | skill: status | adapter: agents | hash: 5d39e79b | DO NOT EDIT — run CodeCannon/sync.py to regenerate -->
