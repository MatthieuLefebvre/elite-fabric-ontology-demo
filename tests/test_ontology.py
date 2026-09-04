"""Offline contract/public-payload tests; no claim of live Fabric or Spark execution."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from generators.common import SPECS

from fabric.ontology_adapter import (
    DEFAULT_CONTRACT,
    UnsupportedCapability,
    build_agent_definition,
    build_agent_draft_definition,
    build_agent_instructions,
    build_measure_sql,
    build_ontology_definition,
    fabric_value_type,
    load_contract,
    relationship_projections,
    required_gold_tables,
    validate_contract,
)

WORKSPACE = "11111111-1111-4111-8111-111111111111"
LAKEHOUSE = "22222222-2222-4222-8222-222222222222"
OTHER_WORKSPACE = "33333333-3333-4333-8333-333333333333"
OTHER_LAKEHOUSE = "44444444-4444-4444-8444-444444444444"
REQUIRED = {
    "Client": "client_number name industry relationship_partner_id credit_status",
    "Matter": "matter_number name client_id practice_group_id legal_entity_id arrangement_type "
              "open_date status parent_matter_id responsible_partner_id billing_partner_id "
              "originating_partner_id",
    "Timekeeper": "timekeeper_id name grade standard_rate office department",
    "PracticeGroup": "code name group_head_id",
    "LegalEntity": "entity_code jurisdiction functional_currency",
    "TimeEntry": "date hours rate value narrative billable_flag status matter_id timekeeper_id",
    "Disbursement": "type amount vendor recoverable_flag matter_id",
    "Proforma": "proforma_number period draft_value reviewer_id status",
    "Invoice": "invoice_number date gross discount net currency due_date ebilling_status",
    "Payment": "date amount method invoice_id",
    "Adjustment": "type amount reason_code approver_id stage time_entry_id proforma_id invoice_id",
    "Budget": "period phase budgeted_hours budgeted_fees matter_id",
    "BillingGuideline": "rule_type threshold effective_from effective_to",
}
MEASURES = {
    "unbilled_wip", "wip_age_days", "realization_rate", "collection_rate",
    "days_sales_outstanding", "leakage", "budget_variance",
}


@pytest.fixture
def contract() -> dict:
    """Load a fresh expanded contract for each test, without shared mutations."""
    return load_contract(DEFAULT_CONTRACT)


def decode(definition: dict) -> dict[str, dict]:
    """Decode and validate the exact public envelope and unique relative part paths."""
    assert set(definition) == {"parts"}
    result = {}
    for part in definition["parts"]:
        assert set(part) == {"path", "payload", "payloadType"}
        assert part["payloadType"] == "InlineBase64"
        path = part["path"]
        assert path not in result and not path.startswith("/") and ".." not in path.split("/")
        result[path] = json.loads(base64.b64decode(part["payload"], validate=True))
    return result


def entity_documents(parts: dict[str, dict]) -> dict[str, dict]:
    """Index only entity definitions, not their binding documents."""
    return {
        value["name"]: value for path, value in parts.items()
        if path.startswith("EntityTypes/") and path.endswith("/definition.json")
    }


def test_expands_thirteen_business_entities_plus_access(contract: dict) -> None:
    assert isinstance(contract["entities"], list)
    assert len(contract["entities"]) == 14
    entities = {item["name"]: item for item in contract["entities"]}
    assert set(entities) == set(REQUIRED) | {"MatterAccess"}
    for name, required in REQUIRED.items():
        assert set(required.split()) <= set(entities[name]["properties"])
    for entity in entities.values():
        assert entity["key"] in entity["properties"]
        assert entity["display_name"] in entity["properties"]
        assert len(entity["description"]) > 80
        for prop in entity["properties"].values():
            assert {"column", "type"} <= set(prop) <= {"column", "type", "expression"}
    assert json.loads(json.dumps(contract)) == contract
    assert load_contract(DEFAULT_CONTRACT.parent) == contract


def test_one_file_per_entity_and_original_sources_or_declared_extensions(contract: dict) -> None:
    root = yaml.safe_load(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    assert len(root["entities"]) == len(set(root["entities"])) == 14
    extensions = contract["gold"]["new_source_fields"]
    columns = {
        table: {token.split(":")[0] for token in spec.split()} for table, spec in SPECS.items()
    }
    for reference in root["entities"]:
        entity = yaml.safe_load((DEFAULT_CONTRACT.parent / reference).read_text(encoding="utf-8"))
        allowed = columns[entity["table"]] | set(extensions.get(entity["table"], []))
        for prop in entity["properties"].values():
            if "expression" not in prop:
                assert prop.get("source_column", prop["column"]) in allowed
    by_name = {entity["name"]: entity for entity in contract["entities"]}
    assert by_name["Client"]["properties"]["name"]["expression"] == "`client_name`"
    assert by_name["Matter"]["properties"]["arrangement_type"]["expression"] == "`fee_arrangement`"
    assert by_name["TimeEntry"]["properties"]["value_cents"]["type"] == "long"
    assert "value_cents" in by_name["TimeEntry"]["properties"]["value"]["expression"]
    assert "/ 100" in by_name["TimeEntry"]["properties"]["value"]["expression"]


def test_relationship_columns_exist_in_gold_or_bridge_schema(contract: dict) -> None:
    columns = {
        table: {token.split(":")[0] for token in spec.split()} for table, spec in SPECS.items()
    }
    for table, additions in contract["gold"]["new_source_fields"].items():
        columns[table].update(additions)
    for entity in contract["entities"]:
        columns[entity["table"]].update(p["column"] for p in entity["properties"].values())
    for relation in contract["relationships"]:
        assert relation["source_column"] in columns[relation["table"]]
        assert relation["target_column"] in columns[relation["table"]]
        for attr in relation.get("attributes", {}).values():
            assert attr["column"] in columns[relation["table"]]


def test_public_entity_definition_and_binding_shapes(contract: dict) -> None:
    parts = decode(build_ontology_definition(contract, WORKSPACE, LAKEHOUSE, "Legal ontology"))
    assert parts[".platform"] == {"metadata": {"type": "Ontology", "displayName": "Legal ontology"}}
    assert parts["definition.json"] == {}
    entities = entity_documents(parts)
    assert len(entities) == 14
    logical_ids = []
    for definition in entities.values():
        assert set(definition) == {
            "id", "namespace", "namespaceType", "name", "entityIdParts",
            "displayNamePropertyId", "visibility", "properties", "timeseriesProperties",
        }
        assert definition["namespace"] == "usertypes"
        assert definition["namespaceType"] == "Custom"
        assert definition["visibility"] == "Visible" and definition["timeseriesProperties"] == []
        prop_ids = {prop["id"] for prop in definition["properties"]}
        assert set(definition["entityIdParts"]) <= prop_ids
        assert definition["displayNamePropertyId"] in prop_ids
        logical_ids.append(definition["id"])
        for prop in definition["properties"]:
            assert set(prop) == {"id", "name", "valueType"}
            assert prop["valueType"] in {"String", "Boolean", "DateTime", "Object", "BigInt", "Double"}
            logical_ids.append(prop["id"])
        prefix = f"EntityTypes/{definition['id']}/DataBindings/"
        bindings = [v for path, v in parts.items() if path.startswith(prefix)]
        assert len(bindings) == 1
        binding = bindings[0]
        assert set(binding) == {"id", "dataBindingConfiguration"}
        assert UUID(binding["id"]).version == 5
        config = binding["dataBindingConfiguration"]
        assert set(config) == {"dataBindingType", "propertyBindings", "sourceTableProperties"}
        assert config["dataBindingType"] == "NonTimeSeries"
        assert {p["targetPropertyId"] for p in config["propertyBindings"]} == prop_ids
        for prop in config["propertyBindings"]:
            assert set(prop) == {"sourceColumnName", "targetPropertyId"}
        assert set(config["sourceTableProperties"]) == {
            "sourceType", "workspaceId", "itemId", "sourceTableName",
        }
    for identifier in logical_ids:
        assert isinstance(identifier, str) and 0 < int(identifier) <= 2**63 - 1
    assert len(set(logical_ids)) == len(logical_ids)


def test_contextualizations_reference_actual_endpoint_keys(contract: dict) -> None:
    parts = decode(build_ontology_definition(contract, WORKSPACE, LAKEHOUSE, "Legal"))
    entities = {entity["id"]: entity for entity in entity_documents(parts).values()}
    relations = {relation["name"]: relation for relation in contract["relationships"]}
    for path, definition in parts.items():
        if not path.startswith("RelationshipTypes/") or not path.endswith("/definition.json"):
            continue
        assert set(definition) == {"id", "namespace", "namespaceType", "name", "source", "target"}
        assert 0 < int(definition["id"]) <= 2**63 - 1
        relation = relations[definition["name"]]
        prefix = f"RelationshipTypes/{definition['id']}/Contextualizations/"
        bindings = [v for p, v in parts.items() if p.startswith(prefix)]
        assert len(bindings) == 1
        binding = bindings[0]
        assert set(binding) == {"id", "dataBindingTable", "sourceKeyRefBindings", "targetKeyRefBindings"}
        assert UUID(binding["id"]).version == 5
        assert binding["dataBindingTable"] == {
            "sourceType": "LakehouseTable", "workspaceId": WORKSPACE, "itemId": LAKEHOUSE,
            "sourceTableName": relation.get("edge_table", relation["table"]),
        }
        for end in ("source", "target"):
            assert set(definition[end]) == {"entityTypeId"}
            entity = entities[definition[end]["entityTypeId"]]
            assert binding[f"{end}KeyRefBindings"] == [{
                "sourceColumnName": relation[f"{end}_column"],
                "targetPropertyId": entity["entityIdParts"][0],
            }]
    assert len(parts) == 2 + 2 * len(entities) + 2 * len(relations)
    # Descriptions, portable cardinality, expressions and relationship attributes
    # must NOT be smuggled into a public part or pretend to be a measure DSL.
    serialized = json.dumps(parts)
    for forbidden in ("sourceSchema", '"attributes"', '"description"', '"measures"',
                      '"cardinality"', '"expression"', '"filter"'):
        assert forbidden not in serialized


def test_deterministic_logical_ids_and_tenant_specific_physical_bindings(contract: dict) -> None:
    original = copy.deepcopy(contract)
    first = build_ontology_definition(contract, WORKSPACE, LAKEHOUSE, "A")
    assert first == build_ontology_definition(contract, WORKSPACE, LAKEHOUSE, "A")
    shuffled = copy.deepcopy(contract)
    shuffled["entities"].reverse()
    shuffled["relationships"].reverse()
    assert first == build_ontology_definition(shuffled, WORKSPACE, LAKEHOUSE, "A")
    assert contract == original
    a = decode(first)
    b = decode(build_ontology_definition(contract, OTHER_WORKSPACE, OTHER_LAKEHOUSE, "B"))
    definitions_a = {p: v for p, v in a.items() if p.endswith("/definition.json")}
    definitions_b = {p: v for p, v in b.items() if p.endswith("/definition.json")}
    assert definitions_a == definitions_b
    physical_a = {v["id"] for p, v in a.items() if "Bindings/" in p or "Contextualizations/" in p}
    physical_b = {v["id"] for p, v in b.items() if "Bindings/" in p or "Contextualizations/" in p}
    assert physical_a.isdisjoint(physical_b)
    assert WORKSPACE not in json.dumps(b) and LAKEHOUSE not in json.dumps(b)
    assert OTHER_WORKSPACE in json.dumps(b) and OTHER_LAKEHOUSE in json.dumps(b)


def test_access_roles_and_exclusive_adjustment_edge_projections(contract: dict) -> None:
    relations = {r["name"]: r for r in contract["relationships"]}
    roles = {r["target_column"] for name, r in relations.items() if name in {
        "MatterResponsiblePartner", "MatterBillingPartner", "MatterOriginatingPartner",
    }}
    assert roles == {"responsible_partner_id", "billing_partner_id", "originating_partner_id"}
    parent = relations["MatterPhaseOfParent"]
    assert parent["source"] == parent["target"] == "Matter"
    assert parent["target_column"] == "parent_matter_id"
    access = relations["TimekeeperMatterAccess"]
    assert access["attributes"]["access_type"]["values"] == ["granted", "screened"]
    access_entity = next(e for e in contract["entities"] if e["name"] == "MatterAccess")
    assert "ELSE 'screened'" in access_entity["properties"]["access_type"]["expression"]
    assert relations["MatterAccessForTimekeeper"]["target"] == "Timekeeper"
    assert relations["MatterAccessForMatter"]["target"] == "Matter"
    projections = {p["name"]: p for p in relationship_projections(contract)}
    tables = required_gold_tables(contract)
    assert tables == sorted(set(tables))
    assert "invoice_guidelines" in tables
    assert {e["table"] for e in contract["entities"]} <= set(tables)
    assert {p["table"] for p in projections.values()} <= set(tables)
    assert set(projections) == {r["name"] for r in relations.values() if r.get("filter")}
    for name, projection in projections.items():
        relation = relations[name]
        assert projection == {
            "name": name, "source_table": relation["table"], "table": relation["edge_table"],
            "filter": relation["filter"],
            "columns": ["firm_id", relation["source_column"], relation["target_column"]],
        }
        assert projection["table"] != projection["source_table"]
    for stage, target in (("time_entry", "TimeEntry"), ("proforma", "Proforma"), ("invoice", "Invoice")):
        edge_filter = projections[f"AdjustmentReduces{target}"]["filter"]
        assert f"stage = '{stage}'" in edge_filter
        assert f"{stage}_id IS NOT NULL" in edge_filter
        for other in {"time_entry", "proforma", "invoice"} - {stage}:
            assert f"{other}_id IS NULL" in edge_filter


def test_all_seven_measure_sql_and_snapshot_semantics(contract: dict) -> None:
    measures = {measure["name"]: measure for measure in contract["measures"]}
    assert set(measures) == MEASURES
    for name in MEASURES:
        sql = build_measure_sql(contract, name)
        assert sql.startswith("WITH authorized_matter_ids")
        assert "{authorization_sql}" not in sql
        assert "current_date" not in sql.lower() and "getdate" not in sql.lower()
        assert ":as_of" in sql and ":firm_id" in sql and ":timekeeper_id" in sql
        assert "NOT EXISTS" in sql and "a.access_type <> 'granted'" in sql
        assert len(measures[name]["description"]) > 100
    wip = build_measure_sql(contract, "unbilled_wip")
    assert wip.count("LEFT ANTI JOIN s_invoices") == 2
    assert "status = 'unbilled'" not in wip
    assert "e.billable_flag = true" in wip and "d.recoverable_flag = true" in wip
    assert "net_time_cents" in measures["realization_rate"]["sql"]
    assert "standard_time_cents" in measures["realization_rate"]["sql"]
    assert "writeoff" not in measures["realization_rate"]["sql"]
    assert "GROUP BY firm_id, matter_id, invoice_id, currency" in measures["collection_rate"]["sql"]
    assert "AVG(DATEDIFF(p.paid_on, i.issued_on))" in measures["days_sales_outstanding"]["sql"]
    assert "not 95" in measures["days_sales_outstanding"]["comparison_note"]
    budget = measures["budget_variance"]["sql"]
    assert "a.phase = b.phase" in budget and "period_end_exclusive" in budget
    assert "AS hours_variance" in budget and "AS fee_variance_cents" in budget
    leakage = build_measure_sql(contract, "leakage", comparison=True)
    assert "cohort_invoice_id" in leakage and "reason_code" in leakage
    assert "NULLIF(d.standard_time_cents, 0)" in leakage
    with pytest.raises(ValueError, match="Unknown measure"):
        build_measure_sql(contract, "invented")
    with pytest.raises(ValueError, match="No comparison_sql"):
        build_measure_sql(contract, "realization_rate", comparison=True)


def test_instructions_preserve_metadata_but_agent_binding_blocks(contract: dict) -> None:
    guide = build_agent_instructions(contract, "Answer concisely.")
    assert guide.startswith("Answer concisely.")
    # JSON string escaping still preserves all authored descriptions on decoding.
    metadata = json.loads(guide[guide.index("{\n"):])
    assert metadata["entities"] == contract["entities"]
    assert metadata["measures"] == contract["measures"]
    assert metadata["relationships"] == contract["relationships"]
    assert metadata["security"] == contract["security"]
    assert "NOT native deployed" in guide
    with pytest.raises(UnsupportedCapability, match="no ontology value") as error:
        build_agent_definition(contract, WORKSPACE, OTHER_WORKSPACE, "Answer concisely.")
    assert isinstance(error.value, ValueError)
    assert "GQL" in str(error.value)
    draft = decode(build_agent_draft_definition(contract, "Answer concisely."))
    assert set(draft) == {"Files/Config/data_agent.json", "Files/Config/draft/stage_config.json"}
    assert set(draft["Files/Config/data_agent.json"]) == {"$schema"}
    assert draft["Files/Config/draft/stage_config.json"]["aiInstructions"] == guide
    assert not any("datasource" in p or "published" in p for p in draft)


@pytest.mark.parametrize(("spark_type", "expected"), [
    ("string", "String"), ("boolean", "Boolean"), ("date", "DateTime"),
    ("timestamp", "DateTime"), ("long", "BigInt"), ("double", "Double"),
    ("decimal(18,2)", "Double"), ("object", "Object"),
])
def test_only_supported_fabric_types(spark_type: str, expected: str) -> None:
    assert fabric_value_type(spark_type) == expected


@pytest.mark.parametrize("bad_type", ["decimal(39,2)", "decimal(2,3)", "array<string>", "Money"])
def test_rejects_unsupported_types(bad_type: str) -> None:
    with pytest.raises(ValueError):
        fabric_value_type(bad_type)


def test_invalid_names_references_types_and_filters_fail_early(contract: dict) -> None:
    bad = copy.deepcopy(contract)
    bad["entities"].append(copy.deepcopy(bad["entities"][0]))
    with pytest.raises(ValueError, match="Duplicate entity"):
        validate_contract(bad)
    bad = copy.deepcopy(contract)
    bad["entities"][0]["key"] = "missing"
    with pytest.raises(ValueError, match="key must reference"):
        validate_contract(bad)
    bad = copy.deepcopy(contract)
    bad["relationships"][0]["target"] = "NotAnEntity"
    with pytest.raises(ValueError, match="Unknown relationship endpoint"):
        validate_contract(bad)
    bad = copy.deepcopy(contract)
    edge = next(r for r in bad["relationships"] if r.get("filter"))
    edge["edge_table"] = edge["table"]
    with pytest.raises(ValueError, match="Conflicting relationship edge table"):
        validate_contract(bad)
    with pytest.raises(ValueError, match="resource GUID"):
        build_ontology_definition(contract, "not-a-guid", LAKEHOUSE, "Legal")


def test_duplicate_yaml_and_path_escape_fail_before_read(tmp_path: Path) -> None:
    root = tmp_path / "ontology.yaml"
    root.write_text("version: 1\nversion: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_contract(root)
    root.write_text("version: 1\nentities: [../outside.yaml]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid contract reference"):
        load_contract(root)