"""03: upload only hash-checked synthetic manifest members to provider Bronze."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from fabric.config import FIRMS
from fabric.fabric_client import FabricError
from fabric.steps.common import Context

TABLES = (
    "adjustments", "billing_guidelines", "budgets", "clients", "disbursements", "firms",
    "invoice_guidelines", "invoices", "legal_entities", "matter_access", "matters", "offices",
    "payments", "practice_groups", "proformas", "rates", "time_entries", "timekeepers",
)


def load_manifest(root: Path) -> tuple[dict, dict, list[tuple[Path, str]]]:
    root = root.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported generator manifest version")
    date.fromisoformat(manifest["as_of"])

    def checked(name: str, digest: str) -> Path:
        if not isinstance(name, str) or "\\" in name:
            raise ValueError("Invalid manifest path")
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Manifest path escaped data directory or is missing")
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != digest:
            raise ValueError("Manifest checksum mismatch")
        return path

    if manifest.get("schema_file") != "schema.json":
        raise ValueError("Expected generator schema.json")
    schema_path = checked("schema.json", manifest["schema_sha256"])
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    tables = schema.get("tables", {})
    if not tables or any(not re.fullmatch(r"[a-z][a-z0-9_]*", t) for t in tables):
        raise ValueError("Invalid schema table names")
    seen = set()
    uploads = []
    for entry in manifest["files"]:
        name, table = entry["path"], entry["table"]
        firm = name.split("/")[0]
        if firm not in FIRMS or table not in tables or name != f"{firm}/{table}.parquet":
            raise ValueError("Manifest firm/table/path mismatch")
        if entry["firm_id"] != f"{firm}_f001" or (firm, table) in seen:
            raise ValueError("Manifest firm identity mismatch or duplicate table")
        if not isinstance(entry["row_count"], int) or entry["row_count"] < 0:
            raise ValueError("Invalid manifest row count")
        seen.add((firm, table))
        uploads.append((checked(name, entry["sha256"]), name))
    if seen != {(f, t) for f in FIRMS for t in tables}:
        raise ValueError("Incomplete per-firm table set")
    # Do not upload expected_answers.json: it is a multi-firm local oracle.
    uploads += [(schema_path, "schema.json"), (root / "manifest.json", "manifest.json")]
    return manifest, schema, uploads


def run(ctx: Context) -> None:
    ctx.plan(3, "upload_manifest_members", role="provider", workspace=ctx.workspace("provider"),
             lakehouse=ctx.ref("provider.bronze"), destination="Files/{firm}/{table}.parquet",
             metadata=["Files/schema.json", "Files/manifest.json"],
             excluded=["expected_answers.json"], integrity="SHA256 and exact firm/table paths")
    if ctx.dry_run:
        return
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.filedatalake import DataLakeServiceClient

    _, _, uploads = load_manifest(ctx.config.data_dir)
    ctx.require_lakehouse("provider.bronze")
    lakehouse = ctx.ref("provider.bronze")
    # Endpoint is fixed; no manifest/config-supplied host receives the storage credential.
    try:
        with DataLakeServiceClient(
            "https://onelake.dfs.fabric.microsoft.com", credential=ctx.credentials["provider"],
            audience="https://storage.azure.com/", logging_enable=False,
            connection_timeout=30, read_timeout=120, retry_total=3,
        ) as service:
            fs = service.get_file_system_client(ctx.workspace("provider"))
            for firm in FIRMS:
                try:
                    fs.get_directory_client(f"{lakehouse}/Files/{firm}").create_directory()
                except ResourceExistsError:
                    pass
            # Publish manifest last. Re-running after a partial upload deterministically repairs it.
            for local, relative in uploads:
                with local.open("rb") as stream:
                    fs.get_file_client(f"{lakehouse}/Files/{relative}").upload_data(
                        stream, overwrite=True, length=local.stat().st_size,
                        max_concurrency=2, timeout=120,
                    )
    except Exception:
        raise FabricError("Provider OneLake upload failed; details suppressed; safely rerun step 03") from None