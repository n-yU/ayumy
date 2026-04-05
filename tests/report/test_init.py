"""Tests for report package core utilities."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from report import JST, date_to_range, get_target_date_range, require_env


class TestRequireEnv:
    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "hello")
        assert require_env("TEST_VAR") == "hello"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_VAR", raising=False)
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            require_env("TEST_VAR")

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "")
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            require_env("TEST_VAR")


class TestDateToRange:
    def test_returns_full_jst_day(self):
        since, until = date_to_range(date(2026, 3, 28))
        assert since == datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        assert until == datetime(2026, 3, 29, 0, 0, tzinfo=JST)

    def test_range_spans_24_hours(self):
        since, until = date_to_range(date(2026, 1, 1))
        assert until - since == timedelta(days=1)


class TestGetTargetDateRange:
    @patch("report.datetime")
    def test_scheduled_returns_previous_day(self, mock_dt):
        mock_now = datetime(2026, 3, 28, 15, 30, 0, tzinfo=JST)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        since, until = get_target_date_range()
        assert since == datetime(2026, 3, 27, 0, 0, tzinfo=JST)
        assert until == datetime(2026, 3, 28, 0, 0, tzinfo=JST)

    @patch("report.datetime")
    def test_manual_returns_today_to_now(self, mock_dt):
        mock_now = datetime(2026, 3, 28, 15, 30, 0, tzinfo=JST)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        since, until = get_target_date_range("manual")
        assert since == datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        assert until == mock_now

    def test_target_date_returns_full_day_range(self):
        since, until = get_target_date_range(target_date="2026-03-25")
        assert since == datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        assert until == datetime(2026, 3, 26, 0, 0, tzinfo=JST)

    def test_target_date_ignores_source(self):
        since, until = get_target_date_range("manual", target_date="2026-03-25")
        assert since == datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        assert until == datetime(2026, 3, 26, 0, 0, tzinfo=JST)
