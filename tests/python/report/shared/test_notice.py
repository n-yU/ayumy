"""Tests for Notice run-scoped collection and logger forwarding."""

import logging

import pytest

from report.shared.notice import Notice, NoticeEntry, NoticeSource


class TestNoticeAdd:
    def setup_method(self):
        self.notice = Notice()

    def test_appends_entry(self):
        self.notice.add(
            NoticeSource.SESSION, "Malformed JSONL line skipped", key="x.jsonl"
        )

        entries = self.notice.entries()
        assert entries == [
            NoticeEntry(
                source=NoticeSource.SESSION,
                title="Malformed JSONL line skipped",
                details={"key": "x.jsonl"},
            )
        ]

    def test_forwards_to_passed_logger_with_details(self, caplog):
        caller = logging.getLogger("caller.module")
        with caplog.at_level(logging.WARNING, logger="caller.module"):
            self.notice.add(
                NoticeSource.GITHUB,
                "PR not found (404)",
                logger=caller,
                repo="owner/r",
                number="42",
            )

        assert caplog.records and caplog.records[0].name == "caller.module"
        assert (
            caplog.records[0].getMessage()
            == "PR not found (404) (repo=owner/r, number=42)"
        )

    def test_forwards_to_passed_logger_without_details(self, caplog):
        caller = logging.getLogger("caller.module")
        with caplog.at_level(logging.WARNING, logger="caller.module"):
            self.notice.add(NoticeSource.PIPELINE, "Heads up", logger=caller)

        assert caplog.records[0].getMessage() == "Heads up"

    def test_exc_info_records_traceback(self, caplog):
        caller = logging.getLogger("caller.module")
        with caplog.at_level(logging.WARNING, logger="caller.module"):
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                self.notice.add(
                    NoticeSource.PIPELINE,
                    "Report generation failed",
                    logger=caller,
                    exc_info=True,
                    date="2026-06-20",
                )

        record = caplog.records[0]
        assert record.exc_info is not None
        assert "RuntimeError: boom" in caplog.text

    def test_uses_default_logger_when_omitted(self, caplog):
        with caplog.at_level(logging.WARNING, logger="report.shared.notice"):
            self.notice.add(NoticeSource.SUMMARY, "Invalid tags removed")

        assert any(r.name == "report.shared.notice" for r in caplog.records)


class TestNoticeContainerProtocol:
    def test_empty_notice_is_falsy(self):
        assert not Notice()

    def test_notice_with_entries_is_truthy(self):
        notice = Notice()
        notice.add(NoticeSource.SESSION, "x")
        assert notice

    def test_len_reflects_entry_count(self):
        notice = Notice()
        assert len(notice) == 0
        notice.add(NoticeSource.SESSION, "x")
        notice.add(NoticeSource.GITHUB, "y")
        assert len(notice) == 2

    def test_entries_returns_copy(self):
        notice = Notice()
        notice.add(NoticeSource.SESSION, "x")
        snapshot = notice.entries()
        notice.add(NoticeSource.GITHUB, "y")
        assert len(snapshot) == 1


class TestNoticeSource:
    def test_str_value_matches_enum_name_lowercase(self):
        # Slack thread post formats `*{source}*`, relying on the string value
        assert str(NoticeSource.SESSION) == "session"
        assert str(NoticeSource.GITHUB) == "github"

    @pytest.mark.parametrize(
        "source",
        [
            NoticeSource.SESSION,
            NoticeSource.GITHUB,
            NoticeSource.NOTION,
            NoticeSource.SUMMARY,
            NoticeSource.PIPELINE,
        ],
    )
    def test_source_kept_on_entry(self, source):
        notice = Notice()
        notice.add(source, "t")
        assert notice.entries()[0].source is source
