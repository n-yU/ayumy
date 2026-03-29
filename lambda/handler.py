import json
import logging
import os

import boto3

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

from report.pipeline import run


def lambda_handler(event, context):
    """Entry point for the Lambda function.

    Retrieves secrets from Secrets Manager and generates daily reports.
    """
    secrets_client = boto3.client("secretsmanager")
    secret_names = {
        "GITHUB_PAT": "ayumy/github-pat",
        "ANTHROPIC_API_KEY": "ayumy/anthropic-api-key",
        "NOTION_SECRET": "ayumy/notion-secret",
        "SLACK_WEBHOOK_URL": "ayumy/slack-webhook-url",
    }

    for env_var, secret_id in secret_names.items():
        try:
            resp = secrets_client.get_secret_value(SecretId=secret_id)
            os.environ[env_var] = resp["SecretString"]
        except Exception as e:
            logger.error("Failed to retrieve secret %s: %s", secret_id, e)
            return {
                "statusCode": 500,
                "body": json.dumps({"error": f"Failed to retrieve secret: {secret_id}"}),
            }

    source = "manual" if event.get("source") == "manual" else None

    try:
        run(source)
    except Exception:
        logger.exception("Report generation failed")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "report failed"}),
        }

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Report generated successfully"}),
    }
