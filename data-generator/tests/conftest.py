"""Shared full-volume fixture; tests never need a cloud service or a live clock."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Also support running pytest against this directory outside the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generate import generate  # noqa: E402
from generators.common import Dataset  # noqa: E402


@pytest.fixture(scope="session")
def dataset() -> Dataset:
    """Generate the default, full-size snapshot once for read-only invariant tests."""
    return generate()