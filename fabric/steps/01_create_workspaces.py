"""01: create workspaces through the shared, safety-checked deployment workflow."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fabric.deploy import step_main  # noqa: E402
from fabric.steps import step01_workspaces  # noqa: E402
from fabric.steps.common import Context  # noqa: E402


def run(ctx: Context) -> None:
    """Run workspace creation with the supplied tenant-aware context."""
    step01_workspaces.run(ctx)


def main(argv: list[str] | None = None) -> int:
    """Invoke the shared CLI with step 01 selected."""
    return step_main(1, argv)


if __name__ == "__main__":
    raise SystemExit(main())