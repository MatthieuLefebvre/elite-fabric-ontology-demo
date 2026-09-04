"""Small, fail-closed Fabric v1 transport; no implicit identity or POST replay."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit
from uuid import UUID, uuid4

import httpx

API_ROOT = "https://api.fabric.microsoft.com/v1/"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"
LOG = logging.getLogger("elite.fabric")


def emit(event: str, **fields: object) -> None:
    """Callers supply only selected metadata, never bodies, tokens, or headers."""
    LOG.info(json.dumps({"event": event, **fields}, sort_keys=True))


def request_id(value: str | None) -> str | None:
    try:
        return str(UUID(value)) if value else None
    except (ValueError, TypeError):
        return None


class FabricError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class AmbiguousWrite(FabricError):
    """Do not replay. Reconcile the saved intent against Fabric first."""


class FabricTimeout(FabricError):
    """Deadline exceeded; a submitted cloud operation may still be running."""


def trusted_url(value: str) -> str:
    """Validate BEFORE attaching a token, including every pagination/Location URL."""
    if not isinstance(value, str) or any(ord(c) < 33 for c in value) or "\\" in value:
        raise FabricError("Untrusted Fabric URL")
    url = urljoin(API_ROOT, value)
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme == "https"
            and parts.hostname == "api.fabric.microsoft.com"
            and parts.port in (None, 443)
            and not parts.username and not parts.password and not parts.fragment
            and parts.path.startswith(("/v1/", "/operations/"))
        )
    except ValueError:
        valid = False
    if not valid or any(p in (".", "..") for p in unquote(parts.path).split("/")):
        raise FabricError("Untrusted Fabric URL")
    return url


def retry_after(response: httpx.Response, default: float = 1.0) -> float:
    value = response.headers.get("Retry-After")
    if value is None:
        return default
    try:
        delay = float(value)
        if not math.isfinite(delay) or delay < 0:
            raise ValueError
        return delay
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            raise FabricError("Invalid Retry-After header") from None


class FabricClient:
    def __init__(
        self, token_provider: Callable[[], str], *, role: str,
        transport: httpx.BaseTransport | None = None, dry_run: bool = False,
        timeout: float = 60, operation_timeout: float = 1800, retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.token_provider = token_provider
        self.role = role
        self.dry_run = dry_run
        self.timeout = timeout
        self.operation_timeout = operation_timeout
        self.retries = retries
        self.sleep, self.clock = sleep, clock
        self.http = httpx.Client(transport=transport, follow_redirects=False, trust_env=False)

    def close(self) -> None:
        self.http.close()

    def _wait(self, seconds: float, deadline: float) -> None:
        if self.clock() + seconds >= deadline:
            raise FabricTimeout("Fabric deadline exceeded; reconcile any submitted operation")
        self.sleep(seconds)

    def request(
        self, method: str, path: str, *, body: dict | None = None,
        allow_status: tuple[int, ...] = (), deadline: float | None = None,
    ) -> httpx.Response:
        if self.dry_run:
            raise FabricError("Network access forbidden in dry-run")
        url = trusted_url(path)
        method = method.upper()
        deadline = deadline if deadline is not None else self.clock() + self.timeout
        correlation = str(uuid4())
        for attempt in range(self.retries + 1):
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise FabricTimeout("Fabric request deadline exceeded")
            try:
                token = self.token_provider()
            except Exception:
                raise FabricError(f"{self.role}: token acquisition failed (details suppressed)") from None
            if self.clock() >= deadline:
                raise FabricTimeout("Token acquisition exceeded request deadline")
            try:
                response = self.http.request(
                    method, url, json=body,
                    headers={"Authorization": f"Bearer {token}",
                             "x-ms-client-request-id": correlation},
                    timeout=min(self.timeout, deadline - self.clock()),
                )
            except httpx.TransportError:
                if method not in ("GET", "HEAD"):
                    raise AmbiguousWrite("Transport failure on write; reconcile, do not replay") from None
                if attempt == self.retries:
                    raise FabricError("Fabric read transport failure") from None
                self._wait(min(2 ** attempt, 16), deadline)
                continue
            emit("http", role=self.role, method=method, status=response.status_code,
                 client_request_id=correlation,
                 request_id=request_id(response.headers.get("request-id")
                                       or response.headers.get("x-ms-request-id")),
                 attempt=attempt)
            # Validate even unused Location headers, but never follow HTTP redirects.
            if response.headers.get("Location"):
                trusted_url(response.headers["Location"])
            if 200 <= response.status_code < 300 or response.status_code in allow_status:
                return response
            transient = response.status_code == 429 or (
                method in ("GET", "HEAD") and response.status_code in (500, 502, 503, 504)
            )
            if transient and attempt < self.retries:
                self._wait(retry_after(response, min(2 ** attempt, 16)), deadline)
                continue
            if method not in ("GET", "HEAD") and response.status_code >= 500:
                raise AmbiguousWrite("Ambiguous write response; reconcile, do not replay",
                                     status=response.status_code)
            raise FabricError(f"Fabric HTTP {response.status_code}; see correlated service diagnostics",
                              status=response.status_code)
        raise FabricError("Retry budget exhausted")

    @staticmethod
    def document(response: httpx.Response) -> dict:
        if not response.content:
            return {}
        try:
            value = response.json()
        except ValueError:
            raise FabricError("Fabric returned invalid JSON") from None
        if not isinstance(value, dict):
            raise FabricError("Fabric returned a non-object document")
        return value

    def get(self, path: str) -> dict:
        return self.document(self.request("GET", path))

    def list(self, path: str, *, collection_keys: tuple[str, ...] = ("value",)) -> list[dict]:
        """Read bounded pages using explicitly allowed collection keys, failing closed."""
        first = trusted_url(path)
        current = first
        visited: set[str] = set()
        values: list[dict] = []
        deadline = self.clock() + self.operation_timeout
        for _ in range(1000):
            if current in visited:
                raise FabricError("Pagination cycle")
            visited.add(current)
            page = self.document(self.request("GET", current, deadline=deadline))
            # Tenant settings currently documents value; an alternate tenantSettings
            # envelope is accepted only when explicitly requested by that caller.
            present = [key for key in collection_keys if key in page]
            if len(present) != 1:
                raise FabricError("Missing or ambiguous Fabric list collection")
            entries = page[present[0]]
            if not isinstance(entries, list) or any(not isinstance(x, dict) for x in entries):
                raise FabricError("Invalid Fabric list response")
            values.extend(entries)
            if page.get("continuationUri"):
                current = trusted_url(page["continuationUri"])
            elif page.get("continuationToken"):
                current = str(httpx.URL(first).copy_set_param(
                    "continuationToken", page["continuationToken"]))
            else:
                return values
        raise FabricError("Pagination limit exceeded")

    def poll(self, response: httpx.Response, *, job: bool = False) -> dict:
        if response.status_code != 202:
            return self.document(response)
        location = response.headers.get("Location")
        if not location:
            raise FabricError("Accepted operation has no Location; reconcile manually")
        location = trusted_url(location)
        deadline = self.clock() + self.operation_timeout
        self._wait(retry_after(response), deadline)
        for _ in range(10000):
            update = self.request("GET", location, deadline=deadline)
            data = self.document(update)
            status = data.get("status")
            if status == ("Completed" if job else "Succeeded"):
                if job:
                    return data
                # The documented LRO result endpoint; never infer an item ID from operation ID.
                result = update.headers.get("Location")
                if not result or result.rstrip("/") == location.rstrip("/"):
                    result = location.rstrip("/") + "/result"
                return self.document(self.request("GET", trusted_url(result), deadline=deadline))
            if status in ("Failed", "Cancelled", "Canceled", "Deduped"):
                raise FabricError(f"Fabric {'job' if job else 'operation'} {status}")
            if status not in ("NotStarted", "Running", "InProgress", "Undefined"):
                raise FabricError("Unknown Fabric operation status; refusing to claim success")
            self._wait(retry_after(update, 5), deadline)
        raise FabricTimeout("Fabric polling limit exceeded")

    def mutate(self, method: str, path: str, body: dict | None = None, *,
               on_response: Callable[[httpx.Response], None] | None = None) -> dict:
        response = self.request(method, path, body=body)
        if on_response:
            on_response(response)
        return self.poll(response)

    def run_notebook(self, workspace_id: str, notebook_id: str, *,
                     on_response: Callable[[httpx.Response], None] | None = None) -> dict:
        # CONFIG_JSON is already injected into the public definition. No undocumented
        # executionData shape or unsupported generic parameters array is sent.
        path = f"workspaces/{workspace_id}/items/{notebook_id}/jobs/RunNotebook/instances"
        response = self.request("POST", path)
        if response.status_code != 202:
            raise FabricError("Notebook execution was not accepted as a job")
        if on_response:
            on_response(response)
        return self.poll(response, job=True)


def live_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise FabricError("Expected a live UUID, not a plan reference") from None


def safe_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", value):
        raise ValueError("Names must start with a letter and contain only letters/digits/underscores")
    return value