"""MCP 1.26.0 Streamable HTTP transport and local delegated device authentication.

JWT payload inspection is NOT signature verification. Only tokens acquired directly
by Azure Identity from the tenant's authority enter this path. Never accept a token
file, a pasted token, a deployment credential, or an arbitrary token supplier in CLI.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

SCOPE = "https://api.fabric.microsoft.com/.default"
MAX_TOOL_PAGES = 20
MAX_TOOLS = 100
MAX_RESPONSE_CHARS = 250_000


class EvaluationError(ValueError):
    """A safe, static diagnostic suitable for a report without exception bodies."""


def guid(value: str, setting: str) -> str:
    """Normalize a required immutable GUID without echoing its input."""
    try:
        parsed = UUID(value)
        if parsed.int == 0:
            raise ValueError
        return str(parsed)
    except (ValueError, TypeError, AttributeError):
        raise EvaluationError(f"INVALID_GUID:{setting}") from None


@dataclass(frozen=True)
class Identity:
    """Expected Harbor delegated user and public-client registration."""

    tenant_id: str
    object_id: str
    client_id: str


def validate_claims(token: str, expected: Identity) -> dict[str, str]:
    """Introspect Azure-acquired claims; reject changed identity or app-only tokens.

    This is an additional consistency check, not a JWT trust implementation.
    Fabric validates signatures/authorization. An appid claim alone is insufficient;
    delegated tokens commonly also contain appid, which is not itself an error.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        if not isinstance(claims, dict):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise EvaluationError("TOKEN_CLAIMS_UNREADABLE") from None
    if str(claims.get("tid", "")).lower() != expected.tenant_id.lower():
        raise EvaluationError("TOKEN_TENANT_MISMATCH")
    if str(claims.get("oid", "")).lower() != expected.object_id.lower():
        raise EvaluationError("TOKEN_USER_MISMATCH")
    if claims.get("idtyp") == "app" or not isinstance(claims.get("scp"), str):
        raise EvaluationError("TOKEN_NOT_DELEGATED")
    if not claims["scp"].split():
        raise EvaluationError("TOKEN_NOT_DELEGATED")
    if str(claims.get("aud", "")).rstrip("/") != SCOPE.removesuffix("/.default"):
        raise EvaluationError("TOKEN_AUDIENCE_MISMATCH")
    if str(claims.get("azp", claims.get("appid", ""))).lower() != expected.client_id.lower():
        raise EvaluationError("TOKEN_PUBLIC_CLIENT_MISMATCH")
    if (type(claims.get("exp")) not in (int, float) or not math.isfinite(claims["exp"])
            or claims["exp"] <= time.time() + 30):
        raise EvaluationError("TOKEN_EXPIRED_OR_EXPIRING")
    return {"tenant_id": expected.tenant_id, "object_id": expected.object_id,
            "client_id": expected.client_id, "authentication": "LOCAL_DEVICE_CODE_DELEGATED"}


def device_prompt(verification_uri: str, user_code: str, expires_on: datetime) -> None:
    """Show the device challenge only on the local console; never persist it."""
    print(f"Open {verification_uri} and enter {user_code}. Expires {expires_on.isoformat()}.",
          flush=True)


class Session(Protocol):
    """Minimal structural interface used by the MCP adapter and offline fakes."""

    async def initialize(self) -> Any:
        """Initialize the MCP connection."""
        ...

    async def list_tools(self, cursor: str | None = None) -> Any:
        """Read a single tools page."""
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke the discovered tool."""
        ...


def string_argument(schema: Any) -> str:
    """Accept precisely one required string, rejecting unsupported constraints.

    A deliberately conservative subset avoids claiming arbitrary JSON Schema
    validation. Constraint-bearing schemas fail explicitly rather than being ignored.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise EvaluationError("TOOL_SCHEMA_EXPECTED_OBJECT")
    allowed = {"type", "properties", "required", "additionalProperties", "title",
               "description", "$schema"}
    if set(schema) - allowed or schema.get("additionalProperties", False) is not False:
        raise EvaluationError("TOOL_SCHEMA_UNSUPPORTED_OBJECT_CONSTRAINT")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or len(properties) != 1:
        raise EvaluationError("TOOL_SCHEMA_EXPECTED_SINGLE_ARGUMENT")
    name, value = next(iter(properties.items()))
    if not isinstance(name, str) or not name or schema.get("required") != [name]:
        raise EvaluationError("TOOL_SCHEMA_ARGUMENT_MUST_BE_REQUIRED")
    if not isinstance(value, dict) or value.get("type") != "string":
        raise EvaluationError("TOOL_SCHEMA_ARGUMENT_MUST_BE_STRING")
    if set(value) - {"type", "title", "description"}:
        raise EvaluationError("TOOL_SCHEMA_UNSUPPORTED_STRING_CONSTRAINT")
    return name


async def query_session(session: Session, prompt: str) -> str:
    """Initialize, exhaust bounded pagination, and invoke exactly one discovered tool."""
    await session.initialize()
    tools: list[Any] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(MAX_TOOL_PAGES):
        page = await session.list_tools(cursor=cursor)
        tools.extend(page.tools)
        if len(tools) > MAX_TOOLS:
            raise EvaluationError("TOOL_DISCOVERY_SIZE_LIMIT")
        cursor = page.nextCursor
        if not cursor:
            break
        if not isinstance(cursor, str) or cursor in seen:
            raise EvaluationError("TOOL_DISCOVERY_CURSOR_CYCLE")
        seen.add(cursor)
    else:
        raise EvaluationError("TOOL_DISCOVERY_PAGE_LIMIT")
    if len(tools) != 1:
        raise EvaluationError("TOOL_DISCOVERY_EXPECTED_EXACTLY_ONE_TOOL")
    tool = tools[0]
    if not isinstance(tool.name, str) or not tool.name:
        raise EvaluationError("TOOL_DISCOVERY_INVALID_NAME")
    argument = string_argument(tool.inputSchema)
    result = await session.call_tool(tool.name, arguments={argument: prompt})
    if result.isError:
        raise EvaluationError("MCP_TOOL_IS_ERROR")
    # Do not coerce images, resources, structuredContent, or error objects into answers.
    text = "\n".join(item.text for item in result.content if item.type == "text")
    if not text.strip():
        raise EvaluationError("MCP_EMPTY_TEXT_RESPONSE")
    if len(text) > MAX_RESPONSE_CHARS:
        raise EvaluationError("MCP_TEXT_RESPONSE_TOO_LARGE")
    return text


async def query_live(endpoint: str, token: str, prompt: str, timeout: float) -> str:
    """Bound the entire connection/query/cleanup in one task for AnyIO compatibility."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def exchange() -> str:
        """Keep transport context managers in the same task as their use."""
        async with streamablehttp_client(
            endpoint, headers={"Authorization": f"Bearer {token}"},
            timeout=timeout, sse_read_timeout=timeout,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                return await query_session(session, prompt)

    try:
        return await asyncio.wait_for(exchange(), timeout=timeout)
    except TimeoutError:
        raise EvaluationError("MCP_QUERY_TIMEOUT") from None
    except ExceptionGroup as group:
        # AnyIO context-manager cleanup can wrap our precise diagnostic. Preserve
        # only known safe codes; never serialize SDK exceptions or HTTP bodies.
        pending: list[BaseException] = list(group.exceptions)
        while pending:
            error = pending.pop()
            if isinstance(error, EvaluationError):
                raise EvaluationError(str(error)) from None
            if isinstance(error, BaseExceptionGroup):
                pending.extend(error.exceptions)
        raise EvaluationError("MCP_TRANSPORT_FAILURE") from None