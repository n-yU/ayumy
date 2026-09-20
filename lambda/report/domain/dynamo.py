"""Shape of the items exchanged with DynamoDB, shared by the session and cost stores."""

from typing import Any

type Item = dict[str, Any]
