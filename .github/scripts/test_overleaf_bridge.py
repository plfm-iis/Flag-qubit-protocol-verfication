"""Data-loss boundary tests using temporary local Git remotes; no credentials."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import overleaf_bridge as bridge


def command(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()


def write(repo, path, content):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content if isinstance(content, bytes) else content.encode())


def save(repo, message="test edit"):
    command(repo, "add", "-A")
    command(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", message)
    command(repo, "push", "origin", "HEAD:main")
    return command(repo, "rev-parse", "HEAD")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bridge-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.github = self.root / "github.git"
        self.overleaf = self.root / "overleaf.git"
        self.gh = self.root / "github-work"
        self.ol = self.root / "overleaf-editor"
        for remote, checkout in [(self.github, self.gh), (self.overleaf, self.ol)]:
            command(self.root, "init", "--bare", "--initial-branch=main", str(remote))
            command(self.root, "init", "--initial-branch=main", str(checkout))
            command(checkout, "remote", "add", "origin", str(remote))
            write(checkout, "main.tex", "original paper\n")
            if checkout == self.gh:
                write(checkout, "AGENTS.md", "keep policy\n")
                write(checkout, "PAPER_CONTEXT.md", "keep context\n")
                write(checkout, ".github/workflows/keep.yml", "keep workflow\n")
                write(checkout, ".ai/keep.txt", "keep notes\n")
            save(checkout, "initial")

    def run_bridge(self, mode, force_dry_run=False):
        bridge.Bridge(self.gh, str(self.overleaf)).run(mode, force_dry_run)

    def bootstrap(self):
        self.run_bridge("refresh")
        return json.loads((self.gh / bridge.STATE).read_text())

    def request(self, approved, expected=None, dry_run=False):
        state = json.loads((self.gh / bridge.STATE).read_text())
        write(self.gh, bridge.REQUEST, json.dumps({"schema": 1, "expected_overleaf_revision": expected or state["overleaf_revision"], "approved_github_commit": approved, "dry_run": dry_run}) + "\n")
        save(self.gh, "request approved sync")

    def heads(self):
        return command(self.github, "rev-parse", "main"), command(self.overleaf, "rev-parse", "main")

    def test_initial_baseline_preserves_metadata(self):
        old_ol = self.heads()[1]
        state = self.bootstrap()
        self.assertEqual(state["overleaf_revision"], old_ol)
        self.assertEqual(state["paper_manifest"], bridge.manifest(self.gh))
        self.assertEqual((self.gh / "AGENTS.md").read_text(), "keep policy\n")
        self.assertEqual((self.gh / ".ai/keep.txt").read_text(), "keep notes\n")
        self.assertEqual(command(self.overleaf, "ls-tree", "--name-only", "main"), "main.tex")

    def test_first_refresh_mismatch_writes_nothing(self):
        write(self.ol, "main.tex", "different source\n")
        save(self.ol)
        before = self.heads()
        with self.assertRaisesRegex(bridge.Refused, "Initial.*differ"):
            self.run_bridge("refresh")
        self.assertEqual(self.heads(), before)
        self.assertFalse((self.gh / bridge.STATE).exists())
        self.assertEqual((self.gh / "main.tex").read_text(), "original paper\n")

    def test_refresh_adds_deletes_and_preserves_metadata(self):
        self.bootstrap()
        (self.ol / "main.tex").unlink()
        write(self.ol, "chapters/new.tex", "new paper\n")
        write(self.ol, "figures/data.bin", b"\x00\xff\x01")
        save(self.ol)
        self.run_bridge("refresh")
        self.assertFalse((self.gh / "main.tex").exists())
        self.assertEqual((self.gh / "figures/data.bin").read_bytes(), b"\x00\xff\x01")
        self.assertEqual((self.gh / ".github/workflows/keep.yml").read_text(), "keep workflow\n")
        self.assertEqual(bridge.manifest(self.gh), bridge.manifest(self.ol))

    def test_refresh_refuses_unsynced_github_paper(self):
        self.bootstrap()
        write(self.gh, "main.tex", "unpublished paper\n")
        save(self.gh)
        before = self.heads()
        with self.assertRaisesRegex(bridge.Refused, "Refresh would overwrite"):
            self.run_bridge("refresh")
        self.assertEqual(self.heads(), before)
        self.assertEqual((self.gh / "main.tex").read_text(), "unpublished paper\n")

    def test_refresh_handles_file_directory_transitions(self):
        self.bootstrap()
        (self.ol / "main.tex").unlink()
        write(self.ol, "main.tex/part.tex", "paper in directory\n")
        save(self.ol)
        self.run_bridge("refresh")
        self.assertEqual((self.gh / "main.tex/part.tex").read_text(), "paper in directory\n")
        (self.ol / "main.tex/part.tex").unlink()
        (self.ol / "main.tex").rmdir()
        write(self.ol, "main.tex", "paper back in file\n")
        save(self.ol)
        self.run_bridge("refresh")
        self.assertEqual((self.gh / "main.tex").read_text(), "paper back in file\n")
        self.assertEqual(bridge.manifest(self.gh), bridge.manifest(self.ol))

    def test_refresh_keeps_tracked_source_files_matched_by_gitignore(self):
        self.bootstrap()
        write(self.ol, ".gitignore", "ignored.tex\n")
        write(self.ol, "ignored.tex", "tracked despite ignore\n")
        command(self.ol, "add", "-f", "ignored.tex")
        save(self.ol)
        self.run_bridge("refresh")
        self.assertEqual(bridge.manifest(self.gh), bridge.manifest(self.ol))
        self.assertIn("ignored.tex", command(self.github, "ls-tree", "--name-only", "main").splitlines())

    def test_sync_without_request_is_read_only(self):
        self.bootstrap()
        before = self.heads()
        self.run_bridge("sync")
        self.assertEqual(self.heads(), before)

    def test_sync_refuses_disjoint_overleaf_change_since_approval(self):
        self.bootstrap()
        write(self.gh, "main.tex", "approved edit\n")
        approved = save(self.gh)
        self.request(approved)
        write(self.ol, "coauthor.tex", "concurrent unrelated edit\n")
        save(self.ol)
        before = self.heads()
        with self.assertRaisesRegex(bridge.Refused, "Overleaf revision changed"):
            self.run_bridge("sync")
        self.assertEqual(self.heads(), before)

    def test_sync_refuses_github_changes_beyond_approved_snapshot(self):
        self.bootstrap()
        write(self.gh, "main.tex", "approved edit\n")
        approved = save(self.gh)
        self.request(approved)
        write(self.gh, "main.tex", "extra unapproved edit\n")
        save(self.gh)
        before = self.heads()
        with self.assertRaisesRegex(bridge.Refused, "differs from the approved"):
            self.run_bridge("sync")
        self.assertEqual(self.heads(), before)

    def test_manual_dry_run_never_replays_write_request(self):
        self.bootstrap()
        write(self.gh, "main.tex", "approved edit\n")
        approved = save(self.gh)
        self.request(approved, dry_run=False)
        before = self.heads()
        self.run_bridge("sync", force_dry_run=True)
        self.assertEqual(self.heads(), before)

    def test_successful_sync_preserves_exact_snapshot_and_updates_baseline(self):
        self.bootstrap()
        (self.gh / "main.tex").unlink()
        write(self.gh, "paper/main.tex", "approved replacement\n")
        write(self.gh, "paper/figure.bin", b"\x00\xff")
        approved = save(self.gh)
        self.request(approved)
        self.run_bridge("sync")
        state = json.loads((self.gh / bridge.STATE).read_text())
        self.assertEqual(state["overleaf_revision"], self.heads()[1])
        self.assertEqual(state["paper_manifest"], bridge.manifest(self.overleaf, "main"))
        self.assertEqual(bridge.manifest(self.gh), bridge.manifest(self.overleaf, "main"))
        self.assertNotIn("AGENTS.md", command(self.overleaf, "ls-tree", "-r", "--name-only", "main").splitlines())
        with self.assertRaisesRegex(bridge.Refused, "Overleaf revision changed"):
            self.run_bridge("sync")

    def test_overleaf_race_rejects_push_without_overwriting(self):
        self.bootstrap()
        write(self.gh, "main.tex", "approved edit\n")
        approved = save(self.gh)
        self.request(approved)
        gh_before = self.heads()[0]
        original_git = bridge.git

        def race(repo, *args, **kwargs):
            if Path(repo).name == "overleaf" and args == ("push", "origin", "HEAD:refs/heads/main"):
                write(self.ol, "main.tex", "concurrent coauthor edit\n")
                save(self.ol)
            return original_git(repo, *args, **kwargs)

        with patch.object(bridge, "git", side_effect=race):
            with self.assertRaisesRegex(bridge.Refused, "git push failed"):
                self.run_bridge("sync")
        self.assertEqual(self.heads()[0], gh_before)
        self.assertEqual(command(self.overleaf, "show", "main:main.tex"), "concurrent coauthor edit")

    def test_git_line_ending_normalization_refuses_unapproved_blob(self):
        self.bootstrap()
        write(self.gh, "main.tex", b"approved CRLF bytes\r\n")
        approved = save(self.gh)
        self.request(approved)
        before = self.heads()
        original_git = bridge.git

        def normalize(repo, *args, **kwargs):
            result = original_git(repo, *args, **kwargs)
            if args and args[0] == "clone":
                # A destination Git attribute changes the bytes added to its index.
                write(Path(args[-1]), ".git/info/attributes", "*.tex text eol=lf\n")
            return result

        with patch.object(bridge, "git", side_effect=normalize):
            with self.assertRaisesRegex(bridge.Refused, "altered the approved paper snapshot"):
                self.run_bridge("sync")
        self.assertEqual(self.heads(), before)

    def test_stale_github_checkout_cannot_write(self):
        self.bootstrap()
        racer = self.root / "early-racer"
        command(self.root, "clone", str(self.github), str(racer))
        write(racer, "main.tex", "newer remote paper\n")
        save(racer)
        before = self.heads()
        with self.assertRaisesRegex(bridge.Refused, "GitHub main changed"):
            self.run_bridge("refresh")
        self.assertEqual(self.heads(), before)

    def test_github_race_after_overleaf_success_reports_partial_success(self):
        state_before = self.bootstrap()
        write(self.gh, "main.tex", "approved edit\n")
        approved = save(self.gh)
        self.request(approved)
        original_git = bridge.git

        def race(repo, *args, **kwargs):
            result = original_git(repo, *args, **kwargs)
            if Path(repo).name == "overleaf" and args == ("push", "origin", "HEAD:refs/heads/main"):
                racer = self.root / "racer"
                command(self.root, "clone", str(self.github), str(racer))
                write(racer, "AGENTS.md", "concurrent metadata edit\n")
                save(racer)
            return result

        with patch.object(bridge, "git", side_effect=race):
            with self.assertRaisesRegex(bridge.Refused, "PARTIAL SUCCESS"):
                self.run_bridge("sync")
        remote_state = json.loads(command(self.github, "show", "main:" + bridge.STATE))
        self.assertEqual(remote_state, state_before)
        self.assertEqual(command(self.github, "show", "main:AGENTS.md"), "concurrent metadata edit")
        self.assertEqual(command(self.overleaf, "show", "main:main.tex"), "approved edit")

    def test_dirty_worktree_and_symlink_are_refused(self):
        write(self.gh, "untracked.tex", "local work\n")
        with self.assertRaisesRegex(bridge.Refused, "not clean"):
            self.run_bridge("refresh")
        (self.gh / "untracked.tex").unlink()
        os.symlink("main.tex", self.gh / "link.tex")
        save(self.gh)
        with self.assertRaisesRegex(bridge.Refused, "Unsupported paper file mode"):
            self.run_bridge("refresh")


if __name__ == "__main__":
    unittest.main(verbosity=2)
