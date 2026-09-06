"""Tests for JST date window calculations."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from report.shared import dates


class TestDateToRange:
    def test_returns_full_jst_day(self):
        since, until = dates.date_to_range(date(2026, 3, 28))
        assert since == datetime(2026, 3, 28, 0, 0, tzinfo=dates.JST)
        assert until == datetime(2026, 3, 29, 0, 0, tzinfo=dates.JST)

    def test_range_spans_24_hours(self):
        since, until = dates.date_to_range(date(2026, 1, 1))
        assert until - since == timedelta(days=1)


class TestGetTargetDateRange:
    @patch("report.shared.dates.datetime")
    def test_scheduled_returns_previous_day(self, mock_dt):
        mock_now = datetime(2026, 3, 28, 15, 30, 0, tzinfo=dates.JST)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        since, until = dates.get_target_date_range()
        assert since == datetime(2026, 3, 27, 0, 0, tzinfo=dates.JST)
        assert until == datetime(2026, 3, 28, 0, 0, tzinfo=dates.JST)

    @patch("report.shared.dates.datetime")
    def test_manual_returns_today_to_now(self, mock_dt):
        mock_now = datetime(2026, 3, 28, 15, 30, 0, tzinfo=dates.JST)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        since, until = dates.get_target_date_range("manual")
        assert since == datetime(2026, 3, 28, 0, 0, tzinfo=dates.JST)
        assert until == mock_now

    def test_target_date_returns_full_day_range(self):
        since, until = dates.get_target_date_range(target_date="2026-03-25")
        assert since == datetime(2026, 3, 25, 0, 0, tzinfo=dates.JST)
        assert until == datetime(2026, 3, 26, 0, 0, tzinfo=dates.JST)

    def test_target_date_ignores_source(self):
        since, until = dates.get_target_date_range("manual", target_date="2026-03-25")
        assert since == datetime(2026, 3, 25, 0, 0, tzinfo=dates.JST)
        assert until == datetime(2026, 3, 26, 0, 0, tzinfo=dates.JST)

    def test_target_date_range_uses_first_date(self):
        since, until = dates.get_target_date_range(target_date="2026-03-25..2026-03-28")
        assert since == datetime(2026, 3, 25, 0, 0, tzinfo=dates.JST)
        assert until == datetime(2026, 3, 26, 0, 0, tzinfo=dates.JST)


class TestParseTargetDates:
    def test_single_date(self):
        result = dates.parse_target_dates("2026-03-28")
        assert result == [date(2026, 3, 28)]

    def test_date_range(self):
        result = dates.parse_target_dates("2026-03-25..2026-03-28")
        assert result == [
            date(2026, 3, 25),
            date(2026, 3, 26),
            date(2026, 3, 27),
            date(2026, 3, 28),
        ]

    def test_same_start_and_end(self):
        result = dates.parse_target_dates("2026-03-28..2026-03-28")
        assert result == [date(2026, 3, 28)]

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError, match="after"):
            dates.parse_target_dates("2026-03-28..2026-03-25")

    def test_range_exceeds_max_days_raises(self):
        with pytest.raises(ValueError, match="exceeds 31 days"):
            dates.parse_target_dates("2026-01-01..2026-02-01")
