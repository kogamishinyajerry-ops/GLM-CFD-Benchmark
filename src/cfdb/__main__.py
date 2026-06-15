"""Entry point for `python -m cfdb`."""

from cfdb.cli import app


def main() -> None:
    """Run the cfdb CLI."""
    app()


if __name__ == "__main__":
    main()
