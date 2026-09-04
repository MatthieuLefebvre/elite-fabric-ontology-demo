"""Independent accounting reconciliations and planted scenario acceptance tests."""

from __future__ import annotations

import copy
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from generators.common import Dataset, Row, month_bounds
from generators.expected import (
    cash_application_metrics,
    compute_expected_answers,
    realization_cohort,
)


def test_all_documents_reconcile_without_duplicate_leakage(dataset: Dataset) -> None:
    """Proforma/invoice quantities trace once to time, costs, and exclusive reductions."""
    tables = dataset.tables
    by_stage: dict[str, dict[str, int]] = {s: {} for s in ("time_entry", "proforma", "invoice")}
    provenance: set[str] = set()
    for adjustment in tables["adjustments"]:
        stage = adjustment["stage"]
        assert adjustment["amount_cents"] > 0
        assert sum(adjustment[s + "_id"] is not None for s in by_stage) == 1
        target = adjustment[stage + "_id"]
        assert target is not None and target not in by_stage[stage]
        assert adjustment["provenance_key"] not in provenance
        provenance.add(adjustment["provenance_key"])
        by_stage[stage][target] = adjustment["amount_cents"]
    entries: dict[str, list[Row]] = defaultdict(list)
    costs: dict[str, list[Row]] = defaultdict(list)
    for entry in tables["time_entries"]:
        if entry["proforma_id"]:
            entries[entry["proforma_id"]].append(entry)
    for cost in tables["disbursements"]:
        if cost["proforma_id"]:
            costs[cost["proforma_id"]].append(cost)
    pfs = {p["proforma_id"]: p for p in tables["proformas"]}
    for pf_id, pf in pfs.items():
        standard = sum(e["standard_value_cents"] for e in entries[pf_id])
        negotiated = sum(e["value_cents"] for e in entries[pf_id])
        reductions = sum(by_stage["time_entry"].get(e["time_entry_id"], 0) for e in entries[pf_id])
        cost = sum(d["amount_cents"] for d in costs[pf_id])
        assert pf["hours"] == sum((e["hours"] for e in entries[pf_id]), Decimal("0.00"))
        assert pf["time_entry_count"] == len(entries[pf_id])
        assert pf["disbursement_count"] == len(costs[pf_id])
        assert pf["standard_time_cents"] == standard
        assert pf["gross_time_cents"] == negotiated
        assert pf["time_entry_reduction_cents"] == reductions
        assert pf["disbursement_cents"] == cost
        assert pf["gross_cents"] == negotiated - reductions + cost
        assert pf["discount_cents"] == by_stage["proforma"].get(pf_id, 0)
        assert pf["net_cents"] == pf["gross_cents"] - pf["discount_cents"]
        assert pf["net_time_cents"] == pf["net_cents"] - cost
    payments: dict[str, int] = defaultdict(int)
    for payment in tables["payments"]:
        assert payment["amount_cents"] > 0  # No refunds or negative cash.
        payments[payment["invoice_id"]] += payment["amount_cents"]
    for invoice in tables["invoices"]:
        pf = pfs[invoice["proforma_id"]]
        assert pf["invoice_id"] == invoice["invoice_id"]
        for field in invoice:
            if field.endswith("_cents") or field in ("hours", "time_entry_count", "disbursement_count"):
                assert invoice[field] == pf[field]
        assert invoice["net_cents"] == invoice["gross_cents"] - invoice["discount_cents"]
        writeoff = by_stage["invoice"].get(invoice["invoice_id"], 0)
        assert 0 <= payments[invoice["invoice_id"]] <= invoice["net_cents"] - writeoff
    assert len({i["proforma_id"] for i in tables["invoices"]}) == len(tables["invoices"])


def test_aged_wip_and_exact_quarter_overruns(dataset: Dataset) -> None:
    """The seeded amounts and ages are deliberate rather than statistical accidents."""
    for slug, count, total in (("harbor", 200, 43200000), ("kestrel", 80, 17280000)):
        answers = dataset.expected_answers["firms"][slug]
        q1 = answers["q1"]
        assert q1["entry_count"] == count
        assert q1["unbilled_time_cents"] == total
        assert q1["hours"] == f"{count * 4}.00"
        assert q1["minimum_age_days"] == 60 and q1["maximum_age_days"] == 120
        assert q1["unbilled_disbursement_cents"] > 0
        assert sum(b["value_cents"] for b in q1["age_buckets"].values()) == total
        assert q1["age_buckets"]["0_29"]["entry_count"] == 0
        assert q1["age_buckets"]["30_59"]["entry_count"] == 0
        assert q1["age_buckets"]["121_plus"]["entry_count"] == 0
        q2 = answers["q2"]
        assert q2["period_start"] == "2026-07-01"
        assert q2["period_end_exclusive"] == "2026-10-01"
        assert q2["actual_through_inclusive"] == "2026-09-04"
        cases = {m["matter_id"]: m for m in q2["over_budget_matters"]}
        assert set(cases) == {f"{slug}_m005", f"{slug}_m006"}
        employment, lease = cases[f"{slug}_m005"], cases[f"{slug}_m006"]
        assert employment["fee_actual_cents"] == 5400000
        assert employment["fee_budget_cents"] == 4000000
        assert employment["fee_variance_cents"] == 1400000
        assert employment["fee_variance_ratio"] == employment["hours_variance_ratio"] == "0.350000"
        assert lease["fee_actual_cents"] == 4320000
        assert lease["fee_variance_cents"] == 320000
        assert lease["fee_variance_ratio"] == lease["hours_variance_ratio"] == "0.080000"


def test_realization_cohorts_and_stage_dominance_both_firms(dataset: Dataset) -> None:
    """Vantage/Redgrave have opposite stages in both firms and different firm mixes."""
    distributions: dict[str, dict[str, int]] = {}
    for slug in ("harbor", "kestrel"):
        q3 = dataset.expected_answers["firms"][slug]["q3"]
        last, previous = q3["last_month"], q3["preceding_month"]
        assert (last["period_start"], last["period_end_exclusive"]) == ("2026-08-01", "2026-09-01")
        assert (previous["period_start"], previous["period_end_exclusive"]) == ("2026-07-01", "2026-08-01")
        current = last["currencies"]["USD"]
        old = previous["currencies"]["USD"]
        assert Decimal(current["issued_prebill_realization"]) < Decimal(old["issued_prebill_realization"])
        assert Decimal(q3["issued_realization_change"]["USD"]) < 0
        for cohort in (current, old):
            reductions = cohort["stage_reductions_cents"]
            denominator = cohort["standard_time_denominator_cents"]
            assert denominator > 0
            assert denominator - cohort["issued_net_time_cents"] == (
                cohort["negotiation_gap_cents"] + reductions["time_entry"] + reductions["proforma"]
            )
            assert cohort["recoverable_time_before_cash_cents"] == cohort["issued_net_time_cents"] - reductions["invoice"]
            selected = [i for i in dataset.tables["invoices"] if i["invoice_id"] in set(cohort["invoice_ids"])]
            assert sum(i["standard_time_cents"] for i in selected) == denominator
            assert sum(i["net_time_cents"] for i in selected) == cohort["issued_net_time_cents"]
            assert sum(i["time_entry_reduction_cents"] for i in selected) == reductions["time_entry"]
            assert sum(i["discount_cents"] for i in selected) == reductions["proforma"]
            invoice_ids = {i["invoice_id"] for i in selected}
            assert sum(a["amount_cents"] for a in dataset.tables["adjustments"]
                       if a["stage"] == "invoice" and a["invoice_id"] in invoice_ids) == reductions["invoice"]
            assert len(cohort["adjustment_ids"]) == len(set(cohort["adjustment_ids"]))
        vantage = current["by_matter"][f"{slug}_m008"]["stage_reductions_cents"]
        redgrave = current["by_matter"][f"{slug}_m009"]["stage_reductions_cents"]
        assert vantage["proforma"] >= 3 * vantage["invoice"] > 0
        assert redgrave["invoice"] >= 3 * redgrave["proforma"] > 0
        distributions[slug] = current["stage_reductions_cents"]
    assert distributions["harbor"]["proforma"] >= 3 * distributions["harbor"]["invoice"]
    assert distributions["kestrel"]["invoice"] >= 3 * distributions["kestrel"]["proforma"]


def test_invoice_writeoffs_cannot_change_issued_realization(dataset: Dataset) -> None:
    """Removing write-offs changes recoverability but never rewrites issued net."""
    changed = dict(dataset.tables)
    changed["adjustments"] = [a for a in dataset.tables["adjustments"] if a["stage"] != "invoice"]
    start, end = month_bounds(dataset.as_of, -1)
    for slug in ("harbor", "kestrel"):
        baseline = realization_cohort(dataset.tables, f"{slug}_f001", f"{slug}_t001", start, end)["currencies"]["USD"]
        result = realization_cohort(changed, f"{slug}_f001", f"{slug}_t001", start, end)["currencies"]["USD"]
        assert result["issued_prebill_realization"] == baseline["issued_prebill_realization"]
        assert result["issued_net_time_cents"] == baseline["issued_net_time_cents"]
        assert result["recoverable_time_before_cash_cents"] > baseline["recoverable_time_before_cash_cents"]


def test_q4_and_actual_sales_dso(dataset: Dataset) -> None:
    """Paid old invoices preserve the distinct sales-ratio construction exactly."""
    for slug in ("harbor", "kestrel"):
        answers = dataset.expected_answers["firms"][slug]
        position = answers["q4"]
        assert position["unbilled_time_cents"] == answers["q1"]["unbilled_time_cents"]
        assert position["outstanding_cents"] == (position["issued_net_cents"] -
                                                  position["collected_cents"] - position["invoice_writeoff_cents"])
        assert position["outstanding_cents"] >= 0
        assert position["source_ids"]["matter"] == f"{slug}_m001"
        dso = answers["solace_dso"]
        assert dso["dso_days"] == "95.000000"
        assert dso["net_credit_sales_cents"] == 4715172
        assert dso["collectible_ar_cents"] == 4977126
        assert dso["collectible_ar_cents"] * 90 == dso["net_credit_sales_cents"] * 95
        invoices = [i for i in dataset.tables["invoices"] if i["matter_id"] == f"{slug}_m010"]
        assert sorted((dataset.as_of - i["issued_on"]).days for i in invoices) == [30, 95, 120, 150]
        paid = sum(p["amount_cents"] for p in dataset.tables["payments"] if p["matter_id"] == f"{slug}_m010")
        assert paid > 0
        assert sum(i["net_cents"] for i in invoices) - paid == dso["collectible_ar_cents"]


def test_completed_cash_application_dso_from_actual_dates(dataset: Dataset) -> None:
    """Solace settles in 90/100 days; Kestrel's Highmere settles in 50/60 days."""
    for slug, expected in (("harbor", 95), ("kestrel", 55)):
        client_matters = {m["matter_id"] for m in dataset.tables["matters"]
                          if m["client_id"] == f"{slug}_c008"}
        assert client_matters == {f"{slug}_m010"}  # No ordinary cash dilutes the client average.
        invoices = {i["invoice_id"]: i for i in dataset.tables["invoices"]
                    if i["matter_id"] in client_matters}
        payments = [p for p in dataset.tables["payments"] if p["invoice_id"] in invoices]
        assert len(payments) == len({p["invoice_id"] for p in payments}) == 2
        lags = []
        for payment in payments:
            invoice = invoices[payment["invoice_id"]]
            assert invoice["issued_on"] <= payment["paid_on"] <= dataset.as_of
            assert payment["amount_cents"] == invoice["net_cents"] > 0
            assert (dataset.as_of - invoice["issued_on"]).days > 90
            lags.append((payment["paid_on"] - invoice["issued_on"]).days)
        assert sorted(lags) == [expected - 5, expected + 5]
        assert sum(lags) == expected * len(payments)
        metric = dataset.expected_answers["firms"][slug]["solace_cash_application_dso"]
        assert metric["average_days"] == metric["cash_weighted_average_days"] == f"{expected}.000000"
        assert metric["application_count"] == 2
        assert metric["payment_ids"] == sorted(p["payment_id"] for p in payments)
        assert metric["invoice_ids"] == sorted(p["invoice_id"] for p in payments)
        assert metric["definition"] != dataset.expected_answers["firms"][slug]["solace_dso"]["definition"]


def test_cash_application_metric_is_fact_derived_and_snapshot_bounded(dataset: Dataset) -> None:
    """Changing cash dates changes the average; future/unpaid observations do not count."""
    tables = dict(dataset.tables)
    tables["payments"] = copy.deepcopy(tables["payments"])
    args = "harbor_f001", "harbor_t001", "harbor_m010", dataset.as_of
    payment = next(p for p in tables["payments"] if p["matter_id"] == "harbor_m010")
    payment["paid_on"] += timedelta(days=2)
    assert cash_application_metrics(tables, *args)["average_days"] == "96.000000"
    payment["paid_on"] = dataset.as_of + timedelta(days=1)
    assert cash_application_metrics(tables, *args)["application_count"] == 1
    tables["payments"] = [p for p in tables["payments"] if p["matter_id"] != "harbor_m010"]
    empty = cash_application_metrics(tables, *args)
    assert empty["application_count"] == 0
    assert empty["average_days"] is empty["cash_weighted_average_days"] is None


def test_expected_answers_recomputed_from_changed_authorized_facts(dataset: Dataset) -> None:
    """Golden answers are derived, not canned amounts hidden in a fixture."""
    tables = dict(dataset.tables)
    tables["time_entries"] = copy.deepcopy(dataset.tables["time_entries"])
    entry = next(e for e in tables["time_entries"] if e["matter_id"] == "harbor_m001" and e["status"] == "unbilled")
    entry["value_cents"] += 12345
    updated = compute_expected_answers(tables, date(2026, 9, 4))
    original = dataset.expected_answers["firms"]["harbor"]["q1"]["unbilled_time_cents"]
    assert updated["firms"]["harbor"]["q1"]["unbilled_time_cents"] == original + 12345