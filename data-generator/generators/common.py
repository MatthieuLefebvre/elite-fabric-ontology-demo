"""Shared contracts, exact arithmetic, calendar boundaries, and configuration.

Money is signed int64 cents in the row's explicit currency. Reductions themselves
are positive amounts, never negative credits. Hours are decimal(10, 2); monetary
rounding is half-up to a cent. No currency conversion is implied by a firm total.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

Row = dict[str, Any]
Tables = dict[str, list[Row]]
Config = dict[str, Any]
DEFAULT_AS_OF = date(2026, 9, 4)
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config"
PRACTICES = ("corporate", "litigation", "ip", "employment", "real_estate")

# S = string, I = int64, D = date32, H = decimal(10,2), M = decimal(18,2), B = boolean.
# A trailing ? is nullable. Field order is part of the serialization contract.
SPECS: dict[str, str] = {
    "firms": "firm_id:S slug:S firm_name:S reporting_currency:S",
    "offices": "office_id:S firm_id:S office_name:S country_code:S",
    "practice_groups": "practice_group_id:S firm_id:S practice_group_name:S group_head_id:S",
    "legal_entities": "legal_entity_id:S firm_id:S office_id:S legal_entity_name:S currency:S jurisdiction:S",
    "timekeepers": (
        "timekeeper_id:S firm_id:S office_id:S legal_entity_id:S practice_group_id:S "
        "full_name:S role:S standard_rate_cents:I standard_rate_currency:S active:B "
        "grade:S office:S department:S"
    ),
    "clients": "client_id:S firm_id:S client_name:S industry:S relationship_partner_id:S credit_status:S",
    "matters": (
        "matter_id:S firm_id:S client_id:S practice_group_id:S legal_entity_id:S "
        "matter_name:S fee_arrangement:S currency:S opened_on:D closed_on:D? "
        "responsible_partner_id:S billing_partner_id:S originating_partner_id:S "
        "parent_matter_id:S? case_code:S narrative:S status:S"
    ),
    "rates": (
        "rate_id:S firm_id:S matter_id:S timekeeper_id:S currency:S hourly_rate_cents:I "
        "effective_from:D effective_to:D"
    ),
    "time_entries": (
        "time_entry_id:S firm_id:S matter_id:S timekeeper_id:S rate_id:S work_date:D "
        "hours:H standard_value_cents:I value_cents:I currency:S status:S "
        "billing_date:D? proforma_id:S? invoice_id:S? narrative:S rate_cents:I billable_flag:B phase:S"
    ),
    "disbursements": (
        "disbursement_id:S firm_id:S matter_id:S incurred_on:D amount_cents:I currency:S "
        "status:S billing_date:D? proforma_id:S? invoice_id:S? description:S type:S vendor:S recoverable_flag:B"
    ),
    "proformas": (
        "proforma_id:S firm_id:S matter_id:S prepared_on:D status:S currency:S "
        "hours:H time_entry_count:I disbursement_count:I "
        "standard_time_cents:I gross_time_cents:I time_entry_reduction_cents:I "
        "disbursement_cents:I gross_cents:I discount_cents:I net_cents:I "
        "net_time_cents:I invoice_id:S? reviewer_id:S"
    ),
    "invoices": (
        "invoice_id:S firm_id:S matter_id:S proforma_id:S issued_on:D due_on:D "
        "status:S currency:S hours:H time_entry_count:I disbursement_count:I "
        "standard_time_cents:I gross_time_cents:I "
        "time_entry_reduction_cents:I disbursement_cents:I gross_cents:I "
        "discount_cents:I net_cents:I net_time_cents:I ebilling_status:S"
    ),
    "payments": (
        "payment_id:S firm_id:S matter_id:S invoice_id:S paid_on:D amount_cents:I "
        "currency:S payment_method:S"
    ),
    "adjustments": (
        "adjustment_id:S firm_id:S matter_id:S stage:S time_entry_id:S? proforma_id:S? "
        "invoice_id:S? adjusted_on:D amount_cents:I currency:S reason:S provenance_key:S reason_code:S approver_id:S"
    ),
    "budgets": (
        "budget_id:S firm_id:S matter_id:S period_start:D period_end_exclusive:D "
        "currency:S fee_budget_cents:I hours_budget:H basis:S phase:S"
    ),
    "billing_guidelines": (
        "billing_guideline_id:S firm_id:S client_id:S currency:S payment_terms_days:I "
        "fee_cap_cents:I? guideline_text:S rule_type:S threshold:M effective_from:D effective_to:D"
    ),
    "invoice_guidelines": (
        "invoice_guideline_id:S firm_id:S invoice_id:S billing_guideline_id:S compliant:B"
    ),
    "matter_access": (
        "matter_access_id:S firm_id:S matter_id:S timekeeper_id:S effect:S reason:S"
    ),
}
PRIMARY_KEYS = {table: spec.split()[0].split(":")[0] for table, spec in SPECS.items()}
FOREIGN_KEYS: dict[str, str] = {
    "firm_id": "firms", "office_id": "offices", "practice_group_id": "practice_groups",
    "legal_entity_id": "legal_entities", "timekeeper_id": "timekeepers",
    "client_id": "clients", "matter_id": "matters", "parent_matter_id": "matters",
    "responsible_partner_id": "timekeepers", "billing_partner_id": "timekeepers",
    "originating_partner_id": "timekeepers", "rate_id": "rates",
    "time_entry_id": "time_entries", "proforma_id": "proformas", "invoice_id": "invoices",
    "billing_guideline_id": "billing_guidelines",
    "relationship_partner_id": "timekeepers", "group_head_id": "timekeepers",
    "reviewer_id": "timekeepers", "approver_id": "timekeepers",
}


@dataclass
class Dataset:
    """In-memory data and the inputs needed to reproduce it; generation performs no I/O."""

    tables: Tables
    seed: int
    as_of: date
    config: Config
    expected_answers: Row


def fields(table: str) -> list[tuple[str, str, bool]]:
    """Return ordered (name, logical type, nullable) column definitions."""
    return [(name, kind.rstrip("?"), kind.endswith("?"))
            for name, kind in (token.split(":") for token in SPECS[table].split())]


def identifier(slug: str, kind: str, number: int) -> str:
    """Build globally firm-prefixed IDs; matters are e.g. harbor_m001."""
    return f"{slug}_{kind}{number:03d}"


def cents(hours: Decimal, rate_cents: int) -> int:
    """Multiply decimal hours by a cents/hour rate, half-up to one cent."""
    return int((hours * rate_cents).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def percentage(amount: int, percent: int) -> int:
    """Calculate an integer percentage of cents with half-up rounding."""
    return int((Decimal(amount) * percent / 100).quantize(Decimal("1"), ROUND_HALF_UP))


def ratio(numerator: int | Decimal, denominator: int | Decimal) -> str | None:
    """Return an exact-input ratio as a six-place decimal string, or null for zero."""
    if not denominator:
        return None
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.000001")))


def shift_month(day: date, months: int) -> date:
    """Shift by calendar months, clamping month-end dates (including leap years)."""
    index = day.year * 12 + day.month - 1 + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def quarter_bounds(day: date) -> tuple[date, date]:
    """Return inclusive quarter start and exclusive next-quarter start."""
    start = date(day.year, ((day.month - 1) // 3) * 3 + 1, 1)
    return start, shift_month(start, 3)


def month_bounds(day: date, offset: int = 0) -> tuple[date, date]:
    """Return a half-open calendar month offset from the snapshot's month."""
    start = shift_month(day.replace(day=1), offset)
    return start, shift_month(start, 1)


def json_default(value: Any) -> str:
    """Encode dates and decimals losslessly; reject other accidental object types."""
    if isinstance(value, (date, Decimal)):
        return str(value)
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Stable, UTF-8-friendly JSON with no timestamps or floating-point serialization."""
    return json.dumps(value, default=json_default, sort_keys=True, indent=2,
                      ensure_ascii=False, allow_nan=False) + "\n"


def load_config(directory: str | Path = DEFAULT_CONFIG) -> Config:
    """Load the three YAML documents and validate before allocating any data."""
    root = Path(directory)
    documents: dict[str, Any] = {}
    for name in ("firms", "clients_matters", "simulation"):
        try:
            with (root / f"{name}.yaml").open(encoding="utf-8") as stream:
                document = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            raise ValueError(f"{name}.yaml contains invalid YAML") from exc
        if not isinstance(document, dict):
            raise ValueError(f"{name}.yaml must contain a mapping")
        documents[name] = document
    config = {
        "firms": documents["firms"].get("firms"),
        "clients": documents["clients_matters"].get("clients"),
        "matters": documents["clients_matters"].get("matters"),
        "simulation": documents["simulation"],
    }
    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    """Reject malformed, insufficient, or internally contradictory scenario inputs.

    Scenario constants are configurable, but the exact 35%/8%/95-day claims are
    defaults, not promises for arbitrary changed budgets or percentage settings.
    """
    for key in ("firms", "clients", "matters"):
        if not isinstance(config.get(key), list) or not config[key]:
            raise ValueError(f"{key} must be a nonempty list")
    sim = config.get("simulation")
    if not isinstance(sim, dict):
        raise ValueError("simulation must be a mapping")
    if type(sim.get("seed")) is not int:
        raise ValueError("seed must be an integer")
    try:
        snapshot = date.fromisoformat(str(sim["as_of"]))
        if not 1902 <= snapshot.year <= 9997:
            raise ValueError("as_of year must be between 1902 and 9997")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("as_of must be an ISO date in years 1902..9997") from exc
    positive = (
        "history_months", "usd_standard_rate_cents", "gbp_standard_rate_cents",
        "quarter_budget_cents", "quarter_hourly_rate_cents", "employment_entries",
        "lease_entries",
    )
    for key in positive:
        if type(sim.get(key)) is not int or sim[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if sim["history_months"] < 6:
        raise ValueError("history_months must be at least 6 for the aged scenarios")
    if snapshot.year * 12 + snapshot.month - sim["history_months"] < 13:
        raise ValueError("history_months precedes the supported calendar")
    for key in ("negotiated_percent", "time_entry_reduction_percent",
                "ordinary_proforma_percent", "ordinary_invoice_percent"):
        maximum = 100 if key == "negotiated_percent" else 50
        if type(sim.get(key)) is not int or not 1 <= sim[key] <= maximum:
            raise ValueError(f"{key} must be an integer in 1..{maximum}")
    for key in ("quarter_budget_hours", "employment_hours", "lease_hours"):
        try:
            hours = Decimal(str(sim[key]))
            if not hours.is_finite() or hours <= 0 or hours.as_tuple().exponent < -2:
                raise ValueError(key)
        except (KeyError, ArithmeticError, ValueError) as exc:
            raise ValueError(f"{key} must be positive decimal hours with at most 2 places") from exc
    if not isinstance(sim.get("canary_token"), str) or not sim["canary_token"].startswith(
        "SYNTHETIC_SCREENED_"
    ):
        raise ValueError("canary_token must be a reserved SYNTHETIC_SCREENED_ token")
    clients = config["clients"]
    if any(not isinstance(name, str) or not name.strip() for name in clients):
        raise ValueError("client names must be nonempty strings")
    if len(set(clients)) != len(clients):
        raise ValueError("client names must be unique")
    matters = config["matters"]
    if any(not isinstance(item, dict) for item in matters):
        raise ValueError("each seeded matter must be a mapping")
    if [m.get("number") for m in matters] != list(range(1, len(matters) + 1)):
        raise ValueError("seeded matter numbers must be consecutive starting at 1")
    required_cases = {1: "aged_wip", 3: "screened", 5: "employment_overrun",
                      6: "lease_overrun", 8: "proforma_leakage", 9: "invoice_leakage",
                      10: "dso_95"}
    for number, case in required_cases.items():
        if len(matters) < number or matters[number - 1].get("case") != case:
            raise ValueError(f"matter {number} must retain case {case}")
    for matter in matters:
        number = matter["number"]
        if not isinstance(matter.get("name"), str) or not matter["name"].strip():
            raise ValueError("matter names must be nonempty")
        if matter.get("practice") not in PRACTICES:
            raise ValueError("unknown practice group")
        client = matter.get("client")
        if type(client) is not int or not 1 <= client <= len(clients):
            raise ValueError("seeded matter client is out of range")
        if matter.get("currency", "USD") not in ("USD", "GBP"):
            raise ValueError("only USD and GBP currency bases are supported")
        parent = matter.get("parent")
        if parent is not None:
            if type(parent) is not int or not 1 <= parent < number:
                raise ValueError("parents must precede children, preventing cycles")
            if matters[parent - 1]["client"] != client:
                raise ValueError("parent and child must belong to the same client")
    for number in (1, 3, 5, 6, 8, 9, 10):
        if matters[number - 1].get("currency", "USD") != "USD":
            raise ValueError("planted financial cases must be USD")
    slugs: set[str] = set()
    for firm in config["firms"]:
        if not isinstance(firm, dict):
            raise ValueError("each firm must be a mapping")
        slug = firm.get("slug")
        if not isinstance(slug, str) or not slug.isascii() or not slug.isalpha():
            raise ValueError("firm slug must contain only ASCII letters")
        if slug != slug.lower() or slug in slugs:
            raise ValueError("firm slugs must be lowercase and unique")
        slugs.add(slug)
        for key in ("name", "partner_name"):
            if not isinstance(firm.get(key), str) or not firm[key].strip():
                raise ValueError(f"firm {key} must be nonempty")
        for key, minimum in (("client_names", firm.get("clients", 0)),
                             ("matter_names", len(matters)), ("given_names", 1),
                             ("surnames", 1)):
            if key in firm:
                names = firm[key]
                if (type(minimum) is not int or not isinstance(names, list) or len(names) < minimum
                        or any(not isinstance(n, str) or not n.strip() for n in names)
                        or len(set(names)) != len(names)):
                    raise ValueError(f"firm {key} must contain sufficient unique nonempty names")
        if "cash_application_days" in firm:
            days = firm["cash_application_days"]
            if type(days) is not int or not 6 <= days <= 110:
                raise ValueError("cash_application_days must be an integer in 6..110")
        minimums = {"clients": max(m["client"] for m in matters),
                    "matters": len(matters), "timekeepers": 6, "aged_wip_entries": 61,
                    "vantage_entries_per_month": 1, "redgrave_entries_per_month": 1,
                    "time_entries": 1}
        for key, minimum in minimums.items():
            if type(firm.get(key)) is not int or firm[key] < minimum:
                raise ValueError(f"firm {key} must be an integer >= {minimum}")
        if firm["clients"] > len(clients) or firm["matters"] < firm["clients"]:
            raise ValueError("insufficient authored clients or matters")
        for key in ("vantage_proforma_percent", "redgrave_invoice_percent"):
            if type(firm.get(key)) is not int or not 1 <= firm[key] <= 75:
                raise ValueError(f"{key} must be an integer in 1..75")
        planted = (firm["aged_wip_entries"] + sim["employment_entries"]
                   + sim["lease_entries"] + 2 * firm["vantage_entries_per_month"]
                   + 2 * firm["redgrave_entries_per_month"] + 105 + 2)
        if firm["time_entries"] < planted + firm["matters"]:
            raise ValueError("time_entries cannot accommodate planted cases and coverage")


def history_start(config: Config, as_of: date) -> date:
    """The inclusive start of the configurable calendar-month service window."""
    return shift_month(as_of, -config["simulation"]["history_months"])


def inclusive_days(start: date, end: date) -> int:
    """Number of inclusive calendar days, rejecting reversed intervals."""
    if end < start:
        raise ValueError("end must not precede start")
    return (end - start + timedelta(days=1)).days