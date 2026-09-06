"""Environment variable and VERSION file readers."""

import os
from functools import lru_cache
from pathlib import Path


def require_env(name: str) -> str:
    """Return the environment variable value.

    Raises:
        ValueError: If the variable is unset or empty.
    """
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value


@lru_cache
def get_version() -> str:
    """Return the ayumy version recorded in the VERSION file."""
    version_path = Path(__file__).resolve().parent.parent.parent / "VERSION"
    return version_path.read_text().strip()
