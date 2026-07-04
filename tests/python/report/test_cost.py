"""Tests for CostStore."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from boto3.dynamodb.conditions import ConditionExpressionBuilder

from report import SummaryUsage
from report.cost import CostDisplay, CostStore, MonthSummary


def _make_store():
    with patch("report.cost.boto3"):
        store = CostStore("table")
    store.table = MagicMock()
    return store


def _condition_values(condition) -> list:
    """Return literal values embedded in a boto3 KeyCondition so tests can assert on the SK ceiling."""
    _, _, values = ConditionExpressionBuilder().build_expression(condition)
    return list(values.values())


class TestStartRecord:
    def setup_method(self):
        self.store = _make_store()

    def test_writes_row_with_reported_false(self):
        usage = SummaryUsage(input_tokens=1000, output_tokens=200, spend_usd=0.012)

        sk = self.store.start_record(date(2026, 3, 28), usage)

        self.store.table.put_item.assert_called_once()
        item = self.store.table.put_item.call_args.kwargs["Item"]
        assert item["year_month"] == "2026-03"
        assert item["date"] == "2026-03-28"
        assert item["sk"] == sk
        assert item["input_tokens"] == 1000
        assert item["output_tokens"] == 200
        assert item["spend_usd"] == Decimal("0.012")
        assert item["reported"] is False

    def test_captures_active_model_and_pricing(self):
        usage = SummaryUsage(input_tokens=0, output_tokens=0, spend_usd=0.0)

        self.store.start_record(date(2026, 3, 28), usage)

        item = self.store.table.put_item.call_args.kwargs["Item"]
        # Snapshot from config.yml at insertion time; refresh if pricing schema changes
        assert item["model"]
        assert item["input_usd_per_1m_tokens"] > 0
        assert item["output_usd_per_1m_tokens"] > 0

    def test_sk_starts_with_target_date(self):
        usage = SummaryUsage(input_tokens=0, output_tokens=0, spend_usd=0.0)

        sk = self.store.start_record(date(2026, 3, 28), usage)

        assert sk.startswith("2026-03-28#")


class TestMarkReported:
    def setup_method(self):
        self.store = _make_store()

    def test_updates_reported_flag(self):
        self.store.mark_reported(date(2026, 3, 28), "2026-03-28#exec")

        self.store.table.update_item.assert_called_once()
        kwargs = self.store.table.update_item.call_args.kwargs
        assert kwargs["Key"] == {"year_month": "2026-03", "sk": "2026-03-28#exec"}
        assert "reported" in kwargs["UpdateExpression"]
        assert kwargs["ExpressionAttributeValues"] == {":true": True}


class TestRunSpendAccumulator:
    def setup_method(self):
        self.store = _make_store()

    def test_accumulates_across_start_record_calls(self):
        self.store.start_record(
            date(2026, 3, 28),
            SummaryUsage(input_tokens=0, output_tokens=0, spend_usd=0.04),
        )
        self.store.start_record(
            date(2026, 3, 28),
            SummaryUsage(input_tokens=0, output_tokens=0, spend_usd=0.05),
        )

        assert self.store._run_spend_usd == 0.09


class TestFetchMonthSummary:
    def setup_method(self):
        self.store = _make_store()

    def _query_returns(self, items):
        self.store.table.query.return_value = {"Items": items}

    def test_sums_spend_and_counts_reported_items(self):
        self._query_returns(
            [
                {"spend_usd": Decimal("0.10"), "reported": True},
                {"spend_usd": Decimal("0.05"), "reported": False},
                {"spend_usd": Decimal("0.02"), "reported": True},
            ]
        )

        summary = self.store.fetch_month_summary("2026-03")

        assert summary.spend_usd == 0.17
        assert summary.report_count == 2

    def test_applies_through_date_ceiling_to_sk(self):
        self._query_returns([])

        self.store.fetch_month_summary("2026-06", through_date=date(2026, 6, 5))

        condition = self.store.table.query.call_args.kwargs["KeyConditionExpression"]
        # SK ceiling "<date>Z" (Z > '#') includes all rows of that date but nothing after
        assert "2026-06-05Z" in _condition_values(condition)

    def test_empty_result_returns_zero_summary(self):
        self._query_returns([])

        summary = self.store.fetch_month_summary("2026-03")

        assert summary == MonthSummary(spend_usd=0.0, report_count=0)


class TestComputeDisplay:
    def setup_method(self):
        self.store = _make_store()

    def test_computes_mom_for_all_three_metrics(self):
        # First query: current month; second query: prev month
        self.store.table.query.side_effect = [
            {
                "Items": [
                    {"spend_usd": Decimal("1.00"), "reported": True},
                    {"spend_usd": Decimal("0.20"), "reported": True},
                ]
            },
            {
                "Items": [
                    {"spend_usd": Decimal("1.00"), "reported": True},
                ]
            },
        ]
        self.store._run_spend_usd = 0.06

        display = self.store.compute_display(date(2026, 7, 5))

        assert isinstance(display, CostDisplay)
        assert display.current_run_spend_usd == 0.06
        assert display.monthly_spend_usd == pytest.approx(1.20)
        assert display.spend_change_pct == pytest.approx(20.0)
        assert display.monthly_report_count == 2
        assert display.report_count_change_pct == pytest.approx(100.0)
        assert display.avg_per_report_usd == pytest.approx(0.60)
        assert display.avg_change_pct == pytest.approx(-40.0)

    def test_first_month_returns_none_change_pct(self):
        self.store.table.query.side_effect = [
            {"Items": [{"spend_usd": Decimal("0.10"), "reported": True}]},
            {"Items": []},
        ]

        display = self.store.compute_display(date(2026, 7, 5))

        assert display.spend_change_pct is None
        assert display.report_count_change_pct is None
        assert display.avg_change_pct is None

    def test_caps_prev_day_at_prev_month_last_day(self):
        self.store.table.query.return_value = {"Items": []}

        # March 31 → February compare should cap at Feb 28 (2026 is not a leap year)
        self.store.compute_display(date(2026, 3, 31))

        prev_query = self.store.table.query.call_args_list[1]
        condition = prev_query.kwargs["KeyConditionExpression"]
        assert "2026-02-28Z" in _condition_values(condition)

    def test_caps_current_side_at_today(self):
        self.store.table.query.return_value = {"Items": []}

        # Future-dated backfill rows in the same month must not inflate the MTD total
        self.store.compute_display(date(2026, 7, 5))

        current_query = self.store.table.query.call_args_list[0]
        condition = current_query.kwargs["KeyConditionExpression"]
        assert "2026-07-05Z" in _condition_values(condition)
