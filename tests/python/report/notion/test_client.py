"""Tests for Notion client pure logic."""

import re
from unittest.mock import MagicMock

import pytest

from report import SessionActivity

from .._builders import (
    REPO,
    REPO_FULL_NAME,
    SINCE,
    TARGET_DATE,
    UNTIL,
    make_commit,
    make_github,
    make_issue,
    make_pull,
    make_repo_activity,
)

REPORT = {"repositories": [{"name": REPO, "summary": [], "tags": []}]}


def _text(block: dict) -> str:
    """Concatenate the rich_text content of one bulleted_list_item block."""
    return "".join(
        rt["text"]["content"] for rt in block["bulleted_list_item"]["rich_text"]
    )


def _texts(blocks: list[dict]) -> list[str]:
    return [_text(b) for b in blocks if b["type"] == "bulleted_list_item"]


def _urls(blocks: list[dict]) -> list[str]:
    # The linked chunk is last because a prefix, when present, is prepended as plain text
    return [
        b["bulleted_list_item"]["rich_text"][-1]["text"]["link"]["url"]
        for b in blocks
        if b["type"] == "bulleted_list_item"
    ]


def _headings(blocks: list[dict]) -> list[str]:
    return [
        b["heading_2"]["rich_text"][0]["text"]["content"]
        for b in blocks
        if b["type"] == "heading_2"
    ]


def _children(block: dict) -> list[dict]:
    return block["bulleted_list_item"].get("children", [])


class TestBuildProperties:
    def test_fills_every_database_property(self, notion_client):
        repo_summary = {
            "name": REPO,
            "summary": ["要点1", "要点2"],
            "tags": ["CI/CD", "Testing"],
        }
        props = notion_client._build_properties(TARGET_DATE, repo_summary, 5, 2, 1, 3)

        assert props["Name"]["title"][0]["text"]["content"] == f"26-03-28: {REPO}"
        assert props["Date"]["date"]["start"] == "2026-03-28"
        assert props["Repository"]["select"]["name"] == REPO
        assert [t["name"] for t in props["Tags"]["multi_select"]] == [
            "CI/CD",
            "Testing",
        ]
        assert props["Commits"]["number"] == 5
        assert props["Merged"]["number"] == 2
        assert props["Closed"]["number"] == 1
        assert props["Sessions"]["number"] == 3
        assert re.fullmatch(
            r"\d+\.\d+\.\d+", props["Version"]["rich_text"][0]["text"]["content"]
        )


class TestStatusSections:
    def test_done_collects_merged_and_closed(self, build_status):
        blocks = build_status(
            pulls=[make_pull(1, "Merged PR", "merged")],
            issues=[
                make_issue(
                    11,
                    "Not planned",
                    "closed",
                    closed_at="2026-03-28T13:00:00+09:00",
                    state_reason="not_planned",
                ),
            ],
        )

        assert _headings(blocks) == ["Done"]
        assert _texts(blocks) == [
            "✅ #1: Merged PR",
            "⚠️ (not planned) #11: Not planned",
        ]
        assert _urls(blocks) == [
            f"https://github.com/{REPO_FULL_NAME}/pull/1",
            f"https://github.com/{REPO_FULL_NAME}/issues/11",
        ]

    def test_in_progress_collects_open_pulls_and_pre_existing_issues(
        self, build_status
    ):
        blocks = build_status(
            pulls=[
                make_pull(3, "WIP", "open", draft=True),
                make_pull(4, "Ready", "open"),
            ],
            issues=[
                make_issue(
                    20, "Old open issue", "open", created_at="2026-03-20T09:00:00+09:00"
                )
            ],
        )

        assert _headings(blocks) == ["In Progress"]
        assert _texts(blocks) == [
            "#3: WIP",
            "#4: Ready",
            "#20: Old open issue",
        ]
        assert _urls(blocks) == [
            f"https://github.com/{REPO_FULL_NAME}/pull/3",
            f"https://github.com/{REPO_FULL_NAME}/pull/4",
            f"https://github.com/{REPO_FULL_NAME}/issues/20",
        ]

    def test_todo_collects_issues_opened_within_window(self, build_status):
        blocks = build_status(
            issues=[
                make_issue(
                    30, "New issue", "open", created_at="2026-03-28T11:00:00+09:00"
                ),
                make_issue(
                    31, "Old open issue", "open", created_at="2026-03-20T09:00:00+09:00"
                ),
            ],
        )

        assert _headings(blocks) == ["In Progress", "TODO"]
        assert _texts(blocks) == [
            "#31: Old open issue",
            "#30: New issue",
        ]
        assert _urls(blocks) == [
            f"https://github.com/{REPO_FULL_NAME}/issues/31",
            f"https://github.com/{REPO_FULL_NAME}/issues/30",
        ]

    def test_reports_items_completed_after_window_as_pending(self, build_status):
        blocks = build_status(
            pulls=[
                make_pull(
                    1,
                    "Merged next day",
                    "merged",
                    merged_at="2026-03-29T10:00:00+09:00",
                )
            ],
            issues=[
                make_issue(
                    10,
                    "Created in window, closed later",
                    "closed",
                    closed_at="2026-03-29T12:00:00+09:00",
                ),
                make_issue(
                    11,
                    "Created earlier, closed later",
                    "closed",
                    created_at="2026-03-20T09:00:00+09:00",
                    closed_at="2026-03-29T13:00:00+09:00",
                ),
            ],
        )

        assert _headings(blocks) == ["In Progress", "TODO"]
        assert _texts(blocks) == [
            "#1: Merged next day",
            "#11: Created earlier, closed later",
            "#10: Created in window, closed later",
        ]

    @pytest.mark.parametrize(
        "activity",
        [
            pytest.param({}, id="no_activity"),
            pytest.param(
                {
                    "pulls": [
                        make_pull(
                            1,
                            "Merged earlier",
                            "merged",
                            merged_at="2026-03-27T10:00:00+09:00",
                        ),
                        make_pull(
                            2,
                            "Rejected earlier",
                            "closed",
                            closed_at="2026-03-27T11:00:00+09:00",
                        ),
                    ],
                    "issues": [
                        make_issue(
                            10,
                            "Closed earlier",
                            "closed",
                            closed_at="2026-03-27T12:00:00+09:00",
                        )
                    ],
                },
                id="completed_before_window",
            ),
        ],
    )
    def test_omits_sections_without_pending_or_completed_items(
        self, build_status, activity
    ):
        assert build_status(**activity) == []


class TestTimelineSection:
    def test_nests_non_merge_commits_under_their_pull(self, build_timeline):
        blocks = build_timeline(
            commits=[
                make_commit(
                    "aaa1111",
                    "branch commit 1",
                    date="2026-03-28T09:30:00+09:00",
                    pull_numbers=[1],
                ),
                make_commit(
                    "bbb2222",
                    "branch commit 2",
                    date="2026-03-28T10:00:00+09:00",
                    pull_numbers=[1],
                ),
                make_commit(
                    "ccc3333",
                    "Squash merge",
                    date="2026-03-28T11:00:00+09:00",
                    pull_numbers=[1],
                ),
            ],
            pulls=[
                make_pull(
                    1,
                    "Add feature",
                    "merged",
                    merged_at="2026-03-28T11:00:00+09:00",
                    merge_commit_sha="ccc3333",
                )
            ],
        )

        assert _headings(blocks) == ["Timeline"]
        # The merge commit stays at top level
        assert _texts(blocks) == [
            "🔀 #1: Add feature",
            "🔸 ccc3333: Squash merge",
        ]
        assert [_text(c) for c in _children(blocks[1])] == [
            "🔸 aaa1111: branch commit 1",
            "🔸 bbb2222: branch commit 2",
        ]

    def test_sorts_nested_commits_by_instant_across_timezones(self, build_timeline):
        # ISO string order would put +00:00 first,
        # but 10:00+09:00 (= 01:00 UTC) precedes 09:00+00:00 (= 18:00 JST)
        blocks = build_timeline(
            commits=[
                make_commit(
                    "bbb2222",
                    "later in time",
                    date="2026-03-28T09:00:00+00:00",
                    pull_numbers=[1],
                ),
                make_commit(
                    "aaa1111",
                    "earlier in time",
                    date="2026-03-28T10:00:00+09:00",
                    pull_numbers=[1],
                ),
            ],
            pulls=[make_pull(1, "feat", "open")],
        )

        assert [_text(c) for c in _children(blocks[1])] == [
            "🔸 aaa1111: earlier in time",
            "🔸 bbb2222: later in time",
        ]

    def test_nests_commit_shared_by_pulls_under_smallest_number(self, build_timeline):
        # A cherry-picked commit appears in two PRs; nesting must not depend on input order
        blocks = build_timeline(
            commits=[make_commit("aaa1111", "shared commit", pull_numbers=[5, 2])],
            pulls=[make_pull(2, "PR two", "open"), make_pull(5, "PR five", "open")],
        )

        assert {_text(b): [_text(c) for c in _children(b)] for b in blocks[1:]} == {
            "🔀 #2: PR two": ["🔸 aaa1111: shared commit"],
            "🔀 #5: PR five": [],
        }

    def test_sorts_pull_header_before_its_merge_commit(self, build_timeline):
        # PR opened before the window, so only the squash merge commit lands in range
        blocks = build_timeline(
            commits=[
                make_commit(
                    "ccc3333",
                    "Squash merge",
                    date="2026-03-28T11:00:00+09:00",
                    pull_numbers=[2],
                )
            ],
            pulls=[
                make_pull(
                    2,
                    "Old PR finally merged",
                    "merged",
                    created_at="2026-03-20T09:00:00+09:00",
                    merged_at="2026-03-28T11:00:00+09:00",
                    merge_commit_sha="ccc3333",
                )
            ],
        )

        assert _texts(blocks) == [
            "🔀 #2: Old PR finally merged",
            "🔸 ccc3333: Squash merge",
        ]
        assert _children(blocks[1]) == []

    def test_renders_direct_commit_at_top_level(self, build_timeline):
        blocks = build_timeline(commits=[make_commit("ddd4444", "Direct commit")])

        assert _texts(blocks) == ["🔸 ddd4444: Direct commit"]

    def test_appends_close_line_for_unmerged_pull(self, build_timeline):
        blocks = build_timeline(
            pulls=[
                make_pull(
                    7,
                    "Rejected",
                    "closed",
                    created_at="2026-03-27T09:00:00+09:00",
                    closed_at="2026-03-28T15:00:00+09:00",
                )
            ]
        )

        assert _texts(blocks) == [
            "🔀 #7: Rejected",
            "⚠️ close: #7: Rejected",
        ]

    def test_renders_issue_open_and_close_lines(self, build_timeline):
        blocks = build_timeline(
            issues=[
                make_issue(
                    5, "New bug", "open", created_at="2026-03-28T08:00:00+09:00"
                ),
                make_issue(
                    7,
                    "Won't fix",
                    "closed",
                    created_at="2026-03-20T09:00:00+09:00",
                    closed_at="2026-03-28T14:00:00+09:00",
                    state_reason="not_planned",
                ),
            ]
        )

        assert _texts(blocks) == [
            "🟢 open: #5: New bug",
            "⚠️ close (not planned): #7: Won't fix",
        ]

    def test_orders_entries_chronologically_across_types(self, build_timeline):
        blocks = build_timeline(
            commits=[
                make_commit(
                    "aaa1111",
                    "commit on PR#1",
                    date="2026-03-28T10:30:00+09:00",
                    pull_numbers=[1],
                ),
                make_commit("ddd4444", "Direct", date="2026-03-28T12:00:00+09:00"),
            ],
            pulls=[make_pull(1, "Feature", "open")],
            issues=[
                make_issue(5, "Bug", "open", created_at="2026-03-28T08:00:00+09:00")
            ],
        )

        assert _texts(blocks) == [
            "🟢 open: #5: Bug",  # 08:00
            "🔀 #1: Feature",  # sorts on the 09:00 open
            "🔸 ddd4444: Direct",  # 12:00
        ]
        assert [_text(c) for c in _children(blocks[2])] == [
            "🔸 aaa1111: commit on PR#1"
        ]

    @pytest.mark.parametrize(
        "activity",
        [
            pytest.param({}, id="no_activity"),
            pytest.param(
                {
                    "pulls": [
                        make_pull(
                            8, "Touched", "open", created_at="2026-03-20T09:00:00+09:00"
                        )
                    ]
                },
                id="pull_without_in_window_event",
            ),
        ],
    )
    def test_omits_timeline_without_in_window_entries(self, build_timeline, activity):
        assert build_timeline(**activity) == []


class TestBuildChildren:
    def test_orders_summary_status_and_timeline_sections(self, notion_client):
        repo_summary = {
            "name": REPO,
            "summary": ["point one", "point two"],
            "tags": [],
        }
        repo_activity = make_repo_activity(
            commits=[make_commit("deadbeef00", "Commit msg")],
            pulls=[
                make_pull(1, "Merged", "merged", merged_at="2026-03-28T11:00:00+09:00")
            ],
            issues=[make_issue(5, "Open today", "open")],
        )

        children = notion_client._build_children(
            repo_summary, repo_activity, SINCE, UNTIL
        )

        assert _headings(children) == ["Summary", "Done", "TODO", "Timeline"]
        assert _texts(children)[:2] == ["point one", "point two"]

    def test_renders_only_summary_when_no_activity(self, notion_client):
        repo_summary = {"name": REPO, "summary": ["only point"], "tags": []}

        children = notion_client._build_children(
            repo_summary, make_repo_activity(), SINCE, UNTIL
        )

        assert _headings(children) == ["Summary"]
        assert _texts(children) == ["only point"]


@pytest.fixture
def page_creator(notion_client):
    """Return a client whose Notion-facing page calls are stubbed out."""
    notion_client._archive_existing_pages = MagicMock(return_value=0)
    notion_client.create_page = MagicMock(return_value="https://notion.so/page1")
    return notion_client


class TestCreateReportPagesWiring:
    def test_passes_window_to_create_page(self, page_creator):
        page_creator.create_report_pages(
            TARGET_DATE, SINCE, UNTIL, REPORT, make_github(), SessionActivity({})
        )

        page_creator.create_page.assert_called_once()
        args = page_creator.create_page.call_args[0]
        # Signature: target_date, repo_summary, repo_activity, since, until, ...
        assert (args[0], args[3], args[4]) == (TARGET_DATE, SINCE, UNTIL)

    def test_counts_only_activity_within_window(self, page_creator):
        activity = make_github(
            commits=[
                make_commit("aaa1111", "In window"),
                make_commit(
                    "bbb2222", "Previous day", date="2026-03-27T10:00:00+09:00"
                ),
            ],
            pulls=[
                make_pull(1, "Merged in window", "merged"),
                make_pull(
                    2,
                    "Merged before window",
                    "merged",
                    merged_at="2026-03-27T10:00:00+09:00",
                ),
                make_pull(
                    3,
                    "Merged after window",
                    "merged",
                    merged_at="2026-03-29T10:00:00+09:00",
                ),
            ],
            issues=[
                make_issue(
                    10,
                    "Closed in window",
                    "closed",
                    closed_at="2026-03-28T12:00:00+09:00",
                ),
                make_issue(
                    11,
                    "Closed before window",
                    "closed",
                    closed_at="2026-03-27T12:00:00+09:00",
                ),
            ],
        )

        page_creator.create_report_pages(
            TARGET_DATE, SINCE, UNTIL, REPORT, activity, SessionActivity({})
        )

        args = page_creator.create_page.call_args[0]
        # Signature: ..., since, until, commits, prs_merged, issues_closed, sessions
        assert args[5:9] == (1, 1, 1, 0)
