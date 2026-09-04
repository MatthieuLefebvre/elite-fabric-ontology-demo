"""Canary, fail-closed access, tenant scoping, and aggregate non-interference tests."""

from __future__ import annotations

import copy
from datetime import date

import pytest
from generators.common import Dataset, canonical_json
from generators.expected import (
    aged_wip,
    cash_application_metrics,
    compute_expected_answers,
    quarter_variances,
    realization_cohort,
)
from generators.security import (
    accessible_matter_ids,
    authorized_rows,
    require_matter,
    secure_billing_position,
    secure_sum,
    sum_by_currency,
)


def test_deny_overrides_grant_and_missing_access_denies(dataset: Dataset) -> None:
    """Roles and coexisting grants do not override an explicit screen."""
    for slug in ("harbor", "kestrel"):
        firm, partner, screened = f"{slug}_f001", f"{slug}_t001", f"{slug}_m003"
        rows = [a for a in dataset.tables["matter_access"] if a["firm_id"] == firm
                and a["timekeeper_id"] == partner and a["matter_id"] == screened]
        assert {a["effect"] for a in rows} == {"deny", "grant"}
        assert screened not in accessible_matter_ids(dataset.tables, firm, partner)
        assert f"{slug}_m001" in accessible_matter_ids(dataset.tables, firm, partner)
        assert not accessible_matter_ids(dataset.tables, firm, f"{slug}_unknown")
        for target in (screened, f"{slug}_absent", "other_m001"):
            with pytest.raises(PermissionError, match="^Matter unavailable or access denied$"):
                require_matter(dataset.tables, firm, partner, target)
            with pytest.raises(PermissionError):
                secure_billing_position(dataset.tables, firm, partner, target, dataset.as_of)
            with pytest.raises(PermissionError):
                aged_wip(dataset.tables, firm, partner, target, dataset.as_of)
            with pytest.raises(PermissionError):
                cash_application_metrics(dataset.tables, firm, partner, target, dataset.as_of)
        # No inherited grant for a child, even if its parent has an explicit grant.
        no_child = dict(dataset.tables)
        no_child["matter_access"] = [a for a in dataset.tables["matter_access"]
                                    if not (a["timekeeper_id"] == partner and a["matter_id"] == f"{slug}_m002")]
        assert f"{slug}_m002" not in accessible_matter_ids(no_child, firm, partner)
        assert f"{slug}_m001" in accessible_matter_ids(no_child, firm, partner)
    assert not accessible_matter_ids(dataset.tables, "kestrel_f001", "harbor_t001")
    assert not authorized_rows(dataset.tables, "time_entries", "kestrel_f001", "harbor_t001")


def test_no_screened_content_or_aggregate_leakage(dataset: Dataset) -> None:
    """Neither canary, screen identity, narrative, nor screen financials enter goldens."""
    token = dataset.config["simulation"]["canary_token"]
    assert any(token in m["narrative"] for m in dataset.tables["matters"])
    assert any(token in e["narrative"] for e in dataset.tables["time_entries"])
    encoded = canonical_json(dataset.expected_answers)
    for forbidden in (token, "Ashworth", "Bellhaven", "harbor_m003", "kestrel_m003"):
        assert forbidden not in encoded
    for slug in ("harbor", "kestrel"):
        for table in ("matters", "time_entries", "disbursements", "adjustments", "invoices", "budgets"):
            rows = authorized_rows(dataset.tables, table, f"{slug}_f001", f"{slug}_t001")
            assert all(r["matter_id"] != f"{slug}_m003" for r in rows)
            assert token not in canonical_json(rows)
    # Inflate screened financials and narratives; every partner golden stays identical.
    changed = copy.deepcopy(dataset.tables)
    for rows in changed.values():
        for row in rows:
            if row.get("matter_id") not in ("harbor_m003", "kestrel_m003"):
                continue
            for key in row:
                if key.endswith("_cents") and isinstance(row[key], int):
                    row[key] *= 1000000
            if "narrative" in row:
                row["narrative"] += " ADDITIONAL_SCREENED_CANARY"
    assert canonical_json(compute_expected_answers(changed, dataset.as_of)) == encoded


def test_restricted_overrun_and_litigation_are_actually_present(dataset: Dataset) -> None:
    """Screening tests are meaningful: forbidden rows would otherwise affect answers."""
    unblocked = dict(dataset.tables)
    unblocked["matter_access"] = [r for r in dataset.tables["matter_access"] if r["effect"] != "deny"]
    for slug in ("harbor", "kestrel"):
        args = f"{slug}_f001", f"{slug}_t001"
        q2 = quarter_variances(unblocked, *args, dataset.as_of)
        assert f"{slug}_m003" in {m["matter_id"] for m in q2["over_budget_matters"]}
        start, end = date(2026, 8, 1), date(2026, 9, 1)
        denied = realization_cohort(dataset.tables, *args, start, end)["currencies"]["USD"]
        granted = realization_cohort(unblocked, *args, start, end)["currencies"]["USD"]
        assert granted["standard_time_denominator_cents"] > denied["standard_time_denominator_cents"]


def test_secure_currency_aggregation(dataset: Dataset) -> None:
    """Secure helpers filter first, refuse non-matter projections, and separate GBP."""
    for slug in ("harbor", "kestrel"):
        firm, partner = f"{slug}_f001", f"{slug}_t001"
        result = secure_sum(dataset.tables, "time_entries", "value_cents", firm, partner)
        assert set(result) == {"GBP", "USD"}
        allowed = accessible_matter_ids(dataset.tables, firm, partner)
        for currency in ("USD", "GBP"):
            assert result[currency] == sum(e["value_cents"] for e in dataset.tables["time_entries"]
                                           if e["firm_id"] == firm and e["matter_id"] in allowed
                                           and e["currency"] == currency)
        with pytest.raises(ValueError):
            authorized_rows(dataset.tables, "clients", firm, partner)
    with pytest.raises(TypeError):
        sum_by_currency([{"currency": "USD", "value": 0.1}], "value")