# /setup

First-run onboarding and configuration walkthrough.

**Source prompt:** [`../../skills/github-agile/setup.md`](../../skills/github-agile/setup.md)

## What it does

`/setup` detects the current state of Code Cannon in your project and guides you through configuration. It handles three scenarios:

1. **You're in the Code Cannon repo itself** — explains how Code Cannon works and offers to help you add it to your project.
2. **Partial setup** — the submodule exists but something is missing (sync.py not initialized, `gh` not installed, config file missing, etc.). Walks through fixes one at a time.
3. **Fully configured** — runs a health check, configures commit signing if needed, offers to populate labels from your GitHub repo, and walks through optional config values.

## Usage

```
/setup
```

No arguments. Run it in any project where Code Cannon is (or should be) installed.

## What it checks

When the submodule is present, `/setup` runs these checks in order, stopping at the first failure:

1. **`CodeCannon/sync.py` present** — submodule initialized?
2. **`gh` installed** — GitHub CLI available?
3. **`gh` authenticated** — logged in to GitHub?
4. **Inside a GitHub repo** — remote configured?
5. **`.codecannon.yaml` present** — project config exists?
6. **Config values valid** — do branch names and commands point to real things?
7. **Skills directories populated** — has sync been run?

Each check explains the problem and shows the fix command.

## Profile selection

When creating `.codecannon.yaml` for the first time, `/setup` asks you to pick a workflow profile:

| Profile | Branch model | AI review | QA | What it configures |
|---|---|---|---|---|
| **Lightweight** | Trunk | Advisory | Off | `BRANCH_PROD`, `REVIEW_GATE: advisory` |
| **Standard** | Two-branch | AI-gated | Optional | `BRANCH_PROD`, `BRANCH_DEV`, `REVIEW_GATE: ai` |
| **Governed** | Two/three-branch | AI-gated | On | All branch/QA settings |
| **Custom** | Manual | Manual | Manual | Opens config for manual editing |

Profiles are starting points — every setting is independently configurable afterward.

## Commit signing

After the health summary, `/setup` checks whether `commit.gpgsign` is already enabled in your git config (local or global). If not, it asks whether your project requires signed commits.

If yes, it verifies a signing key is configured and enables `commit.gpgsign` and `tag.gpgsign` at the repo level. If no signing key exists, it detects the signing format (SSH or GPG) and guides you through providing one.

This is a git-level configuration — no changes to `.codecannon.yaml` or skill templates are needed. All skills that create commits or tags benefit automatically.

## Label population

Once fully configured, `/setup` fetches your repo's existing GitHub labels and offers to write them to `TICKET_LABELS` in `.codecannon.yaml`. This populates the label pool that `/start` uses when creating issues.

You can select all labels or pick specific ones from the list.

## Optional config walkthrough

After label population, `/setup` walks through unset optional config values appropriate for your profile:

- **Lightweight:** `PLATFORM_COMPLIANCE_NOTES`, `CONVENTIONS_NOTES`
- **Standard:** adds `DEFAULT_REVIEWERS`, `TICKET_LABEL_CREATION_ALLOWED`
- **Governed/Custom:** adds `DEFAULT_MILESTONE`, plus all Standard values

For `PLATFORM_COMPLIANCE_NOTES`, it asks about your tech stack and drafts rules based on common violations for those technologies. For `CONVENTIONS_NOTES`, it asks about non-obvious team rules and shapes them into checkable review rules.

Every write requires explicit confirmation.

## Why it's built this way

**Progressive disclosure.** `/setup` only shows you what's relevant to your current state. A new project sees the profile picker. A configured project sees the health check and optional settings. You never have to wade through the full config schema.

**Stop-at-first-failure.** Each check builds on the previous one. There's no point checking config values if `gh` isn't installed. Stopping early keeps the fix list actionable.

**Profile-aware walkthrough.** The optional config walkthrough skips settings that don't apply to your profile. A Lightweight setup doesn't need QA labels or milestone config.

**Draft assistance for review rules.** Rather than asking you to write compliance and convention rules from scratch, `/setup` asks about your stack and drafts rules based on common patterns. You review and edit before anything is written.

## Config keys used

`/setup` can read and write all config keys. It's the only skill that modifies `.codecannon.yaml`.
