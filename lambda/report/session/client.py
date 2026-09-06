"""S3 client for Claude Code session JSONL files."""

import logging
from typing import Any

import boto3

from ..shared.notice import Notice, NoticeSource

logger = logging.getLogger(__name__)


class SessionClient:
    """Client for managing Claude Code session JSONL files on S3."""

    def __init__(self, bucket: str, notice: Notice | None = None) -> None:
        self.s3 = boto3.client("s3")
        self.bucket = bucket
        self._notice = notice or Notice()

    def list_session_objects(self) -> list[dict[str, Any]]:
        prefix = "claude-sessions/"
        objects: list[dict[str, Any]] = []
        paginator = self.s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if not obj["Key"].endswith(".jsonl"):
                    continue
                objects.append(obj)

        return objects

    def read_repo_name(self, project: str) -> str | None:
        """Read the repository name recorded for `project`.

        Missing `.ayumy_repo` is treated as intentional absence; content validation failure is logged as warning.
        """
        key = f"claude-sessions/{project}/.ayumy_repo"
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
        except self.s3.exceptions.NoSuchKey:
            return None
        name = resp["Body"].read().decode("utf-8").strip()
        if name and "/" not in name and ":" not in name:
            return name
        self._notice.add(
            NoticeSource.SESSION,
            "Invalid .ayumy_repo content",
            logger=logger,
            project=project,
            content=repr(name),
        )
        return None

    def delete_sessions(self, keys: list[str]) -> int:
        """Delete the given session objects and return how many were removed.

        Per-key failures are logged as warning; caller (pipeline) decides whether to retry on the next run.
        """
        if not keys:
            return 0

        deleted = 0
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]
            resp = self.s3.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": [{"Key": key} for key in batch],
                    "Quiet": False,
                },
            )
            deleted += len(resp.get("Deleted", []))

            for err in resp.get("Errors", []):
                self._notice.add(
                    NoticeSource.SESSION,
                    "S3 object deletion failed",
                    logger=logger,
                    key=err.get("Key") or "",
                    code=err.get("Code") or "",
                )
        return deleted
