"""Firm-wide synthetic-demo semantic models over the deployed Gold tables."""

from __future__ import annotations

import base64
import json
from datetime import date
from uuid import NAMESPACE_URL, UUID, uuid5


def definition_parts(files: dict[str, dict], *, sort_keys: bool = True) -> dict:
    return {"parts": [
        {"path": path, "payloadType": "InlineBase64",
         "payload": base64.b64encode(json.dumps(value, sort_keys=sort_keys).encode()).decode()}
        for path, value in files.items()
    ]}


def build_semantic_model(
    contract: dict, schema: dict, *, sql_server: str, sql_database: str,
    firm: str, as_of: str,
) -> dict:
    """Build TMSL Direct Lake on SQL; no partner-level authorization is implied."""
    if firm not in {"harbor", "kestrel"}:
        raise ValueError("Unknown firm")
    snapshot = date.fromisoformat(as_of)
    database = str(UUID(sql_database))
    if not sql_server or any(char in sql_server for char in '\"\r\n/\\'):
        raise ValueError("Invalid SQL endpoint host")
    cutoff = f"DATE({snapshot.year},{snapshot.month},{snapshot.day})"
    types = {"string": "string", "long": "int64", "integer": "int64",
             "boolean": "boolean", "date": "dateTime", "timestamp": "dateTime",
             "double": "double"}
    tables = []
    entities = [entity for entity in contract["entities"] if entity["name"] != "MatterAccess"]
    for entity in entities:
        fields = {field["name"]: field["type"]
                  for field in schema["tables"][entity["table"]]["spark_schema"]["fields"]}
        fields.update({prop["column"]: prop["type"] for prop in entity["properties"].values()})
        columns = []
        for column, source_type in sorted(fields.items()):
            data_type = "decimal" if source_type.startswith("decimal") else types[source_type]
            columns.append({
                "name": column, "sourceColumn": column, "dataType": data_type,
                "summarizeBy": "none", "isHidden": column.endswith(("_id", "_cents")),
                "isKey": column == entity["key"],
            })
        tables.append({
            "name": entity["table"], "description": entity["description"],
            "columns": columns, "partitions": [{
                "name": entity["table"], "mode": "directLake",
                "source": {"type": "entity", "entityName": entity["table"],
                           "schemaName": "dbo", "expressionSource": "Gold"},
            }],
        })
    by_name = {table["name"]: table for table in tables}
    relationships = []
    links = [("matters", "clients", "client_id"),
             ("matters", "practice_groups", "practice_group_id"),
             ("matters", "legal_entities", "legal_entity_id"),
             ("time_entries", "timekeepers", "timekeeper_id")]
    links.extend((table, "matters", "matter_id") for table in (
        "time_entries", "disbursements", "proformas", "invoices", "payments", "adjustments", "budgets"))
    for source, target, column in links:
        if source not in by_name or target not in by_name:
            continue
        relationships.append({
            "name": str(uuid5(NAMESPACE_URL, f"elite/{firm}/{source}/{target}/{column}")),
            "fromTable": source, "fromColumn": column, "fromCardinality": "many",
            "toTable": target, "toColumn": column, "toCardinality": "one",
            "crossFilteringBehavior": "oneDirection", "isActive": True,
        })
    issued = f"invoices[status] = \"issued\", invoices[issued_on] <= {cutoff}"
    measures = []

    def measure(name: str, expression: str, description: str, *, ratio: bool = False) -> None:
        measures.append({
            "name": name,
            "expression": f'IF(HASONEVALUE(matters[currency]) && NOT ISBLANK(SELECTEDVALUE(matters[currency])), {expression}, BLANK())',
            "description": description + " Returns blank for mixed currencies. Synthetic firm-wide snapshot.",
            "formatString": "0.0%" if ratio else "#,##0.00;(#,##0.00);-",
            "displayFolder": "Financial overview",
        })

    measure("Issued Fees", f"DIVIDE(CALCULATE(SUM(invoices[net_time_cents]), {issued}), 100)",
            "Issued net time fees, excluding costs and unaffected by later invoice write-offs.")
    measure("Issued Bills", f"DIVIDE(CALCULATE(SUM(invoices[net_cents]), {issued}), 100)",
            "Issued net bills including recoverable costs, through the dataset snapshot.")
    measure("Issued Realization",
            f"DIVIDE(CALCULATE(SUM(invoices[net_time_cents]), {issued}), "
            f"CALCULATE(SUM(invoices[standard_time_cents]), {issued}))",
            "Ratio of issued net time fees to standard time value for the same issued cohort.", ratio=True)
    invoice_ids = f"CALCULATETABLE(VALUES(invoices[invoice_id]), {issued})"
    all_issued_ids = (f"SELECTCOLUMNS(FILTER(ALL(invoices), invoices[status] = \"issued\" "
                  f"&& invoices[issued_on] <= {cutoff}), \"id\", invoices[invoice_id])")
    measure("Cash Collected",
            f"VAR IssuedIds = {invoice_ids} RETURN DIVIDE(CALCULATE(SUM(payments[amount_cents]), "
            f"payments[paid_on] <= {cutoff}, TREATAS(IssuedIds, payments[invoice_id])), 100)",
            "Cash applied through the snapshot to the current issued-invoice cohort, including partial payments.")
    measure("Invoice Write-offs",
            f"VAR IssuedIds = {invoice_ids} RETURN DIVIDE(CALCULATE(SUM(adjustments[amount_cents]), "
            f'adjustments[stage] = "invoice", adjustments[adjusted_on] <= {cutoff}, '
            "TREATAS(IssuedIds, adjustments[invoice_id])), 100)",
            "Post-issue fee write-offs on the selected invoice cohort; reduce receivables, not issued realization.")
    measure("Outstanding Receivables",
            "[Issued Bills] - COALESCE([Cash Collected], 0) - COALESCE([Invoice Write-offs], 0)",
            "Issued bills less matched cash and invoice-stage write-offs at the snapshot.")
    measure("Collection Rate", "DIVIDE([Cash Collected], [Issued Bills])",
            "Matched cash divided by issued net bills for the same issued cohort and currency.", ratio=True)
    wip_filter = (f"time_entries[billable_flag] = TRUE() && time_entries[work_date] <= {cutoff} "
              "&& NOT(time_entries[invoice_id] IN IssuedIds)")
    time_wip = f"SUMX(FILTER(time_entries, {wip_filter}), time_entries[value_cents])"
    costs_wip = (f"SUMX(FILTER(disbursements, disbursements[recoverable_flag] = TRUE() "
             f"&& disbursements[incurred_on] <= {cutoff} "
             "&& NOT(disbursements[invoice_id] IN IssuedIds)), disbursements[amount_cents])")
    measure("Gross Unbilled WIP",
            f"VAR IssuedIds = {all_issued_ids} RETURN DIVIDE({time_wip} + {costs_wip}, 100)",
            "Unissued billable time at recorded negotiated value plus recoverable costs, including draft allocations.")
    measure("Unbilled Time", f"VAR IssuedIds = {all_issued_ids} RETURN DIVIDE({time_wip}, 100)",
            "Billable time not yet on an issued invoice, including work allocated to unconverted drafts.")
    for label, minimum, maximum in (("0-29", 0, 29), ("30-59", 30, 59), ("60-89", 60, 89),
                        ("90-120", 90, 120), ("121+", 121, None)):
        age = f"DATEDIFF(time_entries[work_date], {cutoff}, DAY)"
        band = f"{age} >= {minimum}" + (f" && {age} <= {maximum}" if maximum is not None else "")
        measure(f"WIP Time {label} Days",
            f"VAR IssuedIds = {all_issued_ids} RETURN DIVIDE(SUMX(FILTER(time_entries, "
            f"{wip_filter} && {band}), time_entries[value_cents]), 100)",
            f"Unissued billable time aged {label} calendar days at the snapshot; excludes costs.")
    measure("Fee Reductions",
            f"DIVIDE(CALCULATE(SUM(adjustments[amount_cents]), adjustments[adjusted_on] <= {cutoff}), 100)",
            "Positive fee reductions, counted once per adjustment; split by stage and reason. Not an issued-cohort rate.")
    quarter_start = f"DATE({snapshot.year},{((snapshot.month - 1) // 3) * 3 + 1},1)"
    current_budgets = f"FILTER(budgets, budgets[period_start] = {quarter_start})"
    measure("Quarter Fee Budget", f"DIVIDE(SUMX({current_budgets}, budgets[fee_budget_cents]), 100)",
            "Full approved quarter fee budget, not prorated to the snapshot.")
    measure("Quarter Actual Fees",
            f"DIVIDE(SUMX({current_budgets}, VAR MatterKey = budgets[matter_id] "
            "VAR PhaseKey = budgets[phase] VAR CurrencyKey = budgets[currency] "
            "RETURN CALCULATE(SUM(time_entries[value_cents]), "
            "TREATAS({MatterKey}, time_entries[matter_id]), TREATAS({PhaseKey}, time_entries[phase]), "
            "TREATAS({CurrencyKey}, time_entries[currency]), "
            f"time_entries[work_date] >= {quarter_start}, time_entries[work_date] <= {cutoff})), 100)",
            "Quarter-to-snapshot recorded time value for each matching budget matter, phase and currency; excludes costs.")
    measure("Budget Variance", "[Quarter Actual Fees] - [Quarter Fee Budget]",
            "Quarter-to-date actual fees minus the full-quarter plan; positive means over budget.")
    measure("Budget Used", "DIVIDE([Quarter Actual Fees], [Quarter Fee Budget])",
            "Quarter-to-date recorded fees divided by the full-quarter plan.", ratio=True)
    by_name["matters"]["measures"] = measures + [{
        "name": "Matter Count", "expression": "COUNTROWS(matters)", "formatString": "#,##0",
        "description": "Number of matters in the current filter context.",
    }]
    model = {
        "compatibilityLevel": 1604,
        "model": {
            "culture": "en-US", "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "discourageImplicitMeasures": True,
            "annotations": [{"name": "EliteDemoScope", "value": "Synthetic firm-wide; no partner RLS"},
                            {"name": "EliteSnapshot", "value": as_of}],
            "expressions": [{"name": "Gold", "kind": "m",
                             "expression": f'Sql.Database("{sql_server}", "{database}")'}],
            "tables": tables, "relationships": relationships,
        },
    }
    return definition_parts({
        "definition.pbism": {"version": "4.0", "settings": {"qnaEnabled": False}},
        "model.bim": model,
    })