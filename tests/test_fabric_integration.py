"""Offline regression coverage for the requested script and Fabric entry points."""

import importlib
import json

import httpx
import pytest

from fabric import deploy
from fabric.config import ROOT, Config, Identity, load_config
from fabric.fabric_client import FabricClient, FabricError
from fabric.ontology_adapter import required_gold_tables
from fabric.state import State
from fabric.steps.common import Context
from fabric.steps.step03_upload import TABLES
from fabric.steps.step04_notebooks import notebook_definition
from fabric.steps.step06_shortcuts import run as shortcuts

ENTRY_POINTS = (
    (1, "01_create_workspaces", "step01_workspaces"),
    (2, "02_create_lakehouses", "step02_lakehouses"),
    (3, "03_load_bronze", "step03_upload"),
    (4, "04_run_transformations", "step04_notebooks"),
    (5, "05_create_external_share", "step05_external_share"),
    (6, "06_create_shortcuts", "step06_shortcuts"),
    (7, "07_deploy_ontology", "step07_ontology"),
    (8, "08_create_data_agent", "step08_agent"),
    (99, "99_teardown", None),
)


def context(tmp_path, *, dry_run=True):
    """Build a local context without creating credentials, clients or state files."""
    cfg = Config({}, data_dir=tmp_path / "absent-data", state_path=tmp_path / "state.json")
    return Context(cfg, State(cfg.state_path, cfg.demo_id, dry_run=dry_run), dry_run=dry_run)


@pytest.mark.parametrize("number,module_name,helper_name", ENTRY_POINTS)
def test_numbered_run_delegates(number, module_name, helper_name, tmp_path, monkeypatch):
    """Every exact requested filename exposes a real context-delegating run function."""
    module = importlib.import_module("fabric.steps." + module_name)
    calls = []
    if helper_name is None:
        monkeypatch.setattr(module, "teardown", calls.append)
    else:
        assert deploy.STEP_MODULES[number] == module_name
        helper = importlib.import_module("fabric.steps." + helper_name)
        monkeypatch.setattr(helper, "run", calls.append)
    ctx = context(tmp_path)
    module.run(ctx)
    assert calls == [ctx]


@pytest.mark.parametrize("number,module_name,helper_name", ENTRY_POINTS)
def test_numbered_main_uses_shared_deploy(number, module_name, helper_name, monkeypatch):
    """Standalone invocation preserves flags and delegates to the shared CLI."""
    module = importlib.import_module("fabric.steps." + module_name)
    calls = []
    monkeypatch.setattr(deploy, "main", lambda args: calls.append(args) or 17)
    flags = ["--dry-run", "--config", "chosen.env", "--single-tenant-simulation"]
    assert module.main(flags) == 17
    assert calls == [(["--teardown"] if number == 99 else ["--step", str(number)]) + flags]


@pytest.mark.parametrize("flags", [["--step", "2"], ["--through-step", "4"], ["--teardown"]])
def test_numbered_main_cannot_override_step(flags, monkeypatch):
    """Conflicting selectors never reach the shared deployment implementation."""
    monkeypatch.setattr(deploy, "main", lambda args: pytest.fail("Conflicting selection executed"))
    with pytest.raises(SystemExit) as exc:
        deploy.step_main(1, flags)
    assert exc.value.code == 2


@pytest.mark.parametrize("number,module_name,helper_name", ENTRY_POINTS)
def test_numbered_main_dry_run_is_offline(number, module_name, helper_name, tmp_path, monkeypatch):
    """Every entry point can plan without cloud clients or local state writes."""
    ctx = context(tmp_path)
    monkeypatch.setattr(deploy, "load_config", lambda *args, **kwargs: ctx.config)
    monkeypatch.setattr(FabricClient, "__init__", lambda *args, **kwargs: pytest.fail("Cloud client created"))
    module = importlib.import_module("fabric.steps." + module_name)
    assert module.main(["--dry-run"]) == 0
    assert not ctx.config.state_path.exists()


@pytest.mark.parametrize("fail", [False, True])
def test_execute_logs_elapsed_and_redacts_errors(tmp_path, monkeypatch, fail):
    """Success completes the journal; failure logs duration without leaking exceptions."""
    ctx = context(tmp_path, dry_run=False)
    module = importlib.import_module("fabric.steps.01_create_workspaces")
    events = []
    ticks = iter((10.0, 12.5))
    monkeypatch.setattr(deploy.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(deploy, "emit", lambda event, **fields: events.append({"event": event, **fields}))

    def run(_ctx):
        """Provide a deterministic success or secret-bearing failure."""
        if fail:
            raise FabricError("private-service-body-and-secret")

    monkeypatch.setattr(module, "run", run)
    if fail:
        with pytest.raises(FabricError):
            deploy.execute(ctx, step=1)
    else:
        with ctx.state.locked():
            deploy.execute(ctx, step=1)
    assert [entry["event"] for entry in events] == ["step_started", "step_finished"]
    assert events[-1]["elapsed_seconds"] == 2.5
    assert events[-1]["status"] == ("failed" if fail else "succeeded")
    assert bool(events[-1]["error"]) is fail
    assert (1 in ctx.state.data["completed_steps"]) is not fail
    assert "private-service-body-and-secret" not in json.dumps(events)


@pytest.mark.parametrize("source", ["if broken\n", ["%run other_notebook\n"]])
def test_notebook_rejects_invalid_later_code_cells(tmp_path, source):
    """A valid parameters cell cannot hide invalid Python in a later cell."""
    path = tmp_path / "sample.ipynb"
    notebook = {"nbformat": 4, "metadata": {}, "cells": [
        {"cell_type": "code", "metadata": {"language": "python", "tags": ["parameters"]},
         "source": ["CONFIG_JSON = '{}'\n"]},
        {"cell_type": "markdown", "metadata": {"language": "markdown"}, "source": ["# Documentation"]},
        {"cell_type": "code", "metadata": {"language": "python"}, "source": source},
    ]}
    path.write_text(json.dumps(notebook), encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(SyntaxError):
        notebook_definition(path, {})
    assert path.read_bytes() == original


def test_shortcut_plan_contains_complete_gold_without_manifest_or_cloud(tmp_path):
    """Dry-run loads the real local contract and includes its derived gold tables."""
    ctx = context(tmp_path)
    expected = sorted(set(TABLES) | set(required_gold_tables(ctx.contract())))
    assert set(expected) - set(TABLES), "Regression must exercise derived gold tables"
    shortcuts(ctx)
    assert len(ctx.plans) == 2
    assert all(plan["tables"] == expected for plan in ctx.plans)
    assert not ctx.config.data_dir.exists() and not ctx.config.state_path.exists()


def test_exact_config_defaults_and_moved_files(monkeypatch):
    """Dataclass/dotenv defaults agree on exact notebook, ontology and instruction paths."""
    for key in ("NOTEBOOK01_PATH", "NOTEBOOK02_PATH", "ONTOLOGY_PATH", "AGENT_INSTRUCTIONS_PATH"):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config(ROOT / ".env.example")
    defaults = Config({})
    for field, relative in (
        ("notebook01", "fabric/notebooks/01_bronze_to_silver.ipynb"),
        ("notebook02", "fabric/notebooks/02_silver_to_gold.ipynb"),
        ("ontology_path", "fabric/ontology/ontology.yaml"),
        ("instructions_path", "agent/agent_instructions.md"),
    ):
        assert getattr(cfg, field) == getattr(defaults, field) == ROOT / relative
    assert cfg.instructions_path.is_file() and cfg.ontology_path.is_file()
    assert not (ROOT / "agent/instructions.md").exists()
    for name in ("setup.sh", "run_all.sh", "run_all.ps1"):
        assert (ROOT / "scripts" / name).is_file()
        assert not (ROOT / name).exists()


@pytest.mark.parametrize("collection_key", ["value", "tenantSettings"])
def test_tenant_settings_disabled_setting_blocks_across_pages(tmp_path, collection_key):
    """Both explicitly allowed envelopes retain pagination and disabled-setting failures."""
    requests = []

    def handler(request):
        """Serve a disabled configured setting only on the second read-only page."""
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={collection_key: [], "continuationToken": "next"})
        assert request.url.params["continuationToken"] == "next"
        return httpx.Response(200, json={collection_key: [{"settingName": "ReviewedSetting", "enabled": False}]})

    ctx = context(tmp_path)
    cfg = Config({"harbor": Identity("harbor", core_setting_names=("ReviewedSetting",),
                                     admin_attestation="Reviewed")})
    ctx.config = cfg
    api = FabricClient(lambda: "mock-token", role="harbor", transport=httpx.MockTransport(handler))
    ctx.clients["harbor"] = api
    try:
        report = deploy.tenant_settings_report(ctx, "harbor")
        assert any(row["check"] == "tenant_settings_probe" and row["status"] == "OBSERVED" for row in report)
        assert any(row["check"] == "core_tenant_setting" and row["status"] == "FAIL" and row["blocking"] for row in report)
        assert len(requests) == 2 and all(request.method == "GET" for request in requests)
    finally:
        api.close()


@pytest.mark.parametrize("body", [
    {}, {"tenantSettings": {}}, {"tenantSettings": ["not-an-object"]},
    {"value": [], "tenantSettings": []},
])
def test_tenant_settings_malformed_collections_fail_closed(body):
    """Compatibility never permits an absent, malformed or ambiguous collection."""
    api = FabricClient(lambda: "mock-token", role="harbor",
                       transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    try:
        with pytest.raises(FabricError):
            api.list("admin/tenantsettings", collection_keys=("value", "tenantSettings"))
    finally:
        api.close()


def test_alternate_collection_not_implicitly_allowed():
    """Normal item/workspace list endpoints still require the documented value field."""
    api = FabricClient(lambda: "mock-token", role="provider",
                       transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"tenantSettings": []})))
    try:
        with pytest.raises(FabricError):
            api.list("workspaces")
    finally:
        api.close()