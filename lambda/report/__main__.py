"""Entry point for the report generator (python -m report)."""

import sys

if __name__ == "__main__":
    from .pipeline import run

    try:
        run(source="manual")
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
