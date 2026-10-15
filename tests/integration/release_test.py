from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from release import ReleaseError, release

ROOT = Path(__file__).resolve().parents[2]


class Releases(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="binder-release-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "checkout with spaces"
        self.remote = self.directory / "origin.git"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_") and key != "RELEASE_VERSION"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")
        self.root.mkdir()
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("init", "--quiet", "--bare", "--initial-branch=main", str(self.remote))
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release@example.invalid")
        (self.root / "scripts").mkdir()
        for name in ("release.py", "release.sh"):
            shutil.copy2(ROOT / "scripts" / name, self.root / "scripts" / name)
        shutil.copy2(ROOT / "Makefile", self.root / "Makefile")
        self.commit("Initial fixture")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "--quiet", "origin", "main")
        self.head = self.git("rev-parse", "HEAD")

    def git(self, *args: str, remote: bool = False) -> str:
        return subprocess.check_output(["git", *args], cwd=self.remote if remote else self.root, env=self.env, text=True, stderr=subprocess.PIPE).strip()

    def commit(self, message: str):
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", message)

    def command(self, goal="release", *, version="", answers=""):
        args = ["make", "--no-print-directory", goal]
        if version:
            args.append("VERSION=" + version)
        return subprocess.run(args, cwd=self.root, env=self.env, input=answers, text=True, capture_output=True, timeout=10)

    def assert_no_tags(self):
        self.assertEqual(self.git("tag", "--list"), "")
        self.assertEqual(self.git("tag", "--list", remote=True), "")

    def test_default_release_pushes_an_annotated_tag_at_the_displayed_commit(self):
        result = self.command(answers="\ny\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Proposed next tag: v1.0.0", result.stdout)
        self.assertEqual(self.git("cat-file", "-t", "refs/tags/v1.0.0", remote=True), "tag")
        self.assertEqual(self.git("rev-parse", "v1.0.0^{}", remote=True), self.head)
        self.assertEqual(self.git("rev-parse", "main", remote=True), self.head)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_custom_version_through_make_still_requires_confirmation(self):
        result = self.command(version="2.1.0", answers="yes\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Version to release [", result.stdout)
        self.assertIn("Proceed?", result.stdout)
        self.assertEqual(self.git("rev-parse", "v2.1.0^{}", remote=True), self.head)

    def test_release_pushes_only_its_tag_even_with_follow_tags_enabled(self):
        self.git("tag", "-a", "internal-checkpoint", "-m", "Unpublished checkpoint")
        self.git("config", "push.followTags", "true")
        result = self.command(version="1.0.0", answers="y\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("tag", "--list", remote=True), "v1.0.0")

    def test_dry_run_uses_remote_versions_numerically_without_fetching_or_writing(self):
        for tag in ("v1.9.9", "v1.10.0"):
            self.git("tag", tag)
            self.git("push", "--quiet", "origin", tag)
            self.git("tag", "-d", tag)
        self.git("tag", "v2.0.0-rc.1")
        result = self.command("release-dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Proposed next tag: v1.10.1", result.stdout)
        self.assertIn("no tag created or pushed", result.stdout)
        self.assertEqual(self.git("tag", "--list"), "v2.0.0-rc.1")
        self.assertEqual(self.git("tag", "--list", remote=True), "v1.10.0\nv1.9.9")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_dry_run_accepts_an_explicit_version_without_prompting(self):
        result = self.command("release-dry-run", version="v3.0.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("tag:    v3.0.0", result.stdout)
        self.assertNotIn("Proceed?", result.stdout)
        self.assert_no_tags()

    def test_cancellation_and_end_of_input_create_no_tags(self):
        for answers in ("\nn\n", "\n\n", "", "1.0.0\n"):
            with self.subTest(answers=answers):
                result = self.command(answers=answers)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Aborted", result.stderr)
                self.assert_no_tags()

    def test_invalid_versions_never_reach_tag_creation(self):
        for version in ("v01.0.0", "1.0", "1.0.0-rc.1", "bad", "1.0.0; touch marker"):
            with self.subTest(version=version):
                result = self.command(version=version, answers="y\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Version must look like", result.stderr)
                self.assert_no_tags()
                self.assertFalse((self.root / "marker").exists())

    def test_dirty_tracked_and_untracked_files_block_release(self):
        for name in ("Makefile", "untracked.txt"):
            with self.subTest(path=name):
                path = self.root / name
                original = path.read_text() if path.exists() else None
                path.write_text((original or "") + "\n# Unreleased changes\n")
                result = self.command(answers="\ny\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Working tree is not clean", result.stderr)
                self.assert_no_tags()
                if original is None:
                    path.unlink()
                else:
                    path.write_text(original)

    def test_feature_branches_and_detached_head_block_release(self):
        for target in ("feature", "--detach"):
            with self.subTest(target=target):
                if target == "feature":
                    self.git("checkout", "--quiet", "-b", target)
                else:
                    self.git("checkout", "--quiet", "--detach")
                result = self.command(answers="\ny\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Release from the main branch", result.stderr)
                self.assert_no_tags()
                self.git("checkout", "--quiet", "main")

    def test_unpublished_commits_block_release(self):
        (self.root / "new.txt").write_text("Unpublished change\n")
        self.commit("Unpublished")
        result = self.command(answers="\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HEAD must match origin/main", result.stderr)
        self.assert_no_tags()

    def test_existing_local_and_remote_tags_block_release(self):
        self.git("tag", "v1.2.3")
        for remote in (False, True):
            with self.subTest(remote=remote):
                if remote:
                    self.git("push", "--quiet", "origin", "v1.2.3")
                    self.git("tag", "-d", "v1.2.3")
                result = self.command(version="1.2.3", answers="y\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("already exists", result.stderr)

    def test_release_version_must_advance(self):
        self.git("tag", "v2.0.0")
        result = self.command(version="1.9.0", answers="y\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be newer than v2.0.0", result.stderr)
        self.assertEqual(self.git("tag", "--list", remote=True), "")

    def test_unreachable_remote_blocks_release(self):
        self.git("remote", "set-url", "origin", str(self.directory / "missing.git"))
        result = self.command(answers="\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git("tag", "--list"), "")

    def test_failed_push_retains_the_local_annotated_tag(self):
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'Rejected fixture push' >&2\nexit 1\n")
        hook.chmod(0o755)
        result = self.command(version="1.0.0", answers="y\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Local tag v1.0.0 was retained", result.stderr)
        self.assertEqual(self.git("cat-file", "-t", "v1.0.0"), "tag")
        self.assertEqual(self.git("tag", "--list", remote=True), "")

    def test_head_is_rechecked_after_confirmation(self):
        def confirm(prompt):
            (self.root / "new.txt").write_text("Changed during confirmation\n")
            self.commit("Concurrent commit")
            self.git("push", "--quiet", "origin", "main")
            return "y"
        with patch.dict(os.environ, self.env, clear=True), patch("builtins.input", confirm), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ReleaseError, "HEAD changed during confirmation"):
                release(self.root, "1.0.0", False)
        self.assert_no_tags()


if __name__ == "__main__":
    unittest.main()
