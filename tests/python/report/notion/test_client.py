"""Tests for Notion client pure logic."""

import re
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from config import CONFIG, config
from report.domain.session import SessionActivity
from report.notion import client

from .. import _builders

REPORT = {"repositories": [{"name": _builders.REPO, "summary": [], "tags": []}]}


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
            "name": _builders.REPO,
            "summary": ["要点1", "要点2"],
            "tags": ["CI/CD", "Testing"],
        }
        props = notion_client._build_properties(
            _builders.TARGET_DATE, repo_summary, 5, 2, 1, 3, 4
        )

        assert (
            props["Name"]["title"][0]["text"]["content"]
            == f"26-03-28: {_builders.REPO}"
        )
        assert props["Date"]["date"]["start"] == "2026-03-28"
        assert props["Repository"]["select"]["name"] == _builders.REPO
        assert [t["name"] for t in props["Tags"]["multi_select"]] == [
            "CI/CD",
            "Testing",
        ]
        assert props["Commits"]["number"] == 5
        assert props["Merged"]["number"] == 2
        assert props["Closed"]["number"] == 1
        assert props["Sessions"]["number"] == 3
        assert props["Regens"]["number"] == 4
        assert re.fullmatch(
            r"\d+\.\d+\.\d+", props["Version"]["rich_text"][0]["text"]["content"]
        )


class TestStatusSections:
    def test_done_collects_merged_and_closed(self, build_status):
        blocks = build_status(
            pulls=[_builders.pull(1, "Merged PR", "merged")],
            issues=[
                _builders.issue(
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
            f"https://github.com/{_builders.REPO_FULL_NAME}/pull/1",
            f"https://github.com/{_builders.REPO_FULL_NAME}/issues/11",
        ]

    def test_in_progress_collects_open_pulls_and_pre_existing_issues(
        self, build_status
    ):
        blocks = build_status(
            pulls=[
                _builders.pull(3, "WIP", "open", draft=True),
                _builders.pull(4, "Ready", "open"),
            ],
            issues=[
                _builders.issue(
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
            f"https://github.com/{_builders.REPO_FULL_NAME}/pull/3",
            f"https://github.com/{_builders.REPO_FULL_NAME}/pull/4",
            f"https://github.com/{_builders.REPO_FULL_NAME}/issues/20",
        ]

    def test_todo_collects_issues_opened_within_window(self, build_status):
        blocks = build_status(
            issues=[
                _builders.issue(
                    30, "New issue", "open", created_at="2026-03-28T11:00:00+09:00"
                ),
                _builders.issue(
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
            f"https://github.com/{_builders.REPO_FULL_NAME}/issues/31",
            f"https://github.com/{_builders.REPO_FULL_NAME}/issues/30",
        ]

    def test_reports_items_completed_after_window_as_pending(self, build_status):
        blocks = build_status(
            pulls=[
                _builders.pull(
                    1,
                    "Merged next day",
                    "merged",
                    merged_at="2026-03-29T10:00:00+09:00",
                )
            ],
            issues=[
                _builders.issue(
                    10,
                    "Created in window, closed later",
                    "closed",
                    closed_at="2026-03-29T12:00:00+09:00",
                ),
                _builders.issue(
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
                        _builders.pull(
                            1,
                            "Merged earlier",
                            "merged",
                            merged_at="2026-03-27T10:00:00+09:00",
                        ),
                        _builders.pull(
                            2,
                            "Rejected earlier",
                            "closed",
                            closed_at="2026-03-27T11:00:00+09:00",
                        ),
                    ],
                    "issues": [
                        _builders.issue(
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
                _builders.commit(
                    "aaa1111",
                    "branch commit 1",
                    date="2026-03-28T09:30:00+09:00",
                    pull_numbers=[1],
                ),
                _builders.commit(
                    "bbb2222",
                    "branch commit 2",
                    date="2026-03-28T10:00:00+09:00",
                    pull_numbers=[1],
                ),
                _builders.commit(
                    "ccc3333",
                    "Squash merge",
                    date="2026-03-28T11:00:00+09:00",
                    pull_numbers=[1],
                ),
            ],
            pulls=[
                _builders.pull(
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
                _builders.commit(
                    "bbb2222",
                    "later in time",
                    date="2026-03-28T09:00:00+00:00",
                    pull_numbers=[1],
                ),
                _builders.commit(
                    "aaa1111",
                    "earlier in time",
                    date="2026-03-28T10:00:00+09:00",
                    pull_numbers=[1],
                ),
            ],
            pulls=[_builders.pull(1, "feat", "open")],
        )

        assert [_text(c) for c in _children(blocks[1])] == [
            "🔸 aaa1111: earlier in time",
            "🔸 bbb2222: later in time",
        ]

    def test_nests_commit_shared_by_pulls_under_smallest_number(self, build_timeline):
        # A cherry-picked commit appears in two PRs; nesting must not depend on input order
        blocks = build_timeline(
            commits=[_builders.commit("aaa1111", "shared commit", pull_numbers=[5, 2])],
            pulls=[
                _builders.pull(2, "PR two", "open"),
                _builders.pull(5, "PR five", "open"),
            ],
        )

        assert {_text(b): [_text(c) for c in _children(b)] for b in blocks[1:]} == {
            "🔀 #2: PR two": ["🔸 aaa1111: shared commit"],
            "🔀 #5: PR five": [],
        }

    def test_sorts_pull_header_before_its_merge_commit(self, build_timeline):
        # PR opened before the window, so only the squash merge commit lands in range
        blocks = build_timeline(
            commits=[
                _builders.commit(
                    "ccc3333",
                    "Squash merge",
                    date="2026-03-28T11:00:00+09:00",
                    pull_numbers=[2],
                )
            ],
            pulls=[
                _builders.pull(
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
        blocks = build_timeline(commits=[_builders.commit("ddd4444", "Direct commit")])

        assert _texts(blocks) == ["🔸 ddd4444: Direct commit"]

    def test_appends_close_line_for_unmerged_pull(self, build_timeline):
        blocks = build_timeline(
            pulls=[
                _builders.pull(
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
                _builders.issue(
                    5, "New bug", "open", created_at="2026-03-28T08:00:00+09:00"
                ),
                _builders.issue(
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
                _builders.commit(
                    "aaa1111",
                    "commit on PR#1",
                    date="2026-03-28T10:30:00+09:00",
                    pull_numbers=[1],
                ),
                _builders.commit("ddd4444", "Direct", date="2026-03-28T12:00:00+09:00"),
            ],
            pulls=[_builders.pull(1, "Feature", "open")],
            issues=[
                _builders.issue(
                    5, "Bug", "open", created_at="2026-03-28T08:00:00+09:00"
                )
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
                        _builders.pull(
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
            "name": _builders.REPO,
            "summary": ["point one", "point two"],
            "tags": [],
        }
        repo_activity = _builders.repo_activity(
            commits=[_builders.commit("deadbeef00", "Commit msg")],
            pulls=[
                _builders.pull(
                    1, "Merged", "merged", merged_at="2026-03-28T11:00:00+09:00"
                )
            ],
            issues=[_builders.issue(5, "Open today", "open")],
        )

        children = notion_client._build_children(
            repo_summary, repo_activity, _builders.SINCE, _builders.UNTIL
        )

        assert _headings(children) == ["Summary", "Done", "TODO", "Timeline"]
        assert _texts(children)[:2] == ["point one", "point two"]

    def test_summary_number_reference_links_to_page_repository(self, notion_client):
        repo_summary = {"name": _builders.REPO, "summary": ["マージ #155"], "tags": []}

        children = notion_client._build_children(
            repo_summary,
            _builders.repo_activity(),
            _builders.SINCE,
            _builders.UNTIL,
        )

        rich_text = children[1]["bulleted_list_item"]["rich_text"]
        assert rich_text[-1]["text"]["link"] == {
            "url": f"https://github.com/{_builders.REPO_FULL_NAME}/issues/155"
        }

    def test_renders_only_summary_when_no_activity(self, notion_client):
        repo_summary = {"name": _builders.REPO, "summary": ["only point"], "tags": []}

        children = notion_client._build_children(
            repo_summary,
            _builders.repo_activity(),
            _builders.SINCE,
            _builders.UNTIL,
        )

        assert _headings(children) == ["Summary"]
        assert _texts(children) == ["only point"]


class TestPageIcon:
    def test_uses_configured_icon_for_known_repository(self):
        # The tracked template ships no entries, so the configured path needs a config built here
        icon = config.NotionIcon(name="walk", color="blue")
        configured = replace(
            CONFIG,
            notion=replace(CONFIG.notion, repository_icons={_builders.REPO: icon}),
        )
        with patch("report.notion.client.CONFIG", configured):
            assert client._page_icon(_builders.REPO) == {
                "type": "icon",
                "icon": {"name": "walk", "color": "blue"},
            }

    def test_falls_back_to_default_icon_for_unconfigured_repository(self):
        default = CONFIG.notion.default_icon
        assert client._page_icon("repo-without-an-entry") == {
            "type": "icon",
            "icon": {"name": default.name, "color": default.color},
        }


class TestCreatePage:
    def test_sends_repository_icon_with_the_page(self, notion_client):
        notion_client.create_page(
            _builders.TARGET_DATE,
            {"name": _builders.REPO, "summary": [], "tags": []},
            _builders.repo_activity(),
            _builders.SINCE,
            _builders.UNTIL,
            0,
            0,
            0,
            0,
            0,
        )

        kwargs = notion_client.client.pages.create.call_args.kwargs
        assert kwargs["icon"] == client._page_icon(_builders.REPO)


def _queried_page(page_id: str, repo: str | None, regens: int | None) -> dict:
    properties: dict = {"Regens": {"number": regens}}
    if repo is not None:
        properties["Repository"] = {"select": {"name": repo}}
    return {"id": page_id, "properties": properties}


@pytest.fixture
def archiver(notion_client):
    """Return a client whose data source query yields the given already-recorded pages."""

    def _archive(*pages):
        notion_client._data_source_id = "ds-id"
        notion_client.client.data_sources.query.return_value = {"results": list(pages)}
        return notion_client._archive_existing_pages(_builders.TARGET_DATE)

    return _archive


class TestArchiveExistingPages:
    def test_archives_every_page_recorded_for_the_date(self, archiver, notion_client):
        archiver(
            _queried_page("page1", _builders.REPO, 0),
            _queried_page("page2", "other-repo", 0),
        )

        archived = [
            call.kwargs["page_id"]
            for call in notion_client.client.pages.update.call_args_list
        ]
        assert archived == ["page1", "page2"]

    def test_increments_the_count_carried_from_each_repository(self, archiver):
        assert archiver(
            _queried_page("page1", _builders.REPO, 2),
            _queried_page("page2", "other-repo", 0),
        ) == {_builders.REPO: 3, "other-repo": 1}

    def test_carries_the_highest_count_when_a_repository_has_duplicates(self, archiver):
        assert archiver(
            _queried_page("page1", _builders.REPO, 4),
            _queried_page("page2", _builders.REPO, 1),
        ) == {_builders.REPO: 5}

    def test_counts_pages_predating_the_property_as_never_rebuilt(self, archiver):
        assert archiver(_queried_page("page1", _builders.REPO, None)) == {
            _builders.REPO: 1
        }

    def test_skips_pages_without_a_repository(self, archiver):
        assert archiver(_queried_page("page1", None, 3)) == {}

    def test_carries_nothing_when_the_date_has_no_pages(self, archiver):
        assert archiver() == {}


@pytest.fixture
def page_creator(notion_client):
    """Return a client whose Notion-facing page calls are stubbed out."""
    notion_client._archive_existing_pages = MagicMock(return_value={})
    notion_client.create_page = MagicMock(return_value="https://notion.so/page1")
    return notion_client


class TestCreateReportPagesWiring:
    def test_passes_window_to_create_page(self, page_creator):
        page_creator.create_report_pages(
            _builders.TARGET_DATE,
            _builders.SINCE,
            _builders.UNTIL,
            REPORT,
            _builders.github(),
            SessionActivity({}),
        )

        page_creator.create_page.assert_called_once()
        args = page_creator.create_page.call_args[0]
        # Signature: target_date, repo_summary, repo_activity, since, until, ...
        assert (args[0], args[3], args[4]) == (
            _builders.TARGET_DATE,
            _builders.SINCE,
            _builders.UNTIL,
        )

    def test_counts_only_activity_within_window(self, page_creator):
        activity = _builders.github(
            commits=[
                _builders.commit("aaa1111", "In window"),
                _builders.commit(
                    "bbb2222", "Previous day", date="2026-03-27T10:00:00+09:00"
                ),
            ],
            pulls=[
                _builders.pull(1, "Merged in window", "merged"),
                _builders.pull(
                    2,
                    "Merged before window",
                    "merged",
                    merged_at="2026-03-27T10:00:00+09:00",
                ),
                _builders.pull(
                    3,
                    "Merged after window",
                    "merged",
                    merged_at="2026-03-29T10:00:00+09:00",
                ),
            ],
            issues=[
                _builders.issue(
                    10,
                    "Closed in window",
                    "closed",
                    closed_at="2026-03-28T12:00:00+09:00",
                ),
                _builders.issue(
                    11,
                    "Closed before window",
                    "closed",
                    closed_at="2026-03-27T12:00:00+09:00",
                ),
            ],
        )

        page_creator.create_report_pages(
            _builders.TARGET_DATE,
            _builders.SINCE,
            _builders.UNTIL,
            REPORT,
            activity,
            SessionActivity({}),
        )

        args = page_creator.create_page.call_args[0]
        # Signature: ..., since, until, commits, prs_merged, issues_closed, sessions
        assert args[5:9] == (1, 1, 1, 0)

    def test_records_the_count_carried_from_the_archived_page(self, page_creator):
        page_creator._archive_existing_pages.return_value = {_builders.REPO: 3}

        page_creator.create_report_pages(
            _builders.TARGET_DATE,
            _builders.SINCE,
            _builders.UNTIL,
            REPORT,
            _builders.github(),
            SessionActivity({}),
        )

        assert page_creator.create_page.call_args[0][9] == 3

    def test_records_a_first_build_for_repositories_without_an_archived_page(
        self, page_creator
    ):
        page_creator._archive_existing_pages.return_value = {"other-repo": 5}

        page_creator.create_report_pages(
            _builders.TARGET_DATE,
            _builders.SINCE,
            _builders.UNTIL,
            REPORT,
            _builders.github(),
            SessionActivity({}),
        )

        assert page_creator.create_page.call_args[0][9] == 0
