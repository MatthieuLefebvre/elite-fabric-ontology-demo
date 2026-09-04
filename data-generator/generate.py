"""Python 3.11 deterministic synthetic legal-finance generator and CLI.

Public entry points: load_config(), generate(), write_dataset(), load_dataset().
Generation is pure with respect to the filesystem; only write_dataset writes output.
"""

from __future__ import annotations

import argparse
import copy
import sys
from datetime import date
from pathlib import Path
from random import Random
from typing import Sequence

from generators.activity import generate_activity
from generators.billing import generate_billing
from generators.common import (
    DEFAULT_CONFIG,
    PRIMARY_KEYS,
    SPECS,
    Config,
    Dataset,
    canonical_json,
    load_config,
    validate_config,
)
from generators.engagements import generate_budgets, generate_engagements
from generators.expected import compute_expected_answers
from generators.reference import generate_reference
from generators.security import generate_access
from generators.storage import load_dataset, schema_document, write_dataset
from generators.validation import validate_dataset

__all__ = ["Dataset", "generate", "load_config", "load_dataset", "schema_document", "write_dataset"]


def generate(seed: int | None = None, as_of: date | str | None = None,
             config: Config | None = None) -> Dataset:
    """Return complete tables and computed goldens using exactly one Random(seed).

    Config and caller RNG state are never mutated. The default snapshot is fixed at
    2026-09-04 in simulation.yaml. Rates, case identities, dates and IDs do not depend
    on wall time. Changed seeds change background activity, not the matter ID scheme.
    """
    effective = copy.deepcopy(config if config is not None else load_config())
    # Validate the shape first so malformed caller configs report ValueError, not KeyError.
    validate_config(effective)
    sim = effective["simulation"]
    seed_value = sim["seed"] if seed is None else seed
    if type(seed_value) is not int:
        raise ValueError("seed must be an integer")
    snapshot = as_of if as_of is not None else sim["as_of"]
    if not isinstance(snapshot, date):
        try:
            snapshot = date.fromisoformat(str(snapshot))
        except ValueError as exc:
            raise ValueError("as_of must be an ISO calendar date") from exc
    if type(snapshot) is not date:
        raise ValueError("as_of must be a date, not a datetime")
    sim["seed"], sim["as_of"] = seed_value, str(snapshot)
    validate_config(effective)
    tables = {table: [] for table in SPECS}
    rng = Random(seed_value)
    generate_reference(effective, tables)
    generate_engagements(effective, tables, snapshot)
    generate_activity(effective, tables, snapshot, rng)
    generate_billing(effective, tables, snapshot)
    generate_budgets(effective, tables, snapshot)
    generate_access(effective, tables)
    for table, rows in tables.items():
        rows.sort(key=lambda row: row[PRIMARY_KEYS[table]])
    dataset = Dataset(tables=tables, seed=seed_value, as_of=snapshot, config=effective, expected_answers={})
    validate_dataset(dataset)
    dataset.expected_answers = compute_expected_answers(tables, snapshot)
    return dataset


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments; dry-run computes and validates without writing anything."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=None, help="PRNG seed (default: 42 from config)")
    parser.add_argument("--as-of", default=None, help="ISO snapshot date (default fixed: 2026-09-04)")
    parser.add_argument("--output", type=Path, default=Path("data"), help="Output root (default: data)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Directory with the three YAML configs")
    parser.add_argument("--dry-run", action="store_true", help="Generate and validate in memory; write no files")
    args = parser.parse_args(argv)
    try:
        dataset = generate(seed=args.seed, as_of=args.as_of, config=load_config(args.config))
        if args.dry_run:
            summary = {"dry_run": True, "seed": dataset.seed, "as_of": str(dataset.as_of),
                       "output": args.output.as_posix(),
                       "row_counts": {table: len(rows) for table, rows in dataset.tables.items()},
                       "expected_answers": dataset.expected_answers}
        else:
            summary = write_dataset(dataset, args.output)
        print(canonical_json(summary), end="")
    except (ValueError, OSError) as exc:
        print(f"Generator error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())