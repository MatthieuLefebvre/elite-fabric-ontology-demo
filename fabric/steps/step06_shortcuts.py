"""06: exact-target, no-overwrite simulation shortcuts; read-only external inspection."""

from fabric.config import FIRMS
from fabric.fabric_client import FabricError
from fabric.ontology_adapter import required_gold_tables
from fabric.steps.common import EXTERNAL_BLOCKER, Context, UnsupportedCapability
from fabric.steps.step03_upload import TABLES, load_manifest


def run(ctx: Context) -> None:
    """Plan or ensure raw and derived gold shortcuts without weakening live gates."""
    raw_tables = TABLES if ctx.dry_run else load_manifest(ctx.config.data_dir)[1]["tables"]
    tables = sorted(set(raw_tables) | set(required_gold_tables(ctx.contract())))
    for firm in FIRMS:
        workspace, item = ctx.workspace(firm), ctx.ref(firm + ".lakehouse")
        target = {"workspaceId": ctx.workspace("provider"),
                  "itemId": ctx.ref("provider.gold_" + firm)}
        ctx.plan(6, "ensure_same_tenant_shortcuts" if ctx.config.simulation else "inspect_external_shortcuts",
                 role=firm, workspace=workspace, lakehouse=item, tables=list(tables),
                 target=target, security="NOT partner-level security", conflict_policy="Abort")
        if ctx.dry_run:
            continue
        ctx.require_lakehouse(firm + ".lakehouse")
        client = ctx.clients[firm]
        for table in tables:
            path = f"workspaces/{workspace}/items/{item}/shortcuts"
            expected = {**target, "path": "Tables/" + table}
            response = client.request("GET", f"{path}/Tables/{table}", allow_status=(404,))
            if not ctx.config.simulation:
                if response.status_code == 404:
                    raise UnsupportedCapability("ACCEPTED_EXTERNAL_SHORTCUT_MISSING: " + EXTERNAL_BLOCKER)
                actual = client.document(response).get("target", {})
                if actual.get("type") != "ExternalDataShare" or not actual.get("externalDataShare", {}).get("connectionId"):
                    raise UnsupportedCapability("EXPECTED_EXTERNAL_SHARE_TARGET: not an accepted external shortcut")
                # An opaque connection ID cannot prove source workspace/lakehouse/firm/table.
                continue
            if response.status_code == 200:
                actual = client.document(response)
                if actual.get("name") != table or actual.get("path") != "Tables" or actual.get("target", {}).get("oneLake") != expected:
                    raise FabricError("Shortcut conflicts with desired firm target; refusing overwrite")
                continue
            intent = f"shortcut:{firm}:{table}"
            ctx.state.begin(intent, action="createShortcut", role=firm)
            client.mutate("POST", path, {"path": "Tables", "name": table,
                                        "target": {"oneLake": expected}},
                          on_response=lambda response: ctx.state.receipt(intent, response))
            ctx.state.shortcut(intent, role=firm, path=f"{path}/Tables/{table}", target=expected)
    if not ctx.config.simulation and not ctx.dry_run:
        raise UnsupportedCapability("EXTERNAL_SOURCE_BINDING_UNVERIFIABLE: " + EXTERNAL_BLOCKER)