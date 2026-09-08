"""Explicit administrator-demo deployment, separate from partner-secured steps 5-8."""

from __future__ import annotations

import argparse
import json
from contextlib import ExitStack
from pathlib import Path

from azure.identity import AzureCliCredential

from fabric.analytics import build_semantic_model, definition_parts
from fabric.analytics_agents import build_ontology_agent
from fabric.analytics_reports import build_report, preserve_report_startup
from fabric.config import load_config
from fabric.fabric_client import FabricClient, FabricError
from fabric.ontology_adapter import build_ontology_definition, load_contract, required_gold_tables
from fabric.state import State
from fabric.steps.common import Context


class AnalyticsDestinationContext(Context):
    def workspace(self, role: str) -> str:
        if role != "provider":
            raise FabricError("Analytics destination only supports provider identity")
        return self.ref("provider.analytics_workspace")

    def folder(self, role: str) -> str:
        assignment = self.state.data["analytics_workspace_assignment"]
        workspace = self.validate_record("provider.analytics_workspace")
        if workspace.get("capacityId") != assignment["capacity_id"]:
            raise FabricError("Analytics destination capacity changed")
        folder = self.clients[role].get(
            f"workspaces/{self.workspace(role)}/folders/{assignment['folder_id']}")
        if (folder.get("displayName") != assignment["folder_name"] or
                folder.get("parentFolderId") != assignment["parent_folder_id"]):
            raise FabricError("Analytics destination folder changed")
        return folder["id"]


def deploy_models(ctx: Context, firms: list[str]) -> None:
    contract = load_contract(ctx.config.ontology_path)
    schema = json.loads((ctx.config.data_dir / "schema.json").read_text())
    manifest = json.loads((ctx.config.data_dir / "manifest.json").read_text())
    workspace = ctx.workspace("provider")
    client = ctx.clients["provider"]
    for firm in firms:
        gold_key = f"provider.gold_{firm}"
        ctx.validate_record(gold_key)
        source = client.get(f"workspaces/{workspace}/lakehouses/{ctx.ref(gold_key)}")
        endpoint = source["properties"]["sqlEndpointProperties"]
        if endpoint.get("provisioningStatus") != "Success":
            raise FabricError("Gold SQL endpoint is not ready")
        definition = build_semantic_model(
            contract, schema, sql_server=endpoint["connectionString"],
            sql_database=endpoint["id"], firm=firm, as_of=manifest["as_of"],
        )
        definition["format"] = "TMSL"
        model_id = ctx.ensure_item(
            key=f"provider.analytics_{firm}.model", role="provider", kind="SemanticModel",
            collection="semanticModels", name=f"{firm.title()} Legal Finance - Synthetic Demo",
            definition=definition,
        )
        print(json.dumps({"firm": firm, "semantic_model_id": model_id}), flush=True)


def deploy_reports(ctx: Context, firms: list[str]) -> None:
    manifest = json.loads((ctx.config.data_dir / "manifest.json").read_text())
    for firm in firms:
        model_key = f"provider.analytics_{firm}.model"
        ctx.validate_record(model_key)
        definition = build_report(model_id=ctx.ref(model_key), firm=firm, as_of=manifest["as_of"])
        report_key = f"provider.analytics_{firm}.report"
        if ctx.state.get(report_key):
            ctx.validate_record(report_key)
            existing = ctx.clients["provider"].mutate(
                "POST", f"workspaces/{ctx.workspace('provider')}/reports/{ctx.ref(report_key)}/getDefinition")
            definition = preserve_report_startup(definition, existing["definition"])
        report_id = ctx.ensure_item(
            key=report_key, role="provider", kind="Report",
            collection="reports", name=f"{firm.title()} Finance Insights - Synthetic Demo",
            definition=definition,
        )
        print(json.dumps({"firm": firm, "report_id": report_id}), flush=True)


def deploy_ontologies(ctx: Context, firms: list[str], *, destination: Context | None = None) -> None:
    destination = destination or ctx
    contract = load_contract(ctx.config.ontology_path)
    workspace = ctx.workspace("provider")
    for firm in firms:
        gold_key = f"provider.gold_{firm}"
        report_key = f"provider.analytics_{firm}.report"
        ctx.validate_record(gold_key)
        ctx.validate_record(report_key)
        lakehouse_id = ctx.ref(gold_key)
        tables = ctx.clients["provider"].list(
            f"workspaces/{workspace}/lakehouses/{lakehouse_id}/tables", collection_keys=("data",))
        available = {table["name"] for table in tables if table.get("format", "").casefold() == "delta"}
        missing = set(required_gold_tables(contract)) - available
        if missing:
            raise FabricError("Missing Gold ontology sources: " + ", ".join(sorted(missing)))
        name = f"{firm.title()}_Legal_Ontology_Synthetic_Demo"
        definition = build_ontology_definition(contract, workspace, lakehouse_id, name)
        entity_paths = [part["path"] for part in definition["parts"]
                        if part["path"].startswith("EntityTypes/") and part["path"].endswith("/definition.json")]
        definition["parts"].extend(definition_parts({
            path.replace("/definition.json", "/ResourceLinks/definition.json"): {
                "resourceLinks": [{"type": "PowerBIReport", "workspaceId": workspace,
                                   "itemId": ctx.ref(report_key)}],
            } for path in entity_paths
        }, sort_keys=False)["parts"])
        destination.ensure_item(
            key=f"provider.analytics_{firm}.ontology", role="provider", kind="Ontology",
            collection="ontologies", name=name,
        )
        ontology_id = destination.ensure_item(
            key=f"provider.analytics_{firm}.ontology", role="provider", kind="Ontology",
            collection="ontologies", name=name, definition=definition,
        )
        print(json.dumps({"firm": firm, "ontology_id": ontology_id,
                          "verified_gold_tables": len(available), "scope": "synthetic_firm_wide"}), flush=True)


def deploy_agents(ctx: Context, firms: list[str]) -> None:
    contract = load_contract(ctx.config.ontology_path)
    manifest = json.loads((ctx.config.data_dir / "manifest.json").read_text())
    for firm in firms:
        key = f"provider.analytics_{firm}.ontology"
        record = ctx.validate_record(key)
        ontology_id = ctx.ref(key)
        existing = ctx.clients["provider"].mutate(
            "POST", f"workspaces/{record['workspaceId']}/ontologies/{ontology_id}/getDefinition")["definition"]
        definition = build_ontology_agent(workspace_id=record["workspaceId"], ontology_id=ontology_id,
                                          firm=firm, as_of=manifest["as_of"], ontology_definition=existing)
        import base64
        source = json.loads(base64.b64decode(definition["parts"][-1]["payload"]))
        if {element["id"] for element in source["elements"]} != {entity["name"] for entity in contract["entities"]}:
            raise FabricError("Deployed ontology entities do not match the demo contract")
        agent_id = ctx.ensure_item(
            key=f"provider.analytics_{firm}.agent", role="provider", kind="DataAgent", collection="dataAgents",
            name=f"{firm.title()} Legal Ontology Agent - Synthetic Demo", definition=definition)
        print(json.dumps({"firm": firm, "agent_id": agent_id, "stage": "draft"}), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(".env"))
    parser.add_argument("--firm", choices=["harbor", "kestrel"], action="append")
    parser.add_argument("--stage", choices=["models", "reports", "ontologies", "agents"], default="models")
    parser.add_argument("--administrator-synthetic-demo", action="store_true", required=True,
                        help="Confirm firm-wide synthetic data; no partner RLS or sharing changes")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    config.validate()
    if not config.simulation or config.auth_mode != "azure_cli":
        parser.error("This opt-in workflow requires single-tenant simulation and Azure CLI user auth")
    state = State(config.state_path, config.demo_id)
    if 4 not in state.data["completed_steps"] or state.data["pending"]:
        raise FabricError("Complete core deployment and reconcile pending writes first")
    with ExitStack() as stack:
        credential = AzureCliCredential(tenant_id=config.identities["provider"].tenant_id)
        stack.callback(credential.close)
        client = FabricClient(
            lambda: credential.get_token("https://api.fabric.microsoft.com/.default").token,
            role="provider", operation_timeout=config.timeout,
        )
        stack.callback(client.close)
        ctx = Context(config, state, clients={"provider": client})
        with state.locked():
            state.bind({"mode": config.mode, "identities": {
                role: {"tenant_id": identity.tenant_id, "client_id": identity.client_id,
                       "capacity_id": identity.capacity_id, "workspace_id": identity.workspace_id}
                for role, identity in config.identities.items()
            }, "folder_path": list(config.folder_path)})
            ctx.validate_record("provider.workspace")
            deploy = {"models": deploy_models, "reports": deploy_reports, "ontologies": deploy_ontologies,
                      "agents": deploy_agents}[args.stage]
            if args.stage == "ontologies" and state.get("provider.analytics_workspace"):
                destination = AnalyticsDestinationContext(config, state, clients={"provider": client})
                deploy_ontologies(ctx, args.firm or ["harbor", "kestrel"], destination=destination)
            elif args.stage == "agents" and state.get("provider.analytics_workspace"):
                destination = AnalyticsDestinationContext(config, state, clients={"provider": client})
                deploy_agents(destination, args.firm or ["harbor", "kestrel"])
            else:
                deploy(ctx, args.firm or ["harbor", "kestrel"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())