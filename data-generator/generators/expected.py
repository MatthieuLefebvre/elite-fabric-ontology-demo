"""Factual local golden metrics, computed from authorized rows rather than prose."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .common import Row, Tables, month_bounds, quarter_bounds, ratio
from .security import (
    accessible_matter_ids,
    authorized_rows,
    require_matter,
    secure_billing_position,
)


def aged_wip(tables: Tables, firm_id: str, partner_id: str, matter_id: str, as_of: date) -> Row:
    """Answer Q1 at matter-only grain (children are intentionally not rolled up)."""
    matter = require_matter(tables, firm_id, partner_id, matter_id)
    entries = [e for e in authorized_rows(tables, "time_entries", firm_id, partner_id)
               if e["matter_id"] == matter_id and e["status"] == "unbilled"]
    costs = [d for d in authorized_rows(tables, "disbursements", firm_id, partner_id)
             if d["matter_id"] == matter_id and d["status"] == "unbilled"]
    buckets: dict[str, Row] = {}
    for label, low, high in (("0_29", 0, 29), ("30_59", 30, 59), ("60_89", 60, 89),
                             ("90_120", 90, 120), ("121_plus", 121, 10000000)):
        selected = [e for e in entries if low <= (as_of - e["work_date"]).days <= high]
        buckets[label] = {"entry_count": len(selected),
                          "hours": str(sum((e["hours"] for e in selected), Decimal("0.00"))),
                          "value_cents": sum(e["value_cents"] for e in selected)}
    total = sum(e["value_cents"] for e in entries)
    ages = [(as_of - e["work_date"]).days for e in entries]
    return {
        "matter_id": matter_id, "as_of": str(as_of), "currency": matter["currency"],
        "scope": "single_matter_excluding_children_and_unconverted_drafts",
        "entry_count": len(entries), "unbilled_time_cents": total,
        "unbilled_standard_time_cents": sum(e["standard_value_cents"] for e in entries),
        "hours": str(sum((e["hours"] for e in entries), Decimal("0.00"))),
        "unbilled_disbursement_cents": sum(d["amount_cents"] for d in costs),
        "minimum_age_days": min(ages) if ages else None,
        "maximum_age_days": max(ages) if ages else None,
        "value_weighted_age_days": ratio(sum(e["value_cents"] * (as_of - e["work_date"]).days
                                             for e in entries), total),
        "age_buckets": buckets,
        "source_ids": sorted(e["time_entry_id"] for e in entries),
    }


def quarter_variances(tables: Tables, firm_id: str, partner_id: str, as_of: date) -> Row:
    """Answer Q2 for authorized responsible-partner matters, with a matched period."""
    start, end = quarter_bounds(as_of)
    matters = {m["matter_id"]: m for m in authorized_rows(tables, "matters", firm_id, partner_id)
               if m["responsible_partner_id"] == partner_id}
    actuals: dict[str, tuple[int, Decimal]] = {}
    for entry in authorized_rows(tables, "time_entries", firm_id, partner_id):
        if entry["matter_id"] in matters and start <= entry["work_date"] <= as_of:
            fees, hours = actuals.get(entry["matter_id"], (0, Decimal("0.00")))
            actuals[entry["matter_id"]] = fees + entry["value_cents"], hours + entry["hours"]
    items: list[Row] = []
    for budget in authorized_rows(tables, "budgets", firm_id, partner_id):
        matter_id = budget["matter_id"]
        if matter_id not in matters or (budget["period_start"], budget["period_end_exclusive"]) != (start, end):
            continue
        fees, hours = actuals.get(matter_id, (0, Decimal("0.00")))
        variance = fees - budget["fee_budget_cents"]
        if variance <= 0:
            continue
        items.append({
            "matter_id": matter_id, "matter_name": matters[matter_id]["matter_name"],
            "currency": budget["currency"], "fee_actual_cents": fees,
            "fee_budget_cents": budget["fee_budget_cents"], "fee_variance_cents": variance,
            "fee_variance_ratio": ratio(variance, budget["fee_budget_cents"]),
            "hours_actual": str(hours), "hours_budget": str(budget["hours_budget"]),
            "hours_variance": str(hours - budget["hours_budget"]),
            "hours_variance_ratio": ratio(hours - budget["hours_budget"], budget["hours_budget"]),
            "budget_id": budget["budget_id"],
        })
    return {"period_start": str(start), "period_end_exclusive": str(end),
            "actual_through_inclusive": str(as_of), "partner_role": "responsible_partner_id",
            "basis": "quarter_to_snapshot_negotiated_time_vs_full_quarter_budget_excluding_costs",
            "over_budget_matters": sorted(items, key=lambda r: r["matter_id"])}


def realization_cohort(tables: Tables, firm_id: str, partner_id: str,
                       start: date, end_exclusive: date) -> Row:
    """Answer Q3 using fee-only invoices issued in one matched calendar-month cohort.

    Stage amounts follow exclusive adjustment targets to their issued invoice.
    Invoice write-offs are all recognized through the dataset snapshot for that
    issued cohort, NOT a claim that all their recognition dates fall in the month.
    Draft-only adjustments are excluded from the issued-cohort denominator.
    """
    allowed = accessible_matter_ids(tables, firm_id, partner_id)
    litigation = {p["practice_group_id"] for p in tables["practice_groups"]
                  if p["firm_id"] == firm_id and p["practice_group_name"] == "litigation"}
    matter_ids = {m["matter_id"] for m in tables["matters"] if m["matter_id"] in allowed
                  and m["firm_id"] == firm_id and m["practice_group_id"] in litigation}
    invoices = [i for i in tables["invoices"] if i["firm_id"] == firm_id
                and i["matter_id"] in matter_ids and start <= i["issued_on"] < end_exclusive]
    invoice_ids = {i["invoice_id"] for i in invoices}
    entries = {e["time_entry_id"]: e for e in authorized_rows(tables, "time_entries", firm_id, partner_id)}
    proformas = {p["proforma_id"]: p for p in authorized_rows(tables, "proformas", firm_id, partner_id)}
    matched: list[Row] = []
    for adj in authorized_rows(tables, "adjustments", firm_id, partner_id):
        if adj["stage"] == "time_entry":
            invoice_id = entries[adj["time_entry_id"]]["invoice_id"]
        elif adj["stage"] == "proforma":
            invoice_id = proformas[adj["proforma_id"]]["invoice_id"]
        else:
            invoice_id = adj["invoice_id"]
        if invoice_id in invoice_ids:
            matched.append(adj)

    def summarize(selected: list[Row], adjustments: list[Row]) -> Row:
        """Summarize a single-currency issued cohort without joining duplicate rows."""
        standard = sum(i["standard_time_cents"] for i in selected)
        gross = sum(i["gross_time_cents"] for i in selected)
        net = sum(i["net_time_cents"] for i in selected)
        stages = {stage: sum(a["amount_cents"] for a in adjustments if a["stage"] == stage)
                  for stage in ("time_entry", "proforma", "invoice")}
        return {
            "invoice_count": len(selected), "standard_time_denominator_cents": standard,
            "negotiated_gross_time_cents": gross, "negotiation_gap_cents": standard - gross,
            "issued_net_time_cents": net, "issued_net_total_cents": sum(i["net_cents"] for i in selected),
            "disbursement_cents": sum(i["disbursement_cents"] for i in selected),
            "issued_prebill_realization": ratio(net, standard),
            "stage_reductions_cents": stages,
            "stage_reduction_to_matched_standard": {stage: ratio(amount, standard)
                                                    for stage, amount in stages.items()},
            "recoverable_time_before_cash_cents": net - stages["invoice"],
            "recoverable_time_realization": ratio(net - stages["invoice"], standard),
            "invoice_ids": sorted(i["invoice_id"] for i in selected),
            "adjustment_ids": sorted(a["adjustment_id"] for a in adjustments),
        }

    currencies: dict[str, Row] = {}
    for currency in sorted({i["currency"] for i in invoices}):
        selected = [i for i in invoices if i["currency"] == currency]
        adjustments = [a for a in matched if a["currency"] == currency]
        by_matter: dict[str, Row] = {}
        for matter_id in sorted({i["matter_id"] for i in selected}):
            by_matter[matter_id] = summarize([i for i in selected if i["matter_id"] == matter_id],
                                             [a for a in adjustments if a["matter_id"] == matter_id])
        currencies[currency] = {**summarize(selected, adjustments), "by_matter": by_matter}
    return {
        "period_start": str(start), "period_end_exclusive": str(end_exclusive),
        "scope": "authorized_litigation_only", "cohort_basis": "invoice_issued_on",
        "adjustment_cutoff": "all_recognized_through_dataset_snapshot_for_this_issued_cohort",
        "denominator_basis": "standard_time_value_same_issued_invoices_same_currency_excluding_costs",
        "currencies": currencies,
    }


def dso_metrics(tables: Tables, firm_id: str, partner_id: str, matter_id: str, as_of: date) -> Row:
    """Define sales DSO, not invoice age: net collectible AR / 90-day net sales * 90."""
    position = secure_billing_position(tables, firm_id, partner_id, matter_id, as_of)
    start = as_of - timedelta(days=89)
    invoices = [i for i in authorized_rows(tables, "invoices", firm_id, partner_id)
                if i["matter_id"] == matter_id]
    sales = sum(i["net_cents"] for i in invoices if start <= i["issued_on"] <= as_of)
    return {
        "matter_id": matter_id, "currency": position["currency"],
        "sales_window_start": str(start), "sales_window_end_inclusive": str(as_of),
        "sales_window_days": 90, "net_credit_sales_cents": sales,
        "collectible_ar_cents": position["outstanding_cents"],
        "dso_days": ratio(position["outstanding_cents"] * 90, sales),
        "definition": "collectible_ar_after_writeoffs_and_cash_divided_by_net_90_day_credit_sales_times_90",
    }


def cash_application_metrics(tables: Tables, firm_id: str, partner_id: str,
                             matter_id: str, as_of: date) -> Row:
    """Invoice-to-cash application days, not a sales/AR ratio or unpaid invoice age.

    Grain is one actual payment application through the snapshot, including partial
    applications if present. Unpaid invoices contribute no observations. The planted
    case has one full application per settled invoice, so this also equals its
    completed-invoice settlement average. No fabricated settlement dates are used.
    """
    matter = require_matter(tables, firm_id, partner_id, matter_id)
    invoices = {i["invoice_id"]: i for i in authorized_rows(tables, "invoices", firm_id, partner_id)
                if i["matter_id"] == matter_id and i["issued_on"] <= as_of}
    payments = [p for p in authorized_rows(tables, "payments", firm_id, partner_id)
                if p["matter_id"] == matter_id and p["invoice_id"] in invoices
                and invoices[p["invoice_id"]]["issued_on"] <= p["paid_on"] <= as_of]
    lags = [(p["paid_on"] - invoices[p["invoice_id"]]["issued_on"]).days for p in payments]
    return {
        "matter_id": matter_id, "currency": matter["currency"], "as_of": str(as_of),
        "application_count": len(payments),
        "applied_cash_cents": sum(p["amount_cents"] for p in payments),
        "average_days": ratio(sum(lags), len(lags)),
        "cash_weighted_average_days": ratio(
            sum(days * p["amount_cents"] for days, p in zip(lags, payments, strict=True)),
            sum(p["amount_cents"] for p in payments)),
        "definition": "arithmetic_mean_paid_on_minus_issued_on_per_actual_cash_application_through_snapshot",
        "invoice_ids": sorted({p["invoice_id"] for p in payments}),
        "payment_ids": sorted(p["payment_id"] for p in payments),
    }


def compute_expected_answers(tables: Tables, as_of: date) -> Row:
    """Compute all golden answers locally; never serialize screened matter attributes."""
    firms: dict[str, Row] = {}
    previous_start, previous_end = month_bounds(as_of, -2)
    last_start, last_end = month_bounds(as_of, -1)
    for firm in tables["firms"]:
        firm_id, slug = firm["firm_id"], firm["slug"]
        partner_id, meridian_id, solace_id = f"{slug}_t001", f"{slug}_m001", f"{slug}_m010"
        previous = realization_cohort(tables, firm_id, partner_id, previous_start, previous_end)
        last = realization_cohort(tables, firm_id, partner_id, last_start, last_end)
        changes: dict[str, str | None] = {}
        for currency, values in last["currencies"].items():
            old = previous["currencies"].get(currency, {}).get("issued_prebill_realization")
            new = values["issued_prebill_realization"]
            changes[currency] = str(Decimal(new) - Decimal(old)) if new is not None and old is not None else None
        firms[slug] = {
            "firm_id": firm_id, "partner_id": partner_id,
            "scope": "explicit_grants_minus_denials_before_any_aggregation",
            "q1": aged_wip(tables, firm_id, partner_id, meridian_id, as_of),
            "q2": quarter_variances(tables, firm_id, partner_id, as_of),
            "q3": {"last_month": last, "preceding_month": previous,
                   "issued_realization_change": changes,
                   "interpretation": "Time-entry and proforma reductions affect issued billed/standard. "
                   "Invoice write-offs reduce recoverability, NOT issued net realization. "
                   "Do not claim that both kinds reduce already issued billed/standard."},
            "q4": secure_billing_position(tables, firm_id, partner_id, meridian_id, as_of),
            "q5": {"status": "denied", "message": "Matter unavailable or access denied"},
            "solace_dso": dso_metrics(tables, firm_id, partner_id, solace_id, as_of),
            "solace_cash_application_dso": cash_application_metrics(
                tables, firm_id, partner_id, solace_id, as_of),
        }
    return {"as_of": str(as_of), "money_unit": "integer_cents", "ratios": "decimal_strings",
            "security_notice": "Local filtered test oracle, not a deployed security boundary or agent.",
            "firms": firms}