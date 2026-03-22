"""Claude Code session log client."""

import json
from datetime import datetime
from typing import Any

import boto3

from . import SessionActivity, SessionInfo


class SessionClient:
    """Client for reading and formatting Claude Code session logs from S3."""

    def __init__(self, bucket: str) -> None:
        """Initialize the client with an S3 bucket name.

        Args:
            bucket: S3 bucket name containing session JSONL files
        """
        self.s3 = boto3.client("s3")
        self.bucket = bucket
        self._fetched_keys: list[str] = []

    def list_session_objects(
        self, since: datetime, until: datetime
    ) -> list[dict[str, Any]]:
        """List JSONL objects in claude-sessions/ within the target period.

        Args:
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)

        Returns:
            A list of S3 object metadata dicts with Key and LastModified
        """
        prefix = "claude-sessions/"
        objects: list[dict[str, Any]] = []
        paginator = self.s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if not obj["Key"].endswith(".jsonl"):
                    continue
                last_modified = obj["LastModified"]
                if since <= last_modified < until:
                    objects.append(obj)

        return objects

    def parse_session(
        self, key: str, since: datetime, until: datetime
    ) -> SessionInfo | None:
        """Download and parse a single JSONL session file from S3.

        Filters entries by timestamp to include only those within the
        target period. Entries without a timestamp are skipped.

        Args:
            key: S3 object key (e.g. claude-sessions/project/session.jsonl)
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)

        Returns:
            A SessionInfo dict, or None if the session has no meaningful
            content within the target period
        """
        resp = self.s3.get_object(Bucket=self.bucket, Key=key)
        body = resp["Body"].read().decode("utf-8")

        # Extract project name and session ID from key
        # Format: claude-sessions/{project-name}/{session-id}.jsonl
        parts = key.split("/")
        project = parts[1] if len(parts) >= 3 else "unknown"
        session_id = parts[-1].removesuffix(".jsonl")

        user_messages: list[str] = []
        tools_used: set[str] = set()
        timestamps: list[str] = []

        for line in body.splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            entry_type = entry.get("type")
            timestamp = entry.get("timestamp")

            # Filter entries by timestamp
            if not timestamp:
                continue
            entry_dt = datetime.fromisoformat(timestamp)
            if not (since <= entry_dt < until):
                continue

            if entry_type == "user":
                timestamps.append(timestamp)
                content = entry.get("message", {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    user_messages.append(content.strip())

            elif entry_type == "assistant":
                timestamps.append(timestamp)
                for block in entry.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        tools_used.add(block["name"])

        if not user_messages:
            return None

        timestamps.sort()
        return {
            "session_id": session_id,
            "project": project,
            "start_time": timestamps[0] if timestamps else "",
            "end_time": timestamps[-1] if timestamps else "",
            "user_messages": user_messages,
            "tools_used": sorted(tools_used),
        }

    def _read_repo_name(self, project: str) -> str:
        """Read the repo name from .ayumy_repo metadata file in S3.

        Falls back to the raw project directory name if the metadata
        file does not exist.

        Args:
            project: Project directory name from S3 path

        Returns:
            The repo name, or the original project name as fallback
        """
        key = f"claude-sessions/{project}/.ayumy_repo"
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
            name = resp["Body"].read().decode("utf-8").strip()
            # Validate: must be a plain repo name (no URL fragments)
            if name and "/" not in name and ":" not in name:
                return name
            return project
        except self.s3.exceptions.NoSuchKey:
            return project

    def fetch_sessions(
        self, since: datetime, until: datetime,
    ) -> SessionActivity:
        """Fetch all session logs for the target date range.

        Resolves project directory names to repository names using
        .ayumy_repo metadata files in S3.

        Args:
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)

        Returns:
            A SessionActivity instance. Repos with no sessions are omitted
        """
        data: dict[str, list[SessionInfo]] = {}
        self._fetched_keys: list[str] = []
        # Cache .ayumy_repo lookups per project to avoid repeated S3 reads
        repo_name_cache: dict[str, str] = {}

        for obj in self.list_session_objects(since, until):
            self._fetched_keys.append(obj["Key"])
            session = self.parse_session(obj["Key"], since, until)
            if session is None:
                continue

            project = session["project"]
            if project not in repo_name_cache:
                repo_name_cache[project] = self._read_repo_name(project)
            key = repo_name_cache[project]

            if key not in data:
                data[key] = []
            data[key].append(session)

        # Sort sessions by start_time within each project
        for sessions in data.values():
            sessions.sort(key=lambda s: s["start_time"])

        return SessionActivity(data)

    def archive_sessions(self) -> int:
        """Move fetched JSONL files from claude-sessions/ to processed/.

        Archives exactly the objects that were listed by the preceding
        fetch_sessions() call, avoiding race conditions with late arrivals.
        Copies each object to the processed/ prefix (preserving project
        subdirectory structure) and then deletes the original.

        Returns:
            The number of session files archived
        """
        archived = 0

        for src_key in self._fetched_keys:
            # claude-sessions/{project}/{session}.jsonl -> processed/{project}/{session}.jsonl
            dst_key = "processed/" + src_key.removeprefix("claude-sessions/")

            self.s3.copy_object(
                Bucket=self.bucket,
                CopySource={"Bucket": self.bucket, "Key": src_key},
                Key=dst_key,
            )
            self.s3.delete_object(Bucket=self.bucket, Key=src_key)
            archived += 1

        return archived
