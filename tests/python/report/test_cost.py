"""Tests for CostStore."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from report import SummaryUsage
from report.cost import CostStore


def _make_store():
    with patch("report.cost.boto3"):
        store = CostStore("table")
    store.table = MagicMock()
    return store


class TestStartRecord:
    def test_writes_row_with_reported_false(self):
        store = _make_store()
        usage = SummaryUsage(input_tokens=1000, output_tokens=200, spend_usd=0.012)

        sk = store.start_record(date(2026, 3, 28), usage)

        store.table.put_item.assert_called_once()
        item = store.table.put_item.call_args.kwargs["Item"]
        assert item["year_month"] == "2026-03"
        assert item["date"] == "2026-03-28"
        assert item["sk"] == sk
        assert item["input_tokens"] == 1000
        assert item["output_tokens"] == 200
        assert item["spend_usd"] == Decimal("0.012")
        assert item["reported"] is False

    def test_captures_active_model_and_pricing(self):
        store = _make_store()
        usage = SummaryUsage(input_tokens=0, output_tokens=0, spend_usd=0.0)

        store.start_record(date(2026, 3, 28), usage)

        item = store.table.put_item.call_args.kwargs["Item"]
        # Snapshot from config.yml at insertion time; refresh if pricing schema changes
        assert item["model"]
        assert item["input_usd_per_1m_tokens"] > 0
        assert item["output_usd_per_1m_tokens"] > 0

    def test_sk_starts_with_target_date(self):
        store = _make_store()
        usage = SummaryUsage(input_tokens=0, output_tokens=0, spend_usd=0.0)

        sk = store.start_record(date(2026, 3, 28), usage)

        assert sk.startswith("2026-03-28#")


class TestMarkReported:
    def test_updates_reported_flag(self):
        store = _make_store()

        store.mark_reported(date(2026, 3, 28), "2026-03-28#exec")

        store.table.update_item.assert_called_once()
        kwargs = store.table.update_item.call_args.kwargs
        assert kwargs["Key"] == {"year_month": "2026-03", "sk": "2026-03-28#exec"}
        assert "reported" in kwargs["UpdateExpression"]
        assert kwargs["ExpressionAttributeValues"] == {":true": True}
