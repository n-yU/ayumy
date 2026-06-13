"""DynamoDB session store for session metadata."""

import logging
from datetime import UTC, date, datetime

import boto3
from boto3.dynamodb.conditions import Key

from .. import SessionActivity, SessionInfo
from .parser import SessionLogParser

logger = logging.getLogger(__name__)


class SessionStore:
    """Client for reading and writing session metadata in DynamoDB."""

    def __init__(self, table_name: str, parser: SessionLogParser | None = None) -> None:
        self.table = boto3.resource("dynamodb").Table(table_name)
        self.parser = parser or SessionLogParser()

    def ingest(self, session_client) -> list[str]:
        """Parse all unarchived JSONL from S3 into DynamoDB items and return the processed S3 keys.

        Existing attributes such as `reported_at` are preserved on re-ingestion.
        """
        items, keys = self.parser.build_items(session_client)
        self._write_items(items)
        return keys

    def _write_items(self, items: list[dict]) -> None:
        """Write `items` to DynamoDB via `update_item` so existing `reported_at` values are preserved across re-ingestion."""
        for item in items:
            key = {
                "date": item["date"],
                "repo#session_id": item["repo#session_id"],
            }
            fields = {k: v for k, v in item.items() if k not in key}
            update_expr = "SET " + ", ".join(f"#f_{k} = :v_{k}" for k in fields)
            self.table.update_item(
                Key=key,
                UpdateExpression=update_expr,
                ExpressionAttributeNames={f"#f_{k}": k for k in fields},
                ExpressionAttributeValues={f":v_{k}": v for k, v in fields.items()},
            )

    def fetch_sessions(self, date_str: str) -> SessionActivity:
        """Query the JST date `date_str` (`YYYY-MM-DD`) and return sessions grouped by repository."""
        items = []
        response = self.table.query(
            KeyConditionExpression=Key("date").eq(date_str),
        )
        items.extend(response["Items"])

        while "LastEvaluatedKey" in response:
            response = self.table.query(
                KeyConditionExpression=Key("date").eq(date_str),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response["Items"])

        data: dict[str, list[SessionInfo]] = {}
        for item in items:
            repo = item["repo"]
            session_id = item["repo#session_id"].split("#", 1)[1]
            session_info: SessionInfo = {
                "session_id": session_id,
                "project": item["project"],
                "start_time": item["start_time"],
                "end_time": item["end_time"],
                "user_messages": item["user_messages"],
                "tools_used": item["tools_used"],
                "session_commits": item.get("session_commits", []),
                "session_pulls": [int(n) for n in item.get("session_pulls", [])],
                "session_issues": [int(n) for n in item.get("session_issues", [])],
            }
            data.setdefault(repo, []).append(session_info)

        for sessions in data.values():
            sessions.sort(key=lambda s: s["start_time"])

        return SessionActivity(data)

    def scan_backfill_dates(self, primary_date: date) -> list[date]:
        """Return past dates whose sessions are new (no `reported_at`) or have been updated since the last report (`updated_at > reported_at`), excluding `primary_date`."""
        # Attr-to-attr comparison requires raw expression string
        scan_kwargs = {
            "FilterExpression": "attribute_not_exists(reported_at) OR updated_at > reported_at",
            "ProjectionExpression": "#d",
            "ExpressionAttributeNames": {"#d": "date"},
        }

        dates: set[date] = set()
        response = self.table.scan(**scan_kwargs)
        for item in response["Items"]:
            dates.add(date.fromisoformat(item["date"]))

        while "LastEvaluatedKey" in response:
            response = self.table.scan(
                **scan_kwargs,
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            for item in response["Items"]:
                dates.add(date.fromisoformat(item["date"]))

        return sorted(d for d in dates if d < primary_date)

    def mark_reported(self, date_str: str) -> None:
        """Stamp `reported_at` on every item under the JST date `date_str`."""
        now = datetime.now(UTC).isoformat()

        response = self.table.query(
            KeyConditionExpression=Key("date").eq(date_str),
            ProjectionExpression="#d, #sk",
            ExpressionAttributeNames={
                "#d": "date",
                "#sk": "repo#session_id",
            },
        )
        items = response["Items"]

        while "LastEvaluatedKey" in response:
            response = self.table.query(
                KeyConditionExpression=Key("date").eq(date_str),
                ProjectionExpression="#d, #sk",
                ExpressionAttributeNames={
                    "#d": "date",
                    "#sk": "repo#session_id",
                },
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response["Items"])

        for item in items:
            self.table.update_item(
                Key={
                    "date": item["date"],
                    "repo#session_id": item["repo#session_id"],
                },
                UpdateExpression="SET reported_at = :ts",
                ExpressionAttributeValues={":ts": now},
            )
