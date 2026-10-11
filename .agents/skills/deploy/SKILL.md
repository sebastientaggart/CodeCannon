---
name: deploy
description: "Code Cannon: Bump the project version, create a GitHub Release, and promote to production — handles both versioning and releasing in one step"
---

## What `/deploy` does

`/deploy` is the final step in the workflow. It combines version bumping and release creation into a single command: check state, optionally bump the version, then create a GitHub Release (and in multi-branch mode, promote the deploy branch to production first).

The branching mode changes the shape of the release: in **trunk mode** (`BRANCH_PROD` only) `/deploy` tags and releases the current branch directly; in **multi-branch mode** (`BRANCH_DEV` set, optionally with `BRANCH_TEST`) it first promotes the deploy branch into production through a release PR, and that merge is what closes the linked issues.

---

## Step 1 — Verify branch and sync

Run `git branch --show-current`. The **deploy branch** for this project is:

`dev` (two-branch mode).

If not on the deploy branch, abort: "Switch to `<deploy-branch>` before running `/deploy`."

Then sync it to the remote. The script guards against uncommitted local changes, then runs `git checkout`, `git fetch`, and `git reset --hard origin/<deploy-branch>` as one atomic operation. The deploy branch is never edited locally under the CodeCannon workflow (only fast-forwarded from merges), so the hard reset is the correct sync; the dirty-tree guard catches accidental local edits before they are silently discarded.

```bash
python3 CodeCannon/skills/github-agile/scripts/sync-base-branch.py <deploy-branch>
```

If the script exits non-zero, stop and resolve the issue it reports before continuing.

---

## Step 2 — Check current state

Find the latest version tag (`git describe --tags --abbrev=0`; if none, note this is the first release) and read the current version with `cat VERSION`.

Show the merge commits (and their PRs) since the last tag. The range depends on the mode:

Fetch `main` first — Step 1 only synced the deploy branch, so the local `main` ref may lag `origin/main`. Comparing against a stale local `main` over-reports the release (it re-lists already-promoted PRs and already-closed issues). Compute the range against the freshly-fetched remote ref:

```bash
git fetch origin main
git log origin/main..<deploy-branch> --merges --pretty=format:"%s"
```

Merge-commit subjects have the form `Merge pull request #N from branch/name` — parse the PR numbers, then retrieve each body with `gh pr view <N> --json number,title,body`.

From those PR bodies, compile the release's issue links:

Keep closing keywords and context references **separate — do not merge them into one set**:

- **Close set** — the union of every `Closes #N` line across all constituent PR bodies. These auto-close when the release PR merges into `main`. Record, per constituent PR, the exact `Closes #N` lines so they can be reproduced verbatim in the release PR body.
- **Reference set** — issues mentioned only via `Related to #N` or the legacy `Issue #N` form. These are context links and will **not** close. Legacy `Issue #N` carries no recoverable close-intent, so it stays in the reference set rather than being guessed into the close set; the human gate surfaces it so you can manually close any straggler that should have closed.
- **PRs included** (number + title).

Also check for open unmerged PRs (`gh pr list --state open --json number,title,headRefName`).

Present a summary — current version, latest tag, the PRs/issues since that tag, any open PRs — and ask whether to bump the version before deploying (patch → X.Y.C, minor → X.B.0, major → A.0.0, a specific version, or skip to release the latest existing tag). Wait for their response.

---

## Step 3 — Version bump (if requested)

If the user chose **skip**, use the latest existing tag (`git describe --tags --abbrev=0`) as the release version. If none exists (first release), warn "No version tag found. You must bump the version before deploying." and return to the bump prompt.

If the user chose a bump level, map it to a bump command and run `bump-and-tag.py`, which performs the bump, verifies the resulting tag (creating an annotated fallback if `tag.forceSignAnnotated` silently rejected a lightweight tag), and pushes both the commit and the tag. The resolved version is printed on stdout — capture it as `<new-version>`.

| User says | `--bump-cmd` |
|---|---|
| "patch" / anything mentioning patch | `make bump-patch` |
| "minor" | `make bump-minor` |
| "major" | `make bump-major` |
| A specific version e.g. "2.4.5" | `make set-version V=2.4.5` |

```bash
python3 CodeCannon/skills/github-agile/scripts/bump-and-tag.py \
  --bump-cmd "<bump-command-from-table>" \
  --version-read-cmd "cat VERSION"
```

If the script exits non-zero, stop and resolve the issue it reports before continuing.

---

## Step 4 — Compute release contents

Determine the release version tag (from the bump just performed, or the existing HEAD tag if the user skipped). Find the previous tag for the changelog range: `git describe --abbrev=0 <version-tag>^`.

Reuse the PR list, close set, and reference set already computed in Step 2. If the version bump added new commits, re-fetch as needed.

---

## Step 5 — HUMAN GATE

Show the release summary — the target version, the PRs included, and the issue links:

- Issues that will **close** on merge (the close set, reproduced verbatim from constituent PRs).
- Issues **referenced but not closing** (the reference set — confirm none of these should actually close).

Confirm the deploy branch has been tested:
"Have you tested all of the above on preview? Type 'release' to confirm."

Wait for "release" or an explicit confirmation. Any other response → stop and ask what they'd like to change.

---

## Step 6 — Promote: `<deploy-branch>` → `main`

The release PR's head is a throwaway `release/<version-tag>` branch, never the deploy branch itself.

> **Critical:** GitHub's "Automatically delete head branches" setting deletes a merged PR's head branch a second or two after the merge. A release PR opened with `--head <deploy-branch>` therefore gets the long-lived deploy branch deleted from the remote on every release, unless branch protection happens to block it. Promoting from a throwaway branch keeps the deploy branch from ever being a PR head.

Push the throwaway branch at the deploy branch's current head:

```bash
git push origin <deploy-branch>:refs/heads/release/<version-tag>
```

If the push is rejected because `release/<version-tag>` is left over from an earlier aborted run, delete it (`git push origin --delete release/<version-tag>`) and push again.

Create a temp directory for this invocation (`python3 CodeCannon/skills/github-agile/scripts/make-workdir.py`) and note the returned path — use it for all temp files here.

Use your file-writing tool (not Bash) to create `<tmpdir>/release_pr_body.md`:

```markdown
Release vX.Y.Z

PRs included:
- #17 — Add /docs directory
- #18 — Fix checkout runtime error

Closes #14
Closes #15

Related to #20
```

Reproduce **every** `Closes #N` line from the close set — verbatim, one per line, omitting none. Add a `Related to #N` line for each issue in the reference set so the links appear without triggering an auto-close; if the reference set is empty, omit the `Related to` lines entirely.

Create the PR (do NOT use `--body`, `--body-file -`, or heredocs), with `--head` set to the throwaway branch:

```bash
gh pr create --base main --head release/<version-tag> \
  --title "Release vX.Y.Z" \
  --body-file <tmpdir>/release_pr_body.md
```

> **Critical:** Use the unqualified `#N` form only. Never write `Closes owner/repo#N`, even for same-repo refs — GitHub's closing-keyword parser only populates `closingIssuesReferences` for the unqualified form, and the qualified form silently breaks auto-close. The `Closes #N` lines auto-close the linked issues because this PR merges into `main` (the default branch).

Then merge. Do NOT use `make merge` — it refuses PRs targeting `main`. Use `gh pr merge <pr-number> --merge` directly.

After the merge, remove the throwaway branch:

```bash
git push origin --delete release/<version-tag>
```

If this fails because the remote ref does not exist, GitHub's auto-delete already removed it — that is the expected outcome on repos with the setting on. Continue.

---

## Step 7 — Create the GitHub Release

**Publish confirmation — required before writing the release notes or creating the Release.** The GitHub Release is a public-surface action; the confirmation from Step 5 authorized the promotion, but the public publish gets its own explicit confirmation. Tell the user (substitute the actual tag, e.g. `v0.13.0`):

> Publishing GitHub Release `<version-tag>` — the final public step. Confirm by pasting: `publish <version-tag>`

Wait for the user to paste `publish <version-tag>` (or an explicit version-named variant such as `ship <version-tag>`). Any other response → stop and ask what they'd like to change. The version-named phrase is deliberate: Claude Code's auto-mode safety classifier requires authorization that names the release before `gh release create` runs, so the generic Step 5 confirmation is not relied on for the public publish. If a harness still blocks the call after this confirmation (e.g. an older client), the user can re-confirm with `publish <version-tag> release` to unblock.

The version tag and PR/issue list are already known; the previous tag comes from Step 4 (if there is no previous tag, omit the "Full changelog" line). Create a temp directory if you haven't already (`python3 CodeCannon/skills/github-agile/scripts/make-workdir.py`), then use your file-writing tool (not Bash) to create `<tmpdir>/release_notes.md`:

```markdown
## Changes

- #<issue> — <PR title> (PR #<pr-number>)
[... one line per PR included in this release ...]

**Full changelog:** https://github.com/<owner>/<repo>/compare/<previous-tag>...<version-tag>
```

Format each PR line as `- #<linked-issue> — <PR title> (PR #<N>)`; if a PR had no linked issue, use just the PR title. Then create the release (do NOT use `--notes`, `--notes-file -`, or heredocs):

```bash
gh release create <version-tag> \
  --title "<version-tag>" \
  --notes-file <tmpdir>/release_notes.md
```

Note the release URL from the output.

---

## Step 8 — Report

Tell the user: "Released vX.Y.Z. Linked issues are closed. GitHub Release vX.Y.Z created at `<url>`. Run `make deploy-prod` to ship to production."
<!-- generated by CodeCannon/sync.py | skill: deploy | adapter: agents | hash: c3910377 | DO NOT EDIT — run CodeCannon/sync.py to regenerate -->
