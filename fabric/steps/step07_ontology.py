"""07: parent-owned definition adapter, guarded before any confidential binding."""

from fabric.config import FIRMS
from fabric.steps.common import SECURITY_BLOCKER, Context, validate_definition


def run(ctx: Context) -> None:
    for firm in FIRMS:
        ctx.plan(7, "ontology_definition_plan_BLOCKED", role=firm, workspace=ctx.workspace(firm),
                 lakehouse=ctx.ref(firm + ".lakehouse"), endpoint="/workspaces/{ws}/ontologies",
                 adapter="build_ontology_definition(contract, workspace_id, lakehouse_id, display_name)",
                 definition="public definition parts / InlineBase64", obligation=SECURITY_BLOCKER)
    if ctx.dry_run:
        return
    ctx.require_graph_security()
    contract, adapter = ctx.contract(), ctx.adapter()
    for firm in FIRMS:
        ctx.require_lakehouse(firm + ".lakehouse")
        name = "legal_ontology_" + firm
        definition = validate_definition(adapter.build_ontology_definition(
            contract, ctx.workspace(firm), ctx.ref(firm + ".lakehouse"), name,
        ))
        ctx.ensure_item(key=firm + ".ontology", role=firm, kind="Ontology",
                        collection="ontologies", name=name, definition=definition)