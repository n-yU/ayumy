"""Tests for SessionStore."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from report.session.store import (
    SessionStore,
    _effective_cwd,
    _expand_home,
    _extract_pr_issue_refs,
    _is_cross_repo,
)


def _make_store():
    """Create a SessionStore with mocked boto3."""
    with patch("report.session.store.boto3"):
        store = SessionStore("table")
    store.table = MagicMock()
    return store


def _make_session_client():
    """Create a mock SessionClient."""
    client = MagicMock()
    client.bucket = "bucket"
    return client


def _s3_body(text: str):
    """Create a mock S3 response body."""
    body = MagicMock()
    body.read.return_value = text.encode("utf-8")
    return {"Body": body}


def _jsonl_lines(*entries):
    """Build a JSONL string from entry dicts."""
    return "\n".join(json.dumps(e) for e in entries)


class TestExpandHome:
    @pytest.mark.parametrize(
        ("path", "project_cwd", "expected"),
        [
            pytest.param(
                "/abs/path", "/Users/foo/proj", "/abs/path", id="absolute_no_tilde"
            ),
            pytest.param(
                "relative", "/Users/foo/proj", "relative", id="relative_no_tilde"
            ),
            pytest.param(
                "~/Documents/x",
                "/Users/alice/proj",
                "/Users/alice/Documents/x",
                id="expands_users_prefix",
            ),
            pytest.param(
                "~/work/x",
                "/home/bob/proj",
                "/home/bob/work/x",
                id="expands_home_prefix",
            ),
            pytest.param(
                "~bob/work",
                "/Users/alice/proj",
                "~bob/work",
                id="does_not_expand_named_user_tilde",
            ),
            pytest.param(
                "~", "/Users/alice/proj", "/Users/alice", id="expands_bare_tilde"
            ),
        ],
    )
    def test_expands(self, path, project_cwd, expected):
        assert _expand_home(path, project_cwd) == expected

    def test_falls_back_when_project_cwd_unknown(self, monkeypatch):
        monkeypatch.setenv("HOME", "/tmp/fakehome")
        assert _expand_home("~/foo", None) == "/tmp/fakehome/foo"

    def test_falls_back_for_atypical_project_cwd(self, monkeypatch):
        monkeypatch.setenv("HOME", "/tmp/fakehome")
        assert _expand_home("~/foo", "/srv/app") == "/tmp/fakehome/foo"


class TestEffectiveCwd:
    @pytest.mark.parametrize(
        ("command", "project_cwd", "expected"),
        [
            pytest.param("git commit -m x", "/proj", None, id="no_cd"),
            pytest.param('echo "unterminated', "/proj", None, id="unparseable_command"),
            pytest.param(
                "cd /Users/a/other && git commit",
                "/Users/a/proj",
                "/Users/a/other",
                id="absolute_cd",
            ),
            pytest.param(
                "cd ../other && git commit",
                "/Users/a/proj",
                "/Users/a/other",
                id="relative_cd_against_project_cwd",
            ),
            pytest.param(
                "cd ~/work && git commit",
                "/Users/a/proj",
                "/Users/a/work",
                id="tilde_cd_against_inferred_home",
            ),
            # Only the leading `cd` is recognized; subshells / chained-then-cd are out of scope
            pytest.param(
                "ls && cd /other && git commit", "/proj", None, id="cd_not_leading"
            ),
            pytest.param(
                "( cd /other && git commit )", "/proj", None, id="cd_in_subshell"
            ),
            # `cd <path>` alone has no `&&` chain; treat as project cwd to avoid mis-tagging
            pytest.param("cd /other", "/proj", None, id="cd_without_chain"),
            # `cd -` points to the previous directory which is not derivable from the session log
            pytest.param("cd - && git commit", "/proj", None, id="cd_dash"),
            # `;` and `|` chains are out of recognized scope
            pytest.param(
                "cd /other; git commit", "/proj", None, id="cd_with_semicolon"
            ),
            pytest.param("cd /other | tee log", "/proj", None, id="cd_with_pipe"),
            # Bash allows `&&` without surrounding whitespace; the implementation normalizes spacing
            pytest.param(
                "cd /other&&git commit", "/proj", "/other", id="amp_amp_unspaced"
            ),
            pytest.param(
                "cd /other &&git commit", "/proj", "/other", id="amp_amp_left_spaced"
            ),
            pytest.param(
                "cd /other&& git commit", "/proj", "/other", id="amp_amp_right_spaced"
            ),
        ],
    )
    def test_resolves(self, command, project_cwd, expected):
        assert _effective_cwd(command, project_cwd) == expected

    @pytest.mark.parametrize(
        "command",
        [
            # `~bob/work` cannot be resolved from the session log; returned as absolute
            # so _is_cross_repo flags it as outside project
            pytest.param("cd ~bob/work && git commit", id="named_user_tilde"),
            # Variable expansions and command substitutions cannot be resolved from the
            # session log; treat as cross-repo rather than joining under project_cwd
            pytest.param("cd $OTHER_REPO && git commit", id="dollar_var"),
            pytest.param(
                "cd ${OTHER_REPO}/sub && git commit", id="dollar_braces_with_sub"
            ),
            pytest.param("cd $(pwd)/.. && git commit", id="command_substitution"),
        ],
    )
    def test_unresolvable_cd_classified_as_cross_repo(self, command):
        project_cwd = "/Users/alice/proj"
        result = _effective_cwd(command, project_cwd)
        assert result is not None
        assert _is_cross_repo(result, project_cwd) is True


class TestIsCrossRepo:
    @pytest.mark.parametrize(
        ("effective_cwd", "project_cwd", "expected"),
        [
            pytest.param("/anywhere", None, False, id="project_cwd_unknown"),
            pytest.param(None, "/Users/a/proj", False, id="effective_cwd_none"),
            pytest.param(
                "/Users/a/proj", "/Users/a/proj", False, id="within_project_exact"
            ),
            pytest.param(
                "/Users/a/proj/sub",
                "/Users/a/proj",
                False,
                id="within_project_descendant",
            ),
            pytest.param(
                "/Users/a/other", "/Users/a/proj", True, id="outside_project_sibling"
            ),
            # Same parent path prefix but not a descendant
            pytest.param(
                "/Users/a/project2",
                "/Users/a/project",
                True,
                id="outside_project_prefix_collision",
            ),
        ],
    )
    def test_classifies(self, effective_cwd, project_cwd, expected):
        assert _is_cross_repo(effective_cwd, project_cwd) is expected


class TestExtractPrIssueRefs:
    @pytest.mark.parametrize(
        ("command", "expected_pulls", "expected_issues"),
        [
            pytest.param("gh pr view 87 --json body", {87}, set(), id="gh_pr_view"),
            pytest.param("gh issue close 84", set(), {84}, id="gh_issue_close"),
            pytest.param(
                'gh pr create --title "PR 999" --body "..."',
                set(),
                set(),
                id="gh_pr_create_skipped",
            ),
            pytest.param(
                "gh issue list --limit 30", set(), set(), id="gh_issue_list_skipped"
            ),
            pytest.param(
                "gh api repos/n-yU/ayumy/pulls/82/comments",
                {82},
                set(),
                id="gh_api_pulls_path",
            ),
            pytest.param(
                "gh api repos/n-yU/ayumy/issues/84",
                set(),
                {84},
                id="gh_api_issues_path",
            ),
            pytest.param(
                'git commit -m "Fix #91"', {91}, {91}, id="git_hash_ref_ambiguous"
            ),
            pytest.param(
                "gh pr view 87 && gh issue close 84",
                {87},
                {84},
                id="chained_commands",
            ),
            pytest.param(
                'gh pr edit --body "fix" 87',
                {87},
                set(),
                id="gh_pr_with_flag_value_before_number",
            ),
            # Numbers inside quoted flag values must not be picked up
            pytest.param(
                'gh pr edit --body "fix 999" 87',
                {87},
                set(),
                id="gh_pr_skips_quoted_integer_in_flag_value",
            ),
            pytest.param(
                "ls /tmp/file_42.txt && python build.py 7",
                set(),
                set(),
                id="ignores_unrelated_commands",
            ),
            # `pulls/N` / `issues/N` outside of `gh api` are ignored
            pytest.param(
                "curl https://example.com/repo/pulls/123 && cat ./issues/456.txt",
                set(),
                set(),
                id="does_not_match_path_outside_gh_api",
            ),
        ],
    )
    def test_extracts(self, command, expected_pulls, expected_issues):
        pulls, issues = _extract_pr_issue_refs(command)
        assert pulls == expected_pulls
        assert issues == expected_issues


class TestBuildItems:
    def test_groups_by_date(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "my-repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T23:30:00+09:00",
                "message": {"content": "Day 1 message"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-29T00:30:00+09:00",
                "message": {"content": "Day 2 message"},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 2
        dates = {item["date"] for item in items}
        assert dates == {"2026-03-28", "2026-03-29"}

        for item in items:
            assert item["repo#session_id"] == "my-repo#s1"
            assert item["repo"] == "my-repo"
            assert item["project"] == "proj"

        assert keys == ["claude-sessions/proj/s1.jsonl"]

    def test_aggregates_messages_and_tools(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "First message"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:01:00+09:00",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read"},
                        {"type": "tool_use", "name": "Edit"},
                    ]
                },
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {"content": "Second message"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:06:00+09:00",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read"},
                        {"type": "tool_use", "name": "Bash"},
                    ]
                },
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        item = items[0]
        assert item["user_messages"] == ["First message", "Second message"]
        assert item["tools_used"] == ["Bash", "Edit", "Read"]
        assert item["start_time"] == "2026-03-28T10:00:00+09:00"
        assert item["end_time"] == "2026-03-28T10:06:00+09:00"

    def test_extracts_commits_from_tool_result(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "Fix the bug"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_123",
                            "content": "[feat/login a1b2c3d] Implement login flow\n 2 files changed",
                            "is_error": False,
                        },
                    ]
                },
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:10:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_456",
                            "content": "[feat/login e5f6a7b] Fix test failure\n 1 file changed",
                            "is_error": False,
                        },
                    ]
                },
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == [
            {
                "sha": "a1b2c3d",
                "message": "Implement login flow",
                "timestamp": "2026-03-28T10:05:00+09:00",
            },
            {
                "sha": "e5f6a7b",
                "message": "Fix test failure",
                "timestamp": "2026-03-28T10:10:00+09:00",
            },
        ]

    def test_extracts_root_and_detached_head_commits(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "Init repo"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_root",
                            "content": "[main (root-commit) a1b2c3d] Initial commit\n 1 file changed",
                            "is_error": False,
                        },
                    ]
                },
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:10:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_detach",
                            "content": "[detached HEAD e5f6a7b] Hotfix\n 1 file changed",
                            "is_error": False,
                        },
                    ]
                },
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == [
            {
                "sha": "a1b2c3d",
                "message": "Initial commit",
                "timestamp": "2026-03-28T10:05:00+09:00",
            },
            {
                "sha": "e5f6a7b",
                "message": "Hotfix",
                "timestamp": "2026-03-28T10:10:00+09:00",
            },
        ]

    def test_extracts_commit_after_hook_output(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "Commit with hooks"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_hook",
                            "content": "check formatting... ok\nrunning linter... passed\n[main a1b2c3d] Fix formatting\n 2 files changed",
                            "is_error": False,
                        },
                    ]
                },
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == [
            {
                "sha": "a1b2c3d",
                "message": "Fix formatting",
                "timestamp": "2026-03-28T10:05:00+09:00",
            },
        ]

    def test_extracts_multiple_commits_from_single_tool_result(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "Run commands"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_multi",
                            "content": "[main abc1234] First commit\n 1 file changed\n[main def5678] Second commit\n 2 files changed",
                            "is_error": False,
                        },
                    ]
                },
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == [
            {
                "sha": "abc1234",
                "message": "First commit",
                "timestamp": "2026-03-28T10:05:00+09:00",
            },
            {
                "sha": "def5678",
                "message": "Second commit",
                "timestamp": "2026-03-28T10:05:00+09:00",
            },
        ]

    def test_ignores_error_tool_results(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "Try commit"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_err",
                            "content": "[main abc1234] Some commit\n 1 file changed",
                            "is_error": True,
                        },
                    ]
                },
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == []

    def test_cross_midnight_commit_only_day(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        # Day 1 has a user message; Day 2 has only a tool_result with a commit
        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T23:50:00+09:00",
                "message": {"content": "Fix the bug"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-29T00:05:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_abc",
                            "content": "[main a1b2c3d] Apply fix\n 1 file changed",
                            "is_error": False,
                        },
                    ]
                },
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 2
        day1 = next(i for i in items if i["date"] == "2026-03-28")
        day2 = next(i for i in items if i["date"] == "2026-03-29")

        assert day1["user_messages"] == ["Fix the bug"]
        assert day1["session_commits"] == []
        assert day2["user_messages"] == []
        assert day2["session_commits"] == [
            {
                "sha": "a1b2c3d",
                "message": "Apply fix",
                "timestamp": "2026-03-29T00:05:00+09:00",
            },
        ]
        assert keys == ["claude-sessions/proj/s1.jsonl"]

    def test_skips_no_repo(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = None

        items, keys = store._build_items(client)

        assert items == []
        assert keys == []
        client.s3.get_object.assert_not_called()

    def test_extracts_pr_issue_refs_from_bash(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "ayumy"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "do work"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:01:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "gh pr view 87 --json body"},
                        },
                    ]
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:02:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "gh issue close 84"},
                        },
                    ]
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:03:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {
                                "command": "gh api repos/n-yU/ayumy/pulls/82/comments",
                            },
                        },
                    ]
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:04:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {
                                "command": 'git commit -m "Fix #91 and close #92"',
                            },
                        },
                    ]
                },
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, _ = store._build_items(client)

        assert len(items) == 1
        item = items[0]
        # 87 from `gh pr view`, 82 from `gh api .../pulls/82/...`, 91/92 from git #N
        assert item["session_pulls"] == [82, 87, 91, 92]
        # 84 from `gh issue close`, 91/92 from git #N (ambiguous)
        assert item["session_issues"] == [84, 91, 92]

    def test_ignores_non_bash_tool_use(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "ayumy"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "look at #87"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:01:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "https://github.com/n-yU/ayumy/pull/87",
                        },
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "/tmp/notes_42.md"},
                        },
                    ]
                },
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, _ = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_pulls"] == []
        assert items[0]["session_issues"] == []

    def test_skips_no_user_messages(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert items == []
        assert keys == []


class TestBuildItemsCwdFilter:
    """`cd <path>` to another repo must drop both commits and refs."""

    def setup_method(self):
        self.store = _make_store()
        self.client = _make_session_client()
        self.client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        self.client.read_repo_name.return_value = "repo"

    def _run(self, *entries):
        self.client.s3.get_object.return_value = _s3_body(_jsonl_lines(*entries))
        items, _ = self.store._build_items(self.client)
        return items

    def test_drops_commit_after_cd_to_other_repo(self):
        items = self._run(
            {
                "type": "user",
                "cwd": "/Users/a/proj",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "work"},
            },
            {
                "type": "assistant",
                "cwd": "/Users/a/proj",
                "timestamp": "2026-03-28T10:01:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_x",
                            "name": "Bash",
                            "input": {"command": "cd ~/other && git commit -m x"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "cwd": "/Users/a/proj",
                "timestamp": "2026-03-28T10:02:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_x",
                            "content": "[main abc1234] cross-repo commit\n 1 file",
                            "is_error": False,
                        }
                    ]
                },
            },
        )
        assert items[0]["session_commits"] == []

    def test_keeps_commit_within_project_cwd(self):
        items = self._run(
            {
                "type": "user",
                "cwd": "/Users/a/proj",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "work"},
            },
            {
                "type": "assistant",
                "cwd": "/Users/a/proj",
                "timestamp": "2026-03-28T10:01:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_x",
                            "name": "Bash",
                            "input": {"command": "git commit -m x"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "cwd": "/Users/a/proj",
                "timestamp": "2026-03-28T10:02:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_x",
                            "content": "[main abc1234] in-repo commit\n 1 file",
                            "is_error": False,
                        }
                    ]
                },
            },
        )
        assert items[0]["session_commits"] == [
            {
                "sha": "abc1234",
                "message": "in-repo commit",
                "timestamp": "2026-03-28T10:02:00+09:00",
            }
        ]

    def test_drops_refs_after_cd_to_other_repo(self):
        items = self._run(
            {
                "type": "user",
                "cwd": "/Users/a/proj",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "work"},
            },
            {
                "type": "assistant",
                "cwd": "/Users/a/proj",
                "timestamp": "2026-03-28T10:01:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_x",
                            "name": "Bash",
                            "input": {"command": "cd ~/other && gh pr view 99"},
                        }
                    ]
                },
            },
        )
        assert items[0]["session_pulls"] == []

    def test_does_not_filter_when_project_cwd_missing(self):
        # No entry exposes cwd; the filter must stay disabled so legacy
        # sessions without cwd still record commits
        items = self._run(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "work"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:02:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_x",
                            "content": "[main abc1234] no cwd info\n 1 file",
                            "is_error": False,
                        }
                    ]
                },
            },
        )
        assert len(items[0]["session_commits"]) == 1

    def test_drops_commit_when_entry_cwd_is_outside_project(self):
        # project_cwd is fixed by the first entry; a later assistant entry
        # with cwd outside project must drop commits even without a leading `cd`
        items = self._run(
            {
                "type": "user",
                "cwd": "/Users/a/proj",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "work"},
            },
            {
                "type": "assistant",
                "cwd": "/Users/a/other",
                "timestamp": "2026-03-28T10:01:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_x",
                            "name": "Bash",
                            "input": {"command": "git commit -m x"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "cwd": "/Users/a/other",
                "timestamp": "2026-03-28T10:02:00+09:00",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_x",
                            "content": "[main abc1234] outside cwd\n 1 file",
                            "is_error": False,
                        }
                    ]
                },
            },
        )
        assert items[0]["session_commits"] == []


class TestWriteItems:
    def test_uses_update_item(self):
        store = _make_store()

        items = [
            {
                "date": "2026-03-28",
                "repo#session_id": "repo#s1",
                "repo": "repo",
                "updated_at": "2026-03-28T00:00:00+00:00",
            },
            {
                "date": "2026-03-29",
                "repo#session_id": "repo#s2",
                "repo": "repo",
                "updated_at": "2026-03-29T00:00:00+00:00",
            },
        ]

        store._write_items(items)

        assert store.table.update_item.call_count == 2
        first_call = store.table.update_item.call_args_list[0]
        assert first_call.kwargs["Key"] == {
            "date": "2026-03-28",
            "repo#session_id": "repo#s1",
        }
        assert "UpdateExpression" in first_call.kwargs


class TestIngest:
    def test_returns_processed_keys(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "Hello"},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        keys = store.ingest(client)

        assert keys == ["claude-sessions/proj/s1.jsonl"]
        store.table.update_item.assert_called_once()


class TestFetchSessions:
    def test_returns_session_activity(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "my-repo#s1",
                    "repo": "my-repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["Fix bug"],
                    "tools_used": ["Edit", "Read"],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        assert bool(activity)
        assert "my-repo" in activity
        sessions = activity.get("my-repo")
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "s1"
        assert sessions[0]["user_messages"] == ["Fix bug"]

    def test_groups_by_repo(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo-a#s1",
                    "repo": "repo-a",
                    "project": "proj-a",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["msg1"],
                    "tools_used": [],
                },
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo-b#s2",
                    "repo": "repo-b",
                    "project": "proj-b",
                    "start_time": "2026-03-28T12:00:00+09:00",
                    "end_time": "2026-03-28T13:00:00+09:00",
                    "user_messages": ["msg2"],
                    "tools_used": [],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        assert set(activity.keys()) == {"repo-a", "repo-b"}

    def test_returns_empty_for_no_data(self):
        store = _make_store()
        store.table.query.return_value = {"Items": []}

        activity = store.fetch_sessions("2026-03-28")

        assert not activity

    def test_sorts_sessions_by_start_time(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s2",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T14:00:00+09:00",
                    "end_time": "2026-03-28T15:00:00+09:00",
                    "user_messages": ["later"],
                    "tools_used": [],
                },
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s1",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["earlier"],
                    "tools_used": [],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        sessions = activity.get("repo")
        assert sessions[0]["user_messages"] == ["earlier"]
        assert sessions[1]["user_messages"] == ["later"]

    def test_includes_session_commits(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s1",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["Fix bug"],
                    "tools_used": ["Edit"],
                    "session_commits": [
                        {
                            "sha": "a1b2c3d",
                            "message": "Fix the bug",
                            "timestamp": "2026-03-28T10:30:00+09:00",
                        },
                    ],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        sessions = activity.get("repo")
        assert sessions[0]["session_commits"] == [
            {
                "sha": "a1b2c3d",
                "message": "Fix the bug",
                "timestamp": "2026-03-28T10:30:00+09:00",
            },
        ]

    def test_includes_session_pulls_and_issues(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s1",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["msg"],
                    "tools_used": [],
                    "session_pulls": [87, 82],
                    "session_issues": [84],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        sessions = activity.get("repo")
        assert sessions[0]["session_pulls"] == [87, 82]
        assert sessions[0]["session_issues"] == [84]

    def test_defaults_session_pulls_and_issues_when_missing(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s1",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["msg"],
                    "tools_used": [],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        sessions = activity.get("repo")
        assert sessions[0]["session_pulls"] == []
        assert sessions[0]["session_issues"] == []

    def test_defaults_session_commits_when_missing(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s1",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["msg"],
                    "tools_used": [],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        sessions = activity.get("repo")
        assert sessions[0]["session_commits"] == []


class TestScanBackfillDates:
    def test_returns_unreported_dates(self):
        store = _make_store()
        store.table.scan.return_value = {
            "Items": [
                {"date": "2026-03-26"},
                {"date": "2026-03-27"},
            ],
        }

        dates = store.scan_backfill_dates(date(2026, 3, 28))

        assert dates == [date(2026, 3, 26), date(2026, 3, 27)]

    def test_excludes_primary_date(self):
        store = _make_store()
        store.table.scan.return_value = {
            "Items": [
                {"date": "2026-03-28"},
            ],
        }

        dates = store.scan_backfill_dates(date(2026, 3, 28))

        assert dates == []

    def test_deduplicates_dates(self):
        store = _make_store()
        store.table.scan.return_value = {
            "Items": [
                {"date": "2026-03-27"},
                {"date": "2026-03-27"},
            ],
        }

        dates = store.scan_backfill_dates(date(2026, 3, 28))

        assert dates == [date(2026, 3, 27)]

    def test_returns_empty_when_all_reported(self):
        store = _make_store()
        store.table.scan.return_value = {"Items": []}

        dates = store.scan_backfill_dates(date(2026, 3, 28))

        assert dates == []


class TestMarkReported:
    def test_updates_all_items_for_date(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {"date": "2026-03-28", "repo#session_id": "repo#s1"},
                {"date": "2026-03-28", "repo#session_id": "repo#s2"},
            ],
        }

        store.mark_reported("2026-03-28")

        assert store.table.update_item.call_count == 2
        for c in store.table.update_item.call_args_list:
            assert c.kwargs["UpdateExpression"] == "SET reported_at = :ts"
            assert ":ts" in c.kwargs["ExpressionAttributeValues"]

    def test_no_op_for_empty_date(self):
        store = _make_store()
        store.table.query.return_value = {"Items": []}

        store.mark_reported("2026-03-28")

        store.table.update_item.assert_not_called()
