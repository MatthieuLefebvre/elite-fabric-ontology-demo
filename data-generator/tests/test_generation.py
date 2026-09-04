"""Reproducibility, output bytes, CLI behavior, calendar boundaries, and validation."""

from __future__ import annotations

import ast
import copy
import json
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from generate import generate, load_config, load_dataset, main, write_dataset
from generators.common import (
    SPECS,
    Dataset,
    canonical_json,
    quarter_bounds,
    shift_month,
    validate_config,
)
from generators.storage import arrow_schema, schema_document


def test_same_seed_same_values_and_byte_output(dataset: Dataset, tmp_path: Path) -> None:
    """Every byte, including metadata and JSON, repeats in separate output roots."""
    repeated = generate(seed=42, as_of="2026-09-04")
    assert canonical_json(repeated.tables) == canonical_json(dataset.tables)
    assert repeated.expected_answers == dataset.expected_answers
    left, right = tmp_path / "left", tmp_path / "right"
    manifest = write_dataset(dataset, left)
    write_dataset(repeated, right)
    names = sorted(p.relative_to(left) for p in left.rglob("*") if p.is_file())
    assert len(names) == 2 * len(SPECS) + 3
    for name in names:
        assert (left / name).read_bytes() == (right / name).read_bytes(), name
    assert len(manifest["files"]) == 36
    for item in manifest["files"]:
        assert "\\" not in item["path"]
        arrow = pq.read_table(left / item["path"])
        assert arrow.schema.equals(arrow_schema(item["table"]))
        assert arrow.num_rows == item["row_count"]
        assert set(arrow.column("firm_id").to_pylist()) == {item["firm_id"]}
    loaded = load_dataset(left)
    assert loaded.tables == dataset.tables
    assert loaded.expected_answers == dataset.expected_answers
    assert loaded.config == dataset.config
    assert loaded.seed == 42 and loaded.as_of == date(2026, 9, 4)
    damaged = left / manifest["files"][0]["path"]
    damaged.write_bytes(damaged.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="content hash"):
        load_dataset(left)


def test_spark_schema_is_explicit_for_nullable_and_empty_columns() -> None:
    """Manifest exposes native Spark StructType JSON without relying on inference."""
    document = schema_document()
    assert set(document["tables"]) == set(SPECS)
    assert document["schema_version"] == 1
    for table, spec in document["tables"].items():
        assert spec["spark_schema"]["type"] == "struct"
        fields = spec["spark_schema"]["fields"]
        assert all(f["type"] in ("string", "long", "date", "decimal(10,2)", "decimal(18,2)", "boolean") for f in fields)
        assert "firm_id" in {f["name"] for f in fields}
        assert len(arrow_schema(table)) == len(fields)
    matter_fields = {f["name"]: f for f in document["tables"]["matters"]["spark_schema"]["fields"]}
    assert matter_fields["parent_matter_id"]["nullable"]
    assert matter_fields["closed_on"]["type"] == "date"
    assert not matter_fields["responsible_partner_id"]["nullable"]


def test_different_seeds_change_activity_not_ids(dataset: Dataset) -> None:
    """Seed changes never rename matter identities or erase deliberate anomalies."""
    changed = generate(seed=43)
    assert changed.tables["time_entries"] != dataset.tables["time_entries"]
    assert changed.tables["matters"] == dataset.tables["matters"]
    assert changed.expected_answers["firms"]["harbor"]["q1"]["unbilled_time_cents"] == 43200000
    assert changed.expected_answers["firms"]["kestrel"]["solace_dso"]["dso_days"] == "95.000000"


def test_one_rng_and_no_clock_or_external_corpus() -> None:
    """A source audit catches accidental independent seeds or wall-clock randomness."""
    root = Path(__file__).resolve().parents[1]
    paths = [root / "generate.py", *sorted((root / "generators").glob("*.py"))]
    rng_calls = 0
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "Random":
                    rng_calls += 1
                if isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {"now", "today", "utcnow", "uuid4", "urandom", "seed"}
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in {"faker", "numpy", "secrets"} for alias in node.names)
    assert rng_calls == 1


def test_dry_run_and_cli_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Dry-run does all computation without creating output; invalid dates fail cleanly."""
    output = tmp_path / "never_created"
    assert main(["--dry-run", "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True
    assert result["as_of"] == "2026-09-04" and result["seed"] == 42
    assert result["row_counts"]["time_entries"] == 10500
    assert not output.exists()
    assert main(["--as-of", "not-a-date", "--output", str(output)]) == 2
    assert "as_of" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("snapshot", ["2024-02-29", "2026-01-01", "2026-04-01", "2026-12-31"])
def test_calendar_boundary_snapshots(snapshot: str) -> None:
    """Half-open quarters, leap days and first-of-quarter planting remain valid."""
    generated = generate(as_of=snapshot)
    start, end = quarter_bounds(date.fromisoformat(snapshot))
    assert generated.expected_answers["firms"]["harbor"]["q2"]["period_start"] == str(start)
    assert generated.expected_answers["firms"]["harbor"]["q2"]["period_end_exclusive"] == str(end)
    for slug in ("harbor", "kestrel"):
        answers = generated.expected_answers["firms"][slug]
        assert answers["q1"]["minimum_age_days"] == 60
        assert answers["q1"]["maximum_age_days"] == 120
        assert answers["solace_dso"]["dso_days"] == "95.000000"
        assert answers["solace_cash_application_dso"]["average_days"] == (
            "95.000000" if slug == "harbor" else "55.000000")
        assert {v["fee_variance_ratio"] for v in answers["q2"]["over_budget_matters"]} == {"0.350000", "0.080000"}
    assert all(e["work_date"] <= generated.as_of for e in generated.tables["time_entries"])


def test_calendar_clamping() -> None:
    """Month arithmetic uses real calendar dates rather than approximate day counts."""
    assert shift_month(date(2024, 3, 31), -1) == date(2024, 2, 29)
    assert shift_month(date(2025, 3, 31), -1) == date(2025, 2, 28)
    assert quarter_bounds(date(2026, 12, 31)) == (date(2026, 10, 1), date(2027, 1, 1))


@pytest.mark.parametrize("case", ["slug", "volume", "clients", "timekeepers", "history", "hours", "percent", "parent", "currency", "duplicate_client", "date", "missing"])
def test_minimum_config_validation(case: str) -> None:
    """Invalid scenarios are rejected before partial output or random generation."""
    config = load_config()
    if case == "slug":
        config["firms"][1]["slug"] = "harbor"
    elif case == "volume":
        config["firms"][0]["time_entries"] = 100
    elif case == "clients":
        config["firms"][0]["clients"] = 99
    elif case == "timekeepers":
        config["firms"][0]["timekeepers"] = 3
    elif case == "history":
        config["simulation"]["history_months"] = 2
    elif case == "hours":
        config["simulation"]["employment_hours"] = "1.001"
    elif case == "percent":
        config["simulation"]["ordinary_invoice_percent"] = 101
    elif case == "parent":
        config["matters"][0]["parent"] = 2
    elif case == "currency":
        config["matters"][0]["currency"] = "GBP"
    elif case == "duplicate_client":
        config["clients"][1] = config["clients"][0]
    elif case == "date":
        config["simulation"]["as_of"] = "2026-02-30"
    else:
        del config["simulation"]["usd_standard_rate_cents"]
    with pytest.raises(ValueError):
        validate_config(config)


def test_config_not_mutated_and_yaml_shape_rejected(tmp_path: Path) -> None:
    """Caller config remains unchanged; malformed YAML document shapes are rejected."""
    config = load_config()
    original = copy.deepcopy(config)
    generate(config=config, seed=7)
    assert config == original
    (tmp_path / "firms.yaml").write_text("- not_a_mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_config(tmp_path)