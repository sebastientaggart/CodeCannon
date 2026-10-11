---
name: start
description: "Code Cannon: Start a new feature or bugfix"
argument-hint: "feature description or issue number"
---

## CRITICAL: Order of operations

**You must complete Steps 1–4 before writing any code.**
Do not open any source file with intent to edit until `git branch --show-current` shows a `feature/*` branch.

---

## Determine case

If `$ARGUMENTS` is a number (digits only) → go to **Case B: Resume existing issue**.
Otherwise → go to **Case A: New work**.

---

## Parsing $ARGUMENTS (Case A only)

> Skip this entirely if `$ARGUMENTS` triggered Case B.

> **Execution order:** Resolve labels and milestones **now**, before entering Case A Step 1. If milestone auto-detection requires a user prompt (2+ open milestones), that prompt happens here — not later during issue creation. By the time you reach Step 2's human gate, all metadata must already be resolved so that Step 3 can proceed without re-prompting.

The description may be followed by optional flags — `--label`/`-l` and `--milestone`/`-m`, in any order. Separate the description from the flags yourself; the flags carry these meanings:

- **`--label <value>` / `-l <value>`** — a comma-separated label string used **verbatim**, bypassing label auto-selection entirely for this invocation. Quote values containing spaces (e.g. `--label "good first issue"`).
- **`--milestone <value>` / `-m <value>`** — a milestone name or number (GitHub accepts both names and numbers).

**Label resolution (three-tier, Case A only):**

After parsing flags, determine the active labels in this order:

1. **Per-invocation flag** — if `--label <value>` was in `$ARGUMENTS`, use that value verbatim. Skip all remaining steps.
2. **Pool-based selection** — the allowed label pool is: `bug, documentation, enhancement, chore` (comma-separated). Select 1–3 labels from this pool that genuinely fit the task description and implementation approach. Do not apply labels mechanically — pick only what fits. If no pool label fits the task, fall through to step 3.
   - If any selected label name contains a space (e.g. `good first issue`), quote the entire `--label` value.
3. **No label / creation** — if the pool is empty or no pool label fits:
   - Label creation is not allowed in this project. Omit `--label` entirely. Proceed silently; do not inform the user.

**Milestone resolution (three-tier, Case A only):**

After parsing flags, determine the active milestone in this order:

1. **Per-invocation flag** — if `--milestone <value>` was in `$ARGUMENTS`, use that value. Stop.
3. **Auto-detect** — if no milestone is resolved yet, query open milestones (the script prints `{"count": N, "milestones": [{"number", "title"}, ...]}` for the current repo):
   ```bash
   python3 CodeCannon/skills/github-agile/scripts/list-open-milestones.py
   ```
   - **0 results** → no milestone; proceed without `--milestone`.
   - **1 result** → use its title silently. Inform the user inline: `(milestone: <title>)`.
   - **2+ results** → show the numbered list, ask once: **"Multiple open milestones — which should this issue go under? (enter a number or title, or 'none')"**. Accept milestone number, title, or "none"/"skip". Wait for response before continuing.

> Replace vs append: flags **replace** auto-selection entirely, they do not append. This avoids silent label duplication and milestone conflicts.

---

## Pre-check: Existing feature branch

Before entering Case A or Case B, check the current branch:

```bash
git branch --show-current
```

If already on a `feature/*` branch and `$ARGUMENTS` is **not** a number (i.e. this is new work, not a resume):

Say:

> **"You're already on `feature/<name>`. Would you like to create a GitHub issue linked to this branch and start coding? Or switch to the base branch first? (yes to continue here / no to abort)"**

Stop. Wait for the user to respond.

- **Yes** → proceed to **Case A**, but **skip Step 4** (branch creation) entirely. The current branch is used as-is. In Step 3, after creating the issue, link it to the existing branch by running:
  ```bash
  gh issue develop <number> --base <base-branch> --name <current-branch-name>
  ```
  This links the issue to the branch in GitHub without creating or checking out a new branch. If `gh issue develop` fails because the branch already exists on the remote, that is fine — the link may already be established. Continue to Step 5.
- **No / abort** → stop. Tell the user to switch to the base branch and run `/start` again.

If already on a `feature/*` branch and `$ARGUMENTS` **is** a number → proceed to **Case B** normally (it handles branch checkout itself).

If on any other branch → proceed to Case A or Case B as determined by the `$ARGUMENTS` check above.

---

## Case A: New work (text description)

### Step 1 — Investigate

Read the relevant code using your harness's native file-reading and search tools (read, grep/glob, and the like) rather than shell pipelines — hand-rolled `find … | xargs`, `grep ; awk`, or redirection shapes trigger permission prompts that cannot be permanently allowed. Then propose a concrete implementation approach, specific about which files change and how.

### Step 2 — Approach checkpoint

Say exactly:

> **"Does this approach sound right? Type `go` to create a GitHub issue and branch, or share any questions/adjustments first. To delegate part of the work to another agent, run `/delegate <task description>` before typing `go`."**

Stop. Wait for the user to respond.

The friendly text question is required regardless of harness mode. If your harness is currently in a preview / plan / dry-run mode where you cannot passively stop and wait (and must instead invoke the harness's own approval mechanism), still include the text question in your response. The harness's approval UI mediates the wait, but it is not a substitute for the question itself. Users expect to see the consistent text language across all modes; do not silently swap it for the harness's UI.

Proceed only on unconditional approval. If the user's response includes conditions, questions, or adjustments, treat it as discussion — address their input and re-ask. If the user abandons ("never mind", "stop"), stop — nothing to clean up.

### Step 3 — Create GitHub Issue

Create the issue in two steps — **this exact sequence is mandatory**:

**Step 3a — Create a temp directory and write the body file.** Run:

```bash
python3 CodeCannon/skills/github-agile/scripts/make-workdir.py
```

Note the returned path (e.g. `/tmp/CodeCannon/a8f3b2`). Use this path for all temp files in this invocation.

Then use your file-writing tool (Write in Claude Code, equivalent in other agents) to create `<tmpdir>/issue_body.md` with the structured markdown body (see sections below). Do NOT use Bash/shell to write this file. Do NOT use heredocs, `cat`, or `echo`. The file-writing tool bypasses shell parsing entirely.

**Step 3b — Run `gh issue create`** with `--body-file` pointing to the temp file:

```bash
gh issue create \
  --title "<standalone full sentence — must make sense with no context>" \
  --assignee @me \
  [--label "<resolved labels>"] \
  [--milestone "<resolved milestone>"] \
  --body-file <tmpdir>/issue_body.md
```

> **IMPORTANT — never pass body content inline in the `gh` command.** Do not use `--body`, `--body-file -`, heredocs (`<<EOF` or `<<'EOF'`), or `$(cat ...)`. All of these embed markdown in a Bash command, which triggers permission prompts that cannot be permanently allowed (the shell parser flags `#` headings, quoted delimiters, and substitutions). The two-step pattern above — file-writing tool then `--body-file <path>` — is the only approach that works without prompts across Claude Code, Gemini CLI, Cursor, and Codex.

Use the labels and milestone you already resolved in the Parsing section (before Step 1). Do **not** re-run label or milestone resolution here — the values are final:
- **Labels**: if non-empty, add `--label "<value>"` to the command. If empty, omit `--label` entirely.
- **Milestone**: if non-empty, add `--milestone "<value>"` to the command. If empty, omit `--milestone` entirely.

**Body structure (required sections, in this order):**

```markdown
## Problem to Fix
<what is broken or missing, written for a non-developer — no code or file paths>

## Why it Matters
<the impact or motivation — who is affected and how>

## General Approach
<high-level direction for the fix, in plain language>

## Complexity
**Verification / QA effort:** <trivial | moderate | significant | extensive>
<one-line justification — what makes verification easy or hard for this specific change>

## Acceptance Criteria
- <specific, verifiable outcome>
- <another outcome>
```

All five sections are required. Write for a non-developer audience — no code, no file paths. Acceptance Criteria must be concrete and verifiable (not vague goals).

**Title rules:**
- ✅ `Fix 'Contact Us' footer link pointing to 404 instead of /contact-us`
- ❌ `Fix broken link`

After the command runs, note the issue number from the output URL (e.g. `https://github.com/.../issues/42` → issue `42`).

Show the user: `Created issue #<number>: <title>`

Then immediately post agent implementation notes as a comment.

Use your file-writing tool (not Bash) to create `<tmpdir>/issue_comment.md` (same temp directory from Step 3a):
```markdown
## Agent Implementation Notes

<full technical plan: exact files to change, approach, key decisions, edge cases>
```

Then post it via the comment-posting script (do NOT use `gh issue comment` with `--body` or heredocs):
```bash
python3 CodeCannon/skills/github-agile/scripts/post-issue-comment.py <number> <tmpdir>/issue_comment.md
```

### Step 4 — Create feature branch

Ensure the base branch is a perfect mirror of origin before branching. The script below guards against uncommitted local changes, then runs `git checkout`, `git fetch`, and `git reset --hard origin/<base>` as one atomic operation. The hard reset is safe under the CodeCannon workflow (the integration/production branch is never edited locally — all changes flow through PRs); the dirty-tree guard catches accidental local edits before they get silently discarded.

```bash
python3 CodeCannon/skills/github-agile/scripts/sync-base-branch.py dev
```

If the script exits non-zero, stop and resolve the issue it reports before continuing.

Now create the feature branch:

```bash
gh issue develop <number> --base dev --name feature/<number>-<short-descriptive-name> --checkout
```

> `--base` is required: `gh issue develop` reads the default base from the GitHub API, not from local working state, so `git checkout dev` on its own does not influence which branch the new feature branch is cut from.

Verify the branch was created:

```bash
git branch --show-current
```

Show the user: `On branch feature/<name>`

**Do not proceed to Step 5 until this shows a `feature/*` branch.**

### Step 5 — Write the code

Write the code using your harness's native editing tools. Do NOT commit anything.
To exercise your work as you go, run the project's configured test command rather than hand-rolling a test invocation — a hand-built `python3 -m unittest … > /tmp/…` or similar redirection shape triggers permission prompts that a configured command avoids:

```bash
make test
```

If it fails because the target does not exist, tell the user rather than improvising a replacement.

When done, say: **"When you've verified locally, reply `yes` to submit, or say what to change."**

- User replies `yes` → invoke `/submit-for-review` inline
- User describes changes → iterate, repeat this message

---

## Case B: Resume existing issue (numeric argument)

> **Story-driver recognition:** If the immediately preceding context shows a preamble line of the form `[story-driver: parent=<N> ticket=<K> of <M>]`, the `/start` invocation is being orchestrated by the `/story` driver. Under that signal:
> - Step 2's "Does this match your understanding?" gate is implicitly satisfied (the operator approved the entire story plan at story start). Skip the gate, do not ask, proceed directly toward Step 4.
> - Step 3's investigation findings prompt is suppressed (default to skip silently). Genuine root-cause corrections or project-wide gotchas should still be raised, but routine "this looks like the ticket says" observations stay quiet.
> - Step 5's closing "verified locally" prompt is implicitly approved — automatically proceed to `/submit-for-review` without waiting for `yes`. The `make check` gate inside `/submit-for-review` is the verification safety net under the driver.
>
> Everything else in Case B (loading context, checking out the branch, writing the code, all real escalation triggers) behaves identically. The driver only suppresses the three routine prompts above.

### Step 1 — Load context

```bash
gh issue view <number> --comments
```

Read the full body and all comments. Note: what was done, what remains, branch status.

### Step 2 — Summarize and gate

Tell the user:
- What the issue is about
- What was previously done (from agent notes if present)
- What appears to remain

Ask: **"Does this match your understanding? Type `go` to start coding, or share any questions/adjustments first. To delegate part of the work to another agent, run `/delegate <task description>` before typing `go`."**

Proceed only on unconditional approval. If the user's response includes conditions, questions, or adjustments, treat it as discussion — address their input and re-ask. If the user wants a fresh start, restart as Case A. If the user abandons, stop — nothing to clean up.

### Step 3 — Investigation findings (conditional)

If the investigation in Steps 1–2 revealed anything that isn't already stated or implied by the issue body — a root cause correction, a related side-effect, a project-wide gotcha — present the findings. If the investigation simply confirmed the ticket, skip this step silently and proceed to Step 4.

Create a temp directory for this invocation:

```bash
python3 CodeCannon/skills/github-agile/scripts/make-workdir.py
```

Present numbered findings:

> The investigation surfaced the following:
>
> 1. <finding>
> 2. <finding>
>
> **post** as a comment to the ticket, or **skip** to continue.

- `post` → use your file-writing tool (not Bash) to create `<tmpdir>/investigation_comment.md`:
  ```markdown
  ## Investigation Findings

  - <finding>
  - <finding>
  ```
  Then post it via the comment-posting script:
  ```bash
  python3 CodeCannon/skills/github-agile/scripts/post-issue-comment.py <number> <tmpdir>/investigation_comment.md
  ```
- `skip` → proceed silently.

### Step 4 — Check out branch

Ensure the base branch is a perfect mirror of origin before branching (same safety rationale as Case A Step 4):

```bash
python3 CodeCannon/skills/github-agile/scripts/sync-base-branch.py dev
```

If the script exits non-zero, stop and resolve the issue it reports before continuing.

Find and check out the existing branch, or create a new one linked to the issue:

```bash
gh issue develop <number> --base dev --name feature/<number>-<short-name> --checkout
```

> `--base` is required: `gh issue develop` reads the default base from the GitHub API, not from local working state.

Verify:

```bash
git branch --show-current
```

### Step 5 — Write the code

Continue from where work left off, using your harness's native editing tools. Do NOT commit.
To exercise your work as you go, run the project's configured test command rather than hand-rolling a test invocation — a hand-built `python3 -m unittest … > /tmp/…` or similar redirection shape triggers permission prompts that a configured command avoids:

```bash
make test
```

If it fails because the target does not exist, tell the user rather than improvising a replacement.

When done, say: **"When you've verified locally, reply `yes` to submit, or say what to change."**

- User replies `yes` → invoke `/submit-for-review` inline
- User describes changes → iterate, repeat this message

---

## Hard rules

- Do not write or edit any source file before `git branch --show-current` shows `feature/*`.
- Do not use `make branch` — always use `gh issue develop` so the branch is linked to the issue in GitHub.
- Do not commit during `/start` — commits happen in `/submit-for-review`.
- If already on a feature branch when `/start` is invoked with new work (Case A), prompt the user to either continue on the current branch (skipping branch creation) or abort. See **Pre-check: Existing feature branch** above.
- `gh issue create` must use `--title` and `--body-file` flags. Never pass body content inline or open an interactive editor.
- The issue is assigned to `@me` at creation. If you are creating a ticket on someone else's behalf, remove the assignee after creation with `gh issue edit <number> --remove-assignee @me`.
- Apply resolved labels and milestone to every new issue. Label resolution order: per-invocation flag → pool selection from `bug, documentation, enhancement, chore` → omit `--label` entirely. Never apply a label outside `bug, documentation, enhancement, chore`.
- Milestone resolution order: per-invocation flag → auto-detected from GitHub open milestones. Never prompt for a milestone more than once per invocation.
<!-- generated by CodeCannon/sync.py | skill: start | adapter: claude | hash: bc45ca18 | DO NOT EDIT — run CodeCannon/sync.py to regenerate -->
