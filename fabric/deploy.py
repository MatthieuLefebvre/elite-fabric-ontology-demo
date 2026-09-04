"""Fabric deployment CLI. Full deployment intentionally stops before known preview gaps."""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
import time
from contextlib import ExitStack
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fabric.config import Config, load_config  # noqa: E402
from fabric.fabric_client import (  # noqa: E402
    FABRIC_SCOPE,
    STORAGE_SCOPE,
    FabricClient,
    FabricError,
    emit,
)
from fabric.state import State  # noqa: E402
from fabric.steps.common import (  # noqa: E402
    AGENT_BLOCKER,
    EXTERNAL_BLOCKER,
    SECURITY_BLOCKER,
    Context,
    UnsupportedCapability,
)

STEP_MODULES = {
    1: "01_create_workspaces", 2: "02_create_lakehouses", 3: "03_load_bronze",
    4: "04_run_transformations", 5: "05_create_external_share", 6: "06_create_shortcuts",
    7: "07_deploy_ontology", 8: "08_create_data_agent",
}


def capabilities(config: Config, through_step: int = 8) -> list[dict]:
    """Report documented capabilities without treating an offline plan as live proof."""
    report = [{"check": "core_rest", "status": "SUPPORTED", "blocking": False,
               "detail": "Workspace, capacity, Lakehouse, notebook, job and OneLake APIs documented; not live proof"}]
    for number, name, message in (
        (5, "external_share", EXTERNAL_BLOCKER),
        (7, "raw_graph_security", SECURITY_BLOCKER),
        (8, "ontology_agent_source", AGENT_BLOCKER),
    ):
        applicable = number <= through_step and not (number == 5 and config.simulation)
        report.append({"check": name, "step": number,
                       "status": "BLOCKED" if applicable else "OUT_OF_SCOPE",
                       "blocking": applicable, "detail": message})
    return report


def print_preflight_table(report: list[dict]) -> None:
    """Print curated check labels and statuses, never credentials or service bodies."""
    print("ROLE       CHECK                            STATUS       BLOCKS")
    print("-" * 72)
    for row in report:
        print(f"{row.get('role', 'all'):<10} {row['check']:<32} "
              f"{row['status']:<12} {'yes' if row.get('blocking') else 'no'}")


def tenant_settings_report(ctx: Context, role: str) -> list[dict]:
    """Probe configured settings without inferring scoped effective permissions."""
    identity = ctx.config.identities[role]
    report = []
    try:
        settings = ctx.clients[role].list("admin/tenantsettings", collection_keys=("value", "tenantSettings"))
    except FabricError:
        settings = None
    if settings is None:
        report.append({"role": role, "check": "tenant_settings_probe", "status": "UNKNOWN",
                       "blocking": False, "detail": "Admin API unavailable or insufficient access; NOT a pass"})
    else:
        report.append({"role": role, "check": "tenant_settings_probe", "status": "OBSERVED",
                       "blocking": False, "detail": "Read admin settings; scoped group membership is not inferred"})
    by_name = {s.get("settingName"): s for s in settings or []}
    names = identity.core_setting_names
    all_probed = bool(names)
    explicit_disabled = False
    for name in names:
        setting = by_name.get(name)
        status = "UNKNOWN"
        if setting and setting.get("enabled") is False:
            status, explicit_disabled = "FAIL", True
        elif setting and setting.get("enabled") is True and not (
            setting.get("canSpecifySecurityGroups") or setting.get("enabledSecurityGroups")
            or setting.get("excludedSecurityGroups") or setting.get("delegateToCapacity")
            or setting.get("delegateToWorkspace") or setting.get("delegateToDomain")
        ):
            status = "PASS"
        all_probed = all_probed and status == "PASS"
        # Do not log group membership or arbitrary tenant-setting properties.
        report.append({"role": role, "check": "core_tenant_setting", "status": status,
                       "blocking": status == "FAIL"})
    # Setting-name lists are tenant/version dependent. Admin must attest that the
    # REQUIRED set, effective scope, principal membership and permissions were reviewed.
    # A successful GET alone is never a permission/create-workspace test.
    attested = bool(identity.admin_attestation.strip())
    report.append({"role": role, "check": "core_admin_authorization", 
                   "status": "ATTESTED" if attested else "UNKNOWN",
                   "blocking": explicit_disabled or not attested,
                   "detail": "Explicit admin review reference supplied (not probed)" if attested else
                   "Require *_ADMIN_ATTESTATION: reviewed SP Fabric APIs, workspace creation, scope membership and capacity assignment",
                   "configured_settings_probed": all_probed})
    return report


def preflight(ctx: Context, *, through_step: int = 8, local_inputs: bool = True) -> list[dict]:
    """Validate selected capabilities and inputs before any deployment writes."""
    report = capabilities(ctx.config, through_step)
    if ctx.dry_run:
        report.append({"check": "cloud_preflight", "status": "NOT_RUN", "blocking": False,
                       "detail": "Offline plan only: no token acquisition, cloud calls, IDs or state writes"})
        return report
    ctx.config.validate()
    for role, identity in ctx.config.identities.items():
        try:
            # These are separate credential objects even in a single-tenant simulation.
            ctx.credentials[role].get_token(FABRIC_SCOPE)
            if role == "provider" and through_step >= 3:
                ctx.credentials[role].get_token(STORAGE_SCOPE)
            report.append({"role": role, "check": "token", "status": "PASS", "blocking": False})
        except Exception:
            report.append({"role": role, "check": "token", "status": "FAIL", "blocking": True,
                           "detail": "Client credentials token acquisition failed; details suppressed"})
            continue
        try:
            capacities = ctx.clients[role].list("capacities")
            capacity = next((c for c in capacities if c.get("id") == identity.capacity_id), None)
            active = bool(capacity and capacity.get("state") == "Active")
            report.append({"role": role, "check": "capacity_access_active", "status": "PASS" if active else "FAIL",
                           "blocking": not active,
                           "detail": "GET capacities returns only capacities accessible as contributor/admin; preview SKU/region needs review"})
            ctx.clients[role].list("workspaces")
            report.append({"role": role, "check": "workspace_api", "status": "PASS", "blocking": False,
                           "detail": "Read-only access only; no create permission inferred"})
            if identity.workspace_id:
                workspace = ctx.clients[role].get(f"workspaces/{identity.workspace_id}")
                valid = workspace.get("type") == "Workspace" and workspace.get("capacityId") == identity.capacity_id
                report.append({"role": role, "check": "adopted_workspace", "status": "PASS" if valid else "FAIL",
                               "blocking": not valid})
        except FabricError:
            report.append({"role": role, "check": "capacity_workspace_api", "status": "FAIL", "blocking": True})
        report.extend(tenant_settings_report(ctx, role))
    if local_inputs and through_step >= 3:
        from fabric.steps.step03_upload import load_manifest

        try:
            load_manifest(ctx.config.data_dir)
            report.append({"check": "local_manifest", "status": "PASS", "blocking": False})
        except (OSError, ValueError, KeyError, TypeError):
            report.append({"check": "local_manifest", "status": "FAIL", "blocking": True,
                           "detail": "Generate data first; manifest/schema/firm paths/checksums must be consistent"})
    if local_inputs and through_step >= 4:
        from fabric.steps.step04_notebooks import notebook_definition

        try:
            notebook_definition(ctx.config.notebook01, {})
            notebook_definition(ctx.config.notebook02, {})
            ctx.contract()
            report.append({"check": "notebook_adapter_contract", "status": "PASS", "blocking": False})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError):
            report.append({"check": "notebook_adapter_contract", "status": "FAIL", "blocking": True,
                           "detail": "Parent must supply notebooks with first code parameters cell and load_contract(path)->dict"})
    return report


def teardown(ctx: Context) -> None:
    """Delete recorded creations only; preserve adopted workspaces and untracked children."""
    if ctx.state.data["pending"] and not ctx.dry_run:
        raise FabricError("Pending operations must be reconciled before teardown; no active job deletion")
    for key, shortcut in reversed(list(ctx.state.data.get("shortcuts", {}).items())):
        ctx.plan(0, "delete_created_shortcut", resource=key)
        if ctx.dry_run:
            continue
        client = ctx.clients[shortcut["role"]]
        path = shortcut["path"]
        response = client.request("GET", path, allow_status=(404,))
        if response.status_code != 404:
            if client.document(response).get("target", {}).get("oneLake") != shortcut["target"]:
                raise FabricError("Created shortcut was retargeted; not deleting")
            client.mutate("DELETE", path)
        del ctx.state.data["shortcuts"][key]
        ctx.state.save()
    records = list(ctx.state.data["resources"].items())
    items = [(k, r) for k, r in records if r["kind"] != "Workspace"]
    workspaces = [(k, r) for k, r in records if r["kind"] == "Workspace"]
    for key, record in [*reversed(items), *reversed(workspaces)]:
        if not record["created"]:
            ctx.plan(0, "preserve_preexisting_resource", resource=key)
            continue
        ctx.plan(0, "delete_created_resource_if_still_owned", resource=key,
                 must_be_empty=record["kind"] == "Workspace")
        if ctx.dry_run:
            continue
        client = ctx.clients[record["role"]]
        path = (f"workspaces/{record['id']}" if record["kind"] == "Workspace" else
                f"workspaces/{record['workspace_id']}/items/{record['id']}")
        response = client.request("GET", path, allow_status=(404,))
        if response.status_code == 404:
            ctx.state.removed(key)
            continue
        ctx.validate_record(key)
        if record["kind"] == "Workspace" and client.list(path + "/items"):
            # Includes non-demo artifacts and automatically generated SQL endpoints.
            # Never cascade-delete them. Let the owner review or retry after cleanup.
            ctx.plan(0, "preserve_nonempty_workspace", resource=key)
            continue
        ctx.state.begin("delete:" + key, action="delete", role=record["role"])
        client.mutate("DELETE", path,
                  on_response=lambda response: ctx.state.receipt("delete:" + key, response))
        ctx.state.removed(key)


def execute(ctx: Context, *, step: int | None = None, through_step: int = 8) -> None:
    """Run numbered entry points with prerequisites and sanitized per-step timing."""
    selected = [step] if step is not None else list(range(1, through_step + 1))
    if not ctx.dry_run and ctx.state.data["pending"]:
        raise FabricError("Unresolved write intents exist; reconcile before resuming")
    if step and not ctx.dry_run:
        # External step 05 never completes in real mode. Step 06 can inspect manual
        # acceptance independently, but cannot certify an unverified source binding.
        required = set(range(1, min(step, 5)))
        if step >= 7:
            required.add(6)
        if step >= 8:
            required.add(7)
        if not required.issubset(set(ctx.state.data["completed_steps"])):
            raise FabricError("Standalone step prerequisites not completed; run earlier numbered steps")
    for number in selected:
        started = time.monotonic()
        emit("step_started", step=number, dry_run=ctx.dry_run)
        try:
            module = importlib.import_module("fabric.steps." + STEP_MODULES[number])
            module.run(ctx)
            if not ctx.dry_run:
                ctx.state.complete(number)
        except Exception:
            # Exception text can contain service bodies, paths or secrets.
            emit("step_finished", step=number, dry_run=ctx.dry_run, status="failed",
                 elapsed_seconds=time.monotonic() - started,
                 error="Step failed; details suppressed; inspect saved intents")
            raise
        else:
            emit("step_finished", step=number, dry_run=ctx.dry_run, status="succeeded",
                 elapsed_seconds=time.monotonic() - started, error=None)


def parser() -> argparse.ArgumentParser:
    """Build the shared CLI parser for orchestration and standalone steps."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, help="Explicit dotenv file; process environment overrides")
    result.add_argument("--dry-run", action="store_true", help="Offline plan; no writes or auth")
    topology = result.add_mutually_exclusive_group()
    topology.add_argument("--single-tenant-simulation", action="store_true")
    topology.add_argument("--allow-shared-consumer-tenant", action="store_true")
    selection = result.add_mutually_exclusive_group()
    selection.add_argument("--step", type=int, choices=range(1, 9))
    selection.add_argument("--through-step", type=int, choices=range(1, 9))
    result.add_argument("--teardown", action="store_true")
    result.add_argument("--preflight-only", action="store_true")
    return result


def step_main(number: int, argv: list[str] | None = None) -> int:
    """Delegate a fixed standalone step to the safety-checked deployment CLI."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    cli = parser()
    args = cli.parse_args(arguments)
    if args.step is not None or args.through_step is not None or args.teardown:
        cli.error("Numbered entry points select their own step; omit step/teardown selectors")
    if number == 99:
        return main(["--teardown", *arguments])
    if number not in STEP_MODULES:
        cli.error("Unknown standalone step")
    return main(["--step", str(number), *arguments])


def main(argv: list[str] | None = None, *, preflight_only: bool = False) -> int:
    """Apply preflight, identity isolation and journal locking to every CLI run."""
    args = parser().parse_args(argv)
    if args.teardown and (args.step or args.through_step or args.preflight_only or preflight_only):
        parser().error("--teardown cannot be combined with step/preflight selection")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Third-party diagnostic messages can include URLs/request bodies. Our logger
    # emits only curated JSON metadata; never enable SDK/httpx debug for this workflow.
    for logger in ("azure", "httpx", "httpcore"):
        logging.getLogger(logger).setLevel(logging.CRITICAL)
    try:
        config = load_config(args.config, simulation=args.single_tenant_simulation,
                             shared_consumers=args.allow_shared_consumer_tenant)
        state = State(config.state_path, config.demo_id, dry_run=args.dry_run)
        ctx = Context(config, state, dry_run=args.dry_run)
        through = args.through_step or args.step or 8
        with ExitStack() as stack:
            if not args.dry_run:
                from azure.identity import ClientSecretCredential

                missing = [
                    {"role": role, "check": key, "status": "FAIL", "blocking": True}
                    for role, identity in config.identities.items()
                    for key in ("tenant_id", "client_id", "client_secret", "capacity_id")
                    if not getattr(identity, key)
                ]
                if missing:
                    print_preflight_table(missing)
                    raise UnsupportedCapability(
                        "CONFIGURATION_REQUIRED: fill the named per-tenant settings privately; no resources created"
                    )
                config.validate()
                for role, identity in config.identities.items():
                    credential = ClientSecretCredential(
                        tenant_id=identity.tenant_id, client_id=identity.client_id,
                        client_secret=identity.client_secret, logging_enable=False,
                        connection_timeout=15, read_timeout=30, retry_total=2,
                    )
                    stack.callback(credential.close)
                    ctx.credentials[role] = credential
                    client = FabricClient(
                        lambda credential=credential: credential.get_token(FABRIC_SCOPE).token,
                        role=role, operation_timeout=config.timeout,
                    )
                    stack.callback(client.close)
                    ctx.clients[role] = client
            emit("topology", mode=config.mode, workspace_count=len(config.workspace_roles))
            if not args.teardown:
                report = preflight(ctx, through_step=through)
                for entry in report:
                    emit("preflight", **entry)
                if preflight_only or args.preflight_only:
                    print_preflight_table(report)
                blocked = any(r["blocking"] for r in report)
                if preflight_only or args.preflight_only:
                    return 0 if args.dry_run or not blocked else 2
                if blocked and not args.dry_run:
                    raise UnsupportedCapability("PREFLIGHT_BLOCKED: no resources created; select --through-step 4 for core preparation only")
            with state.locked():
                if not args.dry_run:
                    state.bind({"mode": config.mode, "identities": {
                        r: {"tenant_id": i.tenant_id, "client_id": i.client_id,
                            "capacity_id": i.capacity_id, "workspace_id": i.workspace_id}
                        for r, i in config.identities.items()
                    }})
                if args.teardown:
                    teardown(ctx)
                else:
                    execute(ctx, step=args.step, through_step=through)
            emit("finished", dry_run=args.dry_run, teardown=args.teardown,
                 through_step=through, live_end_to_end_certified=False)
        return 0
    except (FabricError, UnsupportedCapability) as exc:
        emit("blocked", error=str(exc))
        return 2
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        emit("blocked", error="Invalid local configuration, state or contract; details suppressed to protect secrets")
        return 2
    except Exception:
        emit("failed", error="Unexpected failure; details suppressed to protect secrets; inspect saved intents")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())