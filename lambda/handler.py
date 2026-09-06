import json
import logging
import os

import boto3

from report import pipeline
from report.shared import env

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def lambda_handler(event, context):
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
        except Exception as e:
            # Broad: Lambda entry point, surface any failure as 500 to CloudWatch
            logger.exception("Failed to retrieve secret %s: %s", secret_id, e)
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {"error": f"Failed to retrieve secret: {secret_id}"}
                ),
            }

    source = "manual" if event.get("source") == "manual" else None
    target_date = event.get("target_date")

    try:
        timeout_seconds = int(env.require_env("AYUMY_LAMBDA_TIMEOUT"))
        pipeline.run(
            source,
            target_date=target_date,
            memory_limit_mb=int(context.memory_limit_in_mb),
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        # Broad: Lambda entry point, return 500 so CloudWatch records the failure
        logger.exception("Report generation failed")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "report failed"}),
        }

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Report generated successfully"}),
    }
