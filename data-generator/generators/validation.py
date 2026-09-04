"""Executable relational, currency, date, and accounting invariants."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .common import (
    FOREIGN_KEYS,
    PRIMARY_KEYS,
    SPECS,
    Dataset,
    Row,
    cents,
    fields,
    history_start,
    quarter_bounds,
)


def validate_dataset(dataset: Dataset) -> None:
    """Raise ValueError on invalid schemas, references, chronology, or accounting.

    Checks use explicit exceptions (not optimizable assertions) and integer/decimal
    arithmetic. They validate the generated snapshot, not arbitrary historical replay.
    """
    tables, as_of = dataset.tables, dataset.as_of

    def require(condition: bool, message: str) -> None:
        """Enforce an invariant even when Python is invoked with optimization."""
        if not condition:
            raise ValueError(message)

    require(set(tables) == set(SPECS), "Table set differs from schema")
    indices: dict[str, dict[str, Row]] = {}
    global_ids: set[str] = set()
    for table, rows in tables.items():
        pk = PRIMARY_KEYS[table]
        indices[table] = {}
        for row in rows:
            require(set(row) == {name for name, _, _ in fields(table)}, f"Columns differ: {table}")
            for name, kind, nullable in fields(table):
                value = row[name]
                if value is None:
                    require(nullable, f"Unexpected null: {table}.{name}")
                    continue
                valid = {"S": isinstance(value, str), "I": type(value) is int,
                         "D": type(value) is date, "H": isinstance(value, Decimal),
                         "M": isinstance(value, Decimal),
                         "B": type(value) is bool}[kind]
                require(valid, f"Invalid scalar type: {table}.{name}")
                if kind == "I":
                    require(-(2 ** 63) <= value < 2 ** 63, "int64 overflow")
                if kind in ("H", "M"):
                    limit = Decimal("100000000" if kind == "H" else "10000000000000000")
                    require(value.is_finite() and value == value.quantize(Decimal("0.01"))
                            and abs(value) < limit, f"decimal({'10' if kind == 'H' else '18'},2) overflow")
                if name.endswith("_cents"):
                    require(value >= 0, f"Negative financial amount: {table}.{name}")
            require(row[pk] not in global_ids, f"Duplicate global ID: {row[pk]}")
            global_ids.add(row[pk])
            indices[table][row[pk]] = row
    for table, rows in tables.items():
        for row in rows:
            firm = indices["firms"].get(row["firm_id"])
            require(firm is not None, "Unknown firm")
            assert firm is not None
            require(row[PRIMARY_KEYS[table]].startswith(firm["slug"] + "_"), "Unprefixed ID")
            for column, target_table in FOREIGN_KEYS.items():
                target_id = row.get(column)
                if column == PRIMARY_KEYS[table] or target_id is None:
                    continue
                target = indices[target_table].get(target_id)
                require(target is not None, f"Missing FK: {table}.{column}")
                assert target is not None
                require(target["firm_id"] == row["firm_id"], "Cross-firm FK")
                if "matter_id" in row and "matter_id" in target and column != "parent_matter_id":
                    require(target["matter_id"] == row["matter_id"], "Cross-matter financial FK")
            if "currency" in row and "matter_id" in row:
                require(row["currency"] == indices["matters"][row["matter_id"]]["currency"], "Mixed matter currency")
    for matter in tables["matters"]:
        roles = [matter[key] for key in ("responsible_partner_id", "billing_partner_id", "originating_partner_id")]
        require(len(set(roles)) == 3, "Partner roles must be distinct")
        require(all(indices["timekeepers"][role]["role"] == "partner" for role in roles), "Role is not a partner")
        require(indices["legal_entities"][matter["legal_entity_id"]]["currency"] == matter["currency"], "Wrong entity currency")
        seen = {matter["matter_id"]}
        parent_id = matter["parent_matter_id"]
        while parent_id is not None:
            require(parent_id not in seen, "Parent matter cycle")
            seen.add(parent_id)
            parent = indices["matters"][parent_id]
            require(parent["client_id"] == matter["client_id"], "Parent/child client mismatch")
            require(parent["currency"] == matter["currency"], "Parent/child currency mismatch")
            parent_id = parent["parent_matter_id"]
    for table, column in (("clients", "relationship_partner_id"),
                          ("practice_groups", "group_head_id"),
                          ("proformas", "reviewer_id"), ("adjustments", "approver_id")):
        for row in tables[table]:
            require(indices["timekeepers"][row[column]]["role"] == "partner", f"Not a partner: {column}")
            if table == "practice_groups":
                require(indices["timekeepers"][row[column]]["practice_group_id"] == row["practice_group_id"],
                        "Group head practice mismatch")
            if "matter_id" in row:
                matter = indices["matters"][row["matter_id"]]
                require(row[column] == matter["billing_partner_id"], f"Wrong billing reviewer: {column}")
    for tk in tables["timekeepers"]:
        require(tk["office"] == indices["offices"][tk["office_id"]]["office_name"], "Office mismatch")
        require(tk["department"] == indices["practice_groups"][tk["practice_group_id"]]["practice_group_name"], "Department mismatch")
    start = history_start(dataset.config, as_of)
    for entry in tables["time_entries"]:
        tk = indices["timekeepers"][entry["timekeeper_id"]]
        rate = indices["rates"][entry["rate_id"]]
        require(entry["hours"] > 0, "Time must be positive")
        require(start <= entry["work_date"] <= as_of, "Service outside history window")
        require(rate["effective_from"] <= entry["work_date"] <= rate["effective_to"], "Rate outside validity")
        require(rate["timekeeper_id"] == entry["timekeeper_id"], "Rate/timekeeper mismatch")
        require(entry["rate_cents"] == rate["hourly_rate_cents"], "Entry rate mismatch")
        require(entry["billable_flag"] and entry["phase"] == "all", "Unsupported time basis/phase")
        require(tk["standard_rate_currency"] == rate["currency"] == entry["currency"], "Standard/rate currency mismatch")
        require(entry["standard_value_cents"] == cents(entry["hours"], tk["standard_rate_cents"]), "Standard math mismatch")
        require(entry["value_cents"] == cents(entry["hours"], rate["hourly_rate_cents"]), "Negotiated math mismatch")
    stages = {"time_entry": "time_entries", "proforma": "proformas", "invoice": "invoices"}
    adjustments: dict[tuple[str, str], int] = {}
    provenance: set[str] = set()
    for adj in tables["adjustments"]:
        require(adj["stage"] in stages, "Unknown adjustment stage")
        stage = adj["stage"]
        require([s for s in stages if adj[s + "_id"] is not None] == [stage], "Adjustment target must match stage exclusively")
        target_id = adj[stage + "_id"]
        require((stage, target_id) not in adjustments, "Duplicate adjustment stage/target")
        require(adj["provenance_key"] not in provenance, "Duplicate adjustment provenance")
        provenance.add(adj["provenance_key"])
        adjustments[stage, target_id] = adj["amount_cents"]
        target = indices[stages[stage]][target_id]
        date_key = {"time_entry": "work_date", "proforma": "prepared_on", "invoice": "issued_on"}[stage]
        require(target[date_key] <= adj["adjusted_on"] <= as_of, "Adjustment date invalid")
        require(adj["amount_cents"] > 0, "Reductions must be signed-positive amounts")
    time_by_pf: dict[str, list[Row]] = {}
    cost_by_pf: dict[str, list[Row]] = {}
    for table, groups, service_key in (("time_entries", time_by_pf, "work_date"),
                                        ("disbursements", cost_by_pf, "incurred_on")):
        for row in tables[table]:
            require(start <= row[service_key] <= as_of, "Service/cost date outside snapshot")
            status = row["status"]
            require(status in ("unbilled", "proforma", "billed"), "Unknown activity status")
            if status == "unbilled":
                require(row["billing_date"] is row["proforma_id"] is row["invoice_id"] is None, "Unbilled row linked to document")
                if table == "time_entries":
                    require(("time_entry", row["time_entry_id"]) not in adjustments, "Unbilled reduction not supported")
                continue
            require(row["proforma_id"] is not None, "Missing activity proforma")
            pf = indices["proformas"][row["proforma_id"]]
            require(row[service_key] <= pf["prepared_on"] <= as_of, "Draft predates work or exceeds snapshot")
            groups.setdefault(pf["proforma_id"], []).append(row)
            require(row["invoice_id"] == pf["invoice_id"], "Activity/draft invoice mismatch")
            if status == "billed":
                require(row["invoice_id"] is not None, "Billed activity has no invoice")
                require(row["billing_date"] == indices["invoices"][row["invoice_id"]]["issued_on"], "Activity issue date mismatch")
            else:
                require(row["invoice_id"] is None and row["billing_date"] == pf["prepared_on"], "Draft activity has invoice/date mismatch")
    amount_fields = ("standard_time_cents", "gross_time_cents", "time_entry_reduction_cents",
                     "disbursement_cents", "gross_cents", "discount_cents", "net_cents", "net_time_cents")
    for pf in tables["proformas"]:
        entries, costs = time_by_pf.get(pf["proforma_id"], []), cost_by_pf.get(pf["proforma_id"], [])
        require(bool(entries or costs), "Empty proforma")
        gross_time = sum(e["value_cents"] for e in entries)
        reductions = sum(adjustments.get(("time_entry", e["time_entry_id"]), 0) for e in entries)
        cost = sum(d["amount_cents"] for d in costs)
        discount = adjustments.get(("proforma", pf["proforma_id"]), 0)
        require(reductions + discount <= gross_time, "Fee reductions exceed time value")
        require(pf["hours"] == sum((e["hours"] for e in entries), Decimal("0.00")), "Proforma hours mismatch")
        require(pf["time_entry_count"] == len(entries) and pf["disbursement_count"] == len(costs), "Document line counts mismatch")
        expected = (sum(e["standard_value_cents"] for e in entries), gross_time, reductions, cost,
                    gross_time - reductions + cost, discount, gross_time - reductions + cost - discount,
                    gross_time - reductions - discount)
        require(tuple(pf[f] for f in amount_fields) == expected, "Proforma accounting does not reconcile")
        require(pf["status"] == ("converted" if pf["invoice_id"] else "draft"), "Draft state mismatch")
    payments: dict[str, int] = {}
    for payment in tables["payments"]:
        invoice = indices["invoices"][payment["invoice_id"]]
        require(payment["amount_cents"] > 0, "Payment/refund must not be zero or negative")
        require(invoice["issued_on"] <= payment["paid_on"] <= as_of, "Payment date invalid")
        payments[payment["invoice_id"]] = payments.get(payment["invoice_id"], 0) + payment["amount_cents"]
    for invoice in tables["invoices"]:
        pf = indices["proformas"][invoice["proforma_id"]]
        require(pf["invoice_id"] == invoice["invoice_id"], "Draft/invoice not one-to-one")
        require(all(invoice[f] == pf[f] for f in amount_fields), "Invoice changed issued draft values")
        require(all(invoice[f] == pf[f] for f in ("hours", "time_entry_count", "disbursement_count")), "Invoice quantities mismatch")
        require(pf["prepared_on"] <= invoice["issued_on"] <= as_of, "Invoice outside snapshot")
        require(invoice["due_on"] >= invoice["issued_on"], "Due date before issue")
        writeoff = adjustments.get(("invoice", invoice["invoice_id"]), 0)
        require(writeoff <= invoice["net_time_cents"], "Writeoff exceeds net fees")
        require(payments.get(invoice["invoice_id"], 0) <= invoice["net_cents"] - writeoff, "Overpayment of collectible amount")
    seen_guidelines: set[str] = set()
    for guideline in tables["billing_guidelines"]:
        require(guideline["effective_from"] <= as_of <= guideline["effective_to"], "Guideline outside snapshot")
        require(guideline["rule_type"] in ("payment_terms", "fee_cap"), "Unknown guideline rule")
        expected_threshold = (Decimal(guideline["payment_terms_days"]) if guideline["rule_type"] == "payment_terms"
                              else Decimal(guideline["fee_cap_cents"]) / 100 if guideline["fee_cap_cents"] is not None else None)
        require(guideline["threshold"] == expected_threshold, "Guideline threshold/unit mismatch")
    for link in tables["invoice_guidelines"]:
        invoice = indices["invoices"][link["invoice_id"]]
        guideline = indices["billing_guidelines"][link["billing_guideline_id"]]
        matter = indices["matters"][invoice["matter_id"]]
        require(link["invoice_id"] not in seen_guidelines, "Duplicate invoice guideline")
        seen_guidelines.add(link["invoice_id"])
        require(guideline["client_id"] == matter["client_id"] and guideline["currency"] == invoice["currency"], "Wrong invoice guideline")
        require(guideline["effective_from"] <= invoice["issued_on"] <= guideline["effective_to"], "Invoice guideline date mismatch")
    require(seen_guidelines == set(indices["invoices"]), "Missing invoice guidelines")
    qstart, qend = quarter_bounds(as_of)
    for budget in tables["budgets"]:
        require(budget["phase"] == "all", "Budget phase mismatch")
        require((budget["period_start"], budget["period_end_exclusive"]) == (qstart, qend), "Budget quarter mismatch")
        require(budget["fee_budget_cents"] > 0 and budget["hours_budget"] > 0, "Nonpositive budget")
    require({b["matter_id"] for b in tables["budgets"]} == set(indices["matters"]), "Budget coverage mismatch")
    require(all(r["effect"] in ("grant", "deny") for r in tables["matter_access"]), "Unknown access effect")
    for firm in dataset.config["firms"]:
        firm_id = f"{firm['slug']}_f001"
        for table, key in (("clients", "clients"), ("matters", "matters"),
                           ("timekeepers", "timekeepers"), ("time_entries", "time_entries")):
            require(sum(r["firm_id"] == firm_id for r in tables[table]) == firm[key], f"Volume mismatch: {table}")