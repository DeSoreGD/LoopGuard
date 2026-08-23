"""LoopGuard entry points."""

from __future__ import annotations

from selfboss.app import run


def main() -> int:
    """Launch the LoopGuard GUI application."""
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
