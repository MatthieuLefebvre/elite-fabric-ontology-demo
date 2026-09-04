"""04: run transformations through the shared, safety-checked deployment workflow."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fabric.deploy import step_main  # noqa: E402
from fabric.steps import step04_notebooks  # noqa: E402
from fabric.steps.common import Context  # noqa: E402


def run(ctx: Context) -> None:
    """Run parameterized provider-side transformations for both firms."""
    step04_notebooks.run(ctx)


def main(argv: list[str] | None = None) -> int:
    """Invoke the shared CLI with step 04 selected."""
    return step_main(4, argv)


if __name__ == "__main__":
    raise SystemExit(main())