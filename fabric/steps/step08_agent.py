"""08: consume an adapter definition, never fabricate an ontology datasource."""

from fabric.config import FIRMS
from fabric.steps.common import AGENT_BLOCKER, Context, validate_definition


def run(ctx: Context) -> None:
    for firm in FIRMS:
        ctx.plan(8, "data_agent_definition_plan_BLOCKED", role=firm,
                 endpoint="/workspaces/{ws}/dataAgents", workspace=ctx.workspace(firm),
                 ontology=ctx.ref(firm + ".ontology") if ctx.dry_run else "required live ontology",
                 adapter="build_agent_definition(contract, workspace_id, ontology_id, instructions)",
                 obligation=AGENT_BLOCKER, publishing="M365 Word publishing is a separate manual obligation")
    if ctx.dry_run:
        return
    adapter, contract = ctx.adapter(), ctx.contract()
    instructions = ctx.config.instructions_path.read_text(encoding="utf-8")
    definitions = {}
    for firm in FIRMS:
        # The parent must raise UnsupportedCapability(ValueError) for an undocumented source.
        definitions[firm] = validate_definition(adapter.build_agent_definition(
            contract, ctx.workspace(firm), ctx.ref(firm + ".ontology"), instructions,
        ))
    ctx.require_graph_security()
    for firm in FIRMS:
        ctx.ensure_item(key=firm + ".agent", role=firm, kind="DataAgent",
                        collection="dataAgents", name="legal_agent_" + firm,
                        definition=definitions[firm])