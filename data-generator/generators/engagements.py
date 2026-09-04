"""Generate stable matters, explicit partner roles, applicable rates, and budgets."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .common import (
    PRACTICES,
    Config,
    Tables,
    history_start,
    identifier,
    percentage,
    quarter_bounds,
)


def generate_engagements(config: Config, tables: Tables, as_of: date) -> None:
    """Create planted matters first, then extend them without changing seed IDs."""
    sim = config["simulation"]
    for firm in config["firms"]:
        slug = firm["slug"]
        firm_id = identifier(slug, "f", 1)
        rate_number = 0
        guidelines: set[tuple[int, str]] = set()
        client_names = firm.get("client_names", config["clients"])
        # Keep the DSO client's complete invoice/cash population in its authored
        # case: ordinary extension matters would dilute client-level payment days.
        dso_clients = {m["client"] for m in config["matters"] if m.get("case") == "dso_95"}
        extension_clients = [c for c in list(range(9, firm["clients"] + 1))
                     + list(range(1, min(8, firm["clients"]) + 1)) if c not in dso_clients]
        for number in range(1, firm["matters"] + 1):
            if number <= len(config["matters"]):
                source = dict(config["matters"][number - 1])
                if "matter_names" in firm:
                    source["name"] = firm["matter_names"][number - 1]
            else:
                client = extension_clients[(number - len(config["matters"]) - 1) % len(extension_clients)]
                source = {
                    "client": client,
                    "name": f"{client_names[client - 1]} — Advisory workstream {number}",
                    "practice": PRACTICES[(number - 1) % len(PRACTICES)],
                }
            currency = source.get("currency", "USD")
            matter_id = identifier(slug, "m", number)
            client = source["client"]
            guidelines.add((client, currency))
            case = source.get("case", "ordinary")
            responsible = 1 if number in (1, 2, 3, 5, 6, 12) else (number - 1) % 3 + 1
            roles = [((responsible - 1 + offset) % 3) + 1 for offset in range(3)]
            tables["matters"].append({
                "matter_id": matter_id, "firm_id": firm_id,
                "client_id": identifier(slug, "c", client),
                "practice_group_id": identifier(slug, "pg", PRACTICES.index(source["practice"]) + 1),
                "legal_entity_id": identifier(slug, "le", 2 if currency == "GBP" else 1),
                "matter_name": source["name"], "fee_arrangement": source.get("fee_arrangement", "hourly"),
                "currency": currency, "opened_on": history_start(config, as_of), "closed_on": None,
                "responsible_partner_id": identifier(slug, "t", roles[0]),
                "billing_partner_id": identifier(slug, "t", roles[1]),
                "originating_partner_id": identifier(slug, "t", roles[2]),
                "parent_matter_id": identifier(slug, "m", source["parent"]) if source.get("parent") else None,
                "case_code": case,
                "narrative": (f"Restricted synthetic narrative {sim['canary_token']}" if case == "screened"
                              else "Fictional legal engagement; no real client information."),
                "status": "open",
            })
            for tk in tables["timekeepers"]:
                if tk["firm_id"] != firm_id or tk["standard_rate_currency"] != currency:
                    continue
                rate_number += 1
                rate = (sim["quarter_hourly_rate_cents"] if case in
                        ("employment_overrun", "lease_overrun") else
                        percentage(tk["standard_rate_cents"], sim["negotiated_percent"]))
                tables["rates"].append({
                    "rate_id": identifier(slug, "r", rate_number), "firm_id": firm_id,
                    "matter_id": matter_id, "timekeeper_id": tk["timekeeper_id"],
                    "currency": currency, "hourly_rate_cents": rate,
                    "effective_from": history_start(config, as_of), "effective_to": as_of,
                })
        for number, (client, currency) in enumerate(sorted(guidelines), start=1):
            tables["billing_guidelines"].append({
                "billing_guideline_id": identifier(slug, "bg", number), "firm_id": firm_id,
                "client_id": identifier(slug, "c", client), "currency": currency,
                "payment_terms_days": 30, "fee_cap_cents": 100000000 if client == 1 else None,
                "guideline_text": "Invoice monthly; review draft fees; no automatic cap adjustment.",
                "rule_type": "payment_terms", "threshold": Decimal("30.00"),
                "effective_from": history_start(config, as_of), "effective_to": as_of,
            })


def generate_budgets(config: Config, tables: Tables, as_of: date) -> None:
    """Set full-current-quarter fee/hour budgets; actuals are quarter-to-snapshot.

    Fees mean recorded negotiated time value before any reductions, excluding
    costs. Ordinary baselines are constructed from synthetic actuals, not forecasts.
    """
    start, end = quarter_bounds(as_of)
    actuals: dict[str, tuple[int, Decimal]] = {}
    for entry in tables["time_entries"]:
        if start <= entry["work_date"] <= as_of:
            value, hours = actuals.get(entry["matter_id"], (0, Decimal("0.00")))
            actuals[entry["matter_id"]] = value + entry["value_cents"], hours + entry["hours"]
    sim = config["simulation"]
    for matter in tables["matters"]:
        actual, hours = actuals.get(matter["matter_id"], (0, Decimal("0.00")))
        if matter["case_code"] in ("employment_overrun", "lease_overrun"):
            budget = sim["quarter_budget_cents"]
            budget_hours = Decimal(str(sim["quarter_budget_hours"]))
        elif matter["case_code"] == "screened" and actual:
            budget = max(1, actual * 2 // 3)
            budget_hours = (hours * 2 / 3).quantize(Decimal("0.01"))
        else:
            budget = actual + max(100000, actual // 4)
            budget_hours = hours + max(Decimal("10.00"), hours / 4)
        tables["budgets"].append({
            "budget_id": matter["matter_id"] + "_budget", "firm_id": matter["firm_id"],
            "matter_id": matter["matter_id"], "period_start": start, "period_end_exclusive": end,
            "currency": matter["currency"], "fee_budget_cents": budget,
            "hours_budget": budget_hours.quantize(Decimal("0.01")),
            "basis": "negotiated_time_before_reductions_excluding_costs",
            "phase": "all",
        })