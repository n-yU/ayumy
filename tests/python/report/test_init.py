"""Tests for report package core utilities."""

import re
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from config import CONFIG
from report import (
    JST,
    SummaryUsage,
    date_to_range,
    get_target_date_range,
    get_version,
    parse_target_dates,
    require_env,
)


class TestGetVersion:
    def test_returns_semver_string(self):
        version = get_version()
        assert re.fullmatch(r"\d+\.\d+\.\d+", version)


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

    def test_target_date_range_uses_first_date(self):
        since, until = get_target_date_range(target_date="2026-03-25..2026-03-28")
        assert since == datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        assert until == datetime(2026, 3, 26, 0, 0, tzinfo=JST)


class TestSummaryUsage:
    def test_zero_tokens_produce_zero_spend(self):
        usage = SummaryUsage.from_call(0, 0)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.spend_usd == 0.0

    def test_spend_matches_configured_rates(self):
        rates = CONFIG.claude.pricing[CONFIG.claude.model]
        expected = (
            1_000_000 * rates["input_usd_per_1m_tokens"]
            + 500_000 * rates["output_usd_per_1m_tokens"]
        ) / 1_000_000

        usage = SummaryUsage.from_call(1_000_000, 500_000)

        assert usage.spend_usd == pytest.approx(expected)


class TestParseTargetDates:
    def test_single_date(self):
        result = parse_target_dates("2026-03-28")
        assert result == [date(2026, 3, 28)]

    def test_date_range(self):
        result = parse_target_dates("2026-03-25..2026-03-28")
        assert result == [
            date(2026, 3, 25),
            date(2026, 3, 26),
            date(2026, 3, 27),
            date(2026, 3, 28),
        ]

    def test_same_start_and_end(self):
        result = parse_target_dates("2026-03-28..2026-03-28")
        assert result == [date(2026, 3, 28)]

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError, match="after"):
            parse_target_dates("2026-03-28..2026-03-25")

    def test_range_exceeds_max_days_raises(self):
        with pytest.raises(ValueError, match="exceeds 31 days"):
            parse_target_dates("2026-01-01..2026-02-01")
