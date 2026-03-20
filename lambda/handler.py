import json
import os
import subprocess
import sys

import boto3


def lambda_handler(event, context):
    """Entry point for the Lambda function.

    Invokes the report package (co-located in lambda/) with secrets from Secrets Manager.
    """
    secrets_client = boto3.client("secretsmanager")
    secret_names = {
        "GITHUB_PAT": "ayumy/github-pat",
        "ANTHROPIC_API_KEY": "ayumy/anthropic-api-key",
        "NOTION_SECRET": "ayumy/notion-secret",
        "SLACK_WEBHOOK_URL": "ayumy/slack-webhook-url",
    }

    env = os.environ.copy()
    if event.get("source") == "manual":
        env["AYUMY_SOURCE"] = "manual"

    for env_var, secret_id in secret_names.items():
        try:
            resp = secrets_client.get_secret_value(SecretId=secret_id)
            env[env_var] = resp["SecretString"]
        except Exception as e:
            print(f"Failed to retrieve secret {secret_id}: {e}")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": f"Failed to retrieve secret: {secret_id}"}),
            }

    try:
        result = subprocess.run(
            [sys.executable, "-m", "report"],
            cwd=os.path.dirname(__file__),
            env=env,
            capture_output=True,
            text=True,
            timeout=280,
        )
    except subprocess.TimeoutExpired as e:
        print(f"report module timed out after {e.timeout}s")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"report module timed out after {e.timeout}s"}),
        }

    print(f"stdout: {result.stdout}")
    if result.stderr:
        print(f"stderr: {result.stderr}")

    if result.returncode != 0:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": "report module failed",
                "returncode": result.returncode,
                "stderr": result.stderr,
            }),
        }

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Report generated successfully"}),
    }
