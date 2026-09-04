"""Shared resource operations; step modules use only this tenant-aware context."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field

from fabric.config import Config
from fabric.fabric_client import FabricClient, FabricError, emit, live_id
from fabric.state import State


class UnsupportedCapability(ValueError):
    """A named safety/capability obligation, not successful completion."""


EXTERNAL_BLOCKER = (
    "EXTERNAL_SHARE_MANUAL: provider must create one invite per firm for that firm's "
    "gold Tables only; recipient must accept into its own consumer lakehouse. No verified "
    "public create/accept contract. GET shortcut exposes connectionId only, not source binding."
)
SECURITY_BLOCKER = (
    "RAW_GRAPH_SECURITY_VALIDATION_REQUIRED: do not bind confidential raw gold tables. "
    "An access relationship is not RLS. Independently validate deny-overrides, identity "
    "propagation, direct OneLake/SQL/graph access and aggregate non-interference for the "
    "delegated partner; implement an enforceable, verified binding gate before step 07."
)
AGENT_BLOCKER = (
    "ONTOLOGY_DATASOURCE_UNSUPPORTED: the documented DataAgent datasource discriminator "
    "does not include ontology. Do not substitute graph with an ontology ID or publish an empty agent."
)


@dataclass
class Context:
    config: Config
    state: State
    clients: dict[str, FabricClient] = field(default_factory=dict)
    credentials: dict = field(default_factory=dict, repr=False)
    dry_run: bool = False
    plans: list[dict] = field(default_factory=list)

    def plan(self, step: int, action: str, **details: object) -> None:
        entry = {"step": step, "action": action, **details}
        self.plans.append(entry)
        emit("plan" if self.dry_run else "progress", **entry)

    def ref(self, key: str) -> str:
        if self.dry_run:
            return "${" + key + ".id}"
        record = self.state.get(key)
        if not record:
            raise FabricError(f"Missing prerequisite resource {key}; run its earlier step")
        return live_id(record["id"])

    def workspace(self, role: str) -> str:
        return self.ref(self.config.workspace_role(role) + ".workspace")

    def adapter(self):
        try:
            return importlib.import_module("fabric.ontology_adapter")
        except ImportError:
            raise UnsupportedCapability("ONTOLOGY_ADAPTER_REQUIRED: parent must supply fabric.ontology_adapter") from None

    def contract(self) -> dict:
        result = self.adapter().load_contract(self.config.ontology_path)
        if not isinstance(result, dict):
            raise ValueError("load_contract(path) must return an expanded JSON-compatible dict")
        json.dumps(result, allow_nan=False)
        return result

    def validate_record(self, key: str, *, role: str | None = None) -> dict:
        record = self.state.get(key)
        if not record:
            raise FabricError(f"Missing prerequisite resource {key}")
        client = self.clients[role or record["role"]]
        path = (f"workspaces/{live_id(record['id'])}" if record["kind"] == "Workspace" else
                f"workspaces/{live_id(record['workspace_id'])}/items/{live_id(record['id'])}")
        item = client.get(path)
        if item.get("id") != record["id"] or item.get("type") != record["kind"]:
            raise FabricError(f"Resource identity/type changed: {key}")
        if item.get("displayName") != record["name"]:
            raise FabricError(f"Resource renamed; explicit reconciliation required: {key}")
        if record["kind"] != "Workspace" or record["created"]:
            if record["marker"] not in item.get("description", ""):
                raise FabricError(f"Ownership marker missing: {key}")
        return item

    def require_lakehouse(self, key: str, *, role: str | None = None) -> dict:
        item = self.validate_record(key, role=role)
        record = self.state.get(key)
        if item.get("type") != "Lakehouse" or record is None:
            raise FabricError("Expected Lakehouse")
        detail = self.clients[role or record["role"]].get(
            f"workspaces/{record['workspace_id']}/lakehouses/{record['id']}")
        if detail.get("properties", {}).get("defaultSchema"):
            raise FabricError("Schema-enabled lakehouses are incompatible with Tables/table contract")
        return detail

    def ensure_item(self, *, key: str, role: str, kind: str, collection: str,
                    name: str, definition: dict | None = None) -> str:
        if self.dry_run:
            raise FabricError("Use declarative plans, not ensure_item, in dry-run")
        workspace = self.workspace(role)
        client = self.clients[role]
        saved = self.state.get(key)
        if key in self.state.data["pending"]:
            raise FabricError(f"Unresolved write intent: {key}; reconcile manually")
        if saved:
            if saved["role"] != role or saved["workspace_id"] != workspace or saved["kind"] != kind:
                raise FabricError("Recorded item scope mismatch")
            self.validate_record(key)
            item_id = saved["id"]
        else:
            matches = [i for i in client.list(f"workspaces/{workspace}/items")
                       if i.get("displayName", "").casefold() == name.casefold()]
            if len(matches) > 1:
                raise FabricError("Ambiguous existing item name")
            if matches:
                item = client.get(f"workspaces/{workspace}/items/{live_id(matches[0]['id'])}")
                if item.get("type") != kind or self.state.marker(key) not in item.get("description", ""):
                    raise FabricError("Existing item has wrong type or ownership marker; not modifying")
                item_id = live_id(item["id"])
                self.state.record(key, resource_id=item_id, role=role, kind=kind,
                                  name=name, workspace_id=workspace, created=False)
            else:
                body = {"displayName": name, "description": self.state.marker(key)}
                if definition is not None:
                    body["definition"] = validate_definition(definition)
                self.state.begin(key, action="create", role=role)
                item = client.mutate("POST", f"workspaces/{workspace}/{collection}", body,
                                     on_response=lambda response: self.state.receipt(key, response))
                item_id = live_id(item.get("id"))
                if item.get("type") != kind:
                    raise FabricError("Create result type mismatch; reconcile pending intent")
                self.state.record(key, resource_id=item_id, role=role, kind=kind,
                                  name=name, workspace_id=workspace, created=True)
                return item_id
        if definition is not None:
            self.state.begin(key, action="updateDefinition", role=role)
            client.mutate("POST", f"workspaces/{workspace}/items/{item_id}/updateDefinition",
                          {"definition": validate_definition(definition)},
                          on_response=lambda response: self.state.receipt(key, response))
            self.state.finish(key)
        return item_id

    def require_graph_security(self) -> None:
        # Deliberately no environment flag can turn a manual attestation into verified RLS.
        raise UnsupportedCapability(SECURITY_BLOCKER)


def validate_definition(definition: dict) -> dict:
    """Validate the adapter envelope, not its version-specific semantic schema."""
    import base64
    import binascii
    from pathlib import PurePosixPath

    if not isinstance(definition, dict) or not definition.get("parts"):
        raise ValueError("Adapter must return a public definition object with nonempty parts")
    names = set()
    for part in definition["parts"]:
        path = part.get("path", "")
        if not path or path in names or PurePosixPath(path).is_absolute() or ".." in path.split("/"):
            raise ValueError("Invalid or duplicate definition part path")
        names.add(path)
        if part.get("payloadType") != "InlineBase64":
            raise ValueError("Only InlineBase64 definition payloads are supported")
        try:
            base64.b64decode(part["payload"], validate=True)
        except (ValueError, KeyError, TypeError, binascii.Error):
            raise ValueError("Invalid InlineBase64 definition payload") from None
    return definition