"""Checks for currency-safe, correctly bound synthetic firm analytics."""

import base64
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from fabric.analytics import build_semantic_model, definition_parts
from fabric.analytics_reports import build_report, preserve_report_startup
from fabric.deploy_analytics import deploy_ontologies, deploy_reports
from fabric.ontology_adapter import load_contract, required_gold_tables

ROOT = Path(__file__).resolve().parents[1]


def unpack(definition):
    return {part["path"]: json.loads(base64.b64decode(part["payload"]))
            for part in definition["parts"]}


@pytest.mark.parametrize("firm", ["harbor", "kestrel"])
def test_semantic_model_gold_contract(firm):
    contract = load_contract(ROOT / "fabric/ontology/ontology.yaml")
    schema = json.loads((ROOT / "data/schema.json").read_text())
    definition = build_semantic_model(
        contract, schema, sql_server="demo.datawarehouse.fabric.microsoft.com",
        sql_database="11111111-1111-4111-8111-111111111111", firm=firm, as_of="2026-09-04")
    model = unpack(definition)["model.bim"]["model"]
    tables = {table["name"]: table for table in model["tables"]}
    assert "matter_access" not in tables
    for table in tables.values():
        partition = table["partitions"][0]
        assert partition["mode"] == "directLake"
        assert partition["source"]["entityName"] == table["name"]
        assert table["description"]
        assert len({column["name"] for column in table["columns"]}) == len(table["columns"])
    for relation in model["relationships"]:
        assert relation["crossFilteringBehavior"] == "oneDirection"
        for side in ("from", "to"):
            assert relation[side + "Column"] in {
                column["name"] for column in tables[relation[side + "Table"]]["columns"]}
    measures = {measure["name"]: measure for measure in tables["matters"]["measures"]}
    for name in ("Issued Fees", "Issued Bills", "Issued Realization"):
        assert "HASONEVALUE(matters[currency])" in measures[name]["expression"]
        assert "NOT ISBLANK(SELECTEDVALUE(matters[currency]))" in measures[name]["expression"]
        assert "DATE(2026,9,4)" in measures[name]["expression"]
        assert 'invoices[status] = "issued"' in measures[name]["expression"]
    assert "adjustments" not in measures["Issued Realization"]["expression"]
    assert "TREATAS(IssuedIds, payments[invoice_id])" in measures["Cash Collected"]["expression"]
    assert "ALL(invoices)" in measures["Gross Unbilled WIP"]["expression"]
    assert "time_entries[status]" not in measures["Gross Unbilled WIP"]["expression"]
    assert "TREATAS({PhaseKey}, time_entries[phase])" in measures["Quarter Actual Fees"]["expression"]


@pytest.mark.parametrize("firm", ["harbor", "kestrel"])
def test_report_layout_and_model_binding(firm):
    model_id = "11111111-1111-4111-8111-111111111111"
    files = unpack(build_report(model_id=model_id, firm=firm, as_of="2026-09-04"))
    assert files["definition.pbir"]["datasetReference"]["byConnection"]["connectionString"] == f"semanticmodelid={model_id}"
    report = files["definition/report.json"]
    resources = {package["name"]: package for package in report["resourcePackages"]}
    theme_name = report["themeCollection"]["customTheme"]["name"]
    assert theme_name == f"Elite{firm.title()}.json"
    theme_resource = resources["RegisteredResources"]["items"][0]
    assert theme_resource["name"] == theme_resource["path"] == theme_name
    assert f"StaticResources/RegisteredResources/{theme_name}" in files
    pages = files["definition/pages/pages.json"]["pageOrder"]
    assert len(pages) == 4
    for page_name in pages:
        visuals = [value for path, value in files.items()
                   if path.startswith(f"definition/pages/{page_name}/visuals/")]
        assert len(visuals) >= 12
        for index, visual in enumerate(visuals):
            position = visual["position"]
            assert 0 <= position["x"] < position["x"] + position["width"] <= 1440
            assert 0 <= position["y"] < position["y"] + position["height"] <= 900
            for other in visuals[index + 1:]:
                other_position = other["position"]
                assert (position["x"] + position["width"] <= other_position["x"] or
                        other_position["x"] + other_position["width"] <= position["x"] or
                        position["y"] + position["height"] <= other_position["y"] or
                        other_position["y"] + other_position["height"] <= position["y"])


@pytest.mark.parametrize("firm", ["harbor", "kestrel"])
def test_report_slicers_have_space_for_dropdown_controls(firm):
    files = unpack(build_report(model_id="11111111-1111-4111-8111-111111111111",
                                firm=firm, as_of="2026-09-04"))
    for page_name in files["definition/pages/pages.json"]["pageOrder"]:
        slicers = [document for path, document in files.items()
                   if path.startswith(f"definition/pages/{page_name}/visuals/")
                   and document["visual"]["visualType"] == "slicer"]
        assert len(slicers) == 4
        for slicer in slicers:
            assert slicer["position"]["height"] == 70
            visual = slicer["visual"]
            properties = visual["objects"]
            assert properties["data"][0]["properties"]["mode"]["expr"]["Literal"]["Value"] == "'Dropdown'"
            assert properties["header"][0]["properties"]["show"]["expr"]["Literal"]["Value"] == "false"
            title = visual["visualContainerObjects"]["title"][0]["properties"]
            assert title["show"]["expr"]["Literal"]["Value"] == "true"
            if slicer["name"] == "currency":
                assert properties["selection"][0]["properties"]["singleSelect"]["expr"]["Literal"]["Value"] == "true"
                condition = properties["general"][0]["properties"]["filter"]["Where"][0]["Condition"]
                assert condition["In"]["Values"] == [[{"Literal": {"Value": "'USD'"}}]]


@pytest.mark.parametrize("firm", ["harbor", "kestrel"])
def test_slicer_selections_sync_across_all_report_pages(firm):
    files = unpack(build_report(model_id="11111111-1111-4111-8111-111111111111",
                                firm=firm, as_of="2026-09-04"))
    pages = files["definition/pages/pages.json"]["pageOrder"]
    groups = {}
    for path, document in files.items():
        if not path.endswith("/visual.json"):
            continue
        visual = document["visual"]
        if visual["visualType"] != "slicer":
            assert "syncGroup" not in visual
            continue
        group = visual["syncGroup"]
        assert group == {"groupName": f"{firm}_{document['name']}",
                         "fieldChanges": True, "filterChanges": True}
        groups.setdefault(group["groupName"], []).append((path.split("/")[2], visual["query"]))
    assert len(groups) == 4
    for members in groups.values():
        assert {page for page, _ in members} == set(pages)
        assert len(members) == len(pages)
        assert all(query == members[0][1] for _, query in members)


def test_report_update_preserves_tenant_startup_metadata():
    model_id = "11111111-1111-4111-8111-111111111111"
    generated = build_report(model_id=model_id, firm="harbor", as_of="2026-09-04")
    original = unpack(generated)
    tenant_files = unpack(generated)
    tenant_files["definition/version.json"]["version"] = "2.0.0"
    tenant_files["definition/report.json"]["themeCollection"]["baseTheme"] = {
        "name": "TenantTheme", "type": "SharedResources"}
    tenant_files["definition/report.json"]["resourcePackages"].append({
        "name": "SharedResources", "type": "SharedResources",
        "items": [{"name": "TenantTheme", "path": "BaseThemes/TenantTheme.json", "type": "BaseTheme"}]})
    tenant_files["StaticResources/SharedResources/BaseThemes/TenantTheme.json"] = {"name": "TenantTheme"}
    tenant_files["definition/pages/overview/visuals/filter_0/visual.json"]["position"]["height"] = 75
    existing = definition_parts(tenant_files)
    updated = unpack(preserve_report_startup(generated, existing))
    for path, document in tenant_files.items():
        if path in {"definition/report.json", "definition/version.json"} or path.startswith("StaticResources/"):
            assert updated[path] == document
        else:
            assert updated[path] == original[path]
    assert unpack(generated) == original
    assert unpack(existing) == tenant_files

    ctx = Mock()
    ctx.config.data_dir = ROOT / "data"
    ctx.state.get.return_value = {"id": "existing-report"}
    ctx.ref.side_effect = lambda key: model_id if key.endswith(".model") else "existing-report"
    ctx.workspace.return_value = "existing-workspace"
    client = Mock()
    client.mutate.return_value = {"definition": existing}
    ctx.clients = {"provider": client}
    ctx.ensure_item.return_value = "existing-report"
    deploy_reports(ctx, ["harbor"])
    client.mutate.assert_called_once_with(
        "POST", "workspaces/existing-workspace/reports/existing-report/getDefinition")
    assert unpack(ctx.ensure_item.call_args.kwargs["definition"]) == updated


def test_report_update_rejects_missing_startup_metadata():
    generated = build_report(model_id="11111111-1111-4111-8111-111111111111",
                             firm="harbor", as_of="2026-09-04")
    with pytest.raises(ValueError, match="missing PBIR startup metadata"):
        preserve_report_startup(generated, {"parts": []})


def test_ontology_provisions_before_importing_bound_definition():
    contract = load_contract(ROOT / "fabric/ontology/ontology.yaml")
    ctx = Mock()
    ctx.config.ontology_path = ROOT / "fabric/ontology/ontology.yaml"
    ctx.workspace.return_value = "11111111-1111-4111-8111-111111111111"
    ctx.ref.return_value = "22222222-2222-4222-8222-222222222222"
    client = Mock()
    client.list.return_value = [{"name": table, "format": "delta"}
                                for table in required_gold_tables(contract)]
    ctx.clients = {"provider": client}
    ctx.ensure_item.return_value = "33333333-3333-4333-8333-333333333333"
    deploy_ontologies(ctx, ["harbor"])
    provision, bind = ctx.ensure_item.call_args_list
    assert "definition" not in provision.kwargs
    assert provision.kwargs == {key: value for key, value in bind.kwargs.items() if key != "definition"}
    assert provision.kwargs["key"] == "provider.analytics_harbor.ontology"
    parts = unpack(bind.kwargs["definition"])
    assert parts["definition.json"] == {}
    assert any("/DataBindings/" in path for path in parts)
    assert any("/Contextualizations/" in path for path in parts)
    assert any("/ResourceLinks/" in path for path in parts)
    for path, document in parts.items():
        if "/ResourceLinks/" in path:
            assert next(iter(document["resourceLinks"][0])) == "type"


def test_ontology_destination_does_not_change_source_bindings():
    contract = load_contract(ROOT / "fabric/ontology/ontology.yaml")
    source = Mock()
    source.config.ontology_path = ROOT / "fabric/ontology/ontology.yaml"
    source.workspace.return_value = "11111111-1111-4111-8111-111111111111"
    source.ref.return_value = "22222222-2222-4222-8222-222222222222"
    client = Mock()
    client.list.return_value = [{"name": table, "format": "delta"}
                                for table in required_gold_tables(contract)]
    source.clients = {"provider": client}
    destination = Mock()
    destination.ensure_item.return_value = "33333333-3333-4333-8333-333333333333"
    deploy_ontologies(source, ["harbor"], destination=destination)
    source.ensure_item.assert_not_called()
    assert destination.ensure_item.call_count == 2
    parts = unpack(destination.ensure_item.call_args.kwargs["definition"])
    for path, document in parts.items():
        if "/DataBindings/" in path:
            assert document["dataBindingConfiguration"]["sourceTableProperties"]["workspaceId"] == source.workspace.return_value
        if "/ResourceLinks/" in path:
            assert document["resourceLinks"][0]["workspaceId"] == source.workspace.return_value


def test_every_report_field_exists_in_semantic_model():
    contract = load_contract(ROOT / "fabric/ontology/ontology.yaml")
    schema = json.loads((ROOT / "data/schema.json").read_text())
    model_id = "11111111-1111-4111-8111-111111111111"
    model = unpack(build_semantic_model(
        contract, schema, sql_server="demo.datawarehouse.fabric.microsoft.com",
        sql_database=model_id, firm="harbor", as_of="2026-09-04"))["model.bim"]["model"]
    tables = {table["name"]: table for table in model["tables"]}
    files = unpack(build_report(model_id=model_id, firm="harbor", as_of="2026-09-04"))
    supported = {"textbox", "slicer", "card", "lineChart", "barChart", "clusteredBarChart",
                 "clusteredColumnChart", "tableEx"}
    for path, document in files.items():
        if not path.endswith("/visual.json"):
            continue
        visual = document["visual"]
        assert visual["visualType"] in supported
        for role in visual.get("query", {}).get("queryState", {}).values():
            for projection in role["projections"]:
                kind, expression = next(iter(projection["field"].items()))
                table = tables[expression["Expression"]["SourceRef"]["Entity"]]
                definitions = table["measures" if kind == "Measure" else "columns"]
                assert expression["Property"] in {definition["name"] for definition in definitions}