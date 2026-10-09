"""Tests for CodeCannon sync.py — the sync engine."""

import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

# Add the repo root to the path so we can import sync as a module.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import sync


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_skill(tmpdir, name, frontmatter, body):
    """Write a skill into tmpdir/skills/<name>/SKILL.md (Agent Skills layout)."""
    skill_dir = Path(tmpdir) / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    content = f"---\n{frontmatter}\n---\n\n{body}"
    path.write_text(content)
    return path


def _make_args(**overrides):
    """Return a minimal argparse-like namespace for sync_skill."""
    defaults = {"force": False, "dry_run": False}
    defaults.update(overrides)
    return type("Args", (), defaults)()


# ═══════════════════════════════════════════════════════════════════════════════
# YAML PARSING
# ═══════════════════════════════════════════════════════════════════════════════


class TestDequote(unittest.TestCase):

    def test_double_quoted(self):
        self.assertEqual(sync._dequote('"hello world"'), "hello world")

    def test_single_quoted(self):
        self.assertEqual(sync._dequote("'hello world'"), "hello world")

    def test_unquoted(self):
        self.assertEqual(sync._dequote("hello"), "hello")

    def test_double_quoted_escape_quote(self):
        self.assertEqual(sync._dequote(r'"say \"hi\""'), 'say "hi"')

    def test_double_quoted_escape_backslash(self):
        self.assertEqual(sync._dequote(r'"back\\slash"'), "back\\slash")

    def test_single_quoted_no_escapes(self):
        self.assertEqual(sync._dequote(r"'no \" escapes'"), r'no \" escapes')

    def test_empty_double_quotes(self):
        self.assertEqual(sync._dequote('""'), "")

    def test_empty_single_quotes(self):
        self.assertEqual(sync._dequote("''"), "")

    def test_single_char(self):
        self.assertEqual(sync._dequote("x"), "x")

    def test_empty_string(self):
        self.assertEqual(sync._dequote(""), "")


class TestParseYamlSimple(unittest.TestCase):

    def test_flat_key_value(self):
        text = "name: Alice\nage: 30"
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result, {"name": "Alice", "age": "30"})

    def test_nested_dict(self):
        text = "config:\n  FOO: bar\n  BAZ: qux"
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result, {"config": {"FOO": "bar", "BAZ": "qux"}})

    def test_list(self):
        text = "adapters:\n  - claude\n  - cursor"
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result, {"adapters": ["claude", "cursor"]})

    def test_comments_and_blanks_ignored(self):
        text = "# comment\n\nkey: value\n  # nested comment"
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result, {"key": "value"})

    def test_quoted_values(self):
        text = 'config:\n  MSG: "hello world"\n  OTHER: \'single\''
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result["config"]["MSG"], "hello world")
        self.assertEqual(result["config"]["OTHER"], "single")

    def test_empty_value_creates_dict(self):
        text = "config:"
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result, {"config": {}})

    def test_mixed_list_after_dict_key(self):
        """If a key starts as a dict, then encounters a list item, it converts."""
        text = "items:\n  - alpha\n  - beta"
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result["items"], ["alpha", "beta"])

    def test_block_scalar_nested(self):
        """Literal block scalars (|) on nested keys, as the template documents
        for PLATFORM_COMPLIANCE_NOTES / CONVENTIONS_NOTES."""
        text = (
            "config:\n"
            "  NOTES: |\n"
            "    - first rule\n"
            "    - second rule\n"
            "  AFTER: yes\n"
        )
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result["config"]["NOTES"], "- first rule\n- second rule\n")
        self.assertEqual(result["config"]["AFTER"], "yes")

    def test_block_scalar_does_not_clobber_parent_dict(self):
        """Regression: dash-prefixed block content must not convert the
        enclosing config dict into a list."""
        text = (
            "config:\n"
            "  KEY: value\n"
            "  NOTES: |\n"
            "    - a dashed line\n"
        )
        result = sync.parse_yaml_simple(text)
        self.assertIsInstance(result["config"], dict)
        self.assertEqual(result["config"]["KEY"], "value")

    def test_block_scalar_chomping_and_content(self):
        """|- strips the trailing newline; # and blank lines are content."""
        text = (
            "config:\n"
            "  A: |-\n"
            "    line one\n"
            "\n"
            "    # not a comment\n"
        )
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result["config"]["A"], "line one\n\n# not a comment")

    def test_block_scalar_top_level(self):
        text = "notes: |\n  hello\n  world\nother: x"
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result["notes"], "hello\nworld\n")
        self.assertEqual(result["other"], "x")

    def test_block_scalar_at_eof(self):
        text = "config:\n  NOTES: |\n    last line"
        result = sync.parse_yaml_simple(text)
        self.assertEqual(result["config"]["NOTES"], "last line\n")


class TestLoadSchemaDefaults(unittest.TestCase):
    """Tests for load_schema_defaults(), which reads placeholder defaults
    out of config.schema.yaml so main() never has to hardcode them.
    """

    def test_simple_and_block_scalar_defaults(self):
        schema_text = (
            "top_level:\n"
            "\n"
            "  skill_group:\n"
            "    description: not a placeholder default\n"
            "    default: should-not-appear\n"
            "\n"
            "placeholders:\n"
            "\n"
            "  BRANCH_PROD:\n"
            "    description: prod branch\n"
            "    default: \"main\"\n"
            "    category: branches\n"
            "\n"
            "  EMPTY_DEFAULT:\n"
            "    description: intentionally blank\n"
            "    default: \"\"\n"
            "\n"
            "  MULTI_LINE:\n"
            "    description: a list\n"
            "    default: |\n"
            "      - one\n"
            "      - two\n"
            "    category: review\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "config.schema.yaml"
            schema_path.write_text(schema_text)
            result = sync.load_schema_defaults(schema_path)

        self.assertEqual(result["BRANCH_PROD"], "main")
        self.assertEqual(result["EMPTY_DEFAULT"], "")
        self.assertEqual(result["MULTI_LINE"], "- one\n- two\n")
        self.assertNotIn("skill_group", result)

    def test_default_with_inline_comment(self):
        """A trailing ` # comment` after a quoted default must not corrupt the value.
        Regression test: previously `default: "main"  # note` parsed to the literal
        string with quotes and comment still attached, since the comment was never
        stripped before _dequote() (which only strips quotes at the exact string ends).
        """
        schema_text = (
            "placeholders:\n"
            "\n"
            "  BRANCH_PROD:\n"
            "    default: \"main\"  # matches templates/codecannon.yaml\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "config.schema.yaml"
            schema_path.write_text(schema_text)
            result = sync.load_schema_defaults(schema_path)
        self.assertEqual(result["BRANCH_PROD"], "main")

    def test_key_line_with_inline_comment(self):
        """A trailing ` # comment` on a placeholder key line must not hide the key.
        Regression test: previously `FOO:  # note` failed the strict `endswith(':')`
        check, so current_key was never set and the following default: line — guarded
        on `current_key` — was silently skipped, dropping the default entirely.
        """
        schema_text = (
            "placeholders:\n"
            "\n"
            "  FOO:  # a note\n"
            "    default: \"bar\"\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "config.schema.yaml"
            schema_path.write_text(schema_text)
            result = sync.load_schema_defaults(schema_path)
        self.assertEqual(result.get("FOO"), "bar")

    def test_real_schema_defines_stale_days(self):
        """Regression test for #194: STALE_DAYS must have a schema default."""
        result = sync.load_schema_defaults(REPO_ROOT / "config.schema.yaml")
        self.assertEqual(result.get("STALE_DAYS"), "14")

    def test_qa_labels_default_empty_to_preserve_opt_out(self):
        """QA_READY_LABEL/QA_PASSED_LABEL/QA_FAILED_LABEL must default to "" — they
        gate {{#if}} sections in qa.md / submit-for-review.md that templates/codecannon.yaml
        documents as "leave empty to disable". A non-empty schema default would flip
        those sections on for every project that never configured QA labeling.
        """
        result = sync.load_schema_defaults(REPO_ROOT / "config.schema.yaml")
        self.assertEqual(result.get("QA_READY_LABEL"), "")
        self.assertEqual(result.get("QA_PASSED_LABEL"), "")
        self.assertEqual(result.get("QA_FAILED_LABEL"), "")

    def test_real_schema_all_placeholders_have_defaults(self):
        """Every entry under placeholders: in the real schema should parse a default."""
        result = sync.load_schema_defaults(REPO_ROOT / "config.schema.yaml")
        schema_text = (REPO_ROOT / "config.schema.yaml").read_text()
        # Only count keys under `placeholders:` (after the top_level: block ends)
        placeholders_start = schema_text.index("placeholders:")
        placeholder_names = re.findall(
            r"^  ([A-Z_]+):$", schema_text[placeholders_start:], re.MULTILINE)
        self.assertTrue(placeholder_names, "fixture sanity check: schema should list placeholders")
        for name in placeholder_names:
            self.assertIn(name, result, f"{name} has no parsed default")


class TestParseFrontmatter(unittest.TestCase):

    def test_basic_frontmatter(self):
        text = "---\nskill: deploy\ntype: skill\n---\n\nBody content here."
        fm, body = sync.parse_frontmatter(text)
        self.assertEqual(fm["skill"], "deploy")
        self.assertEqual(fm["type"], "skill")
        self.assertEqual(body, "Body content here.")

    def test_no_frontmatter(self):
        text = "Just a body with no frontmatter."
        fm, body = sync.parse_frontmatter(text)
        self.assertEqual(fm, {})
        self.assertEqual(body, "Just a body with no frontmatter.")

    def test_quoted_description(self):
        text = '---\ndescription: "My cool skill"\n---\n\nBody.'
        fm, body = sync.parse_frontmatter(text)
        self.assertEqual(fm["description"], "My cool skill")

    def test_inline_list_in_frontmatter(self):
        text = "---\ntags: [foo, bar, baz]\n---\n\nBody."
        fm, body = sync.parse_frontmatter(text)
        self.assertEqual(fm["tags"], ["foo", "bar", "baz"])

    def test_body_stripped(self):
        text = "---\nk: v\n---\n\n  Body with leading space.  \n\nTrailing."
        fm, body = sync.parse_frontmatter(text)
        self.assertIn("Body with leading space.", body)


# ═══════════════════════════════════════════════════════════════════════════════
# CONDITIONAL BLOCKS
# ═══════════════════════════════════════════════════════════════════════════════


class TestApplyConditionals(unittest.TestCase):

    def test_truthy_keeps_block(self):
        text = "before\n{{#if FOO}}\nkept\n{{/if}}\nafter"
        result = sync.apply_conditionals(text, {"FOO": "yes"})
        self.assertIn("kept", result)
        self.assertIn("before", result)
        self.assertIn("after", result)
        self.assertNotIn("{{#if", result)
        self.assertNotIn("{{/if}}", result)

    def test_leading_underscore_key_is_a_directive(self):
        """A key _IF_OPEN doesn't match fails to open a block, stranding its
        {{/if}} and stopping conditional processing for the whole file. #221."""
        text = "before\n{{#if _INTERNAL}}\nkept\n{{/if}}\nafter"
        result = sync.apply_conditionals(text, {"_INTERNAL": "yes"})
        self.assertIn("kept", result)
        self.assertNotIn("{{#if", result)
        self.assertNotIn("{{/if}}", result)

    def test_unmatched_close_warns(self):
        """Stopping on a stray {{/if}} leaves every later conditional in the
        file unprocessed — that must not happen silently at exit 0. #221."""
        text = "{{/if}}\n{{#if FOO}}\nbody\n{{/if}}"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sync.apply_conditionals(text, {"FOO": "yes"})
        self.assertIn("no matching {{#if}}", buf.getvalue())

    def test_digit_bearing_key_is_a_directive(self):
        """_IF_OPEN's charset must stay in step with find_unresolved: a key one
        matches and the other doesn't is neither expanded nor reported, so the
        literal directive line ships into generated output. #221."""
        text = "before\n{{#if FOO2}}\nkept\n{{/if}}\nafter"
        result = sync.apply_conditionals(text, {"FOO2": "yes"})
        self.assertIn("kept", result)
        self.assertNotIn("{{#if", result)

    def test_falsy_removes_block(self):
        text = "before\n{{#if FOO}}\nremoved\n{{/if}}\nafter"
        result = sync.apply_conditionals(text, {"FOO": ""})
        self.assertNotIn("removed", result)
        self.assertIn("before", result)
        self.assertIn("after", result)

    def test_missing_key_is_falsy(self):
        text = "{{#if MISSING}}\nhidden\n{{/if}}\nvisible"
        result = sync.apply_conditionals(text, {})
        self.assertNotIn("hidden", result)
        self.assertIn("visible", result)

    def test_negated_truthy_removes_block(self):
        text = "{{#if !FOO}}\nhidden\n{{/if}}\nvisible"
        result = sync.apply_conditionals(text, {"FOO": "yes"})
        self.assertNotIn("hidden", result)
        self.assertIn("visible", result)

    def test_negated_falsy_keeps_block(self):
        text = "{{#if !FOO}}\nkept\n{{/if}}\nvisible"
        result = sync.apply_conditionals(text, {"FOO": ""})
        self.assertIn("kept", result)

    def test_boolean_false_string_is_falsy(self):
        for val in ("false", "False", "FALSE", "no", "No", "0"):
            text = "{{#if FLAG}}\nshown\n{{/if}}"
            result = sync.apply_conditionals(text, {"FLAG": val})
            self.assertNotIn("shown", result, f"Expected '{val}' to be falsy")

    def test_nested_conditionals(self):
        text = textwrap.dedent("""\
            {{#if OUTER}}
            outer-start
            {{#if INNER}}
            inner-content
            {{/if}}
            outer-end
            {{/if}}""")
        result = sync.apply_conditionals(text, {"OUTER": "yes", "INNER": "yes"})
        self.assertIn("outer-start", result)
        self.assertIn("inner-content", result)
        self.assertIn("outer-end", result)

    def test_nested_outer_false_removes_all(self):
        text = textwrap.dedent("""\
            {{#if OUTER}}
            outer-start
            {{#if INNER}}
            inner-content
            {{/if}}
            outer-end
            {{/if}}""")
        result = sync.apply_conditionals(text, {"OUTER": "", "INNER": "yes"})
        self.assertNotIn("outer-start", result)
        self.assertNotIn("inner-content", result)

    def test_multiple_independent_blocks(self):
        text = "{{#if A}}\nalpha\n{{/if}}\n{{#if B}}\nbeta\n{{/if}}"
        result = sync.apply_conditionals(text, {"A": "yes", "B": ""})
        self.assertIn("alpha", result)
        self.assertNotIn("beta", result)

    def test_malformed_no_open_tag(self):
        """A lone {{/if}} should not crash — processing just stops."""
        text = "content\n{{/if}}\nmore"
        result = sync.apply_conditionals(text, {})
        # The malformed block stops processing; the text is returned as-is
        self.assertIn("content", result)


# ═══════════════════════════════════════════════════════════════════════════════
# DEPLOY SKILL — BRANCH-MODE RENDERING
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeployModeRendering(unittest.TestCase):
    """Regression tests for #206: deploy.md's release steps were collapsed from three
    near-identical per-mode copies into one shared path. These lock in that each of the
    three branching modes still renders correctly — no leftover directives, the trunk
    path has no promotion PR/merge, and the shared release mechanics appear exactly once.
    """

    MODES = {
        "trunk":        {"BRANCH_PROD": "main", "BRANCH_DEV": "",    "BRANCH_TEST": ""},
        "two-branch":   {"BRANCH_PROD": "main", "BRANCH_DEV": "dev", "BRANCH_TEST": ""},
        "three-branch": {"BRANCH_PROD": "main", "BRANCH_DEV": "dev", "BRANCH_TEST": "test"},
    }

    @classmethod
    def setUpClass(cls):
        text = (REPO_ROOT / "skills" / "github-agile" / "deploy" / "SKILL.md").read_text()
        cls.body = text.split("---\n", 2)[2]

    def _render(self, mode):
        return sync.apply_conditionals(self.body, self.MODES[mode])

    def test_no_leftover_directives_in_any_mode(self):
        for mode in self.MODES:
            out = self._render(mode)
            self.assertNotIn("{{#if", out, f"{mode} left an #if directive")
            self.assertNotIn("{{/if}}", out, f"{mode} left a /if directive")

    def test_trunk_has_no_promotion_pr_or_merge(self):
        out = self._render("trunk")
        self.assertNotIn("gh pr create --base", out)
        self.assertNotIn("gh pr merge", out)

    def test_multibranch_has_promotion_pr_and_merge(self):
        for mode in ("two-branch", "three-branch"):
            out = self._render(mode)
            self.assertIn("gh pr create --base", out, f"{mode} missing release PR")
            self.assertIn("gh pr merge", out, f"{mode} missing merge")

    def test_release_creation_is_present_in_every_mode(self):
        # Every mode must still create the GitHub Release exactly once (as a command).
        for mode in self.MODES:
            out = self._render(mode)
            self.assertIn("gh release create <version-tag>", out, f"{mode} missing release")

    def test_publish_confirmation_appears_exactly_once(self):
        # The public-publish gate must survive the de-duplication and not be triplicated.
        for mode in self.MODES:
            out = self._render(mode)
            self.assertEqual(
                out.count("Publishing GitHub Release"), 1,
                f"{mode} should confirm the publish exactly once")

    def test_critical_unqualified_ref_note_survives_in_multibranch(self):
        # The platform-behaviour note (unqualified #N populates closingIssuesReferences)
        # is load-bearing and belongs to the promotion path only.
        self.assertEqual(self._render("trunk").count("Critical:"), 0)
        for mode in ("two-branch", "three-branch"):
            self.assertEqual(
                self._render(mode).count("Critical:"), 1,
                f"{mode} lost the unqualified-#N platform note")

    def test_step_numbering_matches_mode(self):
        trunk_steps = re.findall(r"^## Step (\d+) ", self._render("trunk"), re.M)
        multi_steps = re.findall(r"^## Step (\d+) ", self._render("two-branch"), re.M)
        # Trunk skips the promotion step, so it has one fewer numbered step.
        self.assertEqual(trunk_steps, ["1", "2", "3", "4", "5", "6", "7"])
        self.assertEqual(multi_steps, ["1", "2", "3", "4", "5", "6", "7", "8"])


# ═══════════════════════════════════════════════════════════════════════════════
# PLACEHOLDER SUBSTITUTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestApplyPlaceholders(unittest.TestCase):

    def test_basic_substitution(self):
        text = "Branch: {{BRANCH_PROD}}"
        result = sync.apply_placeholders(text, {"BRANCH_PROD": "main"})
        self.assertEqual(result, "Branch: main")

    def test_multiple_placeholders(self):
        text = "{{A}} and {{B}}"
        result = sync.apply_placeholders(text, {"A": "alpha", "B": "beta"})
        self.assertEqual(result, "alpha and beta")

    def test_repeated_placeholder(self):
        text = "{{X}} then {{X}}"
        result = sync.apply_placeholders(text, {"X": "val"})
        self.assertEqual(result, "val then val")

    def test_unresolved_left_alone(self):
        text = "{{KNOWN}} and {{UNKNOWN}}"
        result = sync.apply_placeholders(text, {"KNOWN": "ok"})
        self.assertEqual(result, "ok and {{UNKNOWN}}")

    def test_empty_values_dict(self):
        text = "nothing {{HERE}}"
        result = sync.apply_placeholders(text, {})
        self.assertEqual(result, "nothing {{HERE}}")


class TestFindUnresolved(unittest.TestCase):

    def test_finds_unresolved(self):
        text = "{{RESOLVED}} and {{MISSING}}"
        result = sync.find_unresolved(text)
        self.assertEqual(result, ["RESOLVED", "MISSING"])

    def test_no_unresolved(self):
        text = "plain text"
        result = sync.find_unresolved(text)
        self.assertEqual(result, [])

    def test_finds_key_with_digits(self):
        """Keys like PROMPT_PATH2 must be seen, or an unresolved override
        placeholder slips through as a literal output directory. #221."""
        self.assertEqual(sync.find_unresolved("{{PROMPT_PATH2}}"), ["PROMPT_PATH2"])

    def test_finds_key_with_leading_underscore(self):
        """The original charset accepted a leading underscore; the digit
        widening must not have narrowed it. #221."""
        self.assertEqual(sync.find_unresolved("{{_INTERNAL}}"), ["_INTERNAL"])

    def test_ignores_all_digit_token(self):
        """A documented regex backreference or Handlebars snippet like {{1}} is
        not a placeholder and must not fail --validate as a missing key. #221."""
        self.assertEqual(sync.find_unresolved("use {{1}} and {{0}} here"), [])

    def test_ignores_lowercase(self):
        text = "{{lowercase}}"
        result = sync.find_unresolved(text)
        self.assertEqual(result, [])

    def test_multiple_same(self):
        text = "{{A}} and {{A}}"
        result = sync.find_unresolved(text)
        self.assertEqual(result, ["A", "A"])


# ═══════════════════════════════════════════════════════════════════════════════
# HASH AND CHANGE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestContentHash(unittest.TestCase):

    def test_deterministic(self):
        self.assertEqual(sync.content_hash("hello"), sync.content_hash("hello"))

    def test_different_inputs(self):
        self.assertNotEqual(sync.content_hash("a"), sync.content_hash("b"))

    def test_length(self):
        h = sync.content_hash("test")
        self.assertEqual(len(h), 8)

    def test_matches_md5(self):
        expected = hashlib.md5("test".encode()).hexdigest()[:8]
        self.assertEqual(sync.content_hash("test"), expected)


class TestFirstLineHasSyncMarker(unittest.TestCase):

    def test_current_marker(self):
        line = f"<!-- {sync.MARKER} | skill: foo | adapter: bar | hash: abcd1234 | DO NOT EDIT -->"
        self.assertTrue(sync.first_line_has_sync_marker(line))

    def test_legacy_marker(self):
        line = "<!-- generated by CodeCannon/sync.sh | skill: foo | hash: 1234 -->"
        self.assertTrue(sync.first_line_has_sync_marker(line))

    def test_no_marker(self):
        self.assertFalse(sync.first_line_has_sync_marker("just a normal line"))

    def test_empty_line(self):
        self.assertFalse(sync.first_line_has_sync_marker(""))


class TestReadFileInfo(unittest.TestCase):

    def test_nonexistent_file(self):
        stored, body_h, is_gen, migrate = sync.read_file_info("/nonexistent/path")
        self.assertIsNone(stored)
        self.assertFalse(is_gen)

    def test_file_with_current_marker_at_end(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            body = "some content\n"
            h = sync.content_hash(body)
            marker = f"<!-- {sync.MARKER} | skill: test | adapter: claude | hash: {h} | DO NOT EDIT -->"
            f.write(body + marker + "\n")
            f.flush()
            stored, body_h, is_gen, migrate = sync.read_file_info(f.name)
        os.unlink(f.name)
        self.assertEqual(stored, h)
        self.assertEqual(body_h, h)
        self.assertTrue(is_gen)
        self.assertFalse(migrate)

    def test_file_with_legacy_marker_at_start(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            h = "abcd1234"
            marker = f"<!-- {sync.MARKER} | skill: test | adapter: claude | hash: {h} -->"
            body_lines = "body content\n"
            # Legacy: marker at first line
            f.write(marker + "\n" + body_lines)
            f.flush()
            stored, body_h, is_gen, migrate = sync.read_file_info(f.name)
        os.unlink(f.name)
        self.assertEqual(stored, h)
        self.assertTrue(is_gen)
        self.assertTrue(migrate)  # marker at start → needs migration

    def test_file_without_marker(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("plain file\nno marker\n")
            f.flush()
            stored, body_h, is_gen, migrate = sync.read_file_info(f.name)
        os.unlink(f.name)
        self.assertIsNone(stored)
        self.assertFalse(is_gen)
        self.assertFalse(migrate)

    def test_customized_file_detected(self):
        """If the body was edited after sync, body_hash != stored_hash."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            original_body = "original content\n"
            original_hash = sync.content_hash(original_body)
            marker = f"<!-- {sync.MARKER} | skill: test | adapter: claude | hash: {original_hash} | DO NOT EDIT -->"
            # Write with a modified body but the old hash in the marker
            f.write("modified content\n" + marker + "\n")
            f.flush()
            stored, body_h, is_gen, migrate = sync.read_file_info(f.name)
        os.unlink(f.name)
        self.assertEqual(stored, original_hash)
        self.assertNotEqual(body_h, stored)
        self.assertTrue(is_gen)

    def test_legacy_marker_text_detected(self):
        """Files with old marker text (sync.sh) need migration."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            body = "content\n"
            marker = "<!-- generated by CodeCannon/sync.sh | skill: test | hash: abcd1234 -->"
            f.write(body + marker + "\n")
            f.flush()
            stored, body_h, is_gen, migrate = sync.read_file_info(f.name)
        os.unlink(f.name)
        self.assertTrue(is_gen)
        self.assertTrue(migrate)  # legacy marker text


# ═══════════════════════════════════════════════════════════════════════════════
# ADAPTER LOADING
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadAdapter(unittest.TestCase):

    def test_load_claude_adapter(self):
        adapter = sync.load_adapter("claude")
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter["name"], "claude")
        self.assertEqual(adapter["output_directory"], ".claude/skills")
        self.assertTrue(adapter["argument_hint"])

    def test_load_agents_adapter(self):
        adapter = sync.load_adapter("agents")
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter["name"], "agents")
        self.assertEqual(adapter["output_directory"], ".agents/skills")
        self.assertFalse(adapter["argument_hint"])

    def test_legacy_adapter_names_alias_to_agents(self):
        for legacy in ("codex", "cursor", "gemini"):
            adapter = sync.load_adapter(legacy)
            self.assertIsNotNone(adapter, f"{legacy} should resolve via alias")
            self.assertEqual(adapter["name"], "agents")
            self.assertEqual(adapter["output_directory"], ".agents/skills")

    def test_nonexistent_adapter(self):
        adapter = sync.load_adapter("nonexistent_adapter_xyz")
        self.assertIsNone(adapter)


class TestBuildFrontmatter(unittest.TestCase):

    ADAPTER = {"name": "test", "argument_hint": False}
    HINT_ADAPTER = {"name": "test", "argument_hint": True}

    def test_emits_name_and_description(self):
        fm = {"description": "My description"}
        out = sync.build_frontmatter(self.ADAPTER, "deploy", fm)
        self.assertTrue(out.startswith("---\n"))
        self.assertIn("name: deploy\n", out)
        self.assertIn('description: "My description"\n', out)
        self.assertTrue(out.endswith("---\n\n"))

    def test_defaults_description_to_skill_name(self):
        out = sync.build_frontmatter(self.ADAPTER, "test-skill", {})
        self.assertIn('description: "test-skill"', out)

    def test_argument_hint_emitted_when_adapter_opts_in(self):
        fm = {"description": "d", "args": "issue number"}
        out = sync.build_frontmatter(self.HINT_ADAPTER, "s", fm)
        self.assertIn('argument-hint: "issue number"', out)

    def test_argument_hint_suppressed_for_portable_adapter(self):
        fm = {"description": "d", "args": "issue number"}
        out = sync.build_frontmatter(self.ADAPTER, "s", fm)
        self.assertNotIn("argument-hint", out)

    def test_argument_hint_none_is_skipped(self):
        fm = {"description": "d", "args": "none"}
        out = sync.build_frontmatter(self.HINT_ADAPTER, "s", fm)
        self.assertNotIn("argument-hint", out)

    def test_description_quotes_are_escaped(self):
        fm = {"description": 'say "hi"'}
        out = sync.build_frontmatter(self.ADAPTER, "s", fm)
        self.assertIn(r'description: "say \"hi\""', out)

    def test_multiline_description_stays_a_single_yaml_line(self):
        # A block-scalar placeholder substituted into a description must not
        # emit a raw newline inside the quoted scalar (invalid YAML).
        fm = {"description": "line one\nline two\ttabbed"}
        out = sync.build_frontmatter(self.ADAPTER, "s", fm)
        self.assertIn(r'description: "line one\nline two\ttabbed"', out)
        desc_lines = [l for l in out.splitlines() if l.startswith("description:")]
        self.assertEqual(len(desc_lines), 1)


class TestValidateSkillNames(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_skill_passes(self):
        path = _make_skill(self.tmpdir, "good-skill",
                           'name: good-skill\ndescription: "d"', "body")
        self.assertEqual(sync.validate_skill_names([path]), [])

    def test_missing_name_reported(self):
        path = _make_skill(self.tmpdir, "no-name", 'description: "d"', "body")
        errors = sync.validate_skill_names([path])
        self.assertTrue(any("missing required 'name'" in e for e in errors))

    def test_name_directory_mismatch_reported(self):
        path = _make_skill(self.tmpdir, "dir-name",
                           'name: other-name\ndescription: "d"', "body")
        errors = sync.validate_skill_names([path])
        self.assertTrue(any("must match its directory" in e for e in errors))

    def test_spec_violating_name_reported(self):
        path = _make_skill(self.tmpdir, "Bad_Name",
                           'name: Bad_Name\ndescription: "d"', "body")
        errors = sync.validate_skill_names([path])
        self.assertTrue(any("violates the spec" in e for e in errors))

    def test_missing_description_reported(self):
        path = _make_skill(self.tmpdir, "no-desc", 'name: no-desc', "body")
        errors = sync.validate_skill_names([path])
        self.assertTrue(any("missing required 'description'" in e for e in errors))

    def test_output_path_override_entry_is_exempt(self):
        """A prompt-only entry renders as a bare file with no frontmatter, so
        it isn't a skill in the spec sense and must not be held to the spec —
        otherwise it blocks the whole sync over fields nobody reads. #221."""
        path = _make_skill(
            self.tmpdir, "prompt-dir",
            'name: Some_Other_Name\noutput_path_override: ".claude/prompt.md"',
            "body")
        self.assertEqual(sync.validate_skill_names([path]), [])

    def test_override_substituted_before_exemption(self):
        """A placeholder override that resolves to a real path stays exempt."""
        path = _make_skill(
            self.tmpdir, "prompt-dir",
            'name: Some_Other_Name\noutput_path_override: "{{PROMPT_PATH}}"',
            "body")
        self.assertEqual(
            sync.validate_skill_names([path], {"PROMPT_PATH": ".claude/prompt.md"}), [])

    def test_override_resolving_to_empty_is_not_exempt(self):
        """sync_skill branches on the *substituted* override, so one whose
        placeholder resolves to "" renders as an ordinary skill — and must be
        held to the spec, or it emits the very directory the gate prevents."""
        path = _make_skill(
            self.tmpdir, "prompt-dir",
            'name: Some_Other_Name\noutput_path_override: "{{PROMPT_PATH}}"',
            "body")
        errors = sync.validate_skill_names([path], {"PROMPT_PATH": ""})
        self.assertTrue(any("violates the spec" in e for e in errors))

    def test_unresolved_override_is_left_to_validate_output_paths(self):
        """An override still holding {{FOO}} is not a frontmatter problem, so
        the name check stays silent and validate_output_paths owns it. #221."""
        path = _make_skill(
            self.tmpdir, "prompt-dir",
            'name: Some_Other_Name\noutput_path_override: "{{PROMPT_PATH}}"',
            "body")
        self.assertEqual(sync.validate_skill_names([path], {"OTHER": "x"}), [])

    def test_errors_are_group_qualified(self):
        """Auditing spans every group in-repo, and two groups can hold the same
        skill name, so a bare directory name would point at the wrong file."""
        path = _make_skill(self.tmpdir, "dir-name",
                           'name: other-name\ndescription: "d"', "body")
        errors = sync.validate_skill_names([path])
        self.assertTrue(errors)
        expected = f"{path.parent.parent.name}/dir-name/SKILL.md"
        self.assertTrue(all(expected in e for e in errors), errors)


# ═══════════════════════════════════════════════════════════════════════════════
# SYNC SKILL (integration-level)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSyncSkill(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir) / "project"
        self.project_root.mkdir()
        self.adapter = {
            "name": "test",
            "output_directory": ".test/skills",
            "argument_hint": False,
        }
        self.config = {"BRANCH_PROD": "main", "BRANCH_DEV": "dev"}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_skill(self, name, body, **fm_extra):
        fm_lines = [f'name: {name}', 'type: skill', f'description: "Test skill: {name}"']
        for k, v in fm_extra.items():
            fm_lines.append(f'{k}: "{v}"')
        skill_dir = Path(self.tmpdir) / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        path.write_text(f"---\n" + "\n".join(fm_lines) + "\n---\n\n" + body)
        return path

    def _out(self, name):
        return self.project_root / ".test" / "skills" / name / "SKILL.md"

    def test_writes_file_on_first_sync(self):
        skill_path = self._write_skill("demo", "Hello {{BRANCH_PROD}}")
        args = _make_args()
        result = sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        self.assertFalse(result)  # not dry-run, so returns False
        out = self._out("demo")
        self.assertTrue(out.exists())
        content = out.read_text()
        self.assertIn("Hello main", content)
        self.assertIn(sync.MARKER, content)

    def test_dry_run_does_not_write(self):
        skill_path = self._write_skill("demo", "content")
        args = _make_args(dry_run=True)
        result = sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        self.assertTrue(result)  # would write
        out = self._out("demo")
        self.assertFalse(out.exists())

    def test_idempotent_second_sync(self):
        skill_path = self._write_skill("demo", "stable content")
        args = _make_args()
        sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        # Second sync should detect up-to-date
        result = sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        self.assertFalse(result)

    def test_skips_customized_file_without_force(self):
        skill_path = self._write_skill("demo", "original")
        args = _make_args()
        sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)

        # Tamper with the output file body (but keep the marker)
        out = self._out("demo")
        content = out.read_text()
        out.write_text(content.replace("original", "CUSTOMIZED"))

        # Update the source skill to trigger a regeneration attempt
        skill_path.write_text(skill_path.read_text().replace("original", "new-source"))

        result = sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        self.assertFalse(result)
        # File should still contain the customization
        self.assertIn("CUSTOMIZED", out.read_text())

    def test_force_overwrites_customized_file(self):
        skill_path = self._write_skill("demo", "original")
        args = _make_args()
        sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)

        out = self._out("demo")
        content = out.read_text()
        out.write_text(content.replace("original", "CUSTOMIZED"))

        skill_path.write_text(skill_path.read_text().replace("original", "new-source"))

        args_force = _make_args(force=True)
        sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args_force)
        self.assertIn("new-source", out.read_text())
        self.assertNotIn("CUSTOMIZED", out.read_text())

    def test_conditional_blocks_in_sync(self):
        body = textwrap.dedent("""\
            {{#if BRANCH_DEV}}
            Dev: {{BRANCH_DEV}}
            {{/if}}
            {{#if BRANCH_TEST}}
            Test: {{BRANCH_TEST}}
            {{/if}}
            Prod: {{BRANCH_PROD}}""")
        skill_path = self._write_skill("cond", body)
        args = _make_args()
        sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        out = self._out("cond")
        content = out.read_text()
        self.assertIn("Dev: dev", content)
        self.assertNotIn("Test:", content)  # BRANCH_TEST not in config
        self.assertIn("Prod: main", content)

    def test_generated_frontmatter(self):
        skill_path = self._write_skill("demo", "the body")
        args = _make_args()
        sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        content = self._out("demo").read_text()
        self.assertTrue(content.startswith("---\nname: demo\n"))
        self.assertIn('description: "Test skill: demo"', content)

    def test_output_path_override(self):
        skill_path = self._write_skill(
            "custom", "custom body",
            output_path_override=".custom/output.md"
        )
        args = _make_args()
        sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        out = self.project_root / ".custom" / "output.md"
        self.assertTrue(out.exists())
        # Override output is a bare prompt file: body only, no frontmatter
        self.assertTrue(out.read_text().startswith("custom body"))
        # Default path should NOT exist
        self.assertFalse(self._out("custom").exists())

    def test_skips_non_generated_existing_file(self):
        """If a file exists but has no sync marker, skip without --force."""
        out = self._out("demo")
        out.parent.mkdir(parents=True)
        out.write_text("User-created file, no marker.\n")

        skill_path = self._write_skill("demo", "new content")
        args = _make_args()
        result = sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        self.assertFalse(result)
        self.assertIn("User-created file", out.read_text())

    def test_regenerates_when_source_changes(self):
        """If source skill changes but output wasn't customized, regenerate."""
        skill_path = self._write_skill("demo", "version-1")
        args = _make_args()
        sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)

        # Update source (rewrite the skill file)
        skill_path.write_text(skill_path.read_text().replace("version-1", "version-2"))

        sync.sync_skill(skill_path, self.adapter, self.config, self.project_root, args)
        out = self._out("demo")
        self.assertIn("version-2", out.read_text())


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateOutputPaths(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_resolved_override_passes(self):
        path = _make_skill(self.tmpdir, "prompt-dir",
                           'name: prompt-dir\noutput_path_override: "{{P}}"', "body")
        self.assertEqual(sync.validate_output_paths([path], {"P": ".claude/x.md"}), [])

    def test_skill_without_override_is_ignored(self):
        path = _make_skill(self.tmpdir, "plain",
                           'name: plain\ndescription: "d"', "body")
        self.assertEqual(sync.validate_output_paths([path], {}), [])

    def test_undefined_override_placeholder_is_reported(self):
        """An override left holding {{FOO}} would have sync_skill write to a
        literal '{{FOO}}' directory. The fix is a missing config key, not a
        frontmatter change, so it gets its own check and header. #221."""
        path = _make_skill(self.tmpdir, "prompt-dir",
                           'name: prompt-dir\noutput_path_override: "{{PROMPT_PATH}}"',
                           "body")
        errors = sync.validate_output_paths([path], {"OTHER": "x"})
        self.assertEqual(len(errors), 1)
        self.assertIn("output_path_override has undefined placeholder(s): PROMPT_PATH",
                      errors[0])

    def test_digit_bearing_override_placeholder_is_reported(self):
        """find_unresolved is load-bearing here, so its charset must match
        digit-bearing keys or {{PROMPT_PATH2}} ships as a literal path. #221."""
        path = _make_skill(self.tmpdir, "prompt-dir",
                           'name: prompt-dir\noutput_path_override: "{{PROMPT_PATH2}}"',
                           "body")
        errors = sync.validate_output_paths([path], {"OTHER": "x"})
        self.assertEqual(len(errors), 1)
        self.assertIn("PROMPT_PATH2", errors[0])


class TestValidatePlaceholders(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_errors_when_all_defined(self):
        path = _make_skill(self.tmpdir, "ok", 'name: ok\ndescription: "test"', "Use {{FOO}}")
        errors = sync.validate_placeholders([path], {"FOO": "bar"})
        self.assertEqual(errors, [])

    def test_reports_undefined_placeholder(self):
        path = _make_skill(self.tmpdir, "bad", 'name: bad\ndescription: "test"', "Use {{MISSING}}")
        errors = sync.validate_placeholders([path], {})
        self.assertEqual(len(errors), 1)
        self.assertIn("MISSING", errors[0])

    def test_error_names_the_skill_not_just_SKILL_md(self):
        """Every SKILL.md shares a filename, so the label must carry the group
        and skill directory or the report points at nothing. #221."""
        path = _make_skill(self.tmpdir, "bad", 'name: bad\ndescription: "test"', "Use {{MISSING}}")
        errors = sync.validate_placeholders([path], {})
        self.assertIn(f"{path.parent.parent.name}/bad/SKILL.md", errors[0])

    def test_placeholder_in_stripped_conditional_not_reported(self):
        body = "{{#if ACTIVE}}\n{{OPTIONAL}}\n{{/if}}\nPlain text."
        path = _make_skill(self.tmpdir, "cond", 'name: cond\ndescription: "test"', body)
        # ACTIVE is falsy → the block is stripped → OPTIONAL should not be reported
        errors = sync.validate_placeholders([path], {"ACTIVE": ""})
        self.assertEqual(errors, [])

    def test_placeholder_in_kept_conditional_reported(self):
        body = "{{#if ACTIVE}}\n{{OPTIONAL}}\n{{/if}}"
        path = _make_skill(self.tmpdir, "cond", 'name: cond\ndescription: "test"', body)
        # ACTIVE is truthy → OPTIONAL is in final output → should be reported
        errors = sync.validate_placeholders([path], {"ACTIVE": "yes"})
        self.assertEqual(len(errors), 1)
        self.assertIn("OPTIONAL", errors[0])

    def test_description_placeholders_checked(self):
        path = _make_skill(
            self.tmpdir, "desc",
            'skill: desc\ndescription: "Uses {{UNDEFINED}}"',
            "Body is fine."
        )
        errors = sync.validate_placeholders([path], {})
        self.assertEqual(len(errors), 1)
        self.assertIn("UNDEFINED", errors[0])


class TestValidateCommandShapes(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _skill(self, body):
        return _make_skill(self.tmpdir, "s", 'name: s\ndescription: "t"', body)

    def test_clean_command_passes(self):
        path = self._skill("```bash\ngit status\n```")
        self.assertEqual(sync.validate_command_shapes([path]), [])

    def test_flags_command_chaining(self):
        path = self._skill("```bash\ngit fetch origin dev && git merge origin/dev\n```")
        errors = sync.validate_command_shapes([path])
        self.assertEqual(len(errors), 1)
        self.assertIn("&&", errors[0])

    def test_flags_stderr_redirection(self):
        path = self._skill("```bash\ngit describe --tags 2>/dev/null\n```")
        errors = sync.validate_command_shapes([path])
        self.assertEqual(len(errors), 1)
        self.assertIn("2>", errors[0])

    def test_flags_pipe(self):
        path = self._skill("```bash\ngit branch -a | grep origin\n```")
        errors = sync.validate_command_shapes([path])
        self.assertEqual(len(errors), 1)
        self.assertIn("|", errors[0])

    def test_flags_command_substitution(self):
        path = self._skill("```bash\nmake -C $(git rev-parse --show-toplevel) check\n```")
        errors = sync.validate_command_shapes([path])
        self.assertEqual(len(errors), 1)
        self.assertIn("$(", errors[0])

    def test_ignores_non_command_lines(self):
        # First token is not a known command → prose/templates are not flagged.
        path = self._skill("```\nSee foo && bar for details\n```")
        self.assertEqual(sync.validate_command_shapes([path]), [])

    def test_ignores_comment_lines(self):
        path = self._skill("```bash\n# git a && git b\n```")
        self.assertEqual(sync.validate_command_shapes([path]), [])

    def test_bare_fence_is_scanned(self):
        path = self._skill("```\ngit fetch origin dev && git merge origin/dev\n```")
        self.assertEqual(len(sync.validate_command_shapes([path])), 1)


class TestGeneratePermissions(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_permissions_file_skips(self):
        adapter = {"name": "cursor", "permissions_file": None}
        self.assertFalse(sync.generate_permissions(adapter, self.tmpdir, _make_args()))

    def test_writes_allowlist(self):
        adapter = {"name": "claude", "permissions_file": ".claude/settings.json"}
        self.assertTrue(sync.generate_permissions(adapter, self.tmpdir, _make_args()))
        settings = json.loads((self.tmpdir / ".claude/settings.json").read_text())
        allow = settings["permissions"]["allow"]
        self.assertIn("Bash(git:*)", allow)
        self.assertIn("Bash(python3:*)", allow)
        # cd is intentionally excluded so the compound `cd … && …` shape is not blessed.
        self.assertNotIn("Bash(cd:*)", allow)

    def test_idempotent(self):
        adapter = {"name": "claude", "permissions_file": ".claude/settings.json"}
        sync.generate_permissions(adapter, self.tmpdir, _make_args())
        self.assertFalse(sync.generate_permissions(adapter, self.tmpdir, _make_args()))

    def test_handles_non_object_settings_file(self):
        # A valid-JSON-but-not-an-object settings file must not crash.
        settings_path = self.tmpdir / ".claude/settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("[]")
        adapter = {"name": "claude", "permissions_file": ".claude/settings.json"}
        self.assertTrue(sync.generate_permissions(adapter, self.tmpdir, _make_args()))
        settings = json.loads(settings_path.read_text())
        self.assertIn("Bash(git:*)", settings["permissions"]["allow"])

    def test_preserves_existing_settings(self):
        settings_path = self.tmpdir / ".claude/settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "permissions": {"allow": ["Bash(custom:*)"]},
            "other": "keep",
        }))
        adapter = {"name": "claude", "permissions_file": ".claude/settings.json"}
        sync.generate_permissions(adapter, self.tmpdir, _make_args())
        settings = json.loads(settings_path.read_text())
        self.assertIn("Bash(custom:*)", settings["permissions"]["allow"])
        self.assertIn("Bash(git:*)", settings["permissions"]["allow"])
        self.assertEqual(settings["other"], "keep")


class TestPermissionCommandSplit(unittest.TestCase):
    """Regression tests for #208: `commands:` vs `validate_only:` in permissions.yaml.

    `validate_only` commands (e.g. `cd`) must stay legal in skill code blocks
    (validated) while never being emitted as harness allow rules. This split is
    what stops the /setup permission audit from reporting `Bash(cd:*)` as a
    missing rule on every run — the audit reads `commands:`, which no longer
    contains `cd`.
    """

    def test_real_permissions_split(self):
        perms = sync.parse_yaml_simple(
            (REPO_ROOT / "permissions.yaml").read_text())
        # cd lives under validate_only, not commands.
        self.assertNotIn("cd", perms.get("commands", []))
        self.assertIn("cd", perms.get("validate_only", []))

    def test_cd_excluded_from_allow_rules(self):
        rules = sync._allow_rules_from_permissions()
        self.assertNotIn("Bash(cd:*)", rules)
        # A normal command still becomes a rule.
        self.assertIn("Bash(git:*)", rules)

    def test_validate_only_commands_still_validated(self):
        # The union that gates skill code blocks must still include cd, so a
        # skill legitimately using `cd` is not flagged as an unknown command.
        perms = sync.parse_yaml_simple(
            (REPO_ROOT / "permissions.yaml").read_text())
        validated = sync._validated_commands(perms)
        self.assertIn("cd", validated)
        self.assertIn("git", validated)

    def test_validated_commands_unions_both_keys(self):
        # Logic test against a synthetic fixture, independent of the real file.
        perms = {"commands": ["git", "make"], "validate_only": ["cd"]}
        self.assertEqual(
            set(sync._validated_commands(perms)), {"git", "make", "cd"})

    def test_validated_commands_tolerates_missing_validate_only(self):
        self.assertEqual(
            sync._validated_commands({"commands": ["git"]}), ["git"])


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN / CLI INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestMainCLI(unittest.TestCase):
    """Test main() behavior via subprocess or sys.exit interception."""

    def setUp(self):
        self._original_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._original_cwd)

    def _chdir_to_project(self):
        """Change to the project root if .codecannon.yaml exists, else skip."""
        if not (REPO_ROOT / ".codecannon.yaml").exists():
            self.skipTest(".codecannon.yaml not found in repo root")
        os.chdir(REPO_ROOT)

    def test_missing_config_exits_1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("sys.argv", ["sync.py", "--config", os.path.join(tmpdir, "nope.yaml")]):
                with self.assertRaises(SystemExit) as ctx:
                    sync.main()
                self.assertEqual(ctx.exception.code, 1)

    def test_validate_with_real_config(self):
        """Running --validate against the real project config should pass."""
        self._chdir_to_project()
        with patch("sys.argv", ["sync.py", "--validate"]):
            sync.main()

    def test_dry_run_with_real_config(self):
        """Running --dry-run against the real project should exit 0 (no drift) or 1 (drift)."""
        self._chdir_to_project()
        with patch("sys.argv", ["sync.py", "--dry-run"]):
            try:
                sync.main()
            except SystemExit as e:
                self.assertIn(e.code, (None, 0, 1))

    def test_no_adapters_exits_1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "empty.yaml"
            cfg.write_text("config:\n  FOO: bar\n")
            os.chdir(tmpdir)
            with patch("sys.argv", ["sync.py", "--config", str(cfg)]):
                with self.assertRaises(SystemExit) as ctx:
                    sync.main()
                self.assertEqual(ctx.exception.code, 1)

    def test_skill_filter(self):
        """--skill flag should restrict which skills are synced."""
        self._chdir_to_project()
        with patch("sys.argv", ["sync.py", "--dry-run", "--skill", "checkpoint"]):
            try:
                sync.main()
            except SystemExit:
                pass  # acceptable

    def test_nonexistent_skill_filter_exits_1(self):
        self._chdir_to_project()
        with patch("sys.argv", ["sync.py", "--dry-run", "--skill", "nonexistent_skill_xyz"]):
            with self.assertRaises(SystemExit) as ctx:
                sync.main()
            self.assertEqual(ctx.exception.code, 1)

    def test_missing_skill_group_exits_1(self):
        """Config without a skill_group entry should fail loudly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "nogroup.yaml"
            cfg.write_text("adapters:\n  - claude\nconfig:\n  FOO: bar\n")
            os.chdir(tmpdir)
            with patch("sys.argv", ["sync.py", "--config", str(cfg)]):
                with self.assertRaises(SystemExit) as ctx:
                    sync.main()
                self.assertEqual(ctx.exception.code, 1)

    def test_validate_passes_when_optional_placeholder_omitted(self):
        """A project config that omits an optional, schema-defaulted placeholder
        (STALE_DAYS) should still validate — main() must backfill it from
        config.schema.yaml rather than requiring it to be spelled out.
        Regression test for #194.
        """
        self._chdir_to_project()
        real_config = (REPO_ROOT / ".codecannon.yaml").read_text()
        lines = [l for l in real_config.splitlines() if "STALE_DAYS" not in l]
        self.assertNotIn("STALE_DAYS", "\n".join(lines), "fixture setup: STALE_DAYS should be stripped")
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "no-stale-days.yaml"
            cfg.write_text("\n".join(lines) + "\n")
            with patch("sys.argv", ["sync.py", "--config", str(cfg), "--validate"]):
                sync.main()  # should not raise SystemExit(1)

    def _fake_checkout(self, tmpdir, skill_dir_name, frontmatter, extra_groups=None,
                       in_repo=False):
        """Build a throwaway CodeCannon checkout containing a single skill.

        Symlinks the pieces main() reads from CODECANNON_DIR (adapter defs,
        schema, permissions) back to the real repo, so only the skill under
        test is synthetic. `extra_groups` adds {group: {dir: frontmatter}}
        alongside the enabled `testgroup`. `in_repo=True` makes the checkout
        its own project root, the shape that widens auditing to every group.
        Returns the config path to pass as --config.
        """
        # Resolved: os.chdir() below yields a resolved cwd, and main() compares
        # CODECANNON_DIR against it to decide whether it is running in-repo.
        fake_root = (Path(tmpdir) / "codecannon").resolve()
        fake_root.mkdir()
        self.fake_root = fake_root
        for entry in ("adapters", "config.schema.yaml", "permissions.yaml"):
            (fake_root / entry).symlink_to(REPO_ROOT / entry)

        groups = {"testgroup": {skill_dir_name: frontmatter}}
        for group, skills in (extra_groups or {}).items():
            groups.setdefault(group, {}).update(skills)
        for group, skills in groups.items():
            for dir_name, fm in skills.items():
                skill_dir = fake_root / "skills" / group / dir_name
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text(f"---\n{fm}\n---\n\nbody\n")

        project_root = fake_root if in_repo else Path(tmpdir) / "project"
        if not in_repo:
            project_root.mkdir()
        cfg = project_root / ".codecannon.yaml"
        cfg.write_text("skill_group: testgroup\nadapters:\n  - claude\nconfig:\n  FOO: bar\n")
        os.chdir(project_root)
        return cfg

    def test_name_directory_mismatch_blocks_write(self):
        """A frontmatter-name/directory mismatch must abort the write path
        before any output is produced — not just under --validate. #221."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._fake_checkout(
                tmpdir, "real-dir", 'name: other-name\ndescription: "d"')
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg)]):
                    with self.assertRaises(SystemExit) as ctx:
                        sync.main()
            self.assertEqual(ctx.exception.code, 1)
            written = list((Path(tmpdir) / "project").rglob("SKILL.md"))
            self.assertEqual(written, [], "no output should be written when the gate fails")

    def test_name_directory_mismatch_blocks_dry_run(self):
        """CI runs --dry-run, so the gate must fail there too. #221.

        --dry-run exits 1 on pending writes regardless, so assert on the
        reason: the run must stop at the name gate and never reach the
        sync loop that reports what it would write.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._fake_checkout(
                tmpdir, "real-dir", 'name: other-name\ndescription: "d"')
            buf = io.StringIO()
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg), "--dry-run"]):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as ctx:
                            sync.main()
            self.assertEqual(ctx.exception.code, 1)
            out = buf.getvalue()
            self.assertIn("Skill-name validation failed", out)
            self.assertNotIn("would write", out)

    def test_validate_reports_all_checks_despite_name_error(self):
        """--validate is a report, not a fail-fast gate: a name error must not
        short-circuit the placeholder/permission/command-shape results, or each
        class of problem costs its own round trip. #221."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._fake_checkout(
                tmpdir, "real-dir", 'name: other-name\ndescription: "d"')
            buf = io.StringIO()
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg), "--validate"]):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as ctx:
                            sync.main()
            self.assertEqual(ctx.exception.code, 1)
            out = buf.getvalue()
            self.assertIn("Skill-name validation failed", out)
            self.assertNotIn("Skill-name validation passed", out)
            self.assertIn("Placeholder validation", out)
            self.assertIn("Permission validation", out)
            self.assertIn("Command-shape validation", out)

    def test_codecannon_dir_is_resolved_when_loaded_via_symlink(self):
        """CODECANNON_DIR is compared against Path.cwd(), which is always
        symlink-resolved. Loaded through a symlinked path it must still equal
        the real directory, or any symlink in the checkout silently narrows the
        audit scope with no error and exit 0. #221."""
        import importlib.util
        with tempfile.TemporaryDirectory() as tmpdir:
            link = Path(tmpdir) / "linked-checkout"
            link.symlink_to(REPO_ROOT)
            spec = importlib.util.spec_from_file_location(
                "sync_via_symlink", link / "sync.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertEqual(mod.CODECANNON_DIR, REPO_ROOT.resolve())

    def _checkout_with_bad_other_group(self, tmpdir):
        return self._fake_checkout(
            tmpdir, "good-skill", 'name: good-skill\ndescription: "d"',
            extra_groups={"othergroup": {"real-dir": 'name: other-name\ndescription: "d"'}},
            in_repo=True)

    def test_name_violation_in_non_enabled_group_fails_validate(self):
        """Inside the CodeCannon repo, --validate audits every group — same as
        the permission check — so a violation in a group nobody has enabled
        can't ship green through CI, which runs --validate. #221."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._checkout_with_bad_other_group(tmpdir)
            buf = io.StringIO()
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg), "--validate"]):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as ctx:
                            sync.main()
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("other-name", buf.getvalue())

    def test_validate_pass_messages_state_their_scope(self):
        """Other groups are checked without placeholder resolution and their
        overrides aren't checked at all, so the pass lines must not imply full
        coverage. #221."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._fake_checkout(
                tmpdir, "good-skill", 'name: good-skill\ndescription: "d"',
                extra_groups={"othergroup": {"fine": 'name: fine\ndescription: "d"'}},
                in_repo=True)
            buf = io.StringIO()
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg), "--validate"]):
                    with contextlib.redirect_stdout(buf):
                        sync.main()
            out = buf.getvalue()
            self.assertIn("other groups checked without placeholder resolution", out)
            self.assertIn("all override placeholders in testgroup are defined", out)

    def test_name_violation_in_non_enabled_group_does_not_block_write(self):
        """Non-enabled groups are never handed to sync_skill, so they can't
        produce bad output and must not block a local sync — otherwise a WIP
        skill in an unrelated group breaks ./sync.py --force. #221."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._checkout_with_bad_other_group(tmpdir)
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg)]):
                    sync.main()
            out = self.fake_root / ".claude" / "skills" / "good-skill" / "SKILL.md"
            self.assertTrue(out.exists(), "enabled group should still sync")

    def test_unresolved_override_in_enabled_group_blocks_write(self):
        """The enabled group's overrides do reach sync_skill, so an undefined
        placeholder there must stop the write under its own header. #221."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._fake_checkout(
                tmpdir, "prompt-dir",
                'name: prompt-dir\ndescription: "d"\n'
                'output_path_override: "{{NOT_DEFINED}}"')
            buf = io.StringIO()
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg)]):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as ctx:
                            sync.main()
            self.assertEqual(ctx.exception.code, 1)
            out = buf.getvalue()
            self.assertIn("Output-path validation failed", out)
            self.assertIn("NOT_DEFINED", out)
            self.assertNotIn("Skill-name validation failed", out)

    def test_other_group_override_placeholder_does_not_block_sync(self):
        """The widened audit spans groups, but config is per-enabled-group. A
        non-enabled group's override placeholder is not resolvable against the
        current config and must not hard-fail the sync. #221."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._fake_checkout(
                tmpdir, "good-skill", 'name: good-skill\ndescription: "d"',
                extra_groups={"othergroup": {"prompt-dir": (
                    'name: prompt-dir\ndescription: "d"\n'
                    'output_path_override: "{{OTHER_GROUP_PATH}}"')}},
                in_repo=True)
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg)]):
                    sync.main()
            out = self.fake_root / ".claude" / "skills" / "good-skill" / "SKILL.md"
            self.assertTrue(out.exists(), "enabled group should still sync")

    def test_prompt_only_entry_does_not_block_sync(self):
        """An output_path_override entry is exempt from the spec, so a
        name/directory mismatch on one must not abort the run. #221."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._fake_checkout(
                tmpdir, "good-skill", 'name: good-skill\ndescription: "d"',
                extra_groups={"testgroup": {
                    "prompt-dir": 'name: Some_Other_Name\n'
                                  'output_path_override: ".claude/prompt.md"'}})
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg)]):
                    sync.main()
            project = Path(tmpdir) / "project"
            self.assertTrue((project / ".claude" / "skills" / "good-skill" / "SKILL.md").exists())
            self.assertTrue((project / ".claude" / "prompt.md").exists())

    def test_spec_compliant_skill_syncs(self):
        """The gate must not block a compliant skill from being written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = self._fake_checkout(
                tmpdir, "good-skill", 'name: good-skill\ndescription: "d"')
            with patch("sync.CODECANNON_DIR", self.fake_root):
                with patch("sys.argv", ["sync.py", "--config", str(cfg)]):
                    sync.main()
            out = Path(tmpdir) / "project" / ".claude" / "skills" / "good-skill" / "SKILL.md"
            self.assertTrue(out.exists(), "compliant skill should be written")

    def test_nonexistent_skill_group_exits_1(self):
        """skill_group naming a directory that doesn't exist should fail loudly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "badgroup.yaml"
            cfg.write_text("skill_group: does-not-exist\nadapters:\n  - claude\nconfig:\n  FOO: bar\n")
            os.chdir(tmpdir)
            with patch("sys.argv", ["sync.py", "--config", str(cfg)]):
                with self.assertRaises(SystemExit) as ctx:
                    sync.main()
                self.assertEqual(ctx.exception.code, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# GOLDEN-FILE SNAPSHOT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestGoldenFileSnapshots(unittest.TestCase):
    """Verify that sync output for each adapter matches what's already on disk.

    This is a regression test: if a skill template or the sync engine changes,
    the on-disk output should also be updated (via ./sync.py). If it drifts,
    these tests catch it — same as the CI dry-run check, but at unittest level.

    Uses sync_skill to render into a temp directory, then compares against
    the on-disk files. This avoids duplicating the rendering pipeline.
    """

    def test_all_generated_files_are_current(self):
        """Every generated file on disk should match what sync would produce today."""
        import shutil

        project_root = REPO_ROOT
        config_path = project_root / ".codecannon.yaml"
        if not config_path.exists():
            self.skipTest(".codecannon.yaml not found")

        raw_config = sync.parse_yaml_simple(config_path.read_text())
        adapters_list = raw_config.get("adapters", [])
        project_config = raw_config.get("config", {})
        # Same helper main() uses, so this test can't silently drift from real behavior.
        sync.apply_schema_defaults(project_config, REPO_ROOT / "config.schema.yaml")
        skill_group = raw_config.get("skill_group", "")
        if not skill_group:
            self.skipTest("skill_group not set in .codecannon.yaml")

        skills_dir = REPO_ROOT / "skills" / skill_group
        skill_files = sorted(skills_dir.glob("*/SKILL.md"))
        args = _make_args()

        tmpdir = Path(tempfile.mkdtemp())
        try:
            stale = []
            seen_adapters = set()
            for adapter_name in adapters_list:
                adapter = sync.load_adapter(adapter_name)
                if not adapter or adapter["name"] in seen_adapters:
                    continue
                seen_adapters.add(adapter["name"])
                for skill_path in skill_files:
                    # Render via sync_skill into the temp directory
                    sync.sync_skill(skill_path, adapter, project_config, tmpdir, args)

                    # Determine the output path (mirrors sync_skill logic)
                    raw = skill_path.read_text()
                    fm, _ = sync.parse_frontmatter(raw)
                    skill_name = fm.get("name", skill_path.parent.name)
                    output_path_override = fm.get("output_path_override", "")
                    if output_path_override:
                        output_path_override = sync.apply_placeholders(output_path_override, project_config)
                        fresh_path = tmpdir / output_path_override
                        disk_path = project_root / output_path_override
                    else:
                        fresh_path = tmpdir / adapter["output_directory"] / skill_name / "SKILL.md"
                        disk_path = project_root / adapter["output_directory"] / skill_name / "SKILL.md"

                    if not disk_path.exists():
                        stale.append(f"{adapter_name}/{skill_name}: file missing at {disk_path}")
                        continue

                    if not fresh_path.exists():
                        continue  # sync_skill decided not to write (shouldn't happen on fresh dir)

                    if fresh_path.read_text() != disk_path.read_text():
                        stale.append(f"{adapter_name}/{skill_name}: content differs from freshly rendered output")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        if stale:
            self.fail(
                "Generated files are out of date. Run ./sync.py to regenerate.\n"
                + "\n".join(f"  - {s}" for s in stale)
            )


# ═══════════════════════════════════════════════════════════════════════════════
# SYNC-BASE-BRANCH SCRIPT
# ═══════════════════════════════════════════════════════════════════════════════


class TestSyncBaseBranchScript(unittest.TestCase):
    """Exit-code behavior of skills/github-agile/scripts/sync-base-branch.py.

    The script is mechanical — these tests cover the user-visible contract
    (exit codes, dirty-tree guard) without exercising real network operations.
    """

    SCRIPT = REPO_ROOT / "skills" / "github-agile" / "scripts" / "sync-base-branch.py"

    def _git(self, repo, *args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    def _init_repo(self, repo):
        """Initialize a tiny git repo with one committed file on branch 'main'."""
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.email", "test@example.com")
        self._git(repo, "config", "user.name", "Test")
        (repo / "README").write_text("hello\n")
        self._git(repo, "add", "README")
        self._git(repo, "commit", "-q", "-m", "init")

    def _run(self, repo, *args):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )

    def test_missing_argument_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(Path(tmp))
        self.assertEqual(result.returncode, 3)
        self.assertIn("Usage", result.stderr)

    def test_empty_argument_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(Path(tmp), "")
        self.assertEqual(result.returncode, 3)

    def test_dirty_unstaged_tree_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            (repo / "README").write_text("dirty\n")  # unstaged change
            result = self._run(repo, "main")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not clean", result.stderr)

    def test_dirty_staged_tree_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            (repo / "new").write_text("staged\n")
            self._git(repo, "add", "new")  # staged but not committed
            result = self._run(repo, "main")
        self.assertEqual(result.returncode, 1)

    def test_untracked_file_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            (repo / "untracked").write_text("not added\n")  # untracked, not staged
            result = self._run(repo, "main")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not clean", result.stderr)

    def test_clean_tree_no_remote_exits_2(self):
        """With a clean tree but no `origin` remote, git fetch fails — exit 2."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            result = self._run(repo, "main")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
