"""Time and cost activity driven by the caller's single random.Random instance."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from random import Random

from .common import (
    Config,
    Row,
    Tables,
    cents,
    history_start,
    identifier,
    month_bounds,
    quarter_bounds,
    shift_month,
)


def generate_activity(config: Config, tables: Tables, as_of: date, rng: Random) -> None:
    """Plant exact scenarios, then fill to each firm's exact configured time volume.

    billing_date is the intended document date: invoice issue for billed rows,
    draft preparation for proforma rows, null for unbilled rows. It is not cash date.
    """
    sim = config["simulation"]
    start = history_start(config, as_of)
    qstart, _ = quarter_bounds(as_of)
    tks = {row["timekeeper_id"]: row for row in tables["timekeepers"]}
    rates: dict[str, list[Row]] = {}
    for rate in tables["rates"]:
        rates.setdefault(rate["matter_id"], []).append(rate)
    for firm in config["firms"]:
        slug = firm["slug"]
        firm_id = identifier(slug, "f", 1)
        matters = [m for m in tables["matters"] if m["firm_id"] == firm_id]
        by_number = {i: matter for i, matter in enumerate(matters, start=1)}
        entries: list[Row] = []

        def emit(matter: Row, day: date, hours: Decimal, status: str,
                 billing_date: date | None = None) -> None:
            """Append one priced row without consuming any independent randomness."""
            rate = rng.choice(rates[matter["matter_id"]])
            tk = tks[rate["timekeeper_id"]]
            entries.append({
                "time_entry_id": identifier(slug, "te", len(entries) + 1), "firm_id": firm_id,
                "matter_id": matter["matter_id"], "timekeeper_id": tk["timekeeper_id"],
                "rate_id": rate["rate_id"], "work_date": day, "hours": hours,
                "rate_cents": rate["hourly_rate_cents"], "billable_flag": True, "phase": "all",
                "standard_value_cents": cents(hours, tk["standard_rate_cents"]),
                "value_cents": cents(hours, rate["hourly_rate_cents"]),
                "currency": matter["currency"], "status": status, "billing_date": billing_date,
                "proforma_id": None, "invoice_id": None,
                "narrative": (sim["canary_token"] + " Screened work detail." if
                              matter["case_code"] == "screened" else "Synthetic legal services."),
            })

        for i in range(firm["aged_wip_entries"]):
            emit(by_number[1], as_of - timedelta(days=60 + i % 61), Decimal("4.00"), "unbilled")
        for number, prefix in ((5, "employment"), (6, "lease")):
            for _ in range(sim[f"{prefix}_entries"]):
                day = qstart + timedelta(days=rng.randrange((as_of - qstart).days + 1))
                emit(by_number[number], day, Decimal(str(sim[f"{prefix}_hours"])), "unbilled")
        for offset in (-2, -1):
            month, _ = month_bounds(as_of, offset)
            issued_on = month + timedelta(days=14)
            for number, key in ((8, "vantage_entries_per_month"), (9, "redgrave_entries_per_month")):
                for _ in range(firm[key]):
                    emit(by_number[number], issued_on - timedelta(days=7), Decimal("4.00"),
                         "billed", issued_on)

        # Guarantee that dropping the screen changes both Q2 and Q3, once each.
        emit(by_number[3], qstart, Decimal("8.00"), "unbilled")
        last_month, _ = month_bounds(as_of, -1)
        emit(by_number[3], last_month + timedelta(days=7), Decimal("8.00"),
             "billed", last_month + timedelta(days=14))

        # Keep opening AR : recent sales = 1 : 18 for the 95-day sales ratio.
        # Add two fully paid older invoices OUTSIDE the sales window; equal values
        # make both arithmetic and cash-weighted application lags average 95 days
        # for Harbor, 55 for Kestrel. All applications complete before the snapshot.
        for age, count in ((150, 5), (120, 5), (95, 5), (30, 90)):
            issued_on = as_of - timedelta(days=age)
            for _ in range(count):
                emit(by_number[10], issued_on - timedelta(days=7), Decimal("1.00"),
                     "billed", issued_on)

        def ordinary(matter: Row) -> None:
            """Create ordinary history, avoiding contamination of the planted baselines."""
            end = as_of
            if matter["case_code"] == "aged_wip":
                end = as_of - timedelta(days=121)
            elif matter["case_code"] in ("employment_overrun", "lease_overrun"):
                end = qstart - timedelta(days=1)
            day = start + timedelta(days=rng.randrange((end - start).days + 1))
            age = (as_of - day).days
            if age < 30:
                status, billing_date = "unbilled", None
            elif age < 60:
                status, billing_date = "proforma", min(as_of, day + timedelta(days=14))
            else:
                status = "billed"
                billing_date = shift_month(day.replace(day=1), 1) + timedelta(days=5)
            emit(matter, day, Decimal(rng.randint(1, 24)) / 4, status, billing_date)

        ordinary_matters = [m for m in matters if m["case_code"] not in
                    ("dso_95", "proforma_leakage", "invoice_leakage")]
        for matter in ordinary_matters:
            ordinary(matter)
        while len(entries) < firm["time_entries"]:
            ordinary(rng.choice(ordinary_matters))
        tables["time_entries"].extend(entries)
        cost_number = 0
        for index, entry in enumerate(entries):
            # No costs in Solace: the exact DSO case is purely credit fee sales.
            if index % 20 or entry["matter_id"] == by_number[10]["matter_id"]:
                continue
            cost_number += 1
            tables["disbursements"].append({
                "disbursement_id": identifier(slug, "d", cost_number), "firm_id": firm_id,
                "matter_id": entry["matter_id"], "incurred_on": entry["work_date"],
                "amount_cents": rng.randint(10, 500) * 100, "currency": entry["currency"],
                "status": entry["status"], "billing_date": entry["billing_date"],
                "proforma_id": None, "invoice_id": None,
                "description": "Synthetic reimbursable filing or travel cost.",
                "type": "filing_fee" if cost_number % 2 else "travel",
                "vendor": (f"{firm['name']} Synthetic Filing Bureau" if cost_number % 2
                           else f"{firm['name']} Synthetic Travel Services"),
                "recoverable_flag": True,
            })