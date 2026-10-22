import httpx2
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult

from fast_engineer_mcp import __version__
from fast_engineer_mcp.api_client import (
    HTTP_ERROR_CODE,
    UNREACHABLE_CODE,
    Remote,
    describe_http_error,
)

from .conftest import FAKE_INSTRUCTIONS, HP_TO_KW

pytestmark = pytest.mark.anyio

CONVERT = {"value": 1, "from_unit": "hp", "to_unit": "kW"}


# ── describe_http_error ──────────────────────────────────────────────────────

def test_429_anonymous_points_at_the_free_key():
    message, data = describe_http_error(
        429, {"error": "Rate limit exceeded", "detail": "Slow down.", "retry_after_seconds": 90},
        httpx2.Headers({"retry-after": "90"}), has_key=False,
    )
    assert "Retry in 90 s" in message
    assert "fast-engineer-mcp login" in message
    assert data == {"http_status": 429, "detail": "Slow down.", "retry_after_seconds": 90, "kind": "rate_limited"}


def test_429_with_key_does_not_nag_about_login():
    message, _ = describe_http_error(429, {"detail": "Slow down."}, httpx2.Headers(), has_key=True)
    assert "login" not in message


def test_429_falls_back_to_retry_after_header():
    _, data = describe_http_error(429, {}, httpx2.Headers({"retry-after": "7"}), has_key=True)
    assert data["retry_after_seconds"] == "7"


@pytest.mark.parametrize(
    "status, payload, kind, fragment",
    [
        (503, {"detail": "Anonymous access is temporarily disabled."}, "unavailable", "temporarily disabled"),
        (401, {}, "unauthorized", "fast-engineer-mcp login"),
        (403, {"error_description": "needs the project:write scope"}, "forbidden", "project:write"),
        (413, {"error": "Request body too large."}, "too_large", "1 MB"),
        (502, "<html>bad gateway</html>", "http_error", "HTTP 502"),
    ],
)
def test_other_statuses(status, payload, kind, fragment):
    message, data = describe_http_error(status, payload, httpx2.Headers(), has_key=True)
    assert data["kind"] == kind
    assert data["http_status"] == status
    assert fragment in message


# ── Remote against the fake endpoint ─────────────────────────────────────────

async def test_sends_bearer_and_user_agent(fake):
    async with fake.running(), fake.remote("secret-key") as remote:
        result = await remote.call(lambda s: s.call_tool("fe_util_convert", CONVERT))
    assert isinstance(result, CallToolResult) and not result.is_error
    assert result.structured_content["value"] == pytest.approx(HP_TO_KW)
    headers = fake.calls[-1]["headers"]
    assert headers["authorization"] == "Bearer secret-key"
    assert headers["user-agent"] == f"fast-engineer-mcp/{__version__}"


async def test_anonymous_sends_no_authorization_header(fake):
    async with fake.running(), fake.remote() as remote:
        await remote.call(lambda s: s.list_tools())
    assert all("authorization" not in r["headers"] for r in fake.requests)


async def test_instructions_mirror_the_hosted_server(fake):
    async with fake.running(), fake.remote() as remote:
        assert await remote.instructions() == FAKE_INSTRUCTIONS
        assert remote.init_result.server_info.name == "fake-fast-engineer"


async def test_rate_limit_becomes_a_readable_error(fake):
    async with fake.running(), fake.remote() as remote:
        await remote.call(lambda s: s.list_tools())  # connected
        fake.force(429, {"error": "Rate limit exceeded", "detail": "Quota used up.", "retry_after_seconds": 42},
                   {"Retry-After": "42"})
        with pytest.raises(MCPError) as info:
            await remote.call(lambda s: s.call_tool("fe_util_convert", CONVERT))
    assert info.value.code == HTTP_ERROR_CODE
    assert "Quota used up." in info.value.message
    assert "Retry in 42 s" in info.value.message
    assert info.value.data["retry_after_seconds"] == 42


async def test_kill_switch_503(fake):
    async with fake.running(), fake.remote() as remote:
        await remote.call(lambda s: s.list_tools())
        fake.force(503, {"error": "Service unavailable", "detail": "Anonymous access is temporarily disabled."})
        with pytest.raises(MCPError) as info:
            await remote.call(lambda s: s.list_tools())
    assert info.value.code == HTTP_ERROR_CODE
    assert info.value.data["kind"] == "unavailable"


async def test_session_survives_a_translated_error(fake):
    """An HTTP error must not tear down the session: the next call just works."""
    async with fake.running(), fake.remote("k") as remote:
        fake.force(429, {"detail": "later"})
        with pytest.raises(MCPError):
            await remote.call(lambda s: s.list_tools())
        fake.release()
        tools = await remote.call(lambda s: s.list_tools())
    assert [t.name for t in tools.tools] == ["fe_util_convert", "fe_calc_voltage_drop"]


async def test_jsonrpc_errors_pass_through_untouched(fake):
    async with fake.running(), fake.remote() as remote:
        with pytest.raises(MCPError) as info:
            await remote.call(lambda s: s.get_prompt("no_such_prompt", {}))
    assert info.value.code not in (HTTP_ERROR_CODE, UNREACHABLE_CODE)
    assert "Unknown prompt: no_such_prompt" in info.value.message


async def test_tool_errors_are_results_not_exceptions(fake):
    async with fake.running(), fake.remote() as remote:
        result = await remote.call(lambda s: s.call_tool("fe_util_convert", {**CONVERT, "to_unit": "furlongs"}))
    assert result.is_error
    assert "fe_util_convert" in result.content[0].text


async def test_unreachable_endpoint_is_reported_per_call():
    remote = Remote("http://127.0.0.1:9/mcp", None, connect_timeout=2)
    async with remote:
        assert not remote.connected
        with pytest.raises(MCPError) as info:
            await remote.call(lambda s: s.list_tools())
        assert info.value.code == UNREACHABLE_CODE
        assert "127.0.0.1:9" in info.value.message
        assert await remote.instructions() is None
        assert not remote.connected


async def test_reconnects_after_the_connection_drops(fake):
    """Closing the session behind Remote's back must be invisible to the caller."""
    async with fake.running(), fake.remote() as remote:
        first = await remote.ensure()
        await remote.close()
        assert not remote.connected
        tools = await remote.call(lambda s: s.list_tools())
        assert remote.connected
        assert await remote.ensure() is not first
    assert len(tools.tools) == 2
