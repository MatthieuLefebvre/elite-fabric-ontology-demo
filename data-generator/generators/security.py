"""Pure-Python, fail-closed local policy helpers; these do not deploy authorization."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from .common import Config, Row, Tables, identifier


def generate_access(config: Config, tables: Tables) -> None:
    """Grant explicit matter access then screen the demo partner from Ashworth.

    The grant and deny deliberately coexist. Policy must not implement first-match
    wins, infer access from partner roles, or inherit a parent's grant to children.
    """
    for firm in config["firms"]:
        slug = firm["slug"]
        firm_id = identifier(slug, "f", 1)
        number = 0
        for matter in tables["matters"]:
            if matter["firm_id"] != firm_id:
                continue
            for tk in tables["timekeepers"]:
                if tk["firm_id"] != firm_id:
                    continue
                effects = ["grant"]
                if matter["case_code"] == "screened" and tk["timekeeper_id"] == identifier(slug, "t", 1):
                    effects.append("deny")
                for effect in effects:
                    number += 1
                    tables["matter_access"].append({
                        "matter_access_id": identifier(slug, "ma", number), "firm_id": firm_id,
                        "matter_id": matter["matter_id"], "timekeeper_id": tk["timekeeper_id"],
                        "effect": effect, "reason": "ethical_wall" if effect == "deny" else "explicit_grant",
                    })


def accessible_matter_ids(tables: Tables, firm_id: str, partner_id: str) -> frozenset[str]:
    """Return explicit grants minus denials, with identity and tenant validation."""
    if not any(t["timekeeper_id"] == partner_id and t["firm_id"] == firm_id
               for t in tables["timekeepers"]):
        return frozenset()
    known = {m["matter_id"] for m in tables["matters"] if m["firm_id"] == firm_id}
    granted: set[str] = set()
    denied: set[str] = set()
    for row in tables["matter_access"]:
        if row["firm_id"] != firm_id or row["timekeeper_id"] != partner_id:
            continue
        if row["effect"] == "grant":
            granted.add(row["matter_id"])
        else:
            # Unknown effects fail closed, rather than being treated as grants.
            denied.add(row["matter_id"])
    return frozenset((granted - denied) & known)


def authorized_rows(tables: Tables, table: str, firm_id: str, partner_id: str) -> list[Row]:
    """Filter a matter-grain table before projecting or aggregating any information.

    Non-matter tables must be joined through an authorized matter by their caller;
    rejecting them prevents accidentally returning tenant-wide narratives or clients.
    """
    if any("matter_id" not in row for row in tables[table]):
        raise ValueError("authorized_rows only accepts matter-grain tables")
    allowed = accessible_matter_ids(tables, firm_id, partner_id)
    return [r for r in tables[table] if r["firm_id"] == firm_id and r["matter_id"] in allowed]


def sum_by_currency(rows: Iterable[Row], amount_column: str) -> dict[str, int]:
    """Sum already-authorized integer cents, always partitioned by currency."""
    result: dict[str, int] = {}
    for row in rows:
        value = row[amount_column]
        if type(value) is not int:
            raise TypeError("financial aggregation requires integer cents")
        result[row["currency"]] = result.get(row["currency"], 0) + value
    return dict(sorted(result.items()))


def secure_sum(tables: Tables, table: str, amount_column: str,
               firm_id: str, partner_id: str) -> dict[str, int]:
    """Filter access first and then sum cents without ever combining currencies."""
    return sum_by_currency(authorized_rows(tables, table, firm_id, partner_id), amount_column)


def require_matter(tables: Tables, firm_id: str, partner_id: str, matter_id: str) -> Row:
    """Reject absent, foreign, and screened matters with the same non-revealing error."""
    if matter_id not in accessible_matter_ids(tables, firm_id, partner_id):
        raise PermissionError("Matter unavailable or access denied")
    return next(m for m in tables["matters"] if m["matter_id"] == matter_id and m["firm_id"] == firm_id)


def secure_billing_position(tables: Tables, firm_id: str, partner_id: str,
                            matter_id: str, as_of: date) -> Row:
    """Compute a single authorized matter's billing position at the dataset snapshot.

    This helper is not a historical ledger replay: status fields are a snapshot.
    Pass the as_of date used to generate the tables, not an earlier retrospective date.
    """
    matter = require_matter(tables, firm_id, partner_id, matter_id)

    def selected(table: str) -> list[Row]:
        """Select only the already-authorized single matter within its firm."""
        return [r for r in tables[table] if r["firm_id"] == firm_id and r.get("matter_id") == matter_id]

    entries = [e for e in selected("time_entries") if e["status"] == "unbilled"]
    costs = [d for d in selected("disbursements") if d["status"] == "unbilled"]
    drafts = [p for p in selected("proformas") if p["status"] == "draft"]
    invoices = selected("invoices")
    net = sum(i["net_cents"] for i in invoices)
    collected = sum(p["amount_cents"] for p in selected("payments"))
    writeoffs = sum(a["amount_cents"] for a in selected("adjustments") if a["stage"] == "invoice")
    return {
        "matter_id": matter_id, "as_of": str(as_of), "currency": matter["currency"],
        "fee_arrangement": matter["fee_arrangement"],
        "unbilled_time_cents": sum(e["value_cents"] for e in entries),
        "unbilled_disbursement_cents": sum(d["amount_cents"] for d in costs),
        "draft_net_cents": sum(p["net_cents"] for p in drafts),
        "issued_net_cents": net, "collected_cents": collected,
        "invoice_writeoff_cents": writeoffs, "outstanding_cents": net - writeoffs - collected,
        "source_ids": {"matter": matter_id, "invoices": sorted(i["invoice_id"] for i in invoices),
                       "draft_proformas": sorted(p["proforma_id"] for p in drafts),
                       "unbilled_time_entries": sorted(e["time_entry_id"] for e in entries)},
    }