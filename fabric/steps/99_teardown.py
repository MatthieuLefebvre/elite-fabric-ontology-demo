"""99: remove only recorded creations using the existing ownership-safe teardown."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fabric.deploy import step_main, teardown  # noqa: E402
from fabric.steps.common import Context  # noqa: E402


def run(ctx: Context) -> None:
    """Delegate to ownership-checked teardown, preserving adopted resources."""
    teardown(ctx)


def main(argv: list[str] | None = None) -> int:
    """Invoke shared teardown with the same configuration, locking and dry-run flags."""
    return step_main(99, argv)


if __name__ == "__main__":
    raise SystemExit(main())