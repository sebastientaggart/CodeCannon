"""Tests for Makefile.agents.mk recipes.

Runs the real recipe with `make` in a throwaway git repo, with a stub `gh` on
PATH standing in for GitHub.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile.agents.mk"

# `pr view` answers the recipe's two lookups; `pr merge` exits with
# $GH_MERGE_EXIT so a test can simulate GitHub refusing the merge.
GH_STUB = """#!/bin/sh
if [ "$1 $2" = "pr merge" ]; then
  exit "${GH_MERGE_EXIT:-0}"
fi
case "$*" in
  *baseRefName*) echo dev ;;
  *) echo https://example.test/pr/1 ;;
esac
"""


@unittest.skipUnless(shutil.which("make") and shutil.which("git"),
                     "requires make and git")
class TestMergeTarget(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        origin = self.tmpdir / "origin.git"
        self.work = self.tmpdir / "work"
        bindir = self.tmpdir / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")

        self._run("git", "init", "--bare", "-b", "dev", str(origin), cwd=self.tmpdir)
        self._run("git", "init", "-b", "dev", str(self.work), cwd=self.tmpdir)
        self._git("remote", "add", "origin", str(origin))
        self._commit("base")
        self._git("push", "-u", "origin", "dev")
        self._git("checkout", "-b", "feature/x")
        # Never pushed: a reset to origin/dev would discard it.
        self._commit("unpushed work")
        self.feature_head = self._git("rev-parse", "HEAD")

    def _run(self, *argv, cwd):
        return subprocess.run(argv, cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def _git(self, *argv):
        return self._run("git", "-c", "commit.gpgsign=false", "-c", "user.name=t",
                         "-c", "user.email=t@example.test", *argv, cwd=self.work)

    def _commit(self, message):
        self._git("commit", "--allow-empty", "-m", message)

    def _make_merge(self, merge_exit):
        env = dict(self.env, GH_MERGE_EXIT=str(merge_exit))
        return subprocess.run(
            ["make", "-f", str(MAKEFILE), "merge", "INTEGRATION_BRANCH=dev"],
            cwd=self.work, env=env, capture_output=True, text=True)

    def test_refused_merge_stops_before_reset(self):
        """A refused `gh pr merge` must not fall through to the hard reset —
        that would discard unpushed work and report success. #225."""
        result = self._make_merge(merge_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PR merged", result.stdout)
        self.assertEqual(self._git("rev-parse", "--abbrev-ref", "HEAD"), "feature/x")
        self.assertEqual(self._git("rev-parse", "HEAD"), self.feature_head)

    def test_successful_merge_lands_on_integration_branch(self):
        result = self._make_merge(merge_exit=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PR merged into dev.", result.stdout)
        self.assertEqual(self._git("rev-parse", "--abbrev-ref", "HEAD"), "dev")
        self.assertEqual(self._git("rev-parse", "HEAD"),
                         self._git("rev-parse", "origin/dev"))


if __name__ == "__main__":
    unittest.main()
