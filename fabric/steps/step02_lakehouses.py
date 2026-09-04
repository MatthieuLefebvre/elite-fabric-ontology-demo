"""02: non-schema lakehouses; each consumer owns a separate firm lakehouse."""

from fabric.config import FIRMS
from fabric.steps.common import Context


def run(ctx: Context) -> None:
    resources = [("provider", name) for name in ("bronze", "silver", "gold_harbor", "gold_kestrel")]
    resources += [(firm, firm + "_data") for firm in FIRMS]
    for role, name in resources:
        key = role + "." + ("lakehouse" if role in FIRMS else name)
        ctx.plan(2, "ensure_lakehouse", role=role, name=name, resource=key,
                 workspace=ctx.workspace(role), schema_enabled=False)
        if not ctx.dry_run:
            # Omit creationPayload: only enableSchemas=true is documented; false is not.
            ctx.ensure_item(key=key, role=role, kind="Lakehouse", collection="lakehouses", name=name)
            ctx.require_lakehouse(key)