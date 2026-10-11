# Code Cannon Documentation

Code Cannon is a portable agent workflow skill library. Write your team's development workflow once — start, submit-for-review, review, deploy — and sync it to Claude Code, Cursor, and other AI coding agents across all your projects.

## How it works

1. **Skills** live in `skills/<group>/` as plain markdown with `{{PLACEHOLDER}}` tokens for project-specific values. Each `<group>` directory is a domain-specific bundle (e.g. `github-agile`).
2. **Your project picks one group** via `skill_group:` in `.codecannon.yaml`.
3. **`sync.py`** reads the config, walks only the enabled group, substitutes values, and writes each skill as a standard [Agent Skills](https://agentskills.io) folder (`.claude/skills/<name>/SKILL.md`, `.agents/skills/<name>/SKILL.md`) — no group prefix.
4. **Generated files** carry a hash so sync.py can detect manual edits and warn before overwriting.

## The workflow

The intended sequence for a complete change:

```
/start  →  [code + local test]  →  /submit-for-review  →  [QA on preview]  →  /deploy
```

- [`/start`](skills/start.md) — reads code, proposes an approach, **waits for human approval**, then creates the issue, branch, and writes code
- [`/submit-for-review`](skills/submit-for-review.md) — runs checks, commits everything, pushes, opens the PR, spawns an agent review, merges if approved
- [`/review`](skills/review.md) — standalone review on any PR; also called internally by `/submit-for-review`
- [`/deploy`](skills/deploy.md) — bumps version, creates a GitHub Release, promotes to production
- [`/status`](skills/status.md) — read-only snapshot of open PRs, merged work, and open issues
- [`/qa`](skills/qa.md) — view the QA queue or record findings on a specific issue
- [`/setup`](skills/setup.md) — first-run onboarding: check config, labels, and milestone setup

## Human gates

Code Cannon is opinionated about where humans stay in the loop:

- `/start` pauses before creating the issue to confirm the implementation approach.
- `/submit-for-review` pauses after review if there are non-blocking findings, letting you choose to address them or merge now.
- `/deploy` requires an explicit "release" confirmation before promoting to production.
- `/qa` shows the review comment and waits for approval before posting.

The agent commits; you test. `/start` writes code but does not commit — it hands off to you with "run your dev command and test locally." Committing happens in `/submit-for-review`. The human approval loop before shipping is where you catch things the agent missed.

## Quickstart

**Requires Python 3.8+** (stdlib only — no pip install needed).

### 1. Add Code Cannon as a submodule

```bash
git submodule add https://github.com/LightbridgeLab/CodeCannon.git CodeCannon
git submodule update --init
```

### 2. Create your project config

```bash
cp CodeCannon/templates/codecannon.yaml .codecannon.yaml
```

Edit `.codecannon.yaml`:

- Set `skill_group:` to the bundle you want (currently `github-agile`; more groups are planned).
- Configure branch names, check command, deploy commands, and which adapters to generate.

See the [config reference](config-reference.md) for all available settings. Exactly one skill group per project — sync errors out if `skill_group` is missing or names a group that doesn't exist.

### 3. Run sync

```bash
CodeCannon/sync.py
```

This generates skill folders for each adapter listed in your config. For Claude Code, that's `.claude/skills/<name>/SKILL.md`. For Codex CLI, Cursor, and Gemini CLI, `.agents/skills/<name>/SKILL.md`.

### 4. Copy AGENTS.md template (optional)

```bash
cp CodeCannon/templates/AGENTS.md.template AGENTS.md
```

Edit the project-specific section at the bottom.

### 5. Add Makefile targets (optional)

```makefile
# In your Makefile
include CodeCannon/Makefile.agents.mk
```

Or copy the targets from `Makefile.agents.mk` directly.

### 6. Commit and share

Commit `.codecannon.yaml`, `AGENTS.md`, and the generated `.claude/` directory. Every teammate gets a working installation on `git clone` + `git submodule update --init` — no further setup needed.

`.codecannon.yaml` is a team contract, not personal config. Changes to it should be reviewed like any other config change.

Or skip all of this and run `/setup` for a guided walkthrough.

## Keeping skills up to date

```bash
git submodule update --remote CodeCannon   # pull latest skills
CodeCannon/sync.py                         # regenerate skill files
```

If any generated files have been manually customized, sync.py will warn and skip them. Use `--force` to overwrite.

## Migrating from the old `agentgate` submodule

If your project still uses the previous repo URL or folder name:

1. Point `.gitmodules` at `https://github.com/LightbridgeLab/CodeCannon.git` and use submodule path `CodeCannon/` (or rename your existing checkout to match).
2. Rename `.agentgate.yaml` to `.codecannon.yaml`.
3. Run `CodeCannon/sync.py --force` once if needed so generated file headers match the new provenance marker.

## Agent tips and tweaks

Code Cannon skills are agent-agnostic, but each agent has its own quirks. This section collects per-agent configuration tips that smooth out the experience.

### Claude Code

**Reduce approval prompts.** Claude Code prompts for permission on unfamiliar shell commands. Pre-approve the commands Code Cannon uses by adding a `permissions` block to `.claude/settings.json` (project-level, committed) or `.claude/settings.local.json` (personal, git-ignored):

```json
{
  "permissions": {
    "defaultMode": "acceptEdits",
    "allow": [
      "Bash(git:*)",
      "Bash(gh:*)",
      "Bash(make:*)",
      "Bash(python3:*)",
      "Bash(./sync.py:*)",
      "Bash(mkdir:*)",
      "Bash(mktemp:*)"
    ],
    "deny": [
      "Bash(git push --force)",
      "Bash(git push --force:*)",
      "Bash(git reset --hard)",
      "Bash(git reset --hard:*)",
      "Bash(rm -rf:*)",
      "Bash(sudo:*)"
    ]
  }
}
```

`defaultMode: "acceptEdits"` auto-approves file edits and common filesystem ops. `allow` rules pre-approve matching bash commands (wildcards supported). `deny` rules always win — dangerous operations still prompt. Adjust the `allow` list to match your project's tooling. (`Bash(cd:*)` is intentionally omitted — Code Cannon skills use single, statically-analyzable commands, and blessing `cd` would re-invite the compound `cd … && …` shape the allowlist is designed to avoid; `/setup`'s permission audit will not ask for it.)

### Cursor

Cursor does not prompt for shell commands by default, so no permission configuration is needed. Skills work out of the box.

### Codex

Configure via the `codex` CLI's `--full-auto` flag or sandbox settings. Codex runs in a sandboxed environment, so approval prompts are less common but network access may need to be explicitly enabled for `gh` commands.

### Gemini CLI

Configure via `.gemini/settings.json` or the `--auto-approve` flag. Consult Gemini CLI documentation for the latest permission options.

## Further reading

- [Branching models](branching.md) — trunk, two-branch, and three-branch workflows explained
- [Customization guide](customization.md) — how to tailor skills to your project
- [Config reference](config-reference.md) — every `.codecannon.yaml` placeholder documented
- [Adapters](adapters.md) — supported agents and how to add new ones
- Individual skill documentation — see links in the workflow section above
