"""Command-line entry point. Scaffold only: real commands arrive with Milestone 1."""

from __future__ import annotations

import argparse

from family_photo_curator import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="family-photo-curator")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
