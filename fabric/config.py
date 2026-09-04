"""Explicit per-tenant identities; environment-file parsing does not mutate os.environ."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from fabric.fabric_client import live_id, safe_name

ROOT = Path(__file__).resolve().parents[1]
FIRMS = ("harbor", "kestrel")


@dataclass(frozen=True)
class Identity:
    role: str
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    capacity_id: str = ""
    workspace_id: str = ""
    admin_attestation: str = field(default="", repr=False)
    core_setting_names: tuple[str, ...] = ()

    def validate(self) -> None:
        for key in ("tenant_id", "client_id", "capacity_id"):
            if not getattr(self, key):
                raise ValueError(f"Missing {self.role.upper()}_{key.upper()}")
            live_id(getattr(self, key))
        if not self.client_secret:
            raise ValueError(f"Missing {self.role.upper()}_CLIENT_SECRET")
        if self.workspace_id:
            live_id(self.workspace_id)


@dataclass(frozen=True)
class Config:
    identities: dict[str, Identity]
    demo_id: str = "elite_demo"
    simulation: bool = False
    shared_consumers: bool = False
    data_dir: Path = ROOT / "data"
    state_path: Path = ROOT / ".state/fabric-state.json"
    ontology_path: Path = ROOT / "fabric/ontology/ontology.yaml"
    notebook01: Path = ROOT / "fabric/notebooks/01_bronze_to_silver.ipynb"
    notebook02: Path = ROOT / "fabric/notebooks/02_silver_to_gold.ipynb"
    instructions_path: Path = ROOT / "agent/agent_instructions.md"
    timeout: float = 1800

    @property
    def mode(self) -> str:
        if self.simulation:
            return "SINGLE_TENANT_SIMULATION_WEAKER_WITHIN_CONSUMER_SECURITY"
        if self.shared_consumers:
            return "SHARED_CONSUMER_TENANT_WORKSPACE_ISOLATION_ONLY"
        return "THREE_INDEPENDENT_TENANTS"

    @property
    def workspace_roles(self) -> tuple[str, ...]:
        return ("provider", "harbor") if self.simulation else ("provider", *FIRMS)

    def workspace_role(self, role: str) -> str:
        return "harbor" if self.simulation and role in FIRMS else role

    def workspace_name(self, role: str) -> str:
        suffix = "consumer" if self.simulation and role in FIRMS else role
        return safe_name(f"{self.demo_id}_{suffix}")

    def validate(self) -> None:
        safe_name(self.demo_id)
        if not 1 <= self.timeout <= 86400:
            raise ValueError("OPERATION_TIMEOUT_SECONDS must be between 1 and 86400")
        for identity in self.identities.values():
            identity.validate()
        tenants = [live_id(self.identities[r].tenant_id) for r in ("provider", *FIRMS)]
        if self.simulation:
            if len(set(tenants)) != 1:
                raise ValueError("Simulation requires one tenant for all three scoped identities")
            h, k = self.identities["harbor"], self.identities["kestrel"]
            if h.capacity_id != k.capacity_id or h.workspace_id != k.workspace_id:
                raise ValueError("Simulation consumers must use the same capacity and workspace ID")
        elif self.shared_consumers:
            if tenants[0] in tenants[1:]:
                raise ValueError("Shared consumers must remain external to the provider tenant")
        elif len(set(tenants)) != 3:
            raise ValueError("Normal mode requires three tenants; opt into a labeled weaker topology")
        workspace_ids = [self.identities[r].workspace_id for r in self.workspace_roles
                         if self.identities[r].workspace_id]
        if len(set(workspace_ids)) != len(workspace_ids):
            raise ValueError("Provider and consumer workspaces must be distinct")


def load_config(path: str | Path | None = None, *, simulation: bool = False,
                shared_consumers: bool = False) -> Config:
    """Resolve explicit configuration against the repository and process environment."""
    env_path = Path(path).resolve() if path else ROOT / ".env"
    if path and not env_path.is_file():
        raise ValueError("Explicit --config file does not exist")
    # Process environment deliberately overrides the selected file; no interpolation of secrets.
    values = {**(dotenv_values(env_path, interpolate=False) if env_path.is_file() else {}),
              **os.environ}

    def val(key: str, default: str = "") -> str:
        """Read a nonempty setting or its default without interpolation."""
        return str(values.get(key) or default)

    def local(key: str, default: str) -> Path:
        """Resolve configured local paths independently of the caller's directory."""
        p = Path(val(key, default)).expanduser()
        return p.resolve() if p.is_absolute() else (ROOT / p).resolve()

    identities = {}
    for role in ("provider", *FIRMS):
        prefix = role.upper()
        identities[role] = Identity(
            role, val(f"{prefix}_TENANT_ID"), val(f"{prefix}_CLIENT_ID"),
            val(f"{prefix}_CLIENT_SECRET"), val(f"{prefix}_CAPACITY_ID"),
            val(f"{prefix}_WORKSPACE_ID"), val(f"{prefix}_ADMIN_ATTESTATION"),
            tuple(x.strip() for x in val(f"{prefix}_CORE_SETTING_NAMES").split(",") if x.strip()),
        )
    return Config(
        identities=identities, demo_id=safe_name(val("DEMO_ID", "elite_demo")),
        simulation=simulation, shared_consumers=shared_consumers,
        data_dir=local("DATA_DIR", "data"), state_path=local("FABRIC_STATE_PATH", ".state/fabric-state.json"),
        ontology_path=local("ONTOLOGY_PATH", "fabric/ontology/ontology.yaml"),
        notebook01=local("NOTEBOOK01_PATH", "fabric/notebooks/01_bronze_to_silver.ipynb"),
        notebook02=local("NOTEBOOK02_PATH", "fabric/notebooks/02_silver_to_gold.ipynb"),
        instructions_path=local("AGENT_INSTRUCTIONS_PATH", "agent/agent_instructions.md"),
        timeout=float(val("OPERATION_TIMEOUT_SECONDS", "1800")),
    )