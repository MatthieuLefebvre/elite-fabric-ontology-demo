"""Compile a portable contract to the documented Fabric ontology definition (REST v1).

No network calls, deployment claims, or guessed API fields. Public contract checked
2026-09-07; preview service acceptance and caller-level security need live verification.
Logical IDs are tenant-independent; physical binding UUIDs are workspace/item-specific.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml

ONTOLOGY_DEFINITION_URL = (
    "https://learn.microsoft.com/en-us/rest/api/fabric/articles/"
    "item-management/definitions/ontology-definition"
)
AGENT_DEFINITION_URL = (
    "https://learn.microsoft.com/en-us/rest/api/fabric/articles/"
    "item-management/definitions/data-agent-definition"
)
DEFAULT_CONTRACT = Path(__file__).with_name("ontology") / "ontology.yaml"
_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")
_COLUMN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_DECIMAL = re.compile(r"decimal\((\d+),(\d+)\)")
_VALUE_TYPES = {
    "string": "String", "boolean": "Boolean", "date": "DateTime",
    "timestamp": "DateTime", "long": "BigInt", "bigint": "BigInt",
    "int": "BigInt", "integer": "BigInt", "double": "Double",
    "float": "Double", "object": "Object",
}
Contract = dict[str, Any]


class UnsupportedCapability(ValueError):
    """A requested deployment cannot be represented by the documented public API."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently discarding business definitions."""


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    """Construct a string-keyed YAML mapping, rejecting ambiguous duplicate keys."""
    result: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise ValueError(f"Non-string or duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping,
)


def _read_yaml(path: Path) -> dict:
    """Read a mapping without constructing arbitrary Python objects."""
    with path.open(encoding="utf-8") as stream:
        document = yaml.load(stream, Loader=_UniqueKeyLoader)
    if not isinstance(document, dict):
        raise ValueError(f"Expected YAML mapping: {path.name}")
    return document


def _reference(root: Path, value: Any) -> Path:
    """Resolve only local YAML references contained in the contract directory."""
    if not isinstance(value, str) or Path(value).is_absolute():
        raise ValueError("Contract references must be relative YAML paths")
    target = (root / value).resolve()
    if not target.is_relative_to(root) or target.suffix not in {".yaml", ".yml"}:
        raise ValueError(f"Invalid contract reference: {value!r}")
    return target


def _identifier(value: Any, pattern: re.Pattern, context: str) -> str:
    """Validate logical names and simple non-schema Lakehouse table/column names."""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"Invalid {context}: {value!r}")
    return value


def fabric_value_type(spark_type: str) -> str:
    """Map Gold Spark types to the six supported public ontology property types.

    Decimal projects to Double in Fabric; cents must also be exposed as BigInt for
    exact amounts. Keep decimal arithmetic in Gold instead of summing graph doubles.
    """
    if not isinstance(spark_type, str):
        raise ValueError("Property type must be a Spark type string")
    normalized = spark_type.lower().replace(" ", "")
    decimal = _DECIMAL.fullmatch(normalized)
    if decimal:
        precision, scale = map(int, decimal.groups())
        if 1 <= precision <= 38 and 0 <= scale <= precision:
            return "Double"
    if normalized in _VALUE_TYPES:
        return _VALUE_TYPES[normalized]
    raise ValueError(f"Unsupported Gold property type: {spark_type!r}")


def load_contract(path: str | Path = DEFAULT_CONTRACT) -> Contract:
    """Expand local YAML references into a JSON-serializable contract.

    entities is a LIST, properties is a mapping. Source aliases become expressions
    so notebooks need only column, type and optional expression for each property.
    Relationships retain their base table and gain edge_table for filtered edges.
    """
    source = Path(path).resolve()
    if source.is_dir():
        source /= "ontology.yaml"
    root = source.parent
    contract = _read_yaml(source)
    if contract.get("version") != 1:
        raise ValueError("Only portable ontology contract version 1 is supported")
    references = contract.get("entities")
    if not isinstance(references, list) or not references:
        raise ValueError("entities must contain entity YAML references")
    entities = []
    for reference in references:
        entity = _read_yaml(_reference(root, reference))
        properties = entity.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"Missing properties in {reference}")
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                raise ValueError(f"Property {name} must be a mapping")
            source_column = prop.pop("source_column", prop.get("column"))
            _identifier(source_column, _COLUMN, "source column")
            if "expression" not in prop and source_column != prop.get("column"):
                prop["expression"] = f"`{source_column}`"
        entities.append(entity)
    contract["entities"] = entities
    relations = _read_yaml(_reference(root, contract.get("relationships")))
    contract["relationships"] = relations.get("relationships")
    if not isinstance(contract["relationships"], list):
        raise ValueError("relationships must be a list")
    for relation in contract["relationships"]:
        if not isinstance(relation, dict):
            raise ValueError("Each relationship must be a mapping")
        if relation.get("filter"):
            name = _identifier(relation.get("name"), _NAME, "relationship name")
            relation.setdefault("edge_table", "edge_" + name.lower())
    measures = _read_yaml(_reference(root, contract.get("measures")))
    contract["measures"] = measures.pop("measures", None)
    contract["measure_context"] = measures
    contract["security"] = _read_yaml(
        _reference(root, contract.get("security")),
    ).get("security")
    validate_contract(contract)
    # Fail on YAML dates or other non-JSON values before handing this to a notebook.
    json.dumps(contract, allow_nan=False)
    return contract


def _logical_id(kind: str, name: str) -> str:
    """Derive a positive signed-64-bit decimal ID without any tenant input."""
    digest = hashlib.sha256(f"elite-ontology/v1/{kind}/{name}".encode()).digest()
    value = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
    return str(value or 1)


def _property_id(entity: str, prop: str) -> str:
    """Scope property identity to its logical entity rather than its physical column."""
    return _logical_id("property", f"{entity}/{prop}")


def validate_contract(contract: Contract) -> None:
    """Validate portable structure, references, column names, types and ID collisions."""
    if contract.get("version") != 1:
        raise ValueError("Unsupported contract version")
    entities = contract.get("entities")
    if not isinstance(entities, list) or not entities:
        raise ValueError("Expanded entities must be a nonempty list")
    names: set[str] = set()
    tables: set[str] = set()
    ids: set[str] = set()

    def unique_id(identifier: str) -> None:
        if identifier in ids:
            raise ValueError("Logical ID collision; change the conflicting logical name")
        ids.add(identifier)

    for entity in entities:
        if not isinstance(entity, dict):
            raise ValueError("Each entity must be a mapping")
        name = _identifier(entity.get("name"), _NAME, "entity name")
        if name in names:
            raise ValueError(f"Duplicate entity: {name}")
        names.add(name)
        tables.add(_identifier(entity.get("table"), _COLUMN, "entity table"))
        unique_id(_logical_id("entity", name))
        if not isinstance(entity.get("description"), str) or not entity["description"].strip():
            raise ValueError(f"Missing entity description: {name}")
        properties = entity.get("properties")
        if not isinstance(properties, dict) or not properties:
            raise ValueError(f"Missing properties: {name}")
        columns: set[str] = set()
        for prop_name, prop in properties.items():
            _identifier(prop_name, _NAME, "property name")
            unique_id(_property_id(name, prop_name))
            if not isinstance(prop, dict):
                raise ValueError(f"Invalid property mapping: {name}.{prop_name}")
            column = _identifier(prop.get("column"), _COLUMN, "property column")
            if column in columns:
                raise ValueError(f"Duplicate Gold column in {name}: {column}")
            columns.add(column)
            fabric_value_type(prop.get("type"))
            if "expression" in prop and (
                not isinstance(prop["expression"], str) or not prop["expression"].strip()
            ):
                raise ValueError(f"Empty Gold expression: {name}.{prop_name}")
        for field in ("key", "display_name"):
            if entity.get(field) not in properties:
                raise ValueError(f"{name}.{field} must reference a property name")
    relations = contract.get("relationships")
    if not isinstance(relations, list) or any(not isinstance(r, dict) for r in relations):
        raise ValueError("relationships must be a list of mappings")
    relation_names: set[str] = set()
    edge_tables: set[str] = set()
    # Include unmodeled bridge tables in collision checks as well.
    tables.update(r.get("table", "") for r in relations)
    for relation in relations:
        name = _identifier(relation.get("name"), _NAME, "relationship name")
        if name in relation_names:
            raise ValueError(f"Duplicate relationship: {name}")
        relation_names.add(name)
        unique_id(_logical_id("relationship", name))
        if relation.get("source") not in names or relation.get("target") not in names:
            raise ValueError(f"Unknown relationship endpoint: {name}")
        for field in ("table", "source_column", "target_column"):
            _identifier(relation.get(field), _COLUMN, field)
        for field in ("label", "cardinality"):
            if not isinstance(relation.get(field), str) or not relation[field].strip():
                raise ValueError(f"Missing relationship {field}: {name}")
        if "filter" in relation:
            if not isinstance(relation["filter"], str) or not relation["filter"].strip():
                raise ValueError(f"Empty relationship filter: {name}")
            edge = _identifier(relation.get("edge_table"), _COLUMN, "edge table")
            if edge in tables or edge in edge_tables:
                raise ValueError(f"Conflicting relationship edge table: {edge}")
            edge_tables.add(edge)
        elif "edge_table" in relation:
            raise ValueError(f"edge_table requires a filter: {name}")
    measures = contract.get("measures")
    if not isinstance(measures, list) or not measures:
        raise ValueError("measures must be a nonempty list")
    measure_names: set[str] = set()
    for measure in measures:
        if not isinstance(measure, dict):
            raise ValueError("Each measure must be a mapping")
        name = _identifier(measure.get("name"), _COLUMN, "measure name")
        if name in measure_names:
            raise ValueError(f"Duplicate measure: {name}")
        measure_names.add(name)
        for field in ("description", "grain", "unit", "expression", "sql", "comparison_note"):
            if not isinstance(measure.get(field), str) or not measure[field].strip():
                raise ValueError(f"Missing measure {field}: {name}")
        if any(entity not in names for entity in measure.get("source_entities", [])):
            raise ValueError(f"Unknown source entity for measure: {name}")
    security = contract.get("security")
    if not isinstance(security, dict) or not security.get("authorization_sql"):
        raise ValueError("Missing security authorization SQL")
    context = contract.get("measure_context", {})
    if context.get("common_ctes", "").count("{authorization_sql}") != 1:
        raise ValueError("Measure common_ctes must contain exactly one authorization_sql slot")


def relationship_projections(contract: Contract) -> list[dict[str, Any]]:
    """Return Gold edge materializations; filter source rows before projecting keys.

    Each record: {name, source_table, table, filter, columns}. Materialize table from
    source_table after entity aliases, retaining firm_id for tenant and security checks.
    These are business-shape filters, NOT caller access enforcement.
    """
    validate_contract(contract)
    return [
        {
            "name": relation["name"], "source_table": relation["table"],
            "table": relation["edge_table"], "filter": relation["filter"],
            "columns": list(dict.fromkeys([
                "firm_id", relation["source_column"], relation["target_column"],
            ])),
        }
        for relation in contract["relationships"] if relation.get("filter")
    ]


def required_gold_tables(contract: Contract) -> list[str]:
    """List entity, bridge and edge tables needed in each consumer lakehouse.

    Include these in provider Gold and recipient sharing/shortcut plans. The
    contract does not authorize sharing confidential rows or override security gates.
    """
    validate_contract(contract)
    tables = {entity["table"] for entity in contract["entities"]}
    for relation in contract["relationships"]:
        tables.add(relation["table"])
        if relation.get("edge_table"):
            tables.add(relation["edge_table"])
    for table in contract.get("gold", {}).get("passthrough_tables", []):
        tables.add(_identifier(table, _COLUMN, "passthrough table"))
    return sorted(tables)


def _part(path: str, value: dict) -> dict[str, str]:
    """Encode deterministic UTF-8 JSON into a documented InlineBase64 part."""
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return {
        "path": path, "payload": base64.b64encode(payload.encode()).decode("ascii"),
        "payloadType": "InlineBase64",
    }


def _guid(value: str, label: str) -> str:
    """Normalize resource GUIDs without silently accepting missing physical bindings."""
    try:
        parsed = UUID(value)
        if parsed.int == 0:
            raise ValueError("Nil GUID")
        return str(parsed)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{label} must be a non-nil resource GUID") from exc


# VERIFY: ONTOLOGY_V1 — confirm preview property/binding types and graph refresh in target tenant.
def property_description(entity: str, name: str, definition: dict) -> str:
    """Describe property units and business roles using the portable Gold contract."""
    authored = definition.get("description")
    if authored:
        return authored
    meanings = {
        "responsible_partner_id": "Partner accountable for delivery; distinct from billing and originating partners.",
        "billing_partner_id": "Partner accountable for billing decisions, not necessarily delivery or origination.",
        "originating_partner_id": "Partner credited with originating the engagement, not its access authority.",
        "parent_matter_id": "Parent engagement reference; neither access grants nor financial rollups are inherited.",
        "currency": "Recorded ISO currency. Group money by currency; no FX conversion or mixed-currency totals.",
        "net_time_cents": "Issued net time fees excluding costs; realization numerator. Later write-offs do not change it.",
        "standard_time_cents": "Standard time value for this invoice's work; issued realization denominator.",
        "stage": "Exclusive fee reduction stage: time_entry, proforma review, or post-issue invoice write-off.",
        "value_cents": "Recorded negotiated time value before review reductions, in integer cents.",
        "billable_flag": "Whether recorded work is billable. Draft allocation alone does not remove it from WIP.",
        "budgeted_fees_cents": "Full approved quarter fee plan for this matter, phase and currency; not prorated.",
        "phase": "Work phase used to match actual time to budget without duplicating whole-matter plans.",
        "invoice_id": "Invoice reference; only an issued invoice removes allocated work from operational WIP.",
    }
    if name in meanings:
        return meanings[name]
    label = name.replace("_", " ")
    if name.endswith("_cents"):
        return f"{entity} {label}, stored as exact integer cents in the row currency; divide by 100 for display."
    if name.endswith("_id"):
        return f"Stable {label} reference on {entity}; a reference is not an authorization grant."
    if definition["type"] in ("date", "timestamp"):
        return f"{entity} {label}, bound from Gold column {definition['column']}; interpret at the dataset snapshot."
    return f"{entity} {label}, bound from Gold column {definition['column']}."


def build_ontology_definition(
    contract: Contract, workspace_id: str, lakehouse_id: str, display_name: str,
) -> dict:
    """Return {parts: [...]} for the documented public ontology v1 definition.

    Callers supply this as an item's definition, not as the entire create-item body.
    Gold aliases and filtered edge tables MUST exist before this definition is bound.
    No sourceSchema is emitted: this contract targets a non-schema lakehouse.
    """
    validate_contract(contract)
    workspace = _guid(workspace_id, "workspace_id")
    lakehouse = _guid(lakehouse_id, "lakehouse_id")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ValueError("display_name must be nonempty")
    entities = {entity["name"]: entity for entity in contract["entities"]}

    def binding_id(kind: str, name: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"elite-ontology/v1/{workspace}/{lakehouse}/{kind}/{name}"))

    def table_properties(table: str) -> dict:
        return {
            "sourceType": "LakehouseTable", "workspaceId": workspace,
            "itemId": lakehouse, "sourceTableName": table,
        }

    parts = [
        _part(".platform", {"metadata": {"type": "Ontology", "displayName": display_name}}),
        _part("definition.json", {}),
    ]
    for name, entity in sorted(entities.items()):
        entity_id = _logical_id("entity", name)
        properties = entity["properties"]
        parts.append(_part(f"EntityTypes/{entity_id}/definition.json", {
            "id": entity_id, "namespace": "usertypes", "namespaceType": "Custom",
            "name": name, "entityIdParts": [_property_id(name, entity["key"])],
            "displayNamePropertyId": _property_id(name, entity["display_name"]),
            "visibility": "Visible",
              "semanticEnrichment": {"description": entity["description"]},
            "properties": [
                {"id": _property_id(name, prop), "name": prop,
                  "valueType": fabric_value_type(definition["type"]),
                  "semanticEnrichment": {"description": property_description(name, prop, definition)}}
                for prop, definition in sorted(properties.items())
            ],
            "timeseriesProperties": [],
        }))
        binding = binding_id("entity", name)
        parts.append(_part(f"EntityTypes/{entity_id}/DataBindings/{binding}.json", {
            "id": binding,
            "dataBindingConfiguration": {
                "dataBindingType": "NonTimeSeries",
                "propertyBindings": [
                    {"sourceColumnName": definition["column"],
                     "targetPropertyId": _property_id(name, prop)}
                    for prop, definition in sorted(properties.items())
                ],
                "sourceTableProperties": table_properties(entity["table"]),
            },
        }))
    for relation in sorted(contract["relationships"], key=lambda item: item["name"]):
        name = relation["name"]
        relation_id = _logical_id("relationship", name)
        source, target = relation["source"], relation["target"]
        parts.append(_part(f"RelationshipTypes/{relation_id}/definition.json", {
            "id": relation_id, "namespace": "usertypes", "namespaceType": "Custom",
            "name": name, "source": {"entityTypeId": _logical_id("entity", source)},
            "target": {"entityTypeId": _logical_id("entity", target)},
            "semanticEnrichment": {"description": relation["label"]},
        }))
        binding = binding_id("relationship", name)
        table = relation.get("edge_table", relation["table"])
        parts.append(_part(
            f"RelationshipTypes/{relation_id}/Contextualizations/{binding}.json", {
                "id": binding, "dataBindingTable": table_properties(table),
                "sourceKeyRefBindings": [{
                    "sourceColumnName": relation["source_column"],
                    "targetPropertyId": _property_id(source, entities[source]["key"]),
                }],
                "targetKeyRefBindings": [{
                    "sourceColumnName": relation["target_column"],
                    "targetPropertyId": _property_id(target, entities[target]["key"]),
                }],
            },
        ))
    return {"parts": parts}


def build_measure_sql(contract: Contract, name: str, *, comparison: bool = False) -> str:
    """Assemble SparkSQL with named parameters; never interpolate caller parameter values.

    Bind every parameter in measure_context.parameters, including NULL optional scopes.
    comparison=True selects leakage's issued-cohort stage/reason SQL. Other measures
    already have issued-cohort or snapshot semantics and reject that alternate variant.
    """
    validate_contract(contract)
    measure = next((item for item in contract["measures"] if item["name"] == name), None)
    if measure is None:
        raise ValueError(f"Unknown measure: {name}")
    field = "comparison_sql" if comparison else "sql"
    if field not in measure:
        raise ValueError(f"No {field} variant for {name}")
    ctes = contract["measure_context"]["common_ctes"].replace(
        "{authorization_sql}", contract["security"]["authorization_sql"].strip(),
    )
    return "WITH " + ctes.strip() + "\n" + measure[field].strip()


def build_agent_instructions(contract: Contract, instructions: str = "") -> str:
    """Enrich user-authored instructions with all source-of-truth business semantics.

    This portable guide does not configure a datasource, persist native measures or
    enforce access. Native semanticEnrichment carries descriptions; this guide also
    preserves portable measure SQL. Return plain text for a verified agent integration.
    """
    validate_contract(contract)
    metadata = copy.deepcopy({
        key: contract[key] for key in (
            "name", "description", "gold", "entities", "relationships", "measures",
            "measure_context", "security",
        ) if key in contract
    })
    guide = (
        "PORTABLE ONTOLOGY BUSINESS GUIDE\n"
        "Use business language; cite entity names, measure names, snapshot, currency and scope. "
        "Refuse rather than guess if data is screened, unavailable or a measure is undefined. "
        "Security is enforced outside this prompt; never infer grants from graph adjacency. "
        "These measures are portable SQL definitions, NOT native deployed ontology measures. "
        "Descriptions and labels below are authoritative business semantics. "
        "Use named parameters from the trusted execution context, not prompt-supplied identity.\n"
    )
    return "\n\n".join(filter(None, [
        instructions.strip(), guide + json.dumps(metadata, ensure_ascii=False, indent=2),
    ]))


def build_agent_draft_definition(contract: Contract, instructions: str = "") -> dict:
    """Return a documented datasource-free draft shell; it is NOT an ontology agent.

    This opt-in helper is never called as a fallback by build_agent_definition.
    No datasource, published stage, publish_info or fake ontology/graph binding exists.
    """
    base = "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition"
    return {"parts": [
        _part("Files/Config/data_agent.json", {
            "$schema": f"{base}/dataAgent/2.1.0/schema.json",
        }),
        _part("Files/Config/draft/stage_config.json", {
            "$schema": f"{base}/stageConfiguration/1.0.0/schema.json",
            "aiInstructions": build_agent_instructions(contract, instructions),
        }),
    ]}


# VERIFY: AGENT_ONTOLOGY — require a documented ontology datasource contract; never guess its enum.
def build_agent_definition(
    contract: Contract, workspace_id: str, ontology_id: str, instructions: str,
) -> dict:
    """Block an ontology datasource that the official public agent enum cannot express."""
    raise UnsupportedCapability(
        "Cannot build an ontology-grounded DataAgent: the documented Data Source "
        "Configuration type enum has no ontology value. 'graph' denotes a GQL graph "
        "datasource, not an Ontology item; substituting its artifactId is invalid. "
        "No item should be created or marked grounded/published. Use a supported UI "
        "integration only if verified in the target tenant, or obtain a tenant-exported "
        "definition and validate its documented support before adding an explicit "
        "versioned adapter. build_agent_instructions returns the portable guide; "
        "build_agent_draft_definition creates only a datasource-free draft shell. "
        f"Public contract: {AGENT_DEFINITION_URL}"
    )