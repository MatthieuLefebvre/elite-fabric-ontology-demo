"""02: create lakehouses through the shared, safety-checked deployment workflow."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fabric.deploy import step_main  # noqa: E402
from fabric.steps import step02_lakehouses  # noqa: E402
from fabric.steps.common import Context  # noqa: E402


def run(ctx: Context) -> None:
    """Run lakehouse creation with the supplied tenant-aware context."""
    step02_lakehouses.run(ctx)


def main(argv: list[str] | None = None) -> int:
    """Invoke the shared CLI with step 02 selected."""
    return step_main(2, argv)


if __name__ == "__main__":
    raise SystemExit(main())