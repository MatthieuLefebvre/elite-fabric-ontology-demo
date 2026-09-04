"""06: create or inspect shortcuts through the safety-checked deployment workflow."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fabric.deploy import step_main  # noqa: E402
from fabric.steps import step06_shortcuts  # noqa: E402
from fabric.steps.common import Context  # noqa: E402


def run(ctx: Context) -> None:
    """Ensure approved gold shortcuts without overwriting conflicting targets."""
    step06_shortcuts.run(ctx)


def main(argv: list[str] | None = None) -> int:
    """Invoke the shared CLI with step 06 selected."""
    return step_main(6, argv)


if __name__ == "__main__":
    raise SystemExit(main())