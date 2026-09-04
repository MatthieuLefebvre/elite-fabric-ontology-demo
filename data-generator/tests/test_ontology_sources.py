"""Source-side ontology coverage; no fabricated Gold defaults or Spark dependency."""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest
import yaml
from generators.common import FOREIGN_KEYS, Dataset, fields
from generators.storage import arrow_schema, schema_document
from generators.validation import validate_dataset

ONTOLOGY = Path(__file__).resolve().parents[2] / "fabric" / "ontology"
ROLE_FIELDS = (
    ("clients", "relationship_partner_id"), ("practice_groups", "group_head_id"),
    ("proformas", "reviewer_id"), ("adjustments", "approver_id"),
)


def test_all_ontology_properties_have_real_source_columns(dataset: Dataset) -> None:
    """Check every direct mapping and each identifier in the current Spark expressions."""
    contract = yaml.safe_load((ONTOLOGY / "ontology.yaml").read_text(encoding="utf-8"))
    for table, names in contract["gold"]["new_source_fields"].items():
        declared = {name for name, _, _ in fields(table)}
        assert set(names) <= declared
        assert dataset.tables[table]
        for row in dataset.tables[table]:
            assert all(name in row and row[name] is not None for name in names)
    for reference in contract["entities"]:
        entity = yaml.safe_load((ONTOLOGY / reference).read_text(encoding="utf-8"))
        columns = {name for name, _, _ in fields(entity["table"])}
        for prop in entity["properties"].values():
            if "expression" not in prop:
                assert prop.get("source_column", prop["column"]) in columns
                continue
            # All current expressions are simple Spark SQL; ignore quoted literals,
            # function/type invocations and SQL keywords, not source identifiers.
            expression = re.sub(r"'(?:''|[^'])*'", "", prop["expression"])
            for token in re.finditer(r"\b[A-Za-z_][A-Za-z_0-9]*\b", expression):
                if (token.group().upper() in {"CASE", "WHEN", "THEN", "ELSE", "END", "AS"}
                        or expression[token.end():].lstrip().startswith("(")):
                    continue
                assert token.group() in columns, (entity["table"], prop["column"], token.group())


def test_extended_fields_follow_source_relationships(dataset: Dataset) -> None:
    """Rate, staff roles, office/department and phases resolve to actual linked rows."""
    tables = dataset.tables
    tks = {t["timekeeper_id"]: t for t in tables["timekeepers"]}
    offices = {o["office_id"]: o for o in tables["offices"]}
    groups = {g["practice_group_id"]: g for g in tables["practice_groups"]}
    matters = {m["matter_id"]: m for m in tables["matters"]}
    rates = {r["rate_id"]: r for r in tables["rates"]}
    budgets = {b["matter_id"]: b for b in tables["budgets"]}
    for table, column in ROLE_FIELDS:
        assert FOREIGN_KEYS[column] == "timekeepers"
        for row in tables[table]:
            person = tks[row[column]]
            assert person["firm_id"] == row["firm_id"]
            assert person["role"] == "partner"
            if table == "practice_groups":
                assert person["practice_group_id"] == row["practice_group_id"]
            if "matter_id" in row:
                assert row[column] == matters[row["matter_id"]]["billing_partner_id"]
    for tk in tks.values():
        assert tk["office"] == offices[tk["office_id"]]["office_name"]
        assert tk["department"] == groups[tk["practice_group_id"]]["practice_group_name"]
        assert (tk["grade"] == "partner") == (tk["role"] == "partner")
    for entity in tables["legal_entities"]:
        assert entity["jurisdiction"].startswith(offices[entity["office_id"]]["country_code"])
    for entry in tables["time_entries"]:
        assert entry["rate_cents"] == rates[entry["rate_id"]]["hourly_rate_cents"]
        assert entry["billable_flag"] is True
        assert entry["phase"] == budgets[entry["matter_id"]]["phase"] == "all"
    for cost in tables["disbursements"]:
        assert cost["recoverable_flag"] is True
        assert cost["type"] in {"filing_fee", "travel"}
        assert cost["vendor"]
    for adj in tables["adjustments"]:
        assert adj["reason_code"] == {"time_entry": "time_review", "proforma": "fee_concession",
                                      "invoice": "collection_writeoff"}[adj["stage"]]
    assert {i["ebilling_status"] for i in tables["invoices"]} <= {"submitted", "accepted"}


@pytest.mark.parametrize("table,column", ROLE_FIELDS)
@pytest.mark.parametrize("target", ["missing_t001", "kestrel_t001"])
def test_extended_staff_fks_reject_missing_and_cross_firm(
    dataset: Dataset, table: str, column: str, target: str,
) -> None:
    """The new role columns participate in the production FK validator, not just tests."""
    broken = copy.deepcopy(dataset)
    row = next(r for r in broken.tables[table] if r["firm_id"] == "harbor_f001")
    row[column] = target
    with pytest.raises(ValueError, match="Missing FK|Cross-firm FK"):
        validate_dataset(broken)


def test_decimal_threshold_arrow_spark_and_units(dataset: Dataset) -> None:
    """M is a real decimal(18,2), preserved by Arrow and described for Spark casts."""
    schema = arrow_schema("billing_guidelines")
    assert schema.field("threshold").type == pa.decimal128(18, 2)
    document = schema_document()["tables"]["billing_guidelines"]["spark_schema"]
    threshold = next(f for f in document["fields"] if f["name"] == "threshold")
    assert threshold["type"] == "decimal(18,2)"
    assert threshold["metadata"]["units"]["payment_terms"] == "days"
    rows = dataset.tables["billing_guidelines"]
    assert pa.Table.from_pylist(rows, schema=schema).to_pylist() == rows
    for row in rows:
        assert row["rule_type"] == "payment_terms"
        assert row["threshold"] == Decimal(row["payment_terms_days"])
        assert row["threshold"].as_tuple().exponent == -2
        assert row["effective_from"] <= dataset.as_of <= row["effective_to"]


@pytest.mark.parametrize("value", [Decimal("1.001"), Decimal("10000000000000000.00"), Decimal("NaN")])
def test_decimal_threshold_rejects_invalid_precision(dataset: Dataset, value: Decimal) -> None:
    """Reject decimals that cannot be represented by the explicit storage contract."""
    broken = copy.deepcopy(dataset)
    broken.tables["billing_guidelines"][0]["threshold"] = value
    with pytest.raises(ValueError, match=r"decimal\(18,2\) overflow"):
        validate_dataset(broken)


def test_firms_have_disjoint_business_names_and_different_cash_profiles(dataset: Dataset) -> None:
    """Kestrel is not Harbor's customers/staff renamed only by an ID prefix."""
    for table, name in (("clients", "client_name"), ("matters", "matter_name"),
                        ("timekeepers", "full_name")):
        harbor = {r[name] for r in dataset.tables[table] if r["firm_id"] == "harbor_f001"}
        kestrel = {r[name] for r in dataset.tables[table] if r["firm_id"] == "kestrel_f001"}
        assert harbor.isdisjoint(kestrel)
    answers = dataset.expected_answers["firms"]
    assert answers["harbor"]["solace_cash_application_dso"]["average_days"] == "95.000000"
    assert answers["kestrel"]["solace_cash_application_dso"]["average_days"] == "55.000000"


def test_source_access_effect_matches_ontology_mapping(dataset: Dataset) -> None:
    """Keep source grant/deny; the ontology maps those to granted/screened, fail closed."""
    entity = yaml.safe_load((ONTOLOGY / "entities" / "matter_access.yaml").read_text(encoding="utf-8"))
    assert entity["properties"]["access_type"]["expression"] == (
        "CASE WHEN effect = 'grant' THEN 'granted' ELSE 'screened' END")
    assert {r["effect"] for r in dataset.tables["matter_access"]} == {"grant", "deny"}
    for slug in ("harbor", "kestrel"):
        screened = [r for r in dataset.tables["matter_access"]
                    if r["matter_id"] == f"{slug}_m003" and r["timekeeper_id"] == f"{slug}_t001"]
        assert {"granted" if r["effect"] == "grant" else "screened" for r in screened} == {"granted", "screened"}