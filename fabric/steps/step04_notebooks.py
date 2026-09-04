"""04: parameterize public ipynb definitions in memory; execute only in provider."""

from __future__ import annotations

import ast
import base64
import json
from pathlib import Path

from fabric.config import FIRMS
from fabric.steps.common import Context
from fabric.steps.step03_upload import TABLES, load_manifest


def notebook_definition(path: Path, config: dict) -> dict:
    """Parse every pure-Python cell before injecting parameters into a copied definition."""
    notebook = json.loads(path.read_text(encoding="utf-8"))
    if notebook.get("nbformat") != 4:
        raise ValueError("Notebook contract requires nbformat 4")
    code = [c for c in notebook["cells"] if c.get("cell_type") == "code"]
    if not code:
        raise ValueError("First code cell must assign CONFIG_JSON")
    # The checked assignment identifies the parameter cell in source templates.
    # Fabric receives the parameter tag in the uploaded definition.
    code[0].setdefault("metadata", {}).setdefault("tags", [])
    if "parameters" not in code[0]["metadata"]["tags"]:
        code[0]["metadata"]["tags"].append("parameters")
    trees = []
    for cell in code:
        source = cell.get("source", [])
        source = source if isinstance(source, str) else "".join(source)
        trees.append(ast.parse(source))
    tree = trees[0]
    if not any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CONFIG_JSON"
               for t in n.targets) and isinstance(n.value, ast.Constant)
               and isinstance(n.value.value, str) for n in tree.body):
        raise ValueError("First parameters cell must assign CONFIG_JSON to a string")
    encoded = json.dumps(config, sort_keys=True, allow_nan=False)
    # repr produces a Python string literal, including escaping quotes/backslashes/newlines.
    code[0]["source"] = [f"CONFIG_JSON = {encoded!r}\n"]
    for cell in code:
        cell["outputs"], cell["execution_count"] = [], None
    # Absolute ABFSS paths are authoritative; discard template default-lakehouse bindings.
    dependencies = notebook.get("metadata", {}).get("dependencies", {})
    dependencies.pop("lakehouse", None)
    payload = base64.b64encode(json.dumps(notebook, allow_nan=False).encode("utf-8")).decode("ascii")
    return {"format": "ipynb", "parts": [{"path": "notebook-content.ipynb",
            "payloadType": "InlineBase64", "payload": payload}]}


def abfss(workspace: str, lakehouse: str) -> str:
    """Build an explicit OneLake root without relying on a default lakehouse."""
    return f"abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{lakehouse}"


def run(ctx: Context) -> None:
    """Parameterize and execute each firm's transformations in the provider workspace."""
    manifest, schemas = ({"as_of": "${manifest.as_of}"}, {})
    tables = list(TABLES)
    contract = None
    if not ctx.dry_run:
        manifest, schemas, _ = load_manifest(ctx.config.data_dir)
        tables = sorted(schemas["tables"])
        contract = ctx.contract()
        for key in ("provider.bronze", "provider.silver", "provider.gold_harbor", "provider.gold_kestrel"):
            ctx.require_lakehouse(key)
    workspace = ctx.workspace("provider")
    for firm in FIRMS:
        for stage, template, source, target in (
            ("01", ctx.config.notebook01, "bronze", "silver"),
            ("02", ctx.config.notebook02, "silver", "gold_" + firm),
        ):
            source_id, target_id = ctx.ref("provider." + source), ctx.ref("provider." + target)
            config = {"source_workspace_id": workspace, "source_lakehouse_id": source_id,
                      "target_workspace_id": workspace, "target_lakehouse_id": target_id,
                      "firm_slug": firm, "tables": tables, "as_of": manifest["as_of"],
                      "source_abfss": abfss(workspace, source_id),
                      "target_abfss": abfss(workspace, target_id),
                      "schemas": schemas.get("tables", {}),
                      "ontology": contract if contract is not None else "${adapter.load_contract}"}
            ctx.plan(4, "parameterize_upload_run_notebook", role="provider", stage=stage,
                     firm=firm, config=config if ctx.dry_run else {
                         k: v for k, v in config.items() if k not in ("ontology", "schemas")
                     }, parameter="CONFIG_JSON (JSON string)",
                     source_pattern="Files/{firm}/{table}.parquet" if stage == "01" else "Tables/{firm}_{table}",
                     target_pattern="Tables/{firm}_{table}" if stage == "01" else "Tables/{table}",
                     definition_format="ipynb/InlineBase64", job_type="RunNotebook")
            if ctx.dry_run:
                continue
            key = f"provider.notebook{stage}_{firm}"
            item_id = ctx.ensure_item(
                key=key, role="provider", kind="Notebook", collection="notebooks",
                name=f"transform_{stage}_{firm}", definition=notebook_definition(template, config),
            )
            job_key = "job:" + key
            ctx.state.begin(job_key, action="RunNotebook", role="provider")
            ctx.clients["provider"].run_notebook(
                workspace, item_id, on_response=lambda response: ctx.state.receipt(job_key, response),
            )
            ctx.state.finish(job_key)