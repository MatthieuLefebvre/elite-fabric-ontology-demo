"""Tenant-observed ontology datasource bindings for synthetic draft agents."""

import base64
import json

import pytest

from fabric.analytics import definition_parts
from fabric.analytics_agents import build_ontology_agent


@pytest.mark.parametrize("firm", ["harbor", "kestrel"])
def test_agent_binds_only_matching_ontology_as_draft(firm):
    workspace = "11111111-1111-4111-8111-111111111111"
    ontology = "22222222-2222-4222-8222-222222222222"
    definition = build_ontology_agent(
        workspace_id=workspace, ontology_id=ontology, firm=firm, as_of="2026-09-04",
        ontology_definition=definition_parts({"EntityTypes/123/definition.json": {
            "id": "123", "name": "Matter", "properties": [{"name": "matter_id"}, {"name": "currency"}]}}))
    files = {part["path"]: json.loads(base64.b64decode(part["payload"])) for part in definition["parts"]}
    assert len(files) == 3
    assert not any("published" in path or "publish_info" in path for path in files)
    source = next(document for path, document in files.items() if path.endswith("datasource.json"))
    assert source["type"] == "ontology"
    assert source["artifactId"] == ontology and source["workspaceId"] == workspace
    assert source["elements"] == [{"id": "Matter", "is_selected": True, "display_name": "Matter",
                                   "type": "ontology.entity", "description": "matter_id,currency", "children": []}]
    instructions = files["Files/Config/draft/stage_config.json"]["aiInstructions"]
    assert firm.title() in instructions and "2026-09-04" in instructions
    assert "never combine currencies" in instructions and "not partner RLS" in instructions


def test_agent_rejects_empty_ontology():
    with pytest.raises(ValueError, match="empty ontology"):
        build_ontology_agent(workspace_id="11111111-1111-4111-8111-111111111111",
                             ontology_id="22222222-2222-4222-8222-222222222222",
                             firm="harbor", as_of="2026-09-04", ontology_definition={"parts": []})