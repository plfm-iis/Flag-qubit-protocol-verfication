"""Explicit, revision-checked paper exchange with the specified Overleaf project."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


PROJECT = "6adb7e8c4019793a7dcea09f"
REPOSITORY = "plfm-iis/Flag-qubit-protocol-verfication"
REMOTE = f"https://git@git.overleaf.com/{PROJECT}"
STATE = ".github/overleaf-state.json"
RECONCILE_REQUEST = ".github/overleaf-reconcile-request.json"
GITHUB_AUTHORITY_REQUEST = ".github/overleaf-github-authority-request.json"
BASELINE_CHECK_REQUEST = ".github/overleaf-baseline-check-request.json"
REQUEST = ".github/overleaf-sync-request.json"
METADATA_FILES = {"AGENTS.md", "PAPER_CONTEXT.md", "CLAIMS.md"}
SHA = re.compile(r"[0-9a-f]{40}\Z")


class Refused(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def git(repo, *args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode:
        raise Refused(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result


def output(repo, *args):
    return git(repo, *args).stdout.decode("utf-8").strip()


def paper_path(path):
    return path.split("/")[0] not in {".github", ".ai"} and path not in METADATA_FILES


def manifest(repo, ref="HEAD"):
    result = {}
    for entry in git(repo, "ls-tree", "-rz", ref).stdout.split(b"\0"):
        if not entry:
            continue
        attributes, encoded_path = entry.split(b"\t", 1)
        mode, kind, oid = attributes.decode("ascii").split()
        path = encoded_path.decode("utf-8")
        if not paper_path(path):
            continue
        require(not path.startswith("/") and all(p not in {"", ".", "..", ".git"} for p in path.split("/")), "Unsafe paper path.")
        require(kind == "blob" and mode == "100644", f"Unsupported paper file mode at {path}: {mode}. Only regular non-executable files are supported.")
        result[path] = {"oid": oid, "mode": mode}
    return result


def read_json(repo, ref, path):
    if git(repo, "cat-file", "-e", f"{ref}:{path}", check=False).returncode:
        return None
    try:
        value = json.loads(git(repo, "show", f"{ref}:{path}").stdout)
    except (ValueError, UnicodeDecodeError) as error:
        raise Refused(f"Invalid JSON in {path}.") from error
    require(isinstance(value, dict) and value.get("schema") == 1, f"Unsupported schema in {path}.")
    return value


def remote_head(repo, remote="origin"):
    lines = output(repo, "ls-remote", remote, "refs/heads/main").splitlines()
    require(len(lines) == 1, "Remote must have exactly one main branch.")
    return lines[0].split()[0]


def clean(repo):
    require(not output(repo, "status", "--porcelain", "--untracked-files=all"), "Working tree is not clean; no files were changed.")


def ancestor(repo, older, newer):
    return git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def replace_paper(source, destination, source_manifest):
    """Copy only tracked paper blobs; preserve all GitHub bridge metadata."""
    old = manifest(destination)
    for path in sorted(old, key=len, reverse=True):
        if path not in source_manifest:
            target = destination / path
            target.unlink()
            parent = target.parent
            while parent != destination:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    for path, item in source_manifest.items():
        if old.get(path) == item:
            continue
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git(source, "cat-file", "blob", item["oid"]).stdout)
        target.chmod(0o644)


def commit(repo, message, allowed_paths, author=None):
    # The source snapshot may legitimately track files matched by .gitignore.
    git(repo, "add", "-A", "-f")
    staged = set(git(repo, "diff", "--cached", "--name-only", "-z").stdout.decode("utf-8").split("\0")) - {""}
    require(staged <= allowed_paths, "Unexpected files were staged; refusing to commit.")
    if not staged:
        return False
    name, email = author or sync_actor()
    git(repo, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", message)
    return True


def sync_actor():
    """Use GitHub's event identity for human-triggered synchronization."""
    actor = os.environ.get("GITHUB_ACTOR", "")
    actor_id = os.environ.get("SYNC_GITHUB_ACTOR_ID", "")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if (event in {"push", "workflow_dispatch"} and
            re.fullmatch(r"[A-Za-z0-9-]+", actor) and
            re.fullmatch(r"[0-9]+", actor_id)):
        return actor, f"{actor_id}+{actor}@users.noreply.github.com"
    return "paper-sync-bot", "paper-sync-bot@users.noreply.github.com"


def overleaf_author(repo):
    """Trust the recorded Overleaf Git author when an ordinary email exists."""
    parts = git(repo, "show", "-s", "--format=%an%n%ae", "HEAD").stdout.decode("utf-8").strip().splitlines()
    if len(parts) != 2:
        return None
    name, email = parts
    if (not name or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", email)
            or any(x in (name + " " + email).lower()
                   for x in ("overleaf", "paper-sync-bot", "github-actions"))
            or "\n" in name or "\r" in name):
        return None
    return name, email


class Bridge:
    def __init__(self, repo, remote=REMOTE):
        self.repo = Path(repo).resolve()
        self.remote = remote
        self.start = output(self.repo, "rev-parse", "HEAD")

    def check_github(self):
        require(remote_head(self.repo) == self.start, "GitHub main changed; refresh and review again.")

    def state(self):
        state = read_json(self.repo, self.start, STATE)
        if state is not None:
            require(state.get("project_id") == PROJECT, "State belongs to another Overleaf project.")
            require(isinstance(state.get("overleaf_revision"), str) and SHA.fullmatch(state["overleaf_revision"]), "Invalid Overleaf revision in state.")
            require(isinstance(state.get("paper_manifest"), dict), "Missing paper manifest in state.")
        return state

    def save_state(self, revision, files):
        target = self.repo / STATE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"schema": 1, "project_id": PROJECT, "overleaf_revision": revision, "paper_manifest": files}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def run(self, mode, force_dry_run=False):
        clean(self.repo)
        self.check_github()
        print(f"Checking {mode} at GitHub {self.start}, Overleaf project {PROJECT}.")
        with tempfile.TemporaryDirectory(prefix="overleaf-bridge-") as directory:
            overleaf = Path(directory) / "overleaf"
            git(self.repo, "clone", "--quiet", "--branch", "main", self.remote, str(overleaf))
            clean(overleaf)
            revision = output(overleaf, "rev-parse", "HEAD")
            ol_files = manifest(overleaf)
            gh_files = manifest(self.repo)
            state = self.state()
            if mode == "refresh":
                self.refresh(overleaf, revision, ol_files, gh_files, state)
            elif mode == "sync":
                self.sync(overleaf, revision, ol_files, gh_files, state, force_dry_run)
            elif mode == "auto":
                self.auto(overleaf, revision, ol_files, gh_files, state)
            else:
                raise Refused("Expected refresh, sync, or auto.")

    def refresh(self, overleaf, revision, ol_files, gh_files, state):
        if state is None:
            require(gh_files == ol_files, "Initial GitHub/Overleaf paper snapshots differ. No paper was written; compare and approve the differences first.")
        else:
            require(gh_files == state["paper_manifest"], "GitHub has paper edits not confirmed on Overleaf. Refresh would overwrite them; refusing.")
            require(ancestor(overleaf, state["overleaf_revision"], revision), "Overleaf baseline is not an ancestor of the current project; refusing replacement.")
            require(manifest(overleaf, state["overleaf_revision"]) == state["paper_manifest"], "Stored baseline does not match Overleaf history.")
        require(remote_head(overleaf) == revision, "Overleaf changed during refresh; run refresh again.")
        self.check_github()
        replace_paper(overleaf, self.repo, ol_files)
        self.save_state(revision, ol_files)
        changed = commit(self.repo, "[overleaf-sync] Refresh paper and record Overleaf baseline",
                         set(gh_files) | set(ol_files) | {STATE}, author=overleaf_author(overleaf))
        require(manifest(self.repo) == ol_files, "Git staging altered the imported paper snapshot; no GitHub push.")
        if changed:
            self.check_github()
            require(remote_head(overleaf) == revision, "Overleaf changed before refresh completed; run refresh again.")
            git(self.repo, "push", "origin", "HEAD:refs/heads/main")
        require(remote_head(self.repo) == output(self.repo, "rev-parse", "HEAD"), "GitHub changed after refresh; check the latest state again.")
        require(remote_head(overleaf) == revision, "Overleaf changed after refresh; run refresh again before substantive work.")
        print(f"Refresh confirmed Overleaf {revision}; {len(ol_files)} paper files.")

    def auto(self, overleaf, revision, ol_files, gh_files, state):
        """Push one-sided changes only; reject concurrent independent edits."""
        baseline_check = read_json(self.repo, self.start, BASELINE_CHECK_REQUEST)
        if baseline_check is not None:
            self.check_and_rebase_baseline(overleaf, revision, ol_files, gh_files, state)
            return
        authority = read_json(self.repo, self.start, GITHUB_AUTHORITY_REQUEST)
        if authority is not None:
            self.github_authoritative(overleaf, revision, ol_files, gh_files, state, authority)
            return
        require(state is not None, "Missing confirmed Overleaf baseline.")
        baseline = state["paper_manifest"]
        gh_changed = gh_files != baseline
        ol_changed = ol_files != baseline
        if gh_changed and ol_changed:
            request = read_json(self.repo, self.start, RECONCILE_REQUEST)
            if request is not None:
                self.reconcile(overleaf, revision, ol_files, gh_files, state, request)
                return
            raise Refused("Both GitHub and Overleaf changed: conflict, no overwrites.")
        if ol_changed or (not gh_changed and revision != state["overleaf_revision"]):
            self.refresh(overleaf, revision, ol_files, gh_files, state)
            return
        if not gh_changed:
            print("No paper changes; snapshots already synchronized.")
            return
        require(revision == state["overleaf_revision"] and ol_files == baseline,
                "Overleaf revision moved; cannot overwrite.")
        self.check_github()
        require(remote_head(overleaf) == revision, "Overleaf moved during check.")
        replace_paper(self.repo, overleaf, gh_files)
        require(commit(overleaf, "[overleaf-sync] Apply GitHub paper revision " + self.start,
                       set(ol_files) | set(gh_files)), "No changes to synchronize.")
        require(manifest(overleaf) == gh_files, "Git staging modified the paper snapshot.")
        new_revision = output(overleaf, "rev-parse", "HEAD")
        self.check_github()
        require(remote_head(overleaf) == revision, "Overleaf changed before push.")
        git(overleaf, "push", "origin", "HEAD:refs/heads/main")
        print(f"Overleaf accepted GitHub paper revision: {new_revision}.")
        try:
            require(remote_head(overleaf) == new_revision, "Overleaf moved after push.")
            self.check_github()
            self.save_state(new_revision, gh_files)
            commit(self.repo, "[overleaf-sync] Record automatic GitHub sync", {STATE})
            git(self.repo, "push", "origin", "HEAD:refs/heads/main")
            require(remote_head(self.repo) == output(self.repo, "rev-parse", "HEAD"),
                    "GitHub changed after baseline update.")
            require(remote_head(overleaf) == new_revision,
                    "Overleaf changed after baseline update.")
        except Refused as error:
            raise Refused(f"PARTIAL SUCCESS: Overleaf accepted {new_revision}, "
                          f"but baseline update failed: {error}") from error
        print(f"Automatic sync confirmed: {new_revision}.")

    def check_and_rebase_baseline(self, overleaf, revision, ol_files, gh_files, state):
        """Compare exact paper blobs; refresh metadata only if already identical."""
        require(state is not None, "Missing confirmed Overleaf baseline.")
        paths = sorted(set(gh_files) | set(ol_files))
        differences = [path for path in paths if gh_files.get(path) != ol_files.get(path)]
        if differences:
            import difflib
            for path in differences:
                github_oid = gh_files.get(path, {}).get("oid", "<absent>")
                overleaf_oid = ol_files.get(path, {}).get("oid", "<absent>")
                print(f"DIFFERENCE {path}: GitHub={github_oid}; Overleaf={overleaf_oid}", file=sys.stderr)
                if path.endswith(".tex") and path in gh_files and path in ol_files:
                    a = git(self.repo, "show", "HEAD:" + path).stdout.decode("utf-8").splitlines()
                    b = git(overleaf, "show", "HEAD:" + path).stdout.decode("utf-8").splitlines()
                    diff = list(difflib.unified_diff(a, b, fromfile="GitHub/" + path,
                                                     tofile="Overleaf/" + path, n=3))
                    for line in diff[:120]:
                        print(line, file=sys.stderr)
                    if len(diff) > 120:
                        print(f"[DIFF TRUNCATED: {len(diff)} lines]", file=sys.stderr)
            raise Refused("Paper snapshots differ; no contents or baseline were changed.")
        require(ancestor(overleaf, state["overleaf_revision"], revision),
                "Overleaf history no longer descends from recorded baseline.")
        require(manifest(overleaf, state["overleaf_revision"]) == state["paper_manifest"],
                "Recorded baseline does not match Overleaf history.")
        self.check_github()
        require(remote_head(overleaf) == revision, "Overleaf changed before state refresh.")
        self.save_state(revision, gh_files)
        (self.repo / BASELINE_CHECK_REQUEST).unlink()
        require(commit(self.repo, "[overleaf-sync] Confirm identical paper snapshots and refresh baseline",
                       {STATE, BASELINE_CHECK_REQUEST}),
                "Expected a pending request to clear.")
        require(remote_head(overleaf) == revision, "Overleaf changed before GitHub push.")
        self.check_github()
        git(self.repo, "push", "origin", "HEAD:refs/heads/main")
        require(remote_head(overleaf) == revision, "Overleaf changed after baseline update.")
        print(f"VERIFIED: all {len(paths)} paper files identical; baseline set to {revision}.")

    def github_authoritative(self, overleaf, revision, ol_files, gh_files, state, request):
        """One-time approved GitHub-authoritative sync, backing up Overleaf first."""
        require(state is not None, "A confirmed baseline is required.")
        require(request.get("project_id") == PROJECT, "Project mismatch.")
        approved = request.get("approved_github_commit")
        expected_oid = request.get("approved_github_samplepaper_oid")
        require(isinstance(approved, str) and SHA.fullmatch(approved), "Invalid approved commit.")
        require(isinstance(expected_oid, str) and SHA.fullmatch(expected_oid), "Invalid approved manuscript blob.")
        require(ancestor(self.repo, approved, self.start), "Approved commit not in main history.")
        require(manifest(self.repo, approved) == gh_files, "GitHub paper changed since approval.")
        require(gh_files.get("samplepaper.tex", {}).get("oid") == expected_oid,
                "Manuscript differs from approved GitHub version.")
        expected_overleaf_oid = request.get("expected_overleaf_samplepaper_oid")
        require(isinstance(expected_overleaf_oid, str) and SHA.fullmatch(expected_overleaf_oid),
                "Explicit reviewed Overleaf manuscript blob is required.")
        require(ol_files.get("samplepaper.tex", {}).get("oid") == expected_overleaf_oid,
                "Overleaf manuscript changed since comparison; refusing overwrite.")
        baseline = state["paper_manifest"]
        require(ancestor(overleaf, state["overleaf_revision"], revision),
                "Overleaf history rewritten since baseline.")
        require(manifest(overleaf, state["overleaf_revision"]) == baseline,
                "Recorded Overleaf baseline differs from history.")
        require(set(ol_files) == set(gh_files) == set(baseline),
                "Paper file inventories differ; manual reconciliation required.")
        require(all(ol_files[p] == gh_files[p] == baseline[p]
                    for p in baseline if p != "samplepaper.tex"),
                "Another paper file differs; manual reconciliation required.")
        self.check_github()
        require(remote_head(overleaf) == revision, "Overleaf changed before backup.")
        backup = "backup/overleaf-before-github-authority-" + revision[:12]
        git(self.repo, "fetch", "--no-tags", str(overleaf), "HEAD")
        require(output(self.repo, "rev-parse", "FETCH_HEAD") == revision,
                "Overleaf backup fetch did not match current revision.")
        git(self.repo, "push", "origin", revision + ":refs/heads/" + backup)
        print(f"Saved Overleaf revision {revision} in GitHub branch {backup}.")
        self.check_github()
        require(remote_head(overleaf) == revision, "Overleaf changed after backup.")
        replace_paper(self.repo, overleaf, gh_files)
        changed = commit(overleaf, "[overleaf-sync] Apply approved GitHub manuscript",
                         {"samplepaper.tex"})
        require(manifest(overleaf) == gh_files, "Overleaf result differs from GitHub.")
        if changed:
            new_revision = output(overleaf, "rev-parse", "HEAD")
            self.check_github()
            require(remote_head(overleaf) == revision, "Overleaf changed before push.")
            git(overleaf, "push", "origin", "HEAD:refs/heads/main")
            require(remote_head(overleaf) == new_revision, "Overleaf changed after push.")
        else:
            new_revision = revision
        print(f"Overleaf accepted GitHub manuscript at {new_revision}.")
        try:
            self.check_github()
            self.save_state(new_revision, gh_files)
            (self.repo / GITHUB_AUTHORITY_REQUEST).unlink()
            commit(self.repo, "[overleaf-sync] Confirm GitHub-authoritative baseline",
                   {STATE, GITHUB_AUTHORITY_REQUEST})
            git(self.repo, "push", "origin", "HEAD:refs/heads/main")
            require(remote_head(self.repo) == output(self.repo, "rev-parse", "HEAD"),
                    "GitHub baseline update not confirmed.")
            require(remote_head(overleaf) == new_revision,
                    "Overleaf changed after baseline update.")
        except Refused as error:
            raise Refused(f"PARTIAL SUCCESS: Overleaf accepted {new_revision}, "
                          f"but GitHub baseline update failed: {error}") from error
        print(f"GitHub-authoritative sync confirmed at Overleaf {new_revision}.")

    def reconcile(self, overleaf, revision, ol_files, gh_files, state, request):
        """One-shot, exact-blob reconciliation of a reviewed Overleaf manuscript."""
        require(state is not None, "A confirmed baseline is required for reconciliation.")
        require(request.get("project_id") == PROJECT, "Reconciliation project ID mismatch.")
        expected_baseline = request.get("expected_baseline_revision")
        expected_overleaf_oid = request.get("expected_overleaf_samplepaper_oid")
        expected_github_oid = request.get("expected_github_samplepaper_oid")
        require(expected_baseline == state["overleaf_revision"], "Recorded baseline changed since review.")
        require(isinstance(expected_overleaf_oid, str) and SHA.fullmatch(expected_overleaf_oid),
                "Reconciliation requires an exact, reviewed Overleaf manuscript blob SHA.")
        require(isinstance(expected_github_oid, str) and SHA.fullmatch(expected_github_oid),
                "Reconciliation requires an exact, approved GitHub manuscript blob SHA.")
        require(ancestor(overleaf, expected_baseline, revision),
                "Overleaf history was rewritten since the recorded baseline.")
        require(manifest(overleaf, expected_baseline) == state["paper_manifest"],
                "Overleaf history does not match the recorded baseline.")
        require(gh_files.get("samplepaper.tex", {}).get("oid") == expected_github_oid,
                "GitHub manuscript changed since the approved reconciliation request.")
        require(ol_files.get("samplepaper.tex", {}).get("oid") == expected_overleaf_oid,
                "Overleaf manuscript differs from the user-reviewed 3:35 pm snapshot; no writes.")
        baseline = state["paper_manifest"]
        require(set(gh_files) == set(ol_files) == set(baseline),
                "Paper file inventory differs on one side; no writes.")
        require(all(ol_files[path] == gh_files[path] == baseline[path]
                    for path in baseline if path != "samplepaper.tex"),
                "Another paper file changed; no writes.")
        self.check_github()
        require(remote_head(overleaf) == revision, "Overleaf moved before reconciliation.")
        replace_paper(self.repo, overleaf, gh_files)
        require(commit(overleaf, "[overleaf-sync] Reconcile reviewed manuscript from GitHub",
                       {"samplepaper.tex"}), "No Overleaf manuscript change to reconcile.")
        require(manifest(overleaf) == gh_files, "Overleaf staged manuscript differs from GitHub.")
        new_revision = output(overleaf, "rev-parse", "HEAD")
        self.check_github()
        require(remote_head(overleaf) == revision, "Overleaf moved before push.")
        git(overleaf, "push", "origin", "HEAD:refs/heads/main")
        print(f"Overleaf accepted reviewed GitHub manuscript at {new_revision}.")
        try:
            require(remote_head(overleaf) == new_revision, "Overleaf moved after push.")
            self.check_github()
            self.save_state(new_revision, gh_files)
            (self.repo / RECONCILE_REQUEST).unlink()
            commit(self.repo, "[overleaf-sync] Record reconciliation and clear one-shot request",
                   {STATE, RECONCILE_REQUEST})
            git(self.repo, "push", "origin", "HEAD:refs/heads/main")
            require(remote_head(self.repo) == output(self.repo, "rev-parse", "HEAD"),
                    "GitHub baseline commit was not confirmed.")
            require(remote_head(overleaf) == new_revision,
                    "Overleaf moved after GitHub baseline was recorded.")
        except Refused as error:
            raise Refused(f"PARTIAL SUCCESS: Overleaf accepted {new_revision}; "
                          f"GitHub baseline needs reconciliation: {error}") from error
        print(f"Reviewed manuscript reconciliation confirmed at Overleaf {new_revision}.")

    def sync(self, overleaf, revision, ol_files, gh_files, state, force_dry_run):
        request = read_json(self.repo, self.start, REQUEST)
        if request is None:
            require(gh_files == ol_files, "No sync request and paper snapshots differ. Dry run made no changes.")
            if state is not None:
                require(revision == state["overleaf_revision"] and gh_files == state["paper_manifest"], "Baseline is stale; refresh before requesting a sync.")
            print(f"Dry run: connection and equal paper snapshots verified at Overleaf {revision}; no writes.")
            return
        require(isinstance(request.get("dry_run"), bool), "Sync request must explicitly contain boolean dry_run.")
        require(state is not None, "Run an initial refresh before requesting a sync.")
        expected = request.get("expected_overleaf_revision")
        approved = request.get("approved_github_commit")
        require(isinstance(expected, str) and SHA.fullmatch(expected), "Request needs the full expected Overleaf revision.")
        require(isinstance(approved, str) and SHA.fullmatch(approved), "Request needs the full approved GitHub commit.")
        require(expected == state["overleaf_revision"] == revision, "Overleaf revision changed since approval; refresh, regenerate the exact diff, and obtain approval again.")
        require(ol_files == state["paper_manifest"], "Overleaf contents do not match the recorded baseline.")
        require(ancestor(self.repo, approved, self.start), "Approved GitHub commit is not in current main history.")
        require(gh_files == manifest(self.repo, approved), "GitHub paper differs from the approved commit; obtain approval for the current exact diff.")
        self.check_github()
        require(remote_head(overleaf) == revision, "Overleaf changed after approval; no outbound write.")
        if force_dry_run or request["dry_run"]:
            print(f"Dry run: approved snapshot {approved} can be applied to Overleaf {revision}; no writes.")
            return
        replace_paper(self.repo, overleaf, gh_files)
        changed = commit(overleaf, "Apply approved GitHub paper revision " + approved, set(ol_files) | set(gh_files))
        require(manifest(overleaf) == gh_files, "Git staging altered the approved paper snapshot; no Overleaf push.")
        if not changed:
            print("Approved paper snapshot is already in Overleaf; no writes needed.")
            return
        new_revision = output(overleaf, "rev-parse", "HEAD")
        self.check_github()
        require(remote_head(overleaf) == revision, "Overleaf changed before push; no outbound write.")
        git(overleaf, "push", "origin", "HEAD:refs/heads/main")
        # Two remotes cannot be updated atomically. A later failure leaves the
        # confirmed Overleaf commit intact and requires explicit reconciliation.
        print(f"Overleaf accepted approved snapshot: {new_revision}.")
        try:
            require(remote_head(overleaf) == new_revision, "Overleaf moved again after the successful push.")
            self.check_github()
            self.save_state(new_revision, gh_files)
            commit(self.repo, "[overleaf-sync] Record successful approved sync", {STATE})
            git(self.repo, "push", "origin", "HEAD:refs/heads/main")
            require(remote_head(self.repo) == output(self.repo, "rev-parse", "HEAD"), "GitHub moved after recording the baseline.")
            require(remote_head(overleaf) == new_revision, "Overleaf moved after recording the baseline.")
        except Refused as error:
            raise Refused(f"PARTIAL SUCCESS: Overleaf accepted {new_revision}, but GitHub baseline was not confirmed. Do not retry blindly; reconcile both remotes. {error}") from error
        print(f"Sync and GitHub baseline confirmed at Overleaf {new_revision}.")


def main():
    require(len(sys.argv) == 2 and sys.argv[1] in {"refresh", "sync", "auto"}, "Usage: overleaf_bridge.py refresh|sync|auto")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Wrong GitHub repository.")
        require(os.environ.get("GITHUB_REF") == "refs/heads/main", "Bridge runs only on main.")
        require(output(Path.cwd(), "rev-parse", "HEAD") == os.environ.get("GITHUB_SHA"), "Checkout does not match the triggering commit.")
    require(bool(os.environ.get("OVERLEAF_GIT_TOKEN")), "OVERLEAF_GIT_TOKEN is unavailable to this repository.")
    with tempfile.TemporaryDirectory(prefix="overleaf-askpass-") as directory:
        askpass = Path(directory) / "askpass.sh"
        askpass.write_text('#!/bin/sh\ncase "$1" in\n  *Username*) printf "%s\\n" git ;;\n  *Password*) printf "%s\\n" "$OVERLEAF_GIT_TOKEN" ;;\n  *) exit 1 ;;\nesac\n', encoding="utf-8")
        askpass.chmod(0o700)
        os.environ["GIT_ASKPASS"] = str(askpass)
        os.environ["GIT_TERMINAL_PROMPT"] = "0"
        Bridge(Path.cwd()).run(sys.argv[1], os.environ.get("BRIDGE_FORCE_DRY_RUN", "true").lower() != "false")


if __name__ == "__main__":
    try:
        main()
    except Refused as error:
        print(f"Bridge stopped: {error}", file=sys.stderr)
        sys.exit(1)
