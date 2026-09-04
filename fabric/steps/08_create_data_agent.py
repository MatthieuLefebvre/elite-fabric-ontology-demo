"""08: expose data-agent creation without inventing an ontology datasource binding."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fabric.deploy import step_main  # noqa: E402
from fabric.steps import step08_agent  # noqa: E402
from fabric.steps.common import Context  # noqa: E402


def run(ctx: Context) -> None:
    """Plan agent creation or report its explicit live capability blocker."""
    step08_agent.run(ctx)


def main(argv: list[str] | None = None) -> int:
    """Invoke the shared CLI with step 08 selected."""
    return step_main(8, argv)


if __name__ == "__main__":
    raise SystemExit(main())