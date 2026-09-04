"""Read-only preflight entry point; shares deployment flags and capability reporting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fabric.deploy import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(preflight_only=True))