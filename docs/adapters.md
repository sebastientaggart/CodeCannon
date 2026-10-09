# Adapters

Code Cannon skills follow the [Agent Skills](https://agentskills.io) open standard: each skill is a `<skill-name>/SKILL.md` folder with YAML frontmatter (`name`, `description`). Adapters no longer translate between per-tool formats — they just decide which standard skills directories to render into.

Spec compliance is enforced on every sync path, not just under `--validate`: if a skill's frontmatter `name` is missing, violates the spec, or doesn't match its directory name — or its `description` is missing — `sync.py` stops with a non-zero exit before writing anything. This keeps a rename on one side of the pair from silently shipping mismatched output to consumer repos. Entries declaring `output_path_override` are exempt, since they render as bare files with no frontmatter and so aren't skills in the spec sense — but only when the override resolves to a real path. If its placeholder is set to an empty value the entry renders as an ordinary skill and is held to the spec like any other; if the placeholder is undefined entirely, that is reported as its own error rather than exempted. The write path enforces this over the group being synced — those are the skills that would produce bad output. Run from inside the Code Cannon repo itself, `--validate` widens the audit to every skill group, so a name or description violation in a group nobody has turned on still fails CI, which runs `--validate`. A work-in-progress skill in an unrelated group therefore doesn't block a local sync. Override placeholders resolve against the enabled group's config, so a non-enabled group's `output_path_override` is checked only for its raw value; an undefined placeholder in one surfaces when that group is enabled.

## Supported adapters

| Adapter | Output | Read natively by |
|---|---|---|
| `claude` | `.claude/skills/<name>/SKILL.md` | Claude Code |
| `agents` | `.agents/skills/<name>/SKILL.md` | Codex CLI, Cursor, Gemini CLI |

The legacy adapter names `codex`, `cursor`, and `gemini` still work — each is an alias that resolves to `agents`, because all three tools read the shared `.agents/skills/` directory natively (Gemini CLI even gives it precedence over `.gemini/skills/`). Listing any combination of the legacy names renders the `agents` output once.

### Claude Code

The `claude` adapter renders skills into `.claude/skills/`. Claude Code exposes each as a `/skill-name` command and can also load it automatically by description. This adapter additionally emits the `argument-hint` frontmatter extension (from the skill's `args` field) and maintains the committed permission allowlist in `.claude/settings.json`. The `/submit-for-review` skill can spawn a review sub-agent natively.

### Codex CLI, Cursor, Gemini CLI (`agents`)

The `agents` adapter renders skills into `.agents/skills/`, the cross-tool location all three read. Output sticks to spec-standard frontmatter only (`name`, `description`). Skills are triggered by description matching, or by name where the tool supports it.

**Limitation:** these tools do not support sub-agent spawning. The review step in `/submit-for-review` (which spawns a separate review agent) must be performed manually by pasting the review-agent prompt into a new session.

## Upgrading from the pre-standard layout

Sync does not prune output directories it no longer generates. If your project synced before the Agent Skills migration, delete the retired outputs once, re-run sync, and commit:

```bash
git rm -r --ignore-unmatch .claude/commands .cursor/rules .gemini/skills
CodeCannon/sync.py
```

Leaving the old files in place is mostly harmless (Claude Code prefers a skill over a same-named command file), but they will drift from the source skills over time.

## Enabling adapters

List the adapters you want in `.codecannon.yaml`:

```yaml
adapters:
  - claude
  - agents
```

Run `sync.py` to generate the skill folders for all listed adapters. They write to different directories and don't conflict.

## How adapters work

Each adapter lives in `adapters/<name>/` with a single `config.yaml`:

- **`output_directory`** — the skills directory generated files go into
- **`argument_hint`** — whether to emit the Claude Code `argument-hint` frontmatter extension
- **`permissions_file`** — optional path for a committed permission allowlist
- **`alias`** — alternatively, resolve this adapter name to another adapter

Skill bodies still go through the Code Cannon rendering pipeline before output: `{{#if}}` conditional blocks are evaluated and `{{PLACEHOLDER}}` tokens are substituted from `.codecannon.yaml`. That per-project rendering is what `sync.py` exists for — the output format itself is just the standard.

## Adding a new adapter

Most tools that support Agent Skills read `.agents/skills/` already, so start by checking whether the `agents` adapter covers the tool. If it reads only its own directory:

1. Create `adapters/<name>/config.yaml`:
   ```yaml
   agent: <name>
   description: <one-line description>
   output_directory: <the tool's project skills directory>
   argument_hint: false
   ```

2. Test with `sync.py --skill start` against a test project.

3. Document any adapter limitations in `config.yaml` under `notes`.

4. Add the adapter name to `adapters:` in `.codecannon.yaml` for any project that should use it.
