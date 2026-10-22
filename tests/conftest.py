"""A fake fast.engineer endpoint, served in-process.

``FakeRemote`` is a real MCP server (the SDK's Streamable HTTP app, stateless
and JSON-answering like the hosted one) wrapped in an ASGI layer that records
every request's headers and can be told to answer with the plain-JSON HTTP
errors fast.engineer uses for quota and kill-switch replies.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from fast_engineer_mcp.api_client import Remote

FAKE_URL = "http://testserver/mcp"
FAKE_INSTRUCTIONS = "Fake fast.engineer: numbers with provenance."

HP_TO_KW = 0.7457


def build_mcp_server() -> MCPServer:
    server = MCPServer("fake-fast-engineer", instructions=FAKE_INSTRUCTIONS, version="9.9.9")

    @server.tool(name="fe_util_convert", description="Convert a quantity between units. Not for: currency.")
    def convert(value: float, from_unit: str, to_unit: str) -> dict[str, float | str]:
        if (from_unit, to_unit) != ("hp", "kW"):
            raise ValueError(f"Unsupported conversion {from_unit} -> {to_unit}")
        return {"value": value * HP_TO_KW, "unit": to_unit}

    @server.tool(name="fe_calc_voltage_drop", description="Voltage drop along a cable. Not for: DC.")
    def voltage_drop(length_m: float, current_a: float) -> dict[str, float]:
        return {"drop_v": 0.01 * length_m * current_a}

    @server.prompt(name="size_a_cable", description="Walk through sizing one cable.")
    def size_a_cable(load_kw: str) -> str:
        return f"Size a cable for a {load_kw} kW load."

    return server


class FakeRemote:
    def __init__(self):
        self.server = build_mcp_server()
        self.app = self.server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        self.requests: list[dict] = []
        self.forced: tuple[int, dict, dict[str, str]] | None = None

    def force(self, status: int, body: dict, headers: dict[str, str] | None = None) -> None:
        """Answer every following request with this plain-JSON HTTP error, like fast.engineer's gate does."""
        self.forced = (status, body, headers or {})

    def release(self) -> None:
        self.forced = None

    @asynccontextmanager
    async def running(self):
        async with self.server.session_manager.run():
            yield self

    def transport(self) -> httpx2.AsyncBaseTransport:
        return httpx2.ASGITransport(app=self)

    def remote(self, api_key: str | None = None) -> Remote:
        return Remote(FAKE_URL, api_key, transport_factory=self.transport)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = await _read_body(receive)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        try:
            message = json.loads(body) if body else None
        except ValueError:
            message = None
        self.requests.append({"headers": headers, "message": message})
        if self.forced is not None:
            status, payload, extra = self.forced
            raw = [(b"content-type", b"application/json")]
            raw += [(k.lower().encode(), v.encode()) for k, v in extra.items()]
            await send({"type": "http.response.start", "status": status, "headers": raw})
            await send({"type": "http.response.body", "body": json.dumps(payload).encode()})
            return
        await self.app(scope, _replay(body, receive), send)

    @property
    def calls(self) -> list[dict]:
        return [r for r in self.requests if (r["message"] or {}).get("method") == "tools/call"]


async def _read_body(receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replay(body: bytes, receive):
    delivered = False

    async def replay():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


@pytest.fixture
def fake() -> FakeRemote:
    return FakeRemote()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("FAST_ENGINEER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("FAST_ENGINEER_API_KEY", raising=False)
    monkeypatch.delenv("FAST_ENGINEER_MCP_URL", raising=False)
    return tmp_path / "cfg"
