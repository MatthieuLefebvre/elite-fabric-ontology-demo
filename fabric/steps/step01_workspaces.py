"""01: exactly two simulation workspaces, otherwise exactly three workspaces."""

from fabric.fabric_client import FabricError, live_id
from fabric.steps.common import Context


def run(ctx: Context) -> None:
    for role in ctx.config.workspace_roles:
        key = role + ".workspace"
        name = ctx.config.workspace_name(role)
        ctx.plan(1, "ensure_workspace", role=role, name=name, resource=key,
                 capacity=f"{role.upper()}_CAPACITY_ID", topology=ctx.config.mode)
        if ctx.dry_run:
            continue
        identity = ctx.config.identities[role]
        client = ctx.clients[role]
        saved = ctx.state.get(key)
        if saved:
            workspace = ctx.validate_record(key)
            if workspace.get("capacityId") != identity.capacity_id:
                raise FabricError("Saved workspace has a different capacity; not reassigning")
            continue
        if key in ctx.state.data["pending"]:
            raise FabricError(f"Unresolved workspace create: {key}; reconcile manually")
        adopted = bool(identity.workspace_id)
        if adopted:
            workspace = client.get(f"workspaces/{live_id(identity.workspace_id)}")
        else:
            matches = [w for w in client.list("workspaces")
                       if w.get("displayName", "").casefold() == name.casefold()]
            if len(matches) > 1:
                raise FabricError("Ambiguous workspace name")
            workspace = client.get(f"workspaces/{live_id(matches[0]['id'])}") if matches else None
        if workspace:
            if workspace.get("type") != "Workspace":
                raise FabricError("Existing resource is not a regular Workspace")
            if workspace.get("capacityId") != identity.capacity_id:
                raise FabricError("Existing workspace capacity mismatch; not modifying")
            if not adopted and ctx.state.marker(key) not in workspace.get("description", ""):
                raise FabricError("Existing-name workspace lacks deployment ownership marker")
            ctx.state.record(key, resource_id=workspace["id"], role=role, kind="Workspace",
                             name=workspace["displayName"], created=False)
        else:
            ctx.state.begin(key, action="create", role=role)
            workspace = client.mutate("POST", "workspaces", {
                "displayName": name, "capacityId": identity.capacity_id,
                "description": ctx.state.marker(key),
            }, on_response=lambda response: ctx.state.receipt(key, response))
            if workspace.get("type") != "Workspace":
                raise FabricError("Unexpected workspace create result; reconcile pending intent")
            ctx.state.record(key, resource_id=workspace["id"], role=role, kind="Workspace",
                             name=name, created=True)
    if not ctx.dry_run and ctx.config.simulation:
        # Both principals need explicit permission to the shared consumer workspace.
        ctx.validate_record("harbor.workspace", role="kestrel")
    if not ctx.dry_run:
        ids = [ctx.workspace(r) for r in ctx.config.workspace_roles]
        if len(ids) != len(set(ids)):
            raise FabricError("Workspace topology collapsed unexpectedly")