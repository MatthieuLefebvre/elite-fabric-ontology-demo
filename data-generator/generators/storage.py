"""Stable Parquet serialization and explicit Spark-compatible schema manifests."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from .common import (
    FOREIGN_KEYS,
    PRIMARY_KEYS,
    SPECS,
    Dataset,
    Row,
    canonical_json,
    fields,
)

if TYPE_CHECKING:
    import pyarrow as pa


def schema_document() -> Row:
    """Describe field nullability, native Spark types, keys, and money semantics."""
    spark_types = {"S": "string", "I": "long", "D": "date", "H": "decimal(10,2)",
                   "M": "decimal(18,2)", "B": "boolean"}
    tables: dict[str, Row] = {}
    for table in SPECS:
        columns = []
        foreign_keys = []
        for name, kind, nullable in fields(table):
            metadata: Row = {}
            if name.endswith("_cents"):
                metadata = {"unit": "minor_currency_unit", "scale": 2,
                            "currency_column": "standard_rate_currency" if table == "timekeepers" else "currency"}
            if name == "hours" or name == "hours_budget":
                metadata = {"unit": "hours"}
            if name == "threshold":
                metadata = {"unit_column": "rule_type",
                            "units": {"payment_terms": "days", "fee_cap": "major_currency_unit"}}
            columns.append({"name": name, "type": spark_types[kind], "nullable": nullable,
                            "metadata": metadata})
            if name in FOREIGN_KEYS and name != PRIMARY_KEYS[table]:
                target = FOREIGN_KEYS[name]
                foreign_keys.append({"column": name, "table": target, "target_column": PRIMARY_KEYS[target],
                                     "same_firm_required": True})
        tables[table] = {"primary_key": PRIMARY_KEYS[table], "foreign_keys": foreign_keys,
                         "spark_schema": {"type": "struct", "fields": columns}}
    return {
        "schema_version": 1, "id_scheme": "{firm_slug}_{entity_code}{sequence:03d}; e.g. harbor_m001",
        "money": "int64 cents; row currency; never sum across currencies; no FX conversion",
        "standard_value": "hours * timekeeper.standard_rate_cents in standard_rate_currency = matter currency",
        "negotiated_value": "hours * rates.hourly_rate_cents in the same explicit currency",
        "rounding": "decimal half-up to integer cents", "date_intervals": "end_exclusive unless named otherwise",
        "adjustments": "positive reductions; exactly one stage-matching target; fee-only; no negative refunds",
        "realization": "issued net time / standard time for same issued cohort; invoice writeoffs do not change numerator",
        "tables": tables,
    }


def arrow_schema(table: str) -> pa.Schema:
    """Construct a schema even for empty or all-null columns; never infer from rows."""
    import pyarrow as pa

    types = {"S": pa.string(), "I": pa.int64(), "D": pa.date32(),
             "H": pa.decimal128(10, 2), "M": pa.decimal128(18, 2), "B": pa.bool_()}
    return pa.schema([pa.field(name, types[kind], nullable=nullable)
                      for name, kind, nullable in fields(table)])


def write_dataset(dataset: Dataset, output: str | Path = "data") -> Row:
    """Write one Parquet per table per firm plus schema, manifest, and local goldens.

    Byte determinism requires identical Python/PyArrow versions and configuration;
    no path, wall-clock timestamp, random UUID, or run timestamp enters the output.
    The caller owns output directory lifecycle; only named output files are replaced.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    schema_bytes = canonical_json(schema_document()).encode("utf-8")
    (root / "schema.json").write_bytes(schema_bytes)
    files: list[Row] = []
    for firm in sorted(dataset.tables["firms"], key=lambda f: f["slug"]):
        folder = root / firm["slug"]
        folder.mkdir(parents=True, exist_ok=True)
        for table in sorted(SPECS):
            rows = sorted((r for r in dataset.tables[table] if r["firm_id"] == firm["firm_id"]),
                          key=lambda r: r[PRIMARY_KEYS[table]])
            arrow = pa.Table.from_pylist(rows, schema=arrow_schema(table))
            path = folder / f"{table}.parquet"
            pq.write_table(arrow, path, version="2.6", compression="zstd", use_dictionary=False,
                           write_statistics=True, row_group_size=1024, data_page_version="1.0")
            files.append({"firm_id": firm["firm_id"], "table": table,
                          "path": path.relative_to(root).as_posix(), "row_count": len(rows),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    expected_bytes = canonical_json(dataset.expected_answers).encode("utf-8")
    (root / "expected_answers.json").write_bytes(expected_bytes)
    manifest: Row = {
        "schema_version": 1, "seed": dataset.seed, "as_of": str(dataset.as_of),
        "schema_file": "schema.json", "schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "expected_answers_file": "expected_answers.json",
        "expected_answers_sha256": hashlib.sha256(expected_bytes).hexdigest(),
        "writer": {"pyarrow_version": pa.__version__, "compression": "zstd", "row_group_size": 1024},
        "config": dataset.config, "files": files,
        "notice": "Provider-side synthetic source. Do not expose raw files or broad grants as partner-level security.",
    }
    (root / "manifest.json").write_bytes(canonical_json(manifest).encode("utf-8"))
    return manifest


def load_dataset(output: str | Path) -> Dataset:
    """Read generated Parquet using explicit schemas and verify content hashes."""
    import pyarrow.parquet as pq

    root = Path(output).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported schema version")

    def checked(path: str, checksum: str) -> Path:
        """Reject manifest path traversal and any corrupt or stale file."""
        target = (root / path).resolve()
        if not target.is_relative_to(root) or hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
            raise ValueError("Invalid output path or content hash")
        return target

    checked(manifest["schema_file"], manifest["schema_sha256"])
    tables = {table: [] for table in SPECS}
    for item in manifest["files"]:
        path = checked(item["path"], item["sha256"])
        table = item["table"]
        rows = pq.read_table(path, schema=arrow_schema(table)).to_pylist()
        if len(rows) != item["row_count"] or any(r["firm_id"] != item["firm_id"] for r in rows):
            raise ValueError("Manifest row count or tenant mismatch")
        tables[table].extend(rows)
    for table, rows in tables.items():
        rows.sort(key=lambda r: r[PRIMARY_KEYS[table]])
    expected_path = checked(manifest["expected_answers_file"], manifest["expected_answers_sha256"])
    return Dataset(tables=tables, seed=manifest["seed"], as_of=date.fromisoformat(manifest["as_of"]),
                   config=manifest["config"], expected_answers=json.loads(expected_path.read_text(encoding="utf-8")))