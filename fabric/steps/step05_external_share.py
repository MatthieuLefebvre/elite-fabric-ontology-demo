"""05: deliberate stop at the manual external-share boundary, never an invented API."""

from fabric.config import FIRMS
from fabric.steps.common import EXTERNAL_BLOCKER, Context, UnsupportedCapability


def run(ctx: Context) -> None:
    if ctx.config.simulation:
        ctx.plan(5, "external_share_not_applicable", reason="Explicit same-tenant simulation; use step 06")
        return
    for firm in FIRMS:
        ctx.plan(5, "BLOCKED_manual_create_invite_accept", firm=firm,
                 provider_workspace=ctx.workspace("provider"), gold=ctx.ref("provider.gold_" + firm),
                 recipient_workspace=ctx.workspace(firm), recipient_lakehouse=ctx.ref(firm + ".lakehouse"),
                 scope="Only this firm's gold Tables; never bronze, silver, Files or golden answers",
                 obligation=EXTERNAL_BLOCKER)
    if not ctx.dry_run:
        raise UnsupportedCapability(EXTERNAL_BLOCKER)