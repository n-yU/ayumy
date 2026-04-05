"""S3 client for Claude Code session JSONL files."""

import logging
from typing import Any

import boto3

logger = logging.getLogger(__name__)


class SessionClient:
    """Client for managing Claude Code session JSONL files on S3."""

    def __init__(self, bucket: str) -> None:
        """Initialize the client with an S3 bucket name.

        Args:
            bucket: S3 bucket name containing session JSONL files
        """
        self.s3 = boto3.client("s3")
        self.bucket = bucket

    def list_session_objects(self) -> list[dict[str, Any]]:
        """List all unarchived JSONL objects in claude-sessions/.

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
                objects.append(obj)

        return objects

    def read_repo_name(self, project: str) -> str | None:
        """Read the repo name from .ayumy_repo metadata file in S3.

        Args:
            project: Project directory name from S3 path

        Returns:
            The repo name, or None if metadata is missing or invalid
        """
        key = f"claude-sessions/{project}/.ayumy_repo"
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
            name = resp["Body"].read().decode("utf-8").strip()
            # Validate: must be a plain repo name (no URL fragments)
            if name and "/" not in name and ":" not in name:
                return name
            return None
        except self.s3.exceptions.NoSuchKey:
            return None

    def delete_sessions(self, keys: list[str]) -> int:
        """Delete JSONL files from S3.

        Called after successful DynamoDB ingestion to remove
        processed files. If ingestion fails, files are preserved
        for retry on the next execution.

        Args:
            keys: S3 object keys to delete

        Returns:
            The number of files deleted
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
        return deleted
