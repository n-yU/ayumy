"""DynamoDB session store for session metadata."""

import hashlib
import json
import logging
from datetime import UTC, date, datetime

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .. import SessionActivity, SessionInfo
from ..shared.notice import Notice
from .parser import SessionLogParser

logger = logging.getLogger(__name__)


def _content_hash(fields: dict) -> str:
    """Return the SHA-256 hash of the attributes that feed a report."""
    # `updated_at` is stamped on every ingest, so including it would make every hash unique
    content = {k: v for k, v in fields.items() if k != "updated_at"}
    payload = json.dumps(
        content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SessionStore:
    """Client for reading and writing session metadata in DynamoDB."""

    def __init__(
        self,
        table_name: str,
        notice: Notice | None = None,
        parser: SessionLogParser | None = None,
    ) -> None:
        self.table = boto3.resource("dynamodb").Table(table_name)
        self.parser = parser or SessionLogParser(notice)

    def ingest(self, session_client) -> list[str]:
        """Write session metadata parsed from S3 and return the consumed S3 keys; existing attributes such as `reported_at` are preserved on re-ingestion.

        Keys of items skipped as unchanged are included, since their content is already stored.
        """
        items, keys = self.parser.build_items(session_client)
        self._write_items(items)
        return keys

    def _write_items(self, items: list[dict]) -> None:
        """Uses `update_item` to preserve existing `reported_at` values across re-ingestion.

        Writes are conditional on `content_hash` so that re-ingesting a date whose content is unchanged leaves `updated_at` alone,
        keeping the date out of backfill detection.
        """
        skipped = 0
        for item in items:
            key = {
                "date": item["date"],
                "repo#session_id": item["repo#session_id"],
            }
            fields = {k: v for k, v in item.items() if k not in key}
            fields["content_hash"] = _content_hash(fields)
            update_expr = "SET " + ", ".join(f"#f_{k} = :v_{k}" for k in fields)
            try:
                self.table.update_item(
                    Key=key,
                    UpdateExpression=update_expr,
                    ConditionExpression=(
                        "attribute_not_exists(#f_content_hash) "
                        "OR #f_content_hash <> :v_content_hash"
                    ),
                    ExpressionAttributeNames={f"#f_{k}": k for k in fields},
                    ExpressionAttributeValues={f":v_{k}": v for k, v in fields.items()},
                )
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                skipped += 1

        if skipped:
            logger.info("Skipped %d unchanged item(s)", skipped)

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
                "session_commits": item["session_commits"],
                "session_pulls": [int(n) for n in item["session_pulls"]],
                "session_issues": [int(n) for n in item["session_issues"]],
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
