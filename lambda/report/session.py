"""S3 client for Claude Code session JSONL files."""

import logging
from typing import Any

import boto3

logger = logging.getLogger(__name__)


class SessionClient:
    """Client for managing Claude Code session JSONL files on S3.
    """

    def __init__(self, bucket: str) -> None:
        self.s3 = boto3.client("s3")
        self.bucket = bucket

    def list_session_objects(self) -> list[dict[str, Any]]:
        """List all unarchived JSONL objects in `claude-sessions/`.
        """
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
        """Read the repo name from the `.ayumy_repo` metadata file in S3.

        Returns None when the metadata file is missing or empty,
        or when the value is not a bare repo name;
        URL fragments and path separators are rejected to guard against accidental copy-paste of full URLs.
        """
        key = f"claude-sessions/{project}/.ayumy_repo"
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
            name = resp["Body"].read().decode("utf-8").strip()
            if name and "/" not in name and ":" not in name:
                return name
            return None
        except self.s3.exceptions.NoSuchKey:
            return None

    def delete_sessions(self, keys: list[str]) -> int:
        """Delete the given JSONL objects from S3 and return the number actually removed.

        Errors per key are logged but do not raise; the caller (pipeline) decides whether to retry on the next run.
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

            errors = resp.get("Errors", [])
            if errors:
                logger.error(
                    "Failed to delete %d S3 object(s): %s",
                    len(errors),
                    [{"Key": e.get("Key"), "Code": e.get("Code")} for e in errors],
                )
        return deleted
