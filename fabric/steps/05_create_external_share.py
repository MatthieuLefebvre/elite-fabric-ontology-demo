"""05: expose the external-share step without bypassing its live capability gate."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fabric.deploy import step_main  # noqa: E402
from fabric.steps import step05_external_share  # noqa: E402
from fabric.steps.common import Context  # noqa: E402


def run(ctx: Context) -> None:
    """Plan external sharing or report its explicit live blocker."""
    step05_external_share.run(ctx)


def main(argv: list[str] | None = None) -> int:
    """Invoke the shared CLI with step 05 selected."""
    return step_main(5, argv)


if __name__ == "__main__":
    raise SystemExit(main())