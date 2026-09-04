"""Reconciled draft/issued documents and non-overlapping adjustment provenance."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .common import Config, Row, Tables, identifier, month_bounds, percentage


def generate_billing(config: Config, tables: Tables, as_of: date) -> None:
    """Bill activity once; invoice write-offs never mutate issued net or discount.

    All reductions apply to fees, never costs. A time-entry reduction is recognized
    upstream of proforma gross; a proforma reduction is its discount. Invoice-stage
    reductions only reduce collectible AR. Adjustment foreign keys are exclusive:
    a proforma adjustment does NOT also carry the eventual invoice ID.
    """
    sim = config["simulation"]
    firms = {identifier(f["slug"], "f", 1): f for f in config["firms"]}
    matters = {m["matter_id"]: m for m in tables["matters"]}
    last_start, last_end = month_bounds(as_of, -1)
    groups: dict[tuple[str, date, str], dict[str, list[Row]]] = {}
    for table in ("time_entries", "disbursements"):
        for row in tables[table]:
            if row["status"] == "unbilled":
                continue
            key = row["matter_id"], row["billing_date"], row["status"]
            groups.setdefault(key, {"time_entries": [], "disbursements": []})[table].append(row)
    counters: dict[tuple[str, str], int] = {}

    def next_id(slug: str, kind: str) -> str:
        """Allocate table-local sequential IDs in stable sorted document order."""
        key = slug, kind
        counters[key] = counters.get(key, 0) + 1
        return identifier(slug, kind, counters[key])

    def adjustment(matter: Row, stage: str, target: str, day: date, amount: int) -> None:
        """Write exactly one positive fee reduction and one stage-specific target."""
        if amount <= 0:
            return
        slug = firms[matter["firm_id"]]["slug"]
        tables["adjustments"].append({
            "adjustment_id": next_id(slug, "a"), "firm_id": matter["firm_id"],
            "matter_id": matter["matter_id"], "stage": stage,
            "time_entry_id": target if stage == "time_entry" else None,
            "proforma_id": target if stage == "proforma" else None,
            "invoice_id": target if stage == "invoice" else None,
            "adjusted_on": day, "amount_cents": amount, "currency": matter["currency"],
            "reason": {"time_entry": "Pre-draft time review reduction",
                       "proforma": "Draft fee concession (write-down)",
                       "invoice": "Post-issue fee write-off; issued net unchanged"}[stage],
            "reason_code": {"time_entry": "time_review", "proforma": "fee_concession",
                            "invoice": "collection_writeoff"}[stage],
            "approver_id": matter["billing_partner_id"],
            "provenance_key": f"{target}:{stage}:review_v1",
        })

    for (matter_id, billing_date, status), group in sorted(groups.items()):
        matter = matters[matter_id]
        firm = firms[matter["firm_id"]]
        slug = firm["slug"]
        issued = status == "billed"
        prepared_on = billing_date - timedelta(days=2) if issued else billing_date
        pf_id = next_id(slug, "pf")
        invoice_id = next_id(slug, "i") if issued else None
        entries, costs = group["time_entries"], group["disbursements"]
        standard = sum(e["standard_value_cents"] for e in entries)
        gross_time = sum(e["value_cents"] for e in entries)
        te_reduction = 0
        for entry in entries:
            amount = percentage(entry["value_cents"], sim["time_entry_reduction_percent"])
            te_reduction += amount
            adjustment(matter, "time_entry", entry["time_entry_id"], prepared_on, amount)
        cost_amount = sum(c["amount_cents"] for c in costs)
        pf_percent = sim["ordinary_proforma_percent"]
        is_last_month = last_start <= billing_date < last_end
        if matter["case_code"] == "proforma_leakage" and is_last_month:
            pf_percent = firm["vantage_proforma_percent"]
        discount = percentage(gross_time - te_reduction, pf_percent)
        gross = gross_time - te_reduction + cost_amount
        net = gross - discount
        amounts = {
            "hours": sum((e["hours"] for e in entries), Decimal("0.00")),
            "time_entry_count": len(entries), "disbursement_count": len(costs),
            "standard_time_cents": standard, "gross_time_cents": gross_time,
            "time_entry_reduction_cents": te_reduction, "disbursement_cents": cost_amount,
            "gross_cents": gross, "discount_cents": discount, "net_cents": net,
            "net_time_cents": net - cost_amount,
        }
        tables["proformas"].append({
            "proforma_id": pf_id, "firm_id": matter["firm_id"], "matter_id": matter_id,
            "prepared_on": prepared_on, "status": "converted" if issued else "draft",
            "currency": matter["currency"], **amounts, "invoice_id": invoice_id,
            "reviewer_id": matter["billing_partner_id"],
        })
        adjustment(matter, "proforma", pf_id, prepared_on, discount)
        for entry in entries + costs:
            entry["proforma_id"], entry["invoice_id"] = pf_id, invoice_id
        if not issued:
            continue
        guideline = next(g for g in tables["billing_guidelines"] if
                         g["firm_id"] == matter["firm_id"] and
                         g["client_id"] == matter["client_id"] and
                         g["currency"] == matter["currency"])
        tables["invoices"].append({
            "invoice_id": invoice_id, "firm_id": matter["firm_id"], "matter_id": matter_id,
            "proforma_id": pf_id, "issued_on": billing_date,
            "due_on": billing_date + timedelta(days=guideline["payment_terms_days"]),
            "status": "issued", "currency": matter["currency"], **amounts,
            "ebilling_status": "accepted" if (as_of - billing_date).days >= 2 else "submitted",
        })
        tables["invoice_guidelines"].append({
            "invoice_guideline_id": next_id(slug, "ig"), "firm_id": matter["firm_id"],
            "invoice_id": invoice_id, "billing_guideline_id": guideline["billing_guideline_id"],
            "compliant": True,
        })
        inv_percent = sim["ordinary_invoice_percent"]
        if matter["case_code"] in ("dso_95", "healthy"):
            inv_percent = 0
        elif matter["case_code"] == "invoice_leakage" and is_last_month:
            inv_percent = firm["redgrave_invoice_percent"]
        write_off = percentage(amounts["net_time_cents"], inv_percent)
        assert invoice_id is not None
        adjustment(matter, "invoice", invoice_id,
                   min(as_of, billing_date + timedelta(days=5)), write_off)
        age = (as_of - billing_date).days
        if matter["case_code"] == "dso_95":
            if age not in (150, 120):
                continue  # The separate opening/recent unpaid sales-ratio cohort.
            lag = firm.get("cash_application_days", 95) + (-5 if age == 150 else 5)
            paid, paid_on = net, billing_date + timedelta(days=lag)
        elif age < 10:
            continue
        else:
            collectible = net - write_off
            pay_all = age >= 120 or matter["case_code"] == "healthy"
            paid = collectible if pay_all else collectible // 2
            paid_on = min(as_of, billing_date + timedelta(days=30))
        if paid:
            tables["payments"].append({
                "payment_id": next_id(slug, "pay"), "firm_id": matter["firm_id"],
                "matter_id": matter_id, "invoice_id": invoice_id,
                "paid_on": paid_on,
                "amount_cents": paid, "currency": matter["currency"],
                "payment_method": "bank_transfer",
            })