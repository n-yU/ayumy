"""DynamoDB store for Claude API cost execution log."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import boto3
import boto3.dynamodb.conditions as conditions

from config import CONFIG

from .domain import summary
from .shared import dates


@dataclass(frozen=True)
class MonthSummary:
    spend_usd: float
    call_count: int


@dataclass(frozen=True)
class Display:
    """Rendered inputs for the Slack cost line; `*_change_pct` fields are None when the prior period has no data."""

    current_run_spend_usd: float
    monthly_spend_usd: float
    spend_change_pct: float | None
    monthly_call_count: int
    call_count_change_pct: float | None


def _pct_change(current: float, prev: float) -> float | None:
    if prev <= 0:
        return None
    return (current - prev) / prev * 100


class Store:
    """Client for persisting per-execution Claude API cost records."""

    def __init__(self, table_name: str) -> None:
        self.table = boto3.resource("dynamodb").Table(table_name)
        self._run_spend_usd = 0.0

    def start_record(self, target_date: date, usage: summary.Usage) -> None:
        """Record one Claude API call; `model` and pricing are snapshotted onto the row so later config changes do not affect historical spend."""
        executed_at_utc = datetime.now(UTC)
        executed_date_jst = executed_at_utc.astimezone(dates.JST).date()
        year_month = executed_date_jst.strftime("%Y-%m")
        executed_at = executed_at_utc.isoformat()
        sk = f"{executed_date_jst.isoformat()}#{executed_at}"
        rates = CONFIG.claude.pricing[CONFIG.claude.model]
        self.table.put_item(
            Item={
                "year_month": year_month,
                "sk": sk,
                "target_date": target_date.isoformat(),
                "executed_at": executed_at,
                "model": CONFIG.claude.model,
                "input_usd_per_1m_tokens": Decimal(
                    str(rates["input_usd_per_1m_tokens"])
                ),
                "output_usd_per_1m_tokens": Decimal(
                    str(rates["output_usd_per_1m_tokens"])
                ),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "spend_usd": Decimal(str(usage.spend_usd)),
            }
        )
        self._run_spend_usd += usage.spend_usd

    def fetch_month_summary(
        self, year_month: str, through_date: date | None = None
    ) -> MonthSummary:
        """Sum `spend_usd` and count rows; `through_date` caps SK to include only rows up to that JST date."""
        condition = conditions.Key("year_month").eq(year_month)
        if through_date is not None:
            # "Z" (0x5A) sorts after "#" (0x23), so <= "<date>Z" includes all rows for that date
            condition = condition & conditions.Key("sk").lte(
                f"{through_date.isoformat()}Z"
            )
        items = self._query_all(condition)
        spend = sum(float(item["spend_usd"]) for item in items)
        return MonthSummary(spend_usd=spend, call_count=len(items))

    def compute_display(self, today: date) -> Display:
        """Build the Slack cost line inputs for the JST date `today`."""
        current = self.fetch_month_summary(today.strftime("%Y-%m"), through_date=today)

        prev_last = today.replace(day=1) - timedelta(days=1)
        prev_day = min(today.day, prev_last.day)
        prev_through = date(prev_last.year, prev_last.month, prev_day)
        prev = self.fetch_month_summary(
            prev_last.strftime("%Y-%m"), through_date=prev_through
        )

        return Display(
            current_run_spend_usd=self._run_spend_usd,
            monthly_spend_usd=current.spend_usd,
            spend_change_pct=_pct_change(current.spend_usd, prev.spend_usd),
            monthly_call_count=current.call_count,
            call_count_change_pct=_pct_change(current.call_count, prev.call_count),
        )

    def _query_all(self, condition) -> list[dict]:
        items: list[dict] = []
        response = self.table.query(KeyConditionExpression=condition)
        items.extend(response["Items"])
        while "LastEvaluatedKey" in response:
            response = self.table.query(
                KeyConditionExpression=condition,
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response["Items"])
        return items
