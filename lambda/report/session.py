"""Claude Code session log client."""

import json
from datetime import datetime
from typing import Any

import boto3

from . import JST, SessionActivity, SessionInfo


class SessionClient:
    """Client for reading and formatting Claude Code session logs from S3."""

    def __init__(self, bucket: str) -> None:
        """Initialize the client with an S3 bucket name.

        Args:
            bucket: S3 bucket name containing session JSONL files
        """
        self.s3 = boto3.client("s3")
        self.bucket = bucket

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
        session_id = parts[-1].replace(".jsonl", "")

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
                content = entry.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            name = block.get("name")
                            if isinstance(name, str) and name:
                                tools_used.add(name)

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

    def fetch_sessions(
        self, since: datetime, until: datetime
    ) -> SessionActivity:
        """Fetch all session logs for the target date range.

        Args:
            since: Start of the target period (inclusive)
            until: End of the target period (exclusive)

        Returns:
            A dict keyed by project name. Each value is a list of
            SessionInfo dicts. Projects with no sessions are omitted
        """
        activity: SessionActivity = {}
        for obj in self.list_session_objects(since, until):
            session = self.parse_session(obj["Key"])
            if session is None:
                continue
            project = session["project"]
            if project not in activity:
                activity[project] = []
            activity[project].append(session)

        # Sort sessions by start_time within each project
        for sessions in activity.values():
            sessions.sort(key=lambda s: s["start_time"])

        return activity

    def format_activity(self, activity: SessionActivity) -> str:
        """Format session logs into the Claude Code section text for Claude API input.

        Args:
            activity: SessionActivity dict as returned by fetch_sessions()

        Returns:
            A Markdown-formatted string for the "# Claude Code セッション"
            section, suitable for inclusion in the Spec.md §5.3 input format
        """
        if not activity:
            return "# Claude Code セッション\nセッションなし"

        lines = ["# Claude Code セッション"]
        for project_name, sessions in sorted(activity.items()):
            lines.append(f"## プロジェクト: {project_name}")

            for i, session in enumerate(sessions, 1):
                start = self._format_time(session["start_time"])
                end = self._format_time(session["end_time"])
                lines.append(f"### セッション {i} ({start} - {end})")

                for msg in session["user_messages"]:
                    lines.append(f"- ユーザー: {msg}")

                if session["tools_used"]:
                    tools = ", ".join(session["tools_used"])
                    lines.append(f"- ツール使用: {tools}")

        return "\n".join(lines)

    @staticmethod
    def _format_time(iso_timestamp: str) -> str:
        """Convert an ISO timestamp to JST HH:MM format.

        Args:
            iso_timestamp: ISO 8601 timestamp string

        Returns:
            Time string in "HH:MM" format (JST)
        """
        if not iso_timestamp:
            return "??:??"
        dt = datetime.fromisoformat(iso_timestamp).astimezone(JST)
        return dt.strftime("%H:%M")
