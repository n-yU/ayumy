"""DynamoDB session store for ingesting parsed session data."""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

import boto3

from . import JST

logger = logging.getLogger(__name__)


class SessionStore:
    """Client for writing session metadata to DynamoDB."""

    def __init__(self, table_name: str) -> None:
        """Initialize the store with a DynamoDB table name.

        Args:
            table_name: DynamoDB table name for session metadata
        """
        self.table = boto3.resource("dynamodb").Table(table_name)

    def ingest(self, session_client) -> int:
        """Parse all JSONL files and write session items to DynamoDB.

        Downloads every unarchived JSONL from S3, groups entries by
        (JST date, repo, session_id), and writes the resulting items
        to DynamoDB. Existing items with the same key are overwritten.

        Args:
            session_client: SessionClient instance for S3 access

        Returns:
            The number of items written
        """
        items = self._build_items(session_client)
        return self._write_items(items)

    def _build_items(self, session_client) -> list[dict]:
        """Parse JSONL files and build DynamoDB items.

        Groups all entries by (JST date, repo, session_id) without
        date filtering. Skips entries without timestamps and projects
        without a .ayumy_repo metadata file.

        Args:
            session_client: SessionClient instance for S3 access

        Returns:
            A list of DynamoDB item dicts ready for batch writing
        """
        # (date, repo, session_id) -> accumulated entry data
        groups: dict[tuple[str, str, str], dict] = defaultdict(
            lambda: {
                "project": "",
                "timestamps": [],
                "user_messages": [],
                "tools_used": set(),
            }
        )

        repo_cache: dict[str, str | None] = {}

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
                group = groups[group_key]
                group["project"] = project
                group["timestamps"].append(timestamp)

                entry_type = entry.get("type")
                if entry_type == "user":
                    content = entry.get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        group["user_messages"].append(content.strip())
                elif entry_type == "assistant":
                    for block in entry.get("message", {}).get("content", []):
                        if block.get("type") == "tool_use":
                            group["tools_used"].add(block["name"])

        now = datetime.now(timezone.utc).isoformat()
        items = []
        for (date_str, repo, session_id), group in groups.items():
            if not group["user_messages"]:
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
                "updated_at": now,
            })

        return items

    def _write_items(self, items: list[dict]) -> int:
        """Write items to DynamoDB using batch writer.

        Args:
            items: List of DynamoDB item dicts

        Returns:
            The number of items written
        """
        with self.table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
        return len(items)
