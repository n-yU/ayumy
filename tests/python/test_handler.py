"""Tests for the Lambda entry point."""

import os
from unittest.mock import MagicMock, patch

import pytest

import handler


@pytest.fixture
def context():
    ctx = MagicMock()
    ctx.memory_limit_in_mb = 512
    return ctx


@pytest.fixture(autouse=True)
def isolated_env():
    # The handler writes retrieved secrets into os.environ, so the dict is restored after each test
    with patch.dict(os.environ, {"AYUMY_LAMBDA_TIMEOUT": "300"}):
        yield


@pytest.fixture(autouse=True)
def secrets_client():
    with patch("handler.boto3.client") as factory:
        client = factory.return_value
        client.get_secret_value.return_value = {"SecretString": "stub"}
        yield client


class TestLambdaHandler:
    def test_returns_success_when_report_completes(self, context):
        with patch("handler.pipeline.run"):
            assert handler.lambda_handler({}, context)["statusCode"] == 200

    def test_reraises_secret_retrieval_failure(self, context, secrets_client):
        secrets_client.get_secret_value.side_effect = RuntimeError("no such secret")

        with pytest.raises(RuntimeError, match="no such secret"):
            handler.lambda_handler({}, context)

    def test_returns_500_when_report_fails(self, context):
        with patch("handler.pipeline.run", side_effect=RuntimeError("boom")):
            assert handler.lambda_handler({}, context)["statusCode"] == 500
