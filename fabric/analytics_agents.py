"""Synthetic demo drafts using the ontology binding observed in this tenant's portal."""

import base64
import json
from datetime import date
from uuid import UUID

from fabric.analytics import definition_parts


def build_ontology_agent(*, workspace_id: str, ontology_id: str, firm: str,
                         as_of: str, ontology_definition: dict) -> dict:
    if firm not in {"harbor", "kestrel"}:
        raise ValueError("Unknown firm")
    workspace_id, ontology_id = str(UUID(workspace_id)), str(UUID(ontology_id))
    as_of = date.fromisoformat(as_of).isoformat()
    entities = [json.loads(base64.b64decode(part["payload"]))
                for part in ontology_definition["parts"]
                if part["path"].startswith("EntityTypes/")
                and part["path"].endswith("/definition.json") and part["path"].count("/") == 2]
    if not entities:
        raise ValueError("Cannot create an agent for an empty ontology")
    base = "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition"
    name = f"{firm.title()}_Legal_Ontology_Synthetic_Demo"
    instructions = (
        f"You answer questions about {firm.title()} synthetic legal-finance data only. "
        f"This is a firm-wide administrator demo, with snapshot {as_of}; it is not partner RLS. "
        "Use only the attached ontology and read-only queries. Instructions are not an authorization boundary. "
        "Follow platform permissions and do not attempt to access another firm's data. "
        "Use entity relationships and report links to explain results and their provenance. "
        "Never invent rows, metrics, or successful query execution. If data is unavailable, say so. "
        "Ask for currency if unspecified; never combine currencies or perform implicit FX conversion. "
        "Money properties ending in _cents are integer cents: divide by 100 for display, label the currency. "
        "Issued fees and bills include issued invoices only as of the snapshot. "
        "Gross unbilled WIP includes draft-allocated work until invoiced. "
        "Realization uses issued net time fees divided by the same cohort's standard value; "
        "do not subtract adjustments a second time. Match collections and invoice write-offs to their invoice cohort. "
        "Match quarter actuals to budget matter, phase and currency; do not prorate full-quarter budgets. "
        "Avoid double counting through relationships: aggregate each fact at its own grain before combining totals. "
        "Do not treat monetary properties as ready-made report measures. If a requested calculation cannot be "
        "established from the available properties, explain the limitation and refer to the linked finance report."
    )
    return definition_parts({
        "Files/Config/data_agent.json": {"$schema": f"{base}/dataAgent/2.1.0/schema.json"},
        "Files/Config/draft/stage_config.json": {
            "$schema": f"{base}/stageConfiguration/1.0.0/schema.json", "aiInstructions": instructions},
        f"Files/Config/draft/ontology-{name}/datasource.json": {
            "$schema": f"{base}/dataSource/1.0.0/schema.json", "artifactId": ontology_id,
            "workspaceId": workspace_id, "displayName": name, "type": "ontology", "metadata": {},
            "elements": [{"id": entity["name"], "is_selected": True, "display_name": entity["name"],
                          "type": "ontology.entity", "description": ",".join(
                              prop["name"] for prop in entity.get("properties", [])), "children": []}
                         for entity in sorted(entities, key=lambda entity: entity["name"])],
        },
    })