# /deploy

Bump the project version, create a GitHub Release, and promote to production.

**Source prompt:** [`../../skills/github-agile/deploy.md`](../../skills/github-agile/deploy.md)

## What it does

`/deploy` is the final step in the workflow. It combines version bumping and release creation into a single command. Its behavior varies by branching model:

- **Trunk mode:** Optionally bumps the version, then creates a GitHub Release from `BRANCH_PROD` using the version tag.
- **Two-branch mode:** Optionally bumps the version, opens a promotion PR into `BRANCH_PROD` from a throwaway `release/vX.Y.Z` branch cut at the head of `BRANCH_DEV`, merges it (which auto-closes linked issues), and creates a GitHub Release.
- **Three-branch mode:** Same as two-branch but promotes from `BRANCH_TEST` to `BRANCH_PROD`.

## Usage

```
/deploy
```

No arguments. Must be run from the correct branch (determined by your branching model).

## Step-by-step

1. **Verify branch** — checks that you're on the required pre-production branch:
   - **Trunk mode:** `BRANCH_PROD`
   - **Two-branch mode:** `BRANCH_DEV`
   - **Three-branch mode:** `BRANCH_TEST`

   Aborts if on the wrong branch.

2. **Check current state** — finds the latest version tag, reads the current version, shows commits/PRs since the last tag, and lists any open unmerged PRs.

3. **Ask about version bump** — presents bump options with computed values:
   - **major** (e.g. 1.2.3 -> 2.0.0)
   - **minor** (e.g. 1.2.3 -> 1.3.0)
   - **patch** (e.g. 1.2.3 -> 1.2.4)
   - set a specific version
   - or skip (proceed with existing tag)

4. **Version bump** (if requested) — runs the appropriate bump command (`BUMP_PATCH_CMD`, `BUMP_MINOR_CMD`, `BUMP_MAJOR_CMD`, or `SET_VERSION_CMD`), then pushes the commit and tag.

5. **Compute release contents** — finds merge commits since the previous tag, extracts linked PRs and issues.

6. **Human gate** — shows a release summary and waits for you to type "release".

7. **Create GitHub Release** (trunk mode) or **Create promotion PR, merge, then create GitHub Release** (multi-branch modes). Creating the public Release has its own confirmation: you paste `publish vX.Y.Z` (the actual version) right before it publishes.

## Why it's built this way

**Version bump and release in one flow.** Previously these were separate `/version` and `/release` commands. Combining them reduces the workflow from six commands to five and eliminates the possibility of bumping a version but forgetting to release, or vice versa. The version bump step is optional — if you've already tagged, you can skip straight to release.

**Human gate is mandatory.** The "type release to confirm" gate exists because promoting to production is irreversible in practice. The summary gives you one last chance to verify that the right changes are going out.

**The public Release has a second, version-named gate.** Single-word `release` authorizes the promotion and merge, but creating the public GitHub Release asks you to paste `publish vX.Y.Z` (with the real version). Two reasons: (1) it names the exact version you're publishing, so the authorization is unambiguous in the audit trail; and (2) Claude Code's auto-mode safety classifier treats publishing a Release as a public-surface action that needs authorization naming the release — a bare word doesn't satisfy it. Making this an expected, designed step means the publish never stops as a surprise mid-flow after the merge has already landed. If a harness still blocks after you confirm, re-paste `publish vX.Y.Z release` to unblock.

**Issues close on release, not on feature merge.** In multi-branch modes, feature PRs use `Closes #N`, but the keyword is inert until it reaches the default branch — so issues only auto-close when the promotion PR merges to `BRANCH_PROD`. This means issues stay open through the QA/staging phase, giving you visibility into what's deployed where. `/deploy` parses each constituent PR's `Closes #N` lines into a **close set** (reproduced verbatim into the promotion PR, so nothing is dropped) and keeps `Related to #N` / legacy `Issue #N` references in a separate **reference set** that the human gate lists as "referenced but not closing" — letting you catch any straggler that should have closed.

**The promotion PR comes from a throwaway branch.** GitHub's "Automatically delete head branches" setting deletes a merged PR's head branch. If the promotion PR used `BRANCH_DEV` (or `BRANCH_TEST`) as its head, every release would delete that long-lived branch from the remote unless branch protection blocked it — and protection isn't available on every plan. So `/deploy` pushes `release/vX.Y.Z` at the deploy branch's head, promotes from that, and removes it afterwards. The long-lived branch is never a PR head, so the setting is safe to leave on.

**State check surfaces surprises early.** Showing open unmerged PRs at the start prevents accidental releases that miss in-flight work.

**Bump commands are external.** Code Cannon doesn't implement version bumping itself — it delegates to your project's Makefile or scripts. This keeps the skill generic across different version file formats (package.json, VERSION file, setup.py, etc.).

## Config keys used

- `VERSION_READ_CMD` — prints the current version
- `BUMP_PATCH_CMD`, `BUMP_MINOR_CMD`, `BUMP_MAJOR_CMD` — semver bump commands
- `SET_VERSION_CMD` — set an arbitrary version
- `DEPLOY_PREVIEW_CMD` — suggested for deploying to preview after version bump
- `DEPLOY_PROD_CMD` — suggested after release for deploying to production
- `BRANCH_PROD`, `BRANCH_DEV`, `BRANCH_TEST` — determine mode and promotion path
- `MERGE_CMD` — NOT used for promotion merges (uses `gh pr merge` directly)
