"""DynamoDB session store for session metadata."""

import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from . import JST, SessionActivity, SessionInfo

logger = logging.getLogger(__name__)


class SessionStore:
    """Client for reading and writing session metadata in DynamoDB."""

    def __init__(self, table_name: str) -> None:
        """Initialize the store with a DynamoDB table name.

        Args:
            table_name: DynamoDB table name for session metadata
        """
        self.table = boto3.resource("dynamodb").Table(table_name)

    def ingest(self, session_client) -> list[str]:
        """Parse all JSONL files and write session items to DynamoDB.

        Downloads every unarchived JSONL from S3, groups entries by
        (JST date, repo, session_id), and writes the resulting items
        to DynamoDB. Specified fields are updated while preserving
        existing attributes such as reported_at.

        Args:
            session_client: SessionClient instance for S3 access

        Returns:
            S3 keys that were processed
        """
        items, keys = self._build_items(session_client)
        self._write_items(items)
        return keys

    def _build_items(self, session_client) -> tuple[list[dict], list[str]]:
        """Parse JSONL files and build DynamoDB items.

        Groups all entries by (JST date, repo, session_id) without
        date filtering. Skips entries without timestamps and projects
        without a .ayumy_repo metadata file.

        Args:
            session_client: SessionClient instance for S3 access

        Returns:
            A tuple of (DynamoDB items, S3 keys processed)
        """
        # Pattern: [... short-sha] commit message
        # Handles normal, root-commit, and detached HEAD forms
        commit_pattern = re.compile(r"^\[.+\s+([0-9a-f]+)\]\s+(.+)")

        # (date, repo, session_id) -> accumulated entry data
        groups: dict[tuple[str, str, str], dict] = defaultdict(
            lambda: {
                "project": "",
                "timestamps": [],
                "user_messages": [],
                "tools_used": set(),
                "commits": [],
            }
        )

        repo_cache: dict[str, str | None] = {}
        # Track which S3 keys contributed to each group
        key_groups: dict[str, set[tuple[str, str, str]]] = defaultdict(set)

        for obj in session_client.list_session_objects():
            key = obj["Key"]
            parts = key.split("/")
            project = parts[1] if len(parts) >= 3 else "unknown"
            session_id = parts[-1].removesuffix(".jsonl")

            if project not in repo_cache:
                repo_cache[project] = session_client.read_repo_name(project)
            repo = repo_cache[project]
            if repo is None:
                continue

            resp = session_client.s3.get_object(
                Bucket=session_client.bucket, Key=key,
            )
            body = resp["Body"].read().decode("utf-8")

            for line in body.splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed line in %s", key)
                    continue

                timestamp = entry.get("timestamp")
                if not timestamp:
                    continue

                entry_dt = datetime.fromisoformat(timestamp).astimezone(JST)
                date_str = entry_dt.date().isoformat()
                group_key = (date_str, repo, session_id)
                key_groups[key].add(group_key)
                group = groups[group_key]
                group["project"] = project
                group["timestamps"].append(timestamp)

                entry_type = entry.get("type")
                if entry_type == "user":
                    content = entry.get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        group["user_messages"].append(content.strip())
                    elif isinstance(content, list):
                        for block in content:
                            if block.get("type") == "tool_result" and not block.get("is_error"):
                                text = block.get("content", "")
                                if isinstance(text, str):
                                    m = commit_pattern.match(text)
                                    if m:
                                        group["commits"].append({
                                            "sha": m.group(1),
                                            "message": m.group(2),
                                        })
                elif entry_type == "assistant":
                    for block in entry.get("message", {}).get("content", []):
                        if block.get("type") == "tool_use":
                            group["tools_used"].add(block["name"])

        now = datetime.now(timezone.utc).isoformat()
        items = []
        for (date_str, repo, session_id), group in groups.items():
            if not group["user_messages"] and not group["commits"]:
                continue

            timestamps = sorted(group["timestamps"])
            items.append({
                "date": date_str,
                "repo#session_id": f"{repo}#{session_id}",
                "repo": repo,
                "project": group["project"],
                "start_time": timestamps[0],
                "end_time": timestamps[-1],
                "user_messages": group["user_messages"],
                "tools_used": sorted(group["tools_used"]),
                "session_commits": group["commits"],
                "updated_at": now,
            })

        # Only return keys that produced at least one DynamoDB item
        written_groups = {(i["date"], i["repo"], i["repo#session_id"].split("#", 1)[1]) for i in items}
        processed_keys = list(dict.fromkeys(
            k for k, gs in key_groups.items() if gs & written_groups
        ))

        return items, processed_keys

    def _write_items(self, items: list[dict]) -> None:
        """Write items to DynamoDB using update_item.

        Uses SET with attribute assignments so that existing
        reported_at values are preserved across re-ingestion.

        Args:
            items: List of DynamoDB item dicts
        """
        for item in items:
            key = {
                "date": item["date"],
                "repo#session_id": item["repo#session_id"],
            }
            fields = {k: v for k, v in item.items() if k not in key}
            update_expr = "SET " + ", ".join(
                f"#f_{k} = :v_{k}" for k in fields
            )
            self.table.update_item(
                Key=key,
                UpdateExpression=update_expr,
                ExpressionAttributeNames={f"#f_{k}": k for k in fields},
                ExpressionAttributeValues={f":v_{k}": v for k, v in fields.items()},
            )

    def fetch_sessions(self, date_str: str) -> SessionActivity:
        """Query sessions for a specific date from DynamoDB.

        Args:
            date_str: JST date string (YYYY-MM-DD)

        Returns:
            A SessionActivity instance keyed by repository name
        """
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
            }
            data.setdefault(repo, []).append(session_info)

        for sessions in data.values():
            sessions.sort(key=lambda s: s["start_time"])

        return SessionActivity(data)

    def scan_backfill_dates(self, primary_date: date) -> list[date]:
        """Scan DynamoDB for past dates needing report generation.

        Finds dates where reported_at is not set (new sessions) or
        updated_at > reported_at (updated sessions after reporting).

        Args:
            primary_date: The current primary date to exclude

        Returns:
            Sorted list of past dates needing (re-)generation
        """
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
        """Set reported_at on all items for the given date.

        Args:
            date_str: JST date string (YYYY-MM-DD)
        """
        now = datetime.now(timezone.utc).isoformat()

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
