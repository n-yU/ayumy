"""Tests for SessionClient."""

from unittest.mock import MagicMock, patch

from report.session import SessionClient


def _make_client():
    """Create a SessionClient with mocked boto3."""
    with patch("report.session.client.boto3"):
        client = SessionClient("bucket")
    client.s3 = MagicMock()
    return client


class TestDeleteSessions:
    def test_deletes_specified_keys(self):
        client = _make_client()
        client.s3.delete_objects.return_value = {
            "Deleted": [
                {"Key": "claude-sessions/proj/s1.jsonl"},
                {"Key": "claude-sessions/proj/s2.jsonl"},
            ],
        }
        keys = [
            "claude-sessions/proj/s1.jsonl",
            "claude-sessions/proj/s2.jsonl",
        ]

        count = client.delete_sessions(keys)

        assert count == 2
        client.s3.delete_objects.assert_called_once()
        call_args = client.s3.delete_objects.call_args
        objects = call_args.kwargs["Delete"]["Objects"]
        assert len(objects) == 2

    def test_returns_zero_for_empty_list(self):
        client = _make_client()

        count = client.delete_sessions([])

        assert count == 0
        client.s3.delete_objects.assert_not_called()
