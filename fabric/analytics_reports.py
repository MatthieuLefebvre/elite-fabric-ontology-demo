"""Deterministic PBIR legal-finance reports using native Power BI visuals."""

from copy import deepcopy
from uuid import UUID

from fabric.analytics import definition_parts

BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report"


def preserve_report_startup(definition: dict, existing_definition: dict) -> dict:
    """Keep the deployed report's startup metadata and its referenced theme assets."""
    startup_paths = {"definition/report.json", "definition/version.json"}
    existing_paths = {part["path"] for part in existing_definition["parts"]}
    if not startup_paths <= existing_paths:
        raise ValueError("Existing report is missing PBIR startup metadata")

    def is_startup(part):
        return part["path"] in startup_paths or part["path"].startswith("StaticResources/")

    return {**definition, "parts": deepcopy(
        [part for part in definition["parts"] if not is_startup(part)] +
        [part for part in existing_definition["parts"] if is_startup(part)])}


def literal(value):
    encoded = ("true" if value else "false") if isinstance(value, bool) else (
        str(value) + "D" if isinstance(value, (int, float)) else "'" + value.replace("'", "''") + "'")
    return {"expr": {"Literal": {"Value": encoded}}}


def color(value):
    return {"solid": {"color": literal(value)}}


def field(table, name, *, measure=False):
    return {"Measure" if measure else "Column": {
        "Expression": {"SourceRef": {"Entity": table}}, "Property": name,
    }}


def projection(table, name, *, measure=False):
    return {"field": field(table, name, measure=measure), "queryRef": f"{table}.{name}",
            "nativeQueryRef": name}


def build_report(*, model_id: str, firm: str, as_of: str) -> dict:
    """Return PBIR with default currency selection; report is not an RLS boundary."""
    model_id = str(UUID(model_id))
    if firm not in {"harbor", "kestrel"}:
        raise ValueError("Unknown firm")
    accent, secondary = ("#087F8C", "#D95B4C") if firm == "harbor" else ("#24734D", "#C29423")
    ink, muted, paper = "#202629", "#647176", "#F3F6F6"
    theme_name = f"Elite{firm.title()}.json"
    files = {
        "definition.pbir": {
            "$schema": f"{BASE}/definitionProperties/2.0.0/schema.json", "version": "4.0",
            "datasetReference": {"byConnection": {"connectionString": f"semanticmodelid={model_id}"}},
        },
        "definition/version.json": {
            "$schema": f"{BASE}/definition/versionMetadata/1.0.0/schema.json", "version": "4.0.0",
        },
        "definition/report.json": {
            "$schema": f"{BASE}/definition/report/3.1.0/schema.json",
            "themeCollection": {"customTheme": {
                "name": theme_name, "type": "RegisteredResources",
                "reportVersionAtImport": {"visual": "2.0.0", "report": "3.1.0", "page": "2.0.0"},
            }},
            "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                                  "items": [{"name": theme_name, "path": theme_name, "type": "CustomTheme"}]}],
            "settings": {"useEnhancedTooltips": True, "defaultDrillFilterOtherVisuals": True,
                         "allowChangeFilterTypes": True, "useStylableVisualContainerHeader": True},
        },
        f"StaticResources/RegisteredResources/{theme_name}": {
            "name": theme_name, "dataColors": [accent, secondary, "#557FA0", "#AC526C", "#7A8B45", "#565D64"],
            "background": "#FFFFFF", "foreground": ink, "tableAccent": accent,
            "textClasses": {"title": {"fontFace": "DIN", "fontSize": 13, "color": ink},
                            "callout": {"fontFace": "DIN", "fontSize": 30, "color": accent},
                            "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": muted}},
        },
    }
    pages = [("overview", "Financial Overview"), ("working_capital", "WIP & Collections"),
             ("performance", "Matter Performance"), ("matter_detail", "Matter Detail")]
    files["definition/pages/pages.json"] = {
        "$schema": f"{BASE}/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [name for name, _ in pages], "activePageName": "overview",
    }
    for page_name, title in pages:
        page_path = f"definition/pages/{page_name}"
        page = {
            "$schema": f"{BASE}/definition/page/2.0.0/schema.json", "name": page_name,
            "displayName": title, "displayOption": "FitToPage", "width": 1440, "height": 900,
            "objects": {"background": [{"properties": {"color": color(paper), "transparency": literal(0)}}]},
        }
        if page_name == "matter_detail":
            page.update({"type": "Drillthrough", "visibility": "HiddenInViewMode",
                         "filterConfig": {"filters": [{"name": "matter_filter", "field": field("matters", "matter_name"),
                                                        "type": "Categorical", "howCreated": "User"}]},
                         "pageBinding": {"name": "matter_drillthrough", "type": "Drillthrough",
                                         "parameters": [{"name": "matter", "boundFilter": "matter_filter",
                                                         "fieldExpr": field("matters", "matter_name")} ]}})
        files[f"{page_path}/page.json"] = page
        ordinal = 0

        def visual(name, kind, title_text, position, query=None, objects=None):
            nonlocal ordinal
            ordinal += 1
            left, top, width, height = position
            config = {
                "visualType": kind, "drillFilterOtherVisuals": True,
                "visualContainerObjects": {
                    "title": [{"properties": {"show": literal(bool(title_text)), "text": literal(title_text),
                                              "fontColor": color(ink), "fontSize": literal(13), "fontFamily": literal("DIN")}}],
                    "background": [{"properties": {"show": literal(True), "color": color("#FFFFFF"),
                                                   "transparency": literal(0)}}],
                    "border": [{"properties": {"show": literal(False), "radius": literal(4)}}],
                },
            }
            if query:
                config["query"] = {"queryState": query}
            if objects:
                config["objects"] = objects
            if kind == "slicer":
                config["syncGroup"] = {"groupName": f"{firm}_{name}",
                                       "fieldChanges": True, "filterChanges": True}
            files[f"{page_path}/visuals/{name}/visual.json"] = {
                "$schema": f"{BASE}/definition/visualContainer/2.0.0/schema.json", "name": name,
                "position": {"x": left, "y": top, "width": width, "height": height,
                             "z": ordinal, "tabOrder": ordinal}, "visual": config,
            }

        def chart(name, kind, title_text, position, table, column, measures):
            query = {"Category": {"projections": [projection(table, column)]},
                     "Y": {"projections": [projection("matters", measure, measure=True) for measure in measures]}}
            visual(name, kind, title_text, position, query,
                   {"categoryAxis": [{"properties": {"showAxisTitle": literal(False), "fontSize": literal(10)}}],
                    "valueAxis": [{"properties": {"showAxisTitle": literal(False), "fontSize": literal(10)}}],
                    "legend": [{"properties": {"show": literal(len(measures) > 1), "position": literal("Bottom")}}]})

        def cards(names):
            for index, name in enumerate(names):
                visual(f"kpi_{index}", "card", name, (32 + index * 348, 174, 332, 112),
                       {"Values": {"projections": [projection("matters", name, measure=True)]}},
                       {"labels": [{"properties": {"color": color(accent), "fontSize": literal(30),
                                                   "fontFamily": literal("DIN"), "labelPrecision": literal(1)}}],
                        "categoryLabels": [{"properties": {"show": literal(False)}}]})

        def table_visual(name, title_text, position, columns, measures):
            visual(name, "tableEx", title_text, position,
                   {"Values": {"projections": [projection(table, column) for table, column in columns] +
                                               [projection("matters", measure, measure=True) for measure in measures]}},
                   {"grid": [{"properties": {"gridVertical": literal(False), "rowPadding": literal(6)}}],
                    "columnHeaders": [{"properties": {"fontColor": color(ink), "fontSize": literal(10)}}],
                    "values": [{"properties": {"fontSize": literal(10)}}]})

        visual("heading", "textbox", "", (32, 20, 1040, 70), objects={"general": [{"properties": {"paragraphs": [
            {"textRuns": [{"value": f"{firm.title()}  /  {title}",
                           "textStyle": {"fontFamily": "DIN", "fontSize": "28pt", "color": ink}}]},
            {"textRuns": [{"value": f"LEGAL FINANCE   |   SYNTHETIC DEMO   |   SNAPSHOT {as_of}",
                           "textStyle": {"fontFamily": "Segoe UI", "fontSize": "10pt", "color": muted}}]},
        ]}}]})
        default_currency = "USD"
        currency_filter = {"Version": 2, "From": [{"Name": "matter", "Entity": "matters", "Type": 0}],
                           "Where": [{"Condition": {"In": {
                               "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "matter"}}, "Property": "currency"}}],
                               "Values": [[{"Literal": {"Value": f"'{default_currency}'"}}]],
                           }}}]}
        visual("currency", "slicer", "Currency", (1120, 20, 288, 70),
               {"Values": {"projections": [projection("matters", "currency")]}},
               {"data": [{"properties": {"mode": literal("Dropdown")}}],
                "header": [{"properties": {"show": literal(False)}}],
                "selection": [{"properties": {"singleSelect": literal(True)}}],
                "general": [{"properties": {"filter": currency_filter}}]})
        for index, (table, column, label) in enumerate([
            ("practice_groups", "name", "Practice"), ("clients", "name", "Client"), ("matters", "matter_name", "Matter")]):
            visual(f"filter_{index}", "slicer", label, (32 + index * 464, 100, 448, 70),
                   {"Values": {"projections": [projection(table, column)]}},
                   {"data": [{"properties": {"mode": literal("Dropdown")}}],
                    "header": [{"properties": {"show": literal(False)}}]})

        if page_name == "overview":
            cards(["Issued Fees", "Issued Realization", "Gross Unbilled WIP", "Outstanding Receivables"])
            chart("fee_trend", "lineChart", "Issued fees by issue date", (32, 304, 856, 266),
                  "invoices", "issued_on", ["Issued Fees"])
            chart("practice_fees", "barChart", "Fees by practice", (904, 304, 504, 266),
                  "practice_groups", "name", ["Issued Fees"])
            chart("collections", "clusteredColumnChart", "Billed and collected by practice", (32, 586, 680, 280),
                  "practice_groups", "name", ["Issued Bills", "Cash Collected"])
            chart("reductions", "barChart", "Fee reductions by stage", (728, 586, 680, 280),
                  "adjustments", "stage", ["Fee Reductions"])
        elif page_name == "working_capital":
            cards(["Gross Unbilled WIP", "WIP Time 121+ Days", "Cash Collected", "Collection Rate"])
            chart("aging", "barChart", "Unissued time aging by practice", (32, 304, 856, 266),
                  "practice_groups", "name", [f"WIP Time {band} Days" for band in ["0-29", "30-59", "60-89", "90-120", "121+"]])
            chart("ar", "barChart", "Outstanding receivables by practice", (904, 304, 504, 266),
                  "practice_groups", "name", ["Outstanding Receivables"])
            table_visual("wip_matters", "Matter working capital", (32, 586, 1376, 280),
                         [("matters", "matter_name"), ("matters", "currency")],
                         ["Gross Unbilled WIP", "WIP Time 121+ Days", "Outstanding Receivables", "Cash Collected"])
        elif page_name == "performance":
            cards(["Quarter Fee Budget", "Quarter Actual Fees", "Budget Variance", "Budget Used"])
            chart("plan_actual", "clusteredBarChart", "Quarter plan vs actual by practice", (32, 304, 680, 266),
                  "practice_groups", "name", ["Quarter Fee Budget", "Quarter Actual Fees"])
            chart("reasons", "barChart", "Fee reductions by reason", (728, 304, 680, 266),
                  "adjustments", "reason_code", ["Fee Reductions"])
            table_visual("matter_performance", "Quarter-to-date matter performance / full-quarter plan", (32, 586, 1376, 280),
                         [("matters", "matter_name"), ("matters", "currency")],
                         ["Quarter Fee Budget", "Quarter Actual Fees", "Budget Variance", "Issued Realization"])
        else:
            cards(["Issued Fees", "Gross Unbilled WIP", "Outstanding Receivables", "Budget Used"])
            chart("matter_fees", "lineChart", "Matter fees by issue date", (32, 304, 680, 266),
                  "invoices", "issued_on", ["Issued Fees"])
            chart("matter_reductions", "barChart", "Matter fee reductions by stage", (728, 304, 680, 266),
                  "adjustments", "stage", ["Fee Reductions"])
            table_visual("invoice_detail", "Issued invoice ledger", (32, 586, 1376, 280),
                         [("invoices", "invoice_number"), ("invoices", "issued_on"), ("invoices", "due_on")],
                         ["Issued Bills", "Cash Collected", "Invoice Write-offs", "Outstanding Receivables"])
    result = definition_parts(files)
    result["format"] = "PBIR"
    return result