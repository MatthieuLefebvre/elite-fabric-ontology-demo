"""Offline orchestration, ownership, parameter contract and preflight regression tests."""

import base64
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from fabric.config import Config, Identity
from fabric.deploy import capabilities, execute, main, preflight, teardown
from fabric.fabric_client import FabricClient, FabricError
from fabric.ontology_adapter import required_gold_tables
from fabric.state import State
from fabric.steps.common import Context, UnsupportedCapability
from fabric.steps.step01_workspaces import run as workspaces
from fabric.steps.step03_upload import load_manifest
from fabric.steps.step04_notebooks import notebook_definition
from fabric.steps.step06_shortcuts import run as shortcuts
from fabric.steps.step07_ontology import run as ontology

WS = "11111111-1111-4111-8111-111111111111"
ITEM = "22222222-2222-4222-8222-222222222222"
CAP = "33333333-3333-4333-8333-333333333333"
TENANTS = {"provider": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
           "harbor": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
           "kestrel": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"}


def config(tmp_path, *, simulation=False, attested=True):
    identities = {
        role: Identity(role, TENANTS["provider"] if simulation else tenant,
                       ITEM, "test-secret", CAP,
                       admin_attestation="reviewed under TEST-123" if attested else "")
        for role, tenant in TENANTS.items()
    }
    return Config(identities, simulation=simulation, data_dir=tmp_path / "data",
                  state_path=tmp_path / "private" / "state.json")


def mock_client(handler, role="provider"):
    return FabricClient(lambda: "mock-token-" + role, role=role,
                        transport=httpx.MockTransport(handler))


def seed_workspace(state, *, created=False):
    return state.record("provider.workspace", resource_id=WS, role="provider",
                        kind="Workspace", name="elite_demo_provider", created=created)


def seed_item(state, *, created=False):
    return state.record("provider.bronze", resource_id=ITEM, role="provider",
                        kind="Lakehouse", name="bronze", created=created, workspace_id=WS)


def test_simulation_exactly_two_workspaces_two_separate_firm_lakehouses(tmp_path):
    cfg = config(tmp_path, simulation=True)
    cfg.validate()
    ctx = Context(cfg, State(cfg.state_path, cfg.demo_id, dry_run=True), dry_run=True)
    execute(ctx)
    ws = [p for p in ctx.plans if p["action"] == "ensure_workspace"]
    assert [p["name"] for p in ws] == ["elite_demo_provider", "elite_demo_consumer"]
    houses = [p for p in ctx.plans if p["action"] == "ensure_lakehouse" and p["role"] != "provider"]
    assert len(houses) == 2
    assert houses[0]["workspace"] == houses[1]["workspace"]
    assert houses[0]["name"] != houses[1]["name"]
    assert {p["step"] for p in ctx.plans} == set(range(1, 9))
    assert not cfg.state_path.parent.exists()
    assert not ctx.state.data["resources"]


def test_dry_cli_does_not_create_clients_or_write_state(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr("fabric.deploy.load_config", lambda *a, **kw: cfg)
    monkeypatch.setattr(FabricClient, "__init__", lambda *a, **kw: pytest.fail("Offline CLI created a client"))
    assert main(["--dry-run"]) == 0
    assert not cfg.state_path.parent.exists()


def test_workspace_live_request_capacity_and_three_ids(tmp_path):
    cfg = config(tmp_path)
    state = State(cfg.state_path, cfg.demo_id)
    calls = []
    ids = dict(zip(TENANTS, (WS, ITEM, CAP)))

    def api_for(role):
        def handler(request):
            calls.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"value": []})
            body = json.loads(request.content)
            assert body["capacityId"] == CAP
            assert body["displayName"] == "elite_demo_" + role
            assert body["description"] == state.marker(role + ".workspace")
            return httpx.Response(201, json={**body, "id": ids[role], "type": "Workspace"})
        return mock_client(handler, role)

    clients = {role: api_for(role) for role in TENANTS}
    try:
        with state.locked():
            workspaces(Context(cfg, state, clients=clients))
        assert len([r for r in calls if r.method == "POST"]) == 3
        assert all(r["created"] for r in state.data["resources"].values())
        assert len({r["id"] for r in state.data["resources"].values()}) == 3
    finally:
        for api in clients.values():
            api.close()


def test_normal_three_workspaces_and_tenants(tmp_path):
    cfg = config(tmp_path)
    cfg.validate()
    ctx = Context(cfg, State(cfg.state_path, cfg.demo_id, dry_run=True), dry_run=True)
    workspaces(ctx)
    assert len(ctx.plans) == 3
    identities = {r: replace(i, tenant_id=TENANTS["provider"]) for r, i in cfg.identities.items()}
    with pytest.raises(ValueError, match="three tenants"):
        replace(cfg, identities=identities).validate()


def test_shared_consumers_require_explicit_mode(tmp_path):
    cfg = config(tmp_path)
    identities = dict(cfg.identities)
    identities["kestrel"] = replace(identities["kestrel"], tenant_id=TENANTS["harbor"])
    with pytest.raises(ValueError):
        replace(cfg, identities=identities).validate()
    shared = replace(cfg, identities=identities, shared_consumers=True)
    shared.validate()
    assert len(shared.workspace_roles) == 3
    assert "WORKSPACE_ISOLATION_ONLY" in shared.mode


def test_atomic_journal_ownership_and_pending(tmp_path):
    cfg = config(tmp_path)
    state = State(cfg.state_path, cfg.demo_id)
    with state.locked():
        state.begin("provider.workspace", action="create", role="provider")
        with pytest.raises(FabricError, match="Unresolved"):
            state.begin("provider.workspace", action="create", role="provider")
        seed_workspace(state)
        assert not state.data["pending"]
        with pytest.raises(FabricError, match="ownership"):
            seed_workspace(state, created=True)
    reloaded = State(cfg.state_path, cfg.demo_id)
    assert reloaded.get("provider.workspace")["created"] is False
    assert not list(cfg.state_path.parent.glob("*.lock"))
    assert list(cfg.state_path.parent.iterdir()) == [cfg.state_path]


def test_state_lock_and_fake_id_protection(tmp_path):
    cfg = config(tmp_path)
    state = State(cfg.state_path, cfg.demo_id)
    with state.locked():
        with pytest.raises(FabricError, match="lock exists"):
            with State(cfg.state_path, cfg.demo_id).locked():
                pytest.fail("Second lock acquired")
        with pytest.raises(FabricError, match="live UUID"):
            state.record("fake", resource_id="${fake.id}", role="provider", kind="Workspace",
                         name="fake", created=True)


def test_reuse_validates_type_marker_and_never_claims_creation(tmp_path):
    cfg = config(tmp_path)
    state = State(cfg.state_path, cfg.demo_id)
    calls = []
    item = {"id": ITEM, "type": "Lakehouse", "displayName": "bronze",
            "description": state.marker("provider.bronze")}

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"value": [item]})
        return httpx.Response(200, json=item)

    api = mock_client(handler)
    ctx = Context(cfg, state, clients={"provider": api})
    try:
        with state.locked():
            seed_workspace(state)
            assert ctx.ensure_item(key="provider.bronze", role="provider", kind="Lakehouse",
                                   collection="lakehouses", name="bronze") == ITEM
            assert state.get("provider.bronze")["created"] is False
        assert all(r.method == "GET" for r in calls)
    finally:
        api.close()


@pytest.mark.parametrize("kind,description", [("Notebook", "owned"), ("Lakehouse", "someone else's")])
def test_unowned_or_wrong_type_name_collision_no_writes(tmp_path, kind, description):
    cfg = config(tmp_path)
    state = State(cfg.state_path, cfg.demo_id)
    calls = []
    item = {"id": ITEM, "type": kind, "displayName": "bronze", "description": description}

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"value": [item]} if request.url.path.endswith("/items") else item)

    api = mock_client(handler)
    try:
        with state.locked():
            seed_workspace(state)
            ctx = Context(cfg, state, clients={"provider": api})
            with pytest.raises(FabricError, match="ownership"):
                ctx.ensure_item(key="provider.bronze", role="provider", kind="Lakehouse",
                                collection="lakehouses", name="bronze")
        assert all(r.method == "GET" for r in calls)
        assert state.get("provider.bronze") is None
    finally:
        api.close()


def test_create_ambiguous_result_leaves_intent_blocks_resume(tmp_path):
    cfg = config(tmp_path)
    state = State(cfg.state_path, cfg.demo_id)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"value": []}) if request.method == "GET" else httpx.Response(503)

    api = mock_client(handler)
    ctx = Context(cfg, state, clients={"provider": api})
    try:
        with state.locked():
            seed_workspace(state)
            with pytest.raises(FabricError):
                ctx.ensure_item(key="provider.bronze", role="provider", kind="Lakehouse",
                                collection="lakehouses", name="bronze")
            with pytest.raises(FabricError, match="Unresolved"):
                execute(ctx, step=2)
        assert len([r for r in calls if r.method == "POST"]) == 1
        assert "provider.bronze" in State(cfg.state_path, cfg.demo_id).data["pending"]
    finally:
        api.close()


def test_teardown_never_deletes_adopted_workspace_or_non_demo_children(tmp_path):
    cfg = config(tmp_path)
    state = State(cfg.state_path, cfg.demo_id)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"value": [{"id": ITEM, "type": "Notebook", "displayName": "not-demo"}]}
                              if request.url.path.endswith("/items") else
                              {"id": WS, "type": "Workspace", "displayName": "elite_demo_provider",
                               "description": state.marker("provider.workspace")})

    api = mock_client(handler)
    try:
        with state.locked():
            seed_workspace(state, created=True)
            ctx = Context(cfg, state, clients={"provider": api})
            teardown(ctx)
            assert all(r.method == "GET" for r in calls)
            assert state.get("provider.workspace")
            state.data["resources"]["provider.workspace"]["created"] = False
            calls.clear()
            teardown(ctx)
            assert not calls
    finally:
        api.close()


def test_teardown_owned_item_only(tmp_path):
    cfg = config(tmp_path)
    state = State(cfg.state_path, cfg.demo_id)
    calls = []
    item = {"id": ITEM, "type": "Lakehouse", "displayName": "bronze",
            "description": state.marker("provider.bronze")}

    def handler(request):
        calls.append(request)
        return httpx.Response(204) if request.method == "DELETE" else httpx.Response(200, json=item)

    api = mock_client(handler)
    try:
        with state.locked():
            seed_workspace(state)
            seed_item(state, created=True)
            teardown(Context(cfg, state, clients={"provider": api}))
        deletes = [r for r in calls if r.method == "DELETE"]
        assert len(deletes) == 1 and str(deletes[0].url).endswith("/items/" + ITEM)
        assert state.get("provider.workspace") and not state.get("provider.bronze")
    finally:
        api.close()


def write_test_notebook(path, *, tags=None):
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "dependencies": {"lakehouse": {"default_lakehouse": "stale"}}}, "cells": [
        {"cell_type": "markdown", "metadata": {"language": "markdown"}, "source": ["# Test"]},
        {"cell_type": "code", "metadata": {"language": "python", "tags": tags if tags is not None else ["parameters"]},
         "source": ["CONFIG_JSON = '{}'\n"], "outputs": [], "execution_count": None},
        {"cell_type": "code", "metadata": {"language": "python"}, "source": ["import json\n"],
         "outputs": [{"text": "stale output"}], "execution_count": 7},
    ]}
    path.write_text(json.dumps(notebook), encoding="utf-8")


def test_notebook_injection_string_roundtrip_no_file_mutation(tmp_path):
    path = tmp_path / "template.ipynb"
    write_test_notebook(path)
    original = path.read_bytes()
    config_value = {"firm_slug": "harbor", "quoted": "apostrophe ' slash \\ newline\n",
                    "source_workspace_id": WS, "target_lakehouse_id": ITEM, "tables": ["matters"]}
    definition = notebook_definition(path, config_value)
    assert definition["format"] == "ipynb"
    notebook = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
    namespace = {}
    exec("".join(notebook["cells"][1]["source"]), namespace)
    assert json.loads(namespace["CONFIG_JSON"]) == config_value
    assert notebook["cells"][2]["outputs"] == []
    assert "lakehouse" not in notebook["metadata"]["dependencies"]
    assert path.read_bytes() == original


def test_notebook_parameter_assignment_gets_upload_tag(tmp_path):
    """Source templates need no metadata editing; upload adds the verified cell tag."""
    path = tmp_path / "template.ipynb"
    write_test_notebook(path, tags=[])
    original = path.read_bytes()
    definition = notebook_definition(path, {})
    uploaded = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
    assert "parameters" in uploaded["cells"][1]["metadata"]["tags"]
    assert path.read_bytes() == original


def make_manifest(root):
    root.mkdir()
    schema = {"tables": {"matters": {"spark_schema": {"type": "struct", "fields": []}}}}
    schema_bytes = json.dumps(schema).encode()
    (root / "schema.json").write_bytes(schema_bytes)
    entries = []
    for firm in ("harbor", "kestrel"):
        (root / firm).mkdir()
        (root / firm / "matters.parquet").write_bytes(b"synthetic-fixture")
        entries.append({"path": f"{firm}/matters.parquet", "table": "matters", "firm_id": f"{firm}_f001",
                        "row_count": 1, "sha256": hashlib.sha256(b"synthetic-fixture").hexdigest()})
    manifest = {"schema_version": 1, "schema_file": "schema.json", "as_of": "2026-09-04",
                "schema_sha256": hashlib.sha256(schema_bytes).hexdigest(), "files": entries}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_manifest_exact_members_checksums_no_golden_upload(tmp_path):
    root = tmp_path / "data"
    make_manifest(root)
    _, _, uploads = load_manifest(root)
    assert [name for _, name in uploads] == ["harbor/matters.parquet", "kestrel/matters.parquet", "schema.json", "manifest.json"]
    (root / "harbor/matters.parquet").write_bytes(b"corruption")
    with pytest.raises(ValueError, match="checksum"):
        load_manifest(root)


def test_manifest_rejects_traversal(tmp_path):
    root = tmp_path / "data"
    manifest = make_manifest(root)
    manifest["files"][0]["path"] = "../outside.parquet"
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(root)


def test_core_preflight_read_only_admin_unknown_not_pass(tmp_path):
    cfg = config(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/tenantsettings"):
            return httpx.Response(403)
        return httpx.Response(200, json={"value": [{"id": CAP, "state": "Active"}]
                              if request.url.path.endswith("/capacities") else []})

    clients = {r: mock_client(handler, r) for r in TENANTS}
    ctx = Context(cfg, State(cfg.state_path, cfg.demo_id), clients=clients,
                  credentials={r: SimpleNamespace(get_token=lambda scope: "mock") for r in TENANTS})
    try:
        report = preflight(ctx, through_step=4, local_inputs=False)
        assert not any(r["blocking"] for r in report)
        assert {r["status"] for r in report if r["check"] == "tenant_settings_probe"} == {"UNKNOWN"}
        assert {r["status"] for r in report if r["check"] == "core_admin_authorization"} == {"ATTESTED"}
        assert all(r.method == "GET" for r in calls)
        assert not cfg.state_path.exists()
        full = capabilities(cfg)
        assert {r["check"] for r in full if r["blocking"]} == {"external_share", "raw_graph_security", "ontology_agent_source"}
    finally:
        for api in clients.values():
            api.close()


def test_unattested_core_preflight_blocks(tmp_path):
    cfg = config(tmp_path, attested=False)
    clients = {r: mock_client(lambda req: httpx.Response(403), r) for r in TENANTS}
    ctx = Context(cfg, State(cfg.state_path, cfg.demo_id), clients=clients,
                  credentials={r: SimpleNamespace(get_token=lambda scope: "mock") for r in TENANTS})
    try:
        assert any(r["blocking"] for r in preflight(ctx, through_step=2))
    finally:
        for api in clients.values():
            api.close()


def test_raw_graph_security_gate_before_adapter(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    ctx = Context(cfg, State(cfg.state_path, cfg.demo_id))
    monkeypatch.setattr(ctx, "workspace", lambda role: WS)
    monkeypatch.setattr(ctx, "ref", lambda key: ITEM)
    monkeypatch.setattr(ctx, "adapter", lambda: pytest.fail("Adapter must not run before security gate"))
    with pytest.raises(UnsupportedCapability, match="RAW_GRAPH_SECURITY"):
        ontology(ctx)


def test_shortcut_conflict_no_overwrite_and_consumer_identity(tmp_path, monkeypatch):
    cfg = config(tmp_path, simulation=True)
    make_manifest(cfg.data_dir)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"path": "Tables", "name": "matters", "target": {
            "oneLake": {"workspaceId": WS, "itemId": ITEM, "path": "Tables/WRONG_FIRM"}}})

    api = mock_client(handler, "harbor")
    ctx = Context(cfg, State(cfg.state_path, cfg.demo_id), clients={"harbor": api})
    monkeypatch.setattr(ctx, "workspace", lambda role: WS)
    monkeypatch.setattr(ctx, "ref", lambda key: ITEM)
    monkeypatch.setattr(ctx, "require_lakehouse", lambda key: {})
    try:
        with pytest.raises(FabricError, match="conflicts"):
            shortcuts(ctx)
        assert all(r.method == "GET" for r in calls)
        assert calls[0].headers["Authorization"] == "Bearer mock-token-harbor"
    finally:
        api.close()


def test_shortcut_create_contract_journal_and_separate_firm_targets(tmp_path, monkeypatch):
    """Create and journal every raw/derived gold shortcut with firm-specific targets."""
    cfg = config(tmp_path, simulation=True)
    make_manifest(cfg.data_dir)
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        assert body["path"] == "Tables"
        assert body["target"]["oneLake"]["path"] == "Tables/" + body["name"]
        return httpx.Response(201, json=body)

    clients = {r: mock_client(handler, r) for r in ("harbor", "kestrel")}
    state = State(cfg.state_path, cfg.demo_id)
    ctx = Context(cfg, state, clients=clients)
    expected_tables = set(required_gold_tables(ctx.contract())) | {"matters"}
    monkeypatch.setattr(ctx, "workspace", lambda role: WS)
    monkeypatch.setattr(ctx, "ref", lambda key: CAP if key.endswith("gold_kestrel") else ITEM)
    monkeypatch.setattr(ctx, "require_lakehouse", lambda key: {})
    try:
        with state.locked():
            shortcuts(ctx)
        bodies = [json.loads(r.content) for r in calls if r.method == "POST"]
        assert len(bodies) == 2 * len(expected_tables)
        for target_id in (ITEM, CAP):
            assert {body["name"] for body in bodies
                if body["target"]["oneLake"]["itemId"] == target_id} == expected_tables
        assert len(state.data["shortcuts"]) == len(bodies) and not state.data["pending"]
    finally:
        for api in clients.values():
            api.close()


def test_external_shortcut_presence_not_source_binding_proof(tmp_path, monkeypatch):
    """Even every required external shortcut cannot prove an opaque source binding."""
    cfg = config(tmp_path)
    make_manifest(cfg.data_dir)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"name": "matters", "path": "Tables", "target": {
            "type": "ExternalDataShare", "externalDataShare": {"connectionId": CAP}}})

    clients = {r: mock_client(handler, r) for r in ("harbor", "kestrel")}
    ctx = Context(cfg, State(cfg.state_path, cfg.demo_id), clients=clients)
    monkeypatch.setattr(ctx, "workspace", lambda role: WS)
    monkeypatch.setattr(ctx, "ref", lambda key: ITEM)
    monkeypatch.setattr(ctx, "require_lakehouse", lambda key: {})
    try:
        with pytest.raises(UnsupportedCapability, match="SOURCE_BINDING_UNVERIFIABLE"):
            shortcuts(ctx)
        expected_tables = set(required_gold_tables(ctx.contract())) | {"matters"}
        assert len(calls) == 2 * len(expected_tables) and all(r.method == "GET" for r in calls)
    finally:
        for api in clients.values():
            api.close()