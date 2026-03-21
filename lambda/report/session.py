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

    def parse_session(self, key: str) -> SessionInfo | None:
        """Download and parse a single JSONL session file from S3.

        Args:
            key: S3 object key (e.g. claude-sessions/project/session.jsonl)

        Returns:
            A SessionInfo dict, or None if the session has no meaningful
            content
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

            if entry_type == "user":
                if timestamp:
                    timestamps.append(timestamp)
                content = entry.get("message", {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    user_messages.append(content.strip())

            elif entry_type == "assistant":
                if timestamp:
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

    @staticmethod
    def _resolve_repo_name(project: str, repo_names: list[str]) -> str:
        """Map a project name to a repository name by suffix matching.

        Args:
            project: Project directory name from S3 path
                (e.g. "-Users-nyu-Documents-github-ayumy")
            repo_names: Known repository names from GitHub activity

        Returns:
            The matching repo name, or the original project name if no
            match is found
        """
        best_match: str | None = None
        for name in repo_names:
            if project.endswith(f"-{name}"):
                if best_match is None or len(name) > len(best_match):
                    best_match = name
        return best_match if best_match is not None else project

    def fetch_sessions(
        self, since: datetime, until: datetime, repo_names: list[str] | None = None,
    ) -> SessionActivity:
        """Fetch all session logs for the target date range.

        Args:
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)
            repo_names: Known repository names from GitHub activity.
                When provided, project names are mapped to repo names
                by suffix matching

        Returns:
            A SessionActivity instance. Repos with no sessions are omitted
        """
        data: dict[str, list[SessionInfo]] = {}
        names = repo_names or []
        self._fetched_keys: list[str] = []

        for obj in self.list_session_objects(since, until):
            self._fetched_keys.append(obj["Key"])
            session = self.parse_session(obj["Key"])
            if session is None:
                continue
            key = self._resolve_repo_name(session["project"], names)
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
