"""Workshop release gate: the five deliberately planted cases must remain demonstrable."""

from datetime import date
from decimal import Decimal

from generate import generate


def test_workshop_release_invariants() -> None:
    """Assert the named scenarios independently of random background activity."""
    dataset = generate()
    tables = dataset.tables
    assert dataset.as_of == date(2026, 9, 4)
    matters = {row["matter_id"]: row for row in tables["matters"]}
    assert matters["harbor_m001"]["matter_name"] == "Meridian — Acquisition of Calder Systems"
    assert matters["harbor_m002"]["parent_matter_id"] == "harbor_m001"
    wip = [r for r in tables["time_entries"]
           if r["matter_id"] == "harbor_m001" and r["invoice_id"] is None]
    assert sum(r["value_cents"] for r in wip) == 43_200_000
    ages = [(dataset.as_of - r["work_date"]).days for r in wip]
    assert (min(ages), max(ages)) == (60, 120)
    for matter, expected in (("harbor_m005", Decimal("0.35")),
                             ("harbor_m006", Decimal("0.08"))):
        budget = next(r for r in tables["budgets"] if r["matter_id"] == matter
                      and r["period_start"] == date(2026, 7, 1))
        actual = sum(r["value_cents"] for r in tables["time_entries"]
                     if r["matter_id"] == matter and r["work_date"] >= budget["period_start"])
        assert Decimal(actual - budget["fee_budget_cents"]) / budget["fee_budget_cents"] == expected
    alex = next(r["timekeeper_id"] for r in tables["timekeepers"]
                if r["full_name"] == "Alexandra Reyes")
    assert any(r["timekeeper_id"] == alex and r["matter_id"] == "harbor_m003"
               and r["effect"] == "deny" for r in tables["matter_access"])
    for matter, stage in (("harbor_m008", "proforma"), ("harbor_m009", "invoice")):
        adjustments = [r for r in tables["adjustments"] if r["matter_id"] == matter
                       and date(2026, 8, 1) <= r["adjusted_on"] < date(2026, 9, 1)]
        dominant = sum(r["amount_cents"] for r in adjustments if r["stage"] == stage)
        others = sum(r["amount_cents"] for r in adjustments if r["stage"] != stage)
        assert dominant > 0 and dominant >= 3 * others