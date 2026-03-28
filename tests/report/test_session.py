"""Tests for SessionClient snapshot/rollback."""

from unittest.mock import patch


class TestSnapshotAndRollback:
    @patch("report.session.boto3")
    def test_snapshot_returns_current_length(self, mock_boto3):
        from report.session import SessionClient

        client = SessionClient("bucket")
        assert client.snapshot_keys() == 0

        client._fetched_keys = ["key1", "key2"]
        assert client.snapshot_keys() == 2

    @patch("report.session.boto3")
    def test_rollback_discards_keys_after_snapshot(self, mock_boto3):
        from report.session import SessionClient

        client = SessionClient("bucket")
        client._fetched_keys = ["key1", "key2"]
        snapshot = client.snapshot_keys()

        client._fetched_keys.extend(["key3", "key4"])
        assert client.snapshot_keys() == 4

        client.rollback_keys(snapshot)
        assert client._fetched_keys == ["key1", "key2"]
        assert client.snapshot_keys() == 2
