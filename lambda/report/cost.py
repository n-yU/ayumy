"""DynamoDB store for Claude API cost execution log."""

from datetime import UTC, date, datetime
from decimal import Decimal

import boto3

from config import CONFIG

from . import SummaryUsage


class CostStore:
    """Client for persisting per-execution Claude API cost records."""

    def __init__(self, table_name: str) -> None:
        self.table = boto3.resource("dynamodb").Table(table_name)

    def start_record(self, target_date: date, usage: SummaryUsage) -> str:
        """`model` and pricing are snapshotted onto the row so later config changes do not affect historical spend."""
        year_month = target_date.strftime("%Y-%m")
        date_str = target_date.isoformat()
        executed_at = datetime.now(UTC).isoformat()
        sk = f"{date_str}#{executed_at}"
        rates = CONFIG.claude.pricing[CONFIG.claude.model]
        self.table.put_item(
            Item={
                "year_month": year_month,
                "sk": sk,
                "date": date_str,
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
                "reported": False,
            }
        )
        return sk

    def mark_reported(self, target_date: date, sk: str) -> None:
        year_month = target_date.strftime("%Y-%m")
        self.table.update_item(
            Key={"year_month": year_month, "sk": sk},
            UpdateExpression="SET reported = :true",
            ExpressionAttributeValues={":true": True},
        )
