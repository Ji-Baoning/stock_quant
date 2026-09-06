"""``python -m stock_quant`` entry point (thin wrapper over the CLI app)."""

from stock_quant.cli import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
