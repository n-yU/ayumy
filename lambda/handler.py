import json
import os
import subprocess
import sys


def lambda_handler(event, context):
    """Entry point for the Lambda function.

    Invokes report.py (co-located in lambda/) with secrets from Secrets Manager.
    """
    import boto3

    secrets_client = boto3.client("secretsmanager")
    secret_names = {
        "GITHUB_PAT": "ayumy/github-pat",
        "ANTHROPIC_API_KEY": "ayumy/anthropic-api-key",
        "NOTION_TOKEN": "ayumy/notion-token",
        "SLACK_WEBHOOK_URL": "ayumy/slack-webhook-url",
    }

    env = os.environ.copy()
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

    script_path = os.path.join(os.path.dirname(__file__), "report.py")
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=280,
        )
    except subprocess.TimeoutExpired as e:
        print(f"report.py timed out after {e.timeout}s")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"report.py timed out after {e.timeout}s"}),
        }

    print(f"stdout: {result.stdout}")
    if result.stderr:
        print(f"stderr: {result.stderr}")

    if result.returncode != 0:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": "report.py failed",
                "returncode": result.returncode,
                "stderr": result.stderr,
            }),
        }

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Report generated successfully"}),
    }
