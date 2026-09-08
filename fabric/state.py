"""Atomic local ownership journal with an exclusive deployment lock.

This is not a cloud transaction. Pending writes survive crashes and prevent replay.
Keep the journal private and backed up. Never run multiple hosts against one demo.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fabric.fabric_client import FabricError, live_id, operation_url, request_id


class State:
    def __init__(self, path: Path, demo_id: str, *, dry_run: bool = False):
        self.path, self.demo_id, self.dry_run = Path(path), demo_id, dry_run
        self.data = {"version": 1, "demo_id": demo_id, "resources": {}, "pending": {}, "shortcuts": {},
                     "completed_steps": [], "context": None}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise FabricError("Invalid ownership journal; do not overwrite") from None
            if self.data.get("version") != 1 or self.data.get("demo_id") != demo_id:
                raise FabricError("Ownership journal version/deployment mismatch")
            if not isinstance(self.data.get("resources"), dict) or not isinstance(
                self.data.get("pending"), dict
            ):
                raise FabricError("Malformed ownership journal")
        self._locked = False

    def marker(self, key: str) -> str:
        return f"[elite-demo:{self.demo_id}:{key}]"

    @contextmanager
    def locked(self):
        if self.dry_run:
            yield self
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise FabricError("Deployment lock exists; verify no active process before removing it") from None
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
            self._locked = True
            # Prevent a stale reader from overwriting a journal changed before lock acquisition.
            if self.path.exists():
                current = json.loads(self.path.read_text(encoding="utf-8"))
                if current != self.data:
                    raise FabricError("Journal changed before lock acquisition; restart")
            yield self
        finally:
            self._locked = False
            os.close(fd)
            lock.unlink()

    def save(self) -> None:
        if self.dry_run:
            return
        if not self._locked:
            raise FabricError("Ownership journal requires an exclusive lock")
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.data, handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def bind(self, context: dict) -> None:
        old = self.data.get("context")
        if old and old != context:
            raise FabricError("Journal identity/topology mismatch; use a separate state path")
        self.data["context"] = context
        self.save()

    def get(self, key: str) -> dict | None:
        return self.data["resources"].get(key)

    def begin(self, key: str, *, action: str, role: str) -> None:
        if self.dry_run:
            raise FabricError("Dry-run cannot record write intents")
        if key in self.data["pending"]:
            raise FabricError(f"Unresolved write intent: {key}; reconcile manually before retry")
        self.data["pending"][key] = {"action": action, "role": role}
        self.save()

    def finish(self, key: str) -> None:
        self.data["pending"].pop(key, None)
        self.save()

    def receipt(self, key: str, response) -> None:
        """Persist only trusted polling coordinates/UUIDs for manual crash recovery."""
        pending = self.data["pending"][key]
        if response.status_code == 202:
            location = operation_url(response)
            if "?" in location:
                raise FabricError("Unexpected query-bearing operation Location; reconcile manually")
            pending["location"] = location
        pending["request_id"] = request_id(response.headers.get("request-id")
                                           or response.headers.get("x-ms-request-id"))
        pending["operation_id"] = request_id(response.headers.get("x-ms-operation-id"))
        self.save()

    def record(self, key: str, *, resource_id: str, role: str, kind: str,
               name: str, created: bool, workspace_id: str | None = None,
               parent_folder_id: str | None = None, folder_id: str | None = None) -> dict:
        if self.dry_run:
            raise FabricError("Dry-run IDs must not enter the ownership journal")
        resource_id = live_id(resource_id)
        old = self.get(key)
        if old and (old["id"] != resource_id or old["created"] != created):
            raise FabricError("Refusing to change recorded resource ownership")
        record = {"id": resource_id, "role": role, "kind": kind, "name": name,
                  "created": created, "workspace_id": workspace_id, "marker": self.marker(key)}
        if parent_folder_id is not None:
            record["parent_folder_id"] = live_id(parent_folder_id)
        if folder_id is not None:
            record["folder_id"] = live_id(folder_id)
        self.data["resources"][key] = record
        self.data["pending"].pop(key, None)
        self.save()
        return record

    def removed(self, key: str) -> None:
        self.data["resources"].pop(key, None)
        self.data["pending"].pop("delete:" + key, None)
        self.data["completed_steps"] = []
        self.save()

    def complete(self, step: int) -> None:
        self.data["completed_steps"] = sorted(set(self.data["completed_steps"]) | {step})
        self.save()

    def shortcut(self, key: str, *, role: str, path: str, target: dict) -> None:
        self.data.setdefault("shortcuts", {})[key] = {"role": role, "path": path, "target": target}
        self.data["pending"].pop(key, None)
        self.save()