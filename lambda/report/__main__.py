"""Entry point for the report generator (python -m report)."""

if __name__ == "__main__":
    from .pipeline import run

    run(source="manual")
