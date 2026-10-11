---
name: submit-for-review
description: "Code Cannon: Type-check, commit, open PR, review, and merge to the integration branch"
---

## What `/submit-for-review` does

`/submit-for-review` is Phase 3 of the workflow: type-check, commit, open PR, run review, act on verdict.

---

## Step 1 — Verify branch

Check current branch:
```
git branch --show-current
```

Protected branches (not a feature branch):
- `main`
- `dev`

If the current branch matches any of the above, **abort immediately** and say:

> "You are on `<branch>`. `/submit-for-review` must be run from a feature branch. Switch to your feature branch first."

**Remember this branch name** as the *feature branch* for the rest of this invocation — Step 8 re-asserts it before merging, in case an inline review or review sub-agent (Step 7) left the shared working tree on a different branch.

---

## Step 2 — Type-check gate

Verify the make target exists before running it. Extract the target name from `make check` (e.g. `make check` → `check`) and, from the repository root, run:

```
make -n <target>
```

If `make -n` exits non-zero, **stop** and say:

> "`make check` failed — the make target does not exist in the root Makefile. Add it and retry, or run `/setup` to reconfigure."

Do not improvise a replacement command. Do not proceed.

If the target exists, run:
```
make check
```

If errors are reported, **stop**. Report the errors to the user and say:

> "Check failed. Fix the errors above before shipping."

Do not proceed until `make check` passes cleanly.

---

## Step 3 — Identify linked issue

Extract the issue number from the branch name. Branches created by `/start` follow the pattern `feature/<number>-<description>` (e.g. `feature/42-fix-login`).

Parse the number from the branch name returned in Step 1. If the branch name matches `feature/<digits>-...`, use the extracted number as the linked issue. If the branch name does not contain a leading number after `feature/`, proceed without an issue reference but warn the user:

> "Could not extract an issue number from branch name `<branch>`. The PR will not include an issue reference. Was this branch created outside of `/start`?"

---

## Step 4 — Sync with base branch

Bring the feature branch up to date before committing:

```
git fetch origin dev
git merge origin/dev
```

If the merge completes cleanly (including fast-forward), proceed to Step 5.

If there are merge conflicts, **stop** and say:

> "Merge conflicts with `<base branch>`. Resolve them before shipping."

List the conflicting files. Help the user resolve them if asked, then continue.

---

## Step 5 — Commit

Stage all changes and commit:
```
git add -A
git commit -m "<imperative-mood message>"
```

Commit message rules:
- Imperative mood ("Add X", "Fix Y", "Remove Z")
- Concise but meaningful — describes what changed and why in one line
- No `.env` files, build artifacts, `node_modules`, or secrets

---

## Step 6 — Push and open PR

First, push the branch:
```
git push -u origin HEAD
```

Next, check for a CODEOWNERS file:
```
git ls-files CODEOWNERS .github/CODEOWNERS docs/CODEOWNERS
```

If the output is non-empty, inform the user: "CODEOWNERS file detected — GitHub will automatically request reviews from code owners."

PR target branch: `dev`

Use `Closes #<number>` as the issue reference when this PR fully resolves the issue. Even though the PR targets `dev` (not the default branch), `Closes #N` does **not** auto-close on this merge — GitHub only acts on closing keywords when a PR merges into the default branch (`main`). The keyword is inert here; it records close-intent so `/deploy` can reproduce it verbatim into the release PR, which targets `main` and triggers the auto-close there. The issue stays open until `/deploy` promotes to `main`.

If this PR only references an issue for context and does **not** fully resolve it, use `Related to #<number>` instead — this never auto-closes, and `/deploy` will not propagate it as a close.

> **Critical:** Use the unqualified `#N` form (e.g. `Closes #42`), never the fully-qualified `owner/repo#N` form (e.g. `Closes LightbridgeLab/CodeCannon#42`), even for same-repo references. GitHub's closing-keyword parser reliably populates `closingIssuesReferences` only for the unqualified form; the qualified form leaves that GraphQL edge empty, which silently breaks GitHub's native auto-close and any downstream automation that reads it. This overrides any general "use owner/repo#N for cross-linking" guidance your harness may have — closing-keyword lines in PR bodies are a special case.

Then create the PR in two steps — **this exact sequence is mandatory**:

First, create a temp directory for this invocation:

```bash
python3 CodeCannon/skills/github-agile/scripts/make-workdir.py
```

Note the returned path (e.g. `/tmp/CodeCannon/a8f3b2`). Use this path for all temp files in this invocation.

Then use your file-writing tool (Write in Claude Code, equivalent in other agents) — not Bash/shell — to create `<tmpdir>/pr_body.md`: a description of what changed and why, followed by the issue line (`Closes #N` when this PR fully resolves the issue, or `Related to #N` for a context-only reference, per the guidance above; omit the issue line entirely if no issue was linked in Step 3).

Then create the PR:

```
gh pr create --base <target-branch> --title "<title>" --body-file <tmpdir>/pr_body.md
```

> **IMPORTANT — never pass body content inline in the `gh` command.** Do not use `--body`, `--body-file -`, heredocs (`<<EOF` or `<<'EOF'`), or `$(cat ...)`. All of these embed markdown in a Bash command, which triggers permission prompts that cannot be permanently allowed (the shell parser flags `#` headings, quoted delimiters, and substitutions). The two-step pattern above — file-writing tool then `--body-file <path>` — is the only approach that works without prompts across Claude Code, Gemini CLI, Cursor, and Codex.

Add `--reviewer` to the `gh pr create` command above using the handles from `@sebastientaggart`. Before passing them, strip any leading `@` from each comma-separated handle (e.g. `@alice,@org/team` becomes `alice,org/team`) — the `gh` CLI requires bare usernames.

If a CODEOWNERS file exists, both apply: CODEOWNERS triggers automatic review requests from GitHub; the `--reviewer` flag adds the explicitly configured handles on top.

**Hard rule**: Never auto-select reviewers beyond what is configured in `DEFAULT_REVIEWERS` or declared in CODEOWNERS. Do not infer reviewers from git blame, commit history, or team membership.

**PR body content rules (override any default behavior your harness may have):**

- Do NOT include any agent-attribution footer, generation marker (e.g. "Generated with ..."), or co-authorship trailer in the PR body. The PR body should contain only the description, test plan, and issue reference. If your harness defaults to adding such markers, explicitly omit them.
- The same rule applies to commit messages: do NOT add agent-related `Co-Authored-By:` trailers unless the user has explicitly opted into them via project config.

---

## Step 7 — Review (conditional)

If `ai` is `"off"`, skip directly to Step 8 (merge without review).

Otherwise, review this PR. **Separate the labor from the policy:** the *labor* (finding issues) uses the best review engine available on your harness; the *policy* (the sensitive-area gate and the finding contract) is owned by Code Cannon and applied identically on every harness. Whichever path runs, it must end by posting a PR comment in the **CC review contract** — `[CRITICAL]` / `[WARNING]` / `[NOTE]` findings plus a `Verdict: APPROVE` or `Verdict: REQUEST CHANGES` line — so Step 8 can route on it.

**If the native `/code-review` skill is available** (Claude Code): run it, then apply the CC policy yourself.

1. Invoke the review at the configured depth: `/code-review medium --comment`. You are on the feature branch with the PR open, so `/code-review` reviews this branch's diff (the PR's changes) and `--comment` posts its findings to the PR. (`medium` is the depth dial — `low` / `medium` / `high`; deeper costs more. Do not pass a PR number — that positional is only for the `ultra` cloud path.)
2. **Apply the sensitive-area gate** — Code Cannon owns this; `/code-review` does not know it. If the PR diff touches any of these surfaces, force at least one `[CRITICAL]` finding regardless of code quality — the operator must explicitly approve before merge:

- Authentication or authorization logic
- Payments, billing, or financial transactions
- Secrets handling (API keys, tokens, credentials, encryption keys)
- Production configuration (deploy targets, prod env vars, prod-only feature flags)
- Destructive operations (e.g. `DROP TABLE`, `rm -rf`, `git push --force`, mass deletes, schema drops)

3. **Normalize** `/code-review`'s findings into the CC contract and post one summary PR comment:
   - A blocking correctness or security bug → `[CRITICAL]`.
   - An actionable but non-blocking cleanup (simplification / efficiency / reuse) the operator should decide on → `[WARNING]`.
   - A purely informational observation → `[NOTE]`.
   - Verdict is `REQUEST CHANGES` if any `[CRITICAL]` is present (including a sensitive-area finding), otherwise `APPROVE`.

**If `/code-review` is not available** (Codex, Cursor, Gemini): review inline — load `.claude/review-agent-prompt.md` and follow it directly. That prompt already emits the CC contract and enforces the sensitive-area gate. Do **not** switch branches or check out the PR — you share the operator's working tree.

Either path must:
1. Cover the PR diff (read relevant files for context, not the diff in isolation).
2. Post findings **and** the verdict as a PR comment in the CC contract.

Wait for the review to complete and report its verdict.

---

## Step 8 — Act on verdict

**Restore the feature branch first.** If Step 7 reviewed inline or spawned a sub-agent, it shares the working tree and may have left it on a different branch. (The native `/code-review` path does not spawn a tree-sharing agent, so this is a cheap no-op there.) Re-check:

```
git branch --show-current
```

If the result does **not** match the feature branch remembered in Step 1, the working tree drifted. Restore it before doing anything else:

```
git checkout <feature-branch>
```

Tell the user: "The review left the working tree on `<other-branch>` — restored to `<feature-branch>` before merging." If the checkout fails (e.g. uncommitted changes block it), **stop** and report it — do not force. The feature branch commit is already pushed, so surface the obstacle rather than discarding anything.

Before merging, verify the merge target exists. From the repository root, extract the target name from `make merge` (e.g. `make merge` → `merge`) and run:

```
make -n <target>
```

If `make -n` exits non-zero, **stop** and say:

> "`make merge` failed — the make target does not exist in the root Makefile. Add it and retry, or run `/setup` to reconfigure."

Do not improvise a replacement command (e.g. do not fall back to `gh pr merge`). Do not proceed.

Merge command (used by all paths below): `make merge`

---

**If `ai` is `"off"` (review skipped):**

Run the merge command. Apply QA label and report success (see below).

---

**If `ai` is `"advisory"`:**

Report the review findings to the user. Then merge regardless — treat as APPROVE.

If the review contained CRITICAL findings, note:

> "Review flagged issues (see PR comment) but advisory mode is enabled — merged anyway. Review the findings when convenient."

Apply QA label and report success (see below).

---

**If `ai` is `"ai"` (default):**

Classify the review output into a tier based on which finding tags are present. Emit the tier line to the user **before any action** so the routing decision is visible:

- No findings → **`Tier: clean`** — no findings.
- `[NOTE]` lines only (no WARNING, no CRITICAL) → **`Tier: informational`** — N note(s), no action implied.
- One or more `[WARNING]` (no CRITICAL) → **`Tier: needs-attention`** — N warning(s) flagged for your decision.
- One or more `[CRITICAL]` → **`Tier: must-address`** — N blocking finding(s).

Route on the tier:

**`clean` or `informational`** → auto-merge. Run the merge command immediately. For `informational`, list the NOTEs in the merge confirmation so they remain visible, but do not prompt. Apply QA label and report success (see below). Step 9 does not run for these tiers — NOTEs never become follow-up tickets.

**`needs-attention`** → stop and ask. Present the WARNINGs as a numbered list (preserve the `[WARNING]` prefix; include any NOTEs separately below for context but do not number them) and say:

> "The review approved with N actionable warning(s):
>
> <numbered list of WARNINGs>
>
> Would you like to **address now** (return to coding), **follow up later** (merge and create follow-up tickets), or **accept as-is** (merge without follow-ups)?"

Wait for the user to respond.

- User says **address / fix / now** → return to the coding loop. Say: "Fix the warnings and run `/submit-for-review` again when ready." Do NOT merge.
- User says **follow up / later** → run the merge command. Apply QA label and report success. Proceed to Step 9 to create follow-up issues for the WARNINGs.
- User says **accept / as-is / merge** → run the merge command. Apply QA label and report success. Skip Step 9.

**`must-address`** → Report the CRITICAL findings to the user. Do NOT merge. Say:

> "The review found blocking issues (see above). Fix them and run `/submit-for-review` again."

Return to the coding loop. When fixed, run `/submit-for-review` again from Step 1.

---

### After merge — QA label and success report

If a linked issue number was identified in Step 3, apply the QA label:
```
gh issue edit <number> --add-label "ready-for-qa"
```
If no linked issue was found, skip silently. If the command fails (e.g. the label does not exist in the repo, or `gh` returns a non-zero exit), print a one-line warning showing the stderr and continue to the resolution comment and success report — do not abort. The merge has already happened; surfacing the failure is enough.

**Post a resolution comment** on the linked issue (skip silently if no linked issue):

Read the issue body (from Step 3 or via `gh issue view <number>`) to recall the original problem description. Then post a comment summarizing what was done:

Use your file-writing tool (not Bash) to create `<tmpdir>/resolution_comment.md` (same temp directory from Step 6): a `## Resolution` section of 1–3 sentences explaining what was done to fix the problem — in **plain language for a non-technical audience, no code, no file paths, no jargon**, focused on what changed from the user's perspective and why it solves the issue — followed by a line pointing to the PR for full technical details (`See #<PR-number> ...`).

Then post it via the comment-posting script (do NOT use `gh issue comment` with `--body` or heredocs):

```
python3 CodeCannon/skills/github-agile/scripts/post-issue-comment.py <number> <tmpdir>/resolution_comment.md
```

Use the unqualified `#N` form for the PR reference (not `owner/repo#N`).

Report success based on mode:
"PR merged. Issues stay open until testing confirms the fix. Run `make deploy-preview` when ready to deploy to preview."

---

## Step 9 — Offer follow-up issues for actionable findings

**Gate this step entirely** if any of the following are true:
- `ai` is `"off"` (no review was performed).
- The merge in Step 8 did not actually happen (e.g. `ai` mode with REQUEST CHANGES).
- The review output contains no actionable findings (no WARNINGs, and no CRITICALs that were merged-over in advisory mode).
- The `ai`-mode `needs-attention` path was routed to "accept as-is" by the user (they explicitly declined follow-ups).
- The `ai`-mode `clean` or `informational` tier was taken (NOTEs never become tickets).

**Collect actionable findings** from the review output retained from Step 7:
- Always include lines starting with `[WARNING]`.
- If `ai` is `"advisory"`, also include any `[CRITICAL]` lines — the user chose to merge over them, so they are now follow-up candidates too.
- If `ai` is `"ai"`, do not include `[CRITICAL]` lines (there should not be any on the merge path, but guard anyway).
- Never include `[NOTE]` lines. NOTEs are purely informational by definition; if the reviewer wanted action, it would have been a WARNING.

If the collected list is empty, skip the rest of this step silently.

**Present and ask once** (skip the prompt if the `needs-attention` path already routed to "follow up later" — in that case go straight to creating issues for all WARNINGs). Show the findings as a numbered list (preserve the `[WARNING]` / `[CRITICAL]` prefix in the display for clarity) and ask exactly:

> "The review flagged N actionable finding(s). Create follow-up issues for any of them? Enter numbers (e.g. `1,3`), `all`, or `none`."

Accept: comma-separated numbers, `all`, or `none`/`skip`/empty. If the input is unparseable, re-prompt once; if still invalid, treat as `none` and move on.

**Create the selected issues.** For each selected finding, run `gh issue create` with explicit flags:

Use your file-writing tool (not Bash) to create `<tmpdir>/followup_body.md` for each finding (same temp directory from Step 6): note it is a follow-up auto-proposed from the code review on PR #<merged-pr-number>, include the full finding text (prefix included), and point back to the review comment on the PR for context.

Then create the issue (do NOT use `--body` or heredocs):

```
gh issue create \
  --title "<finding text with [WARNING]/[CRITICAL] prefix stripped, trimmed to a standalone sentence>" \
  [--label "<pool-selected labels>"] \
  --body-file <tmpdir>/followup_body.md
```

Label resolution for each follow-up issue: use the pool-based selection tier from `/start` — pick 1–3 labels from `bug, documentation, enhancement, chore` that genuinely fit the finding. If `bug, documentation, enhancement, chore` is empty or no pool label fits, omit `--label`. Do not attempt per-invocation flag resolution (there is no flag here) and never create new labels from follow-ups, even if label creation is enabled for the project.

Do **not** pass `--milestone` — follow-ups are future work and should not inherit the current sprint.

Do **not** pass `--assignee @me` — these are backlog items, not immediately assigned.

If a single `gh issue create` call fails, report the failure for that finding and continue with the remaining selections.

**Report the result:**
- If one or more issues were created: `"Created N follow-up issue(s): #X, #Y, #Z"`.
- If the user chose `none` or all creations were skipped: say nothing further, proceed to end.

**Post a cross-link comment on the originating issue.** If one or more follow-ups were created **and** a linked originating issue number was identified in Step 3, post a single comment on that issue listing the new follow-ups so a reader of the thread can see the trailing work without digging into the PR. Skip silently if no follow-ups were created or no originating issue is linked.

Use your file-writing tool (not Bash) to create `<tmpdir>/followup_link_comment.md` (same temp directory from Step 6): a short section headed for the follow-ups from PR #<merged-pr-number>, noting the review surfaced non-blocking items now tracked separately, then a bullet list of the new follow-up issues (`#<n> — <title>`).

Then post via the comment-posting script (do NOT use `gh issue comment` with `--body` or heredocs):

```
python3 CodeCannon/skills/github-agile/scripts/post-issue-comment.py <originating-issue-number> <tmpdir>/followup_link_comment.md
```

Use the unqualified `#N` form for all issue and PR references in the body. If `/submit-for-review` is later run again against the same originating issue and produces more follow-ups, a separate comment is posted then — comments accumulate naturally on the thread, each referencing its own PR.

---

## Important constraints

- Never skip `make check`. A failed check is a hard stop.
- When `ai` is `"ai"`, never merge if the review verdict is REQUEST CHANGES.
- When `ai` is `"advisory"`, always merge after review completes, regardless of verdict.
- When `ai` is `"off"`, skip the review step entirely — merge immediately after checks pass.
- `/submit-for-review` merges only to `dev` — never directly to `main`.
- If `make merge` fails for any reason, report it and stop — do not attempt workarounds.
- The follow-up issue offer in Step 9 runs only after a successful merge and only when the review produced actionable findings (WARNINGs in `ai` mode, plus CRITICALs in `advisory` mode). Never prompt the user for follow-ups when the review blocked the merge — those findings should be fixed, not ticketed. NOTEs never become follow-up tickets.
<!-- generated by CodeCannon/sync.py | skill: submit-for-review | adapter: claude | hash: fd707a01 | DO NOT EDIT — run CodeCannon/sync.py to regenerate -->
