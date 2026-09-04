"""Independent structural and currency assertions in addition to runtime validation."""

from __future__ import annotations

import copy
import re
from datetime import date
from decimal import Decimal

import pytest
from generators.common import (
    FOREIGN_KEYS,
    PRIMARY_KEYS,
    SPECS,
    Dataset,
    cents,
    fields,
    history_start,
    quarter_bounds,
)
from generators.validation import validate_dataset


def test_exact_volumes_and_window(dataset: Dataset) -> None:
    """Both tenants meet configured volume and service-window contracts."""
    assert len(dataset.tables) == 18
    for slug, clients, matters, tks, entries in (("harbor", 15, 60, 40, 8000), ("kestrel", 10, 25, 15, 2500)):
        for table, expected in (("clients", clients), ("matters", matters),
                                ("timekeepers", tks), ("time_entries", entries)):
            assert sum(r["firm_id"] == f"{slug}_f001" for r in dataset.tables[table]) == expected
        dates = [e["work_date"] for e in dataset.tables["time_entries"] if e["firm_id"] == f"{slug}_f001"]
        assert min(dates) >= date(2025, 3, 4)
        assert max(dates) <= date(2026, 9, 4)
        assert (max(dates) - min(dates)).days >= 530
    assert dataset.as_of == date(2026, 9, 4)


def test_schema_keys_and_all_foreign_keys(dataset: Dataset) -> None:
    """Every FK exists in the same firm; all entity IDs are globally unique strings."""
    indices = {table: {row[PRIMARY_KEYS[table]]: row for row in rows}
               for table, rows in dataset.tables.items()}
    ids: set[str] = set()
    for table, rows in dataset.tables.items():
        for row in rows:
            pk = PRIMARY_KEYS[table]
            assert row[pk] not in ids
            ids.add(row[pk])
            assert isinstance(row[pk], str)
            assert row[pk].startswith(row["firm_id"].split("_")[0] + "_")
            assert set(row) == {name for name, _, _ in fields(table)}
            assert all(re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in row)
            for column, target_table in FOREIGN_KEYS.items():
                if column == pk or row.get(column) is None:
                    continue
                target = indices[target_table][row[column]]
                assert target["firm_id"] == row["firm_id"]
                assert isinstance(row[column], str)
            for name, kind, nullable in fields(table):
                assert nullable or row[name] is not None
                if kind == "I" and row[name] is not None:
                    assert type(row[name]) is int
    assert set(indices) == set(SPECS)


def test_matter_hierarchy_roles_and_exact_seeds(dataset: Dataset) -> None:
    """Preserve named workshop cases, independent role columns, and acyclic children."""
    tables = dataset.tables
    matters = {m["matter_id"]: m for m in tables["matters"]}
    tks = {t["timekeeper_id"]: t for t in tables["timekeepers"]}
    clients = {c["client_id"]: c for c in tables["clients"]}
    assert {f["firm_name"] for f in tables["firms"]} == {"Harbor & Vance LLP", "Kestrel Legal"}
    assert tks["harbor_t001"]["full_name"] == "Alexandra Reyes"
    assert tks["harbor_t001"]["practice_group_id"] == "harbor_pg001"
    expected_names = {
        1: "Meridian — Acquisition of Calder Systems", 2: "Meridian — Post-completion integration",
        3: "Ashworth — Regulatory investigation", 4: "Northwind — Patent portfolio review",
        5: "Castellan — Employment tribunal series", 6: "Castellan — Lease renegotiations",
        7: "Dunmore — Joint venture formation", 8: "Vantage — Contract dispute",
        9: "Redgrave — Product liability defence", 10: "Solace — Fund formation",
    }
    for slug in ("harbor", "kestrel"):
        assert sum(m["parent_matter_id"] is not None for m in matters.values()
                   if m["firm_id"] == f"{slug}_f001") >= 3
        for number, name in expected_names.items():
            expected = name if slug == "harbor" else dataset.config["firms"][1]["matter_names"][number - 1]
            assert matters[f"{slug}_m{number:03d}"]["matter_name"] == expected
        assert matters[f"{slug}_m001"]["fee_arrangement"] == "fixed_fee_capped"
        assert matters[f"{slug}_m002"]["parent_matter_id"] == f"{slug}_m001"
        assert clients[matters[f"{slug}_m001"]["client_id"]]["client_name"] == (
            "Meridian Industrial Group" if slug == "harbor" else "Asterwick Engineering")
    for number in (1, 2, 5, 6):
        assert matters[f"harbor_m{number:03d}"]["responsible_partner_id"] == "harbor_t001"
    for matter in matters.values():
        roles = [matter[k] for k in ("responsible_partner_id", "billing_partner_id", "originating_partner_id")]
        assert len(set(roles)) == 3
        assert all(tks[role]["role"] == "partner" for role in roles)
        seen = {matter["matter_id"]}
        current = matter
        while current["parent_matter_id"]:
            parent = matters[current["parent_matter_id"]]
            assert parent["matter_id"] not in seen
            seen.add(parent["matter_id"])
            assert parent["client_id"] == matter["client_id"]
            current = parent


def test_currency_rate_basis_and_decimal_hours(dataset: Dataset) -> None:
    """No implicit FX, mixed rate bases, or binary floating point in financial rows."""
    tables = dataset.tables
    tks = {t["timekeeper_id"]: t for t in tables["timekeepers"]}
    rates = {r["rate_id"]: r for r in tables["rates"]}
    matters = {m["matter_id"]: m for m in tables["matters"]}
    entities = {e["legal_entity_id"]: e for e in tables["legal_entities"]}
    for entry in tables["time_entries"]:
        tk, rate, matter = tks[entry["timekeeper_id"]], rates[entry["rate_id"]], matters[entry["matter_id"]]
        assert isinstance(entry["hours"], Decimal)
        assert entry["standard_value_cents"] == cents(entry["hours"], tk["standard_rate_cents"])
        assert entry["value_cents"] == cents(entry["hours"], rate["hourly_rate_cents"])
        assert tk["standard_rate_currency"] == rate["currency"] == entry["currency"] == matter["currency"]
        assert entities[matter["legal_entity_id"]]["currency"] == matter["currency"]
        assert rate["effective_from"] <= entry["work_date"] <= rate["effective_to"]
    for rows in tables.values():
        assert all(not isinstance(value, float) for row in rows for value in row.values())
    for slug in ("harbor", "kestrel"):
        dunmore = matters[f"{slug}_m007"]
        assert dunmore["currency"] == "GBP"
        assert dunmore["legal_entity_id"] == f"{slug}_le002"
        assert all(m["currency"] == "USD" for m in matters.values() if
                   m["firm_id"] == f"{slug}_f001" and m["matter_id"] != dunmore["matter_id"])


def test_complete_runtime_validator(dataset: Dataset) -> None:
    """The production validator also passes all complete default tables."""
    validate_dataset(dataset)
    assert history_start(dataset.config, dataset.as_of) == date(2025, 3, 4)
    assert quarter_bounds(dataset.as_of) == (date(2026, 7, 1), date(2026, 10, 1))


@pytest.mark.parametrize("mutation", ["duplicate", "foreign_firm", "currency", "cycle", "overpayment", "stage"])
def test_validator_rejects_corruption(dataset: Dataset, mutation: str) -> None:
    """Checks fail on deliberately corrupted relations and financial targets."""
    broken = copy.deepcopy(dataset)
    if mutation == "duplicate":
        broken.tables["time_entries"].append(copy.deepcopy(broken.tables["time_entries"][0]))
    elif mutation == "foreign_firm":
        broken.tables["time_entries"][0]["timekeeper_id"] = "kestrel_t001"
    elif mutation == "currency":
        broken.tables["time_entries"][0]["currency"] = "GBP"
    elif mutation == "cycle":
        broken.tables["matters"][0]["parent_matter_id"] = "harbor_m002"
    elif mutation == "overpayment":
        broken.tables["payments"][0]["amount_cents"] = 10 ** 12
    else:
        adj = broken.tables["adjustments"][0]
        adj["stage"] = "invoice" if adj["stage"] != "invoice" else "proforma"
    with pytest.raises(ValueError):
        validate_dataset(broken)