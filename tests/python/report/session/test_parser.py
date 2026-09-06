"""Tests for SessionLogParser."""

import logging

import pytest

from report.session import parser

from . import _builders

COMMIT_TS = "2026-03-28T10:05:00+09:00"
PROJECT_CWD = "/Users/a/proj"


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
        assert parser._expand_home(path, project_cwd) == expected

    def test_falls_back_when_project_cwd_unknown(self, monkeypatch):
        monkeypatch.setenv("HOME", "/tmp/fakehome")
        assert parser._expand_home("~/foo", None) == "/tmp/fakehome/foo"

    def test_falls_back_for_atypical_project_cwd(self, monkeypatch):
        monkeypatch.setenv("HOME", "/tmp/fakehome")
        assert parser._expand_home("~/foo", "/srv/app") == "/tmp/fakehome/foo"


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
        assert parser._effective_cwd(command, project_cwd) == expected

    @pytest.mark.parametrize(
        "command",
        [
            # `~bob/work` is unresolvable, so it surfaces as absolute and is classified outside project
            pytest.param("cd ~bob/work && git commit", id="named_user_tilde"),
            # Shell expansions are unresolvable, so they get classified as cross-repo
            pytest.param("cd $OTHER_REPO && git commit", id="dollar_var"),
            pytest.param(
                "cd ${OTHER_REPO}/sub && git commit", id="dollar_braces_with_sub"
            ),
            pytest.param("cd $(pwd)/.. && git commit", id="command_substitution"),
        ],
    )
    def test_unresolvable_cd_classified_as_cross_repo(self, command):
        project_cwd = "/Users/alice/proj"
        result = parser._effective_cwd(command, project_cwd)
        assert result is not None
        assert parser._is_cross_repo(result, project_cwd) is True


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
        assert parser._is_cross_repo(effective_cwd, project_cwd) is expected


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
        pulls, issues = parser._extract_pr_issue_refs(command)
        assert pulls == expected_pulls
        assert issues == expected_issues


class TestBuildItems:
    def test_groups_by_date(self, run_parser):
        items, keys = run_parser(
            _builders.user("2026-03-28T23:30:00+09:00", "Day 1 message"),
            _builders.user("2026-03-29T00:30:00+09:00", "Day 2 message"),
            repo="my-repo",
        )

        assert len(items) == 2
        dates = {item["date"] for item in items}
        assert dates == {"2026-03-28", "2026-03-29"}

        for item in items:
            assert item["repo#session_id"] == "my-repo#s1"
            assert item["repo"] == "my-repo"
            assert item["project"] == "proj"

        assert keys == [_builders.SESSION_KEY]

    def test_aggregates_messages_and_tools(self, run_parser):
        items, _ = run_parser(
            _builders.user("2026-03-28T10:00:00+09:00", "First message"),
            _builders.assistant(
                "2026-03-28T10:01:00+09:00",
                _builders.tool_use_block("Read"),
                _builders.tool_use_block("Edit"),
            ),
            _builders.user("2026-03-28T10:05:00+09:00", "Second message"),
            _builders.assistant(
                "2026-03-28T10:06:00+09:00",
                _builders.tool_use_block("Read"),
                _builders.tool_use_block("Bash"),
            ),
        )

        assert len(items) == 1
        item = items[0]
        assert item["user_messages"] == ["First message", "Second message"]
        assert item["tools_used"] == ["Bash", "Edit", "Read"]
        assert item["start_time"] == "2026-03-28T10:00:00+09:00"
        assert item["end_time"] == "2026-03-28T10:06:00+09:00"

    @pytest.mark.parametrize(
        ("content", "is_error", "expected"),
        [
            pytest.param(
                "[feat/login a1b2c3d] Implement login flow\n 2 files changed",
                False,
                [("a1b2c3d", "Implement login flow")],
                id="branch_commit",
            ),
            pytest.param(
                "[main (root-commit) a1b2c3d] Initial commit\n 1 file changed",
                False,
                [("a1b2c3d", "Initial commit")],
                id="root_commit",
            ),
            pytest.param(
                "[detached HEAD e5f6a7b] Hotfix\n 1 file changed",
                False,
                [("e5f6a7b", "Hotfix")],
                id="detached_head",
            ),
            pytest.param(
                "check formatting... ok\nrunning linter... passed\n[main a1b2c3d] Fix formatting\n 2 files changed",
                False,
                [("a1b2c3d", "Fix formatting")],
                id="after_hook_output",
            ),
            pytest.param(
                "[main abc1234] First commit\n 1 file changed\n[main def5678] Second commit\n 2 files changed",
                False,
                [("abc1234", "First commit"), ("def5678", "Second commit")],
                id="two_commits_in_one_result",
            ),
            pytest.param(
                "[main abc1234] Some commit\n 1 file changed",
                True,
                [],
                id="error_result",
            ),
        ],
    )
    def test_extracts_commits(self, run_parser, content, is_error, expected):
        items, _ = run_parser(
            _builders.user("2026-03-28T10:00:00+09:00", "work"),
            _builders.tool_result(COMMIT_TS, content, is_error=is_error),
        )

        assert len(items) == 1
        assert items[0]["session_commits"] == [
            {"sha": sha, "message": message, "timestamp": COMMIT_TS}
            for sha, message in expected
        ]

    def test_commit_timestamp_follows_its_tool_result(self, run_parser):
        items, _ = run_parser(
            _builders.user("2026-03-28T10:00:00+09:00", "Fix the bug"),
            _builders.tool_result(
                "2026-03-28T10:05:00+09:00",
                "[feat/login a1b2c3d] Implement login flow\n 2 files changed",
            ),
            _builders.tool_result(
                "2026-03-28T10:10:00+09:00",
                "[feat/login e5f6a7b] Fix test failure\n 1 file changed",
            ),
        )

        assert [c["timestamp"] for c in items[0]["session_commits"]] == [
            "2026-03-28T10:05:00+09:00",
            "2026-03-28T10:10:00+09:00",
        ]

    def test_cross_midnight_commit_only_day(self, run_parser):
        # Day 1 has a user message; Day 2 has only a tool_result with a commit
        items, keys = run_parser(
            _builders.user("2026-03-28T23:50:00+09:00", "Fix the bug"),
            _builders.tool_result(
                "2026-03-29T00:05:00+09:00",
                "[main a1b2c3d] Apply fix\n 1 file changed",
            ),
        )

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
        assert keys == [_builders.SESSION_KEY]

    def test_skips_no_repo(self, run_parser, session_client):
        items, keys = run_parser(repo=None)

        assert items == []
        assert keys == []
        session_client.s3.get_object.assert_not_called()

    def test_merges_pr_issue_refs_across_bash_entries(self, run_parser):
        items, _ = run_parser(
            _builders.user("2026-03-28T10:00:00+09:00", "do work"),
            _builders.bash("2026-03-28T10:01:00+09:00", "gh pr view 87 --json body"),
            _builders.bash("2026-03-28T10:02:00+09:00", "gh issue close 84"),
            _builders.bash("2026-03-28T10:03:00+09:00", 'git commit -m "Fix #12"'),
            repo="ayumy",
        )

        assert len(items) == 1
        item = items[0]
        # Refs from every entry are merged and sorted; `#N` in git args counts as both
        assert item["session_pulls"] == [12, 87]
        assert item["session_issues"] == [12, 84]

    def test_ignores_non_bash_tool_use(self, run_parser):
        items, _ = run_parser(
            _builders.user("2026-03-28T10:00:00+09:00", "look at #87"),
            _builders.assistant(
                "2026-03-28T10:01:00+09:00",
                {"type": "text", "text": "https://github.com/n-yU/ayumy/pull/87"},
                _builders.tool_use_block("Read", file_path="/tmp/notes_42.md"),
            ),
            repo="ayumy",
        )

        assert len(items) == 1
        assert items[0]["session_pulls"] == []
        assert items[0]["session_issues"] == []

    def test_skips_no_user_messages(self, run_parser):
        items, keys = run_parser(
            _builders.assistant(
                "2026-03-28T10:00:00+09:00",
                {"type": "text", "text": "hello"},
            ),
        )

        assert items == []
        assert keys == []


class TestBuildItemsTypeViolations:
    """Session log spec violations surface as warnings instead of silent skips."""

    def test_non_string_cwd_logs_warning(self, run_parser, caplog):
        with caplog.at_level(logging.WARNING, logger="report.session.parser"):
            run_parser(_builders.user("2026-03-28T10:00:00+09:00", "msg", cwd=123))

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings
        assert "cwd" in warnings[0].getMessage()

    def test_non_string_bash_command_logs_warning(self, run_parser, caplog):
        with caplog.at_level(logging.WARNING, logger="report.session.parser"):
            run_parser(
                _builders.bash("2026-03-28T10:00:00+09:00", ["ls"], tool_use_id="t1")
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings
        assert "command" in warnings[0].getMessage()


class TestBuildItemsCwdFilter:
    """`cd <path>` to another repo must drop both commits and refs."""

    @pytest.mark.parametrize(
        ("command", "entry_cwd", "expected_shas"),
        [
            pytest.param(
                "cd ~/other && git commit -m x",
                PROJECT_CWD,
                [],
                id="cd_to_other_repo",
            ),
            pytest.param(
                "git commit -m x", PROJECT_CWD, ["abc1234"], id="within_project_cwd"
            ),
            pytest.param(
                "git commit -m x",
                "/Users/a/other",
                [],
                id="entry_cwd_outside_project",
            ),
        ],
    )
    def test_filters_commits_by_cwd(
        self, run_parser, command, entry_cwd, expected_shas
    ):
        items, _ = run_parser(
            _builders.user("2026-03-28T10:00:00+09:00", "work", cwd=PROJECT_CWD),
            _builders.bash(
                "2026-03-28T10:01:00+09:00",
                command,
                tool_use_id="tu_x",
                cwd=entry_cwd,
            ),
            _builders.tool_result(
                "2026-03-28T10:02:00+09:00",
                "[main abc1234] commit\n 1 file",
                tool_use_id="tu_x",
                cwd=entry_cwd,
            ),
        )
        assert [c["sha"] for c in items[0]["session_commits"]] == expected_shas

    def test_drops_refs_after_cd_to_other_repo(self, run_parser):
        items, _ = run_parser(
            _builders.user("2026-03-28T10:00:00+09:00", "work", cwd=PROJECT_CWD),
            _builders.bash(
                "2026-03-28T10:01:00+09:00",
                "cd ~/other && gh pr view 99",
                tool_use_id="tu_x",
                cwd=PROJECT_CWD,
            ),
        )
        assert items[0]["session_pulls"] == []

    def test_does_not_filter_when_project_cwd_missing(self, run_parser):
        # Legacy sessions without any cwd field must still record commits
        items, _ = run_parser(
            _builders.user("2026-03-28T10:00:00+09:00", "work"),
            _builders.tool_result(
                "2026-03-28T10:02:00+09:00",
                "[main abc1234] no cwd info\n 1 file",
                tool_use_id="tu_x",
            ),
        )
        assert len(items[0]["session_commits"]) == 1

    def test_drops_commit_when_entry_cwd_is_outside_project(self, run_parser):
        # A later entry whose own cwd is outside project_cwd must drop commits even without a leading `cd`
        items, _ = run_parser(
            _builders.user("2026-03-28T10:00:00+09:00", "work", cwd="/Users/a/proj"),
            _builders.bash(
                "2026-03-28T10:01:00+09:00",
                "git commit -m x",
                tool_use_id="tu_x",
                cwd="/Users/a/other",
            ),
            _builders.tool_result(
                "2026-03-28T10:02:00+09:00",
                "[main abc1234] outside cwd\n 1 file",
                tool_use_id="tu_x",
                cwd="/Users/a/other",
            ),
        )
        assert items[0]["session_commits"] == []
