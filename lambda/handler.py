import json
import logging
import os
from typing import Any, Protocol

import boto3

from report import pipeline
from report.shared import env

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


class LambdaContext(Protocol):
    """The parts of the Lambda runtime context this handler reads."""

    memory_limit_in_mb: int | str

    def get_remaining_time_in_millis(self) -> int: ...


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    secrets_client = boto3.client("secretsmanager")
    secret_names = {
        "GITHUB_PAT": "ayumy/github-pat",
        "ANTHROPIC_API_KEY": "ayumy/anthropic-api-key",
        "NOTION_SECRET": "ayumy/notion-secret",
        "SLACK_BOT_TOKEN": "ayumy/slack-bot-token",
    }

    for env_var, secret_id in secret_names.items():
        try:
            resp = secrets_client.get_secret_value(SecretId=secret_id)
            os.environ[env_var] = resp["SecretString"]
        except Exception:
            # Raised rather than returned: Slack has no client yet, so the Errors alarm is the only signal left
            logger.exception("Failed to retrieve secret %s", secret_id)
            raise

    source = "manual" if event.get("source") == "manual" else None
    target_date = event.get("target_date")

    try:
        timeout_seconds = int(env.require_env("AYUMY_LAMBDA_TIMEOUT"))
        pipeline.run(
            source,
            target_date=target_date,
            memory_limit_mb=int(context.memory_limit_in_mb),
            timeout_seconds=timeout_seconds,
            remaining_ms=context.get_remaining_time_in_millis,
        )
    except pipeline.NotifiedFailure:
        # Returned rather than raised: Slack already carries this failure, and the Errors alarm would only repeat it
        logger.exception("Report generation failed")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "report failed"}),
        }

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Report generated successfully"}),
    }
