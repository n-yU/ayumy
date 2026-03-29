"""Entry point for the report generator (python -m report)."""

import logging
import sys

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    from .pipeline import run

    try:
        run(source="manual")
    except ValueError as e:
        logging.getLogger(__name__).error("%s", e)
        sys.exit(1)
