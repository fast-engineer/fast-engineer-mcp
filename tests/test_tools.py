"""The local server end to end, in memory: a client session talks to ``build_server``, which forwards to the fake."""

from __future__ import annotations

from contextlib import asynccontextmanager

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams

from fast_engineer_mcp import __version__
from fast_engineer_mcp.server import FALLBACK_INSTRUCTIONS, SERVER_NAME, build_server

from .conftest import FAKE_INSTRUCTIONS, HP_TO_KW

pytestmark = pytest.mark.anyio

CONVERT = {"value": 2, "from_unit": "hp", "to_unit": "kW"}


@asynccontextmanager
async def local_server(fake, api_key=None):
    """Yield a ClientSession connected over memory streams to the local proxy server, which uses the fake."""
    async with fake.running(), fake.remote(api_key) as remote:
        server = build_server(remote, instructions=await remote.instructions())
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    server.run, server_streams[0], server_streams[1], server.create_initialization_options(),
                )
                async with ClientSession(*client_streams) as session:
                    init = await session.initialize()
                    yield session, init
                tg.cancel_scope.cancel()


async def test_initialize_mirrors_hosted_instructions_and_names_the_package(fake):
    async with local_server(fake) as (session, init):
        assert init.instructions == FAKE_INSTRUCTIONS
        assert init.server_info.name == SERVER_NAME
        assert init.server_info.version == __version__
        assert init.capabilities.tools is not None
        assert init.capabilities.prompts is not None


async def test_tools_list_is_the_hosted_catalogue_schemas_included(fake):
    async with local_server(fake) as (session, _):
        tools = await session.list_tools()
    names = [t.name for t in tools.tools]
    assert names == ["fe_util_convert", "fe_calc_voltage_drop"]
    convert = tools.tools[0]
    assert set(convert.input_schema["properties"]) == {"value", "from_unit", "to_unit"}
    assert convert.output_schema is not None
    assert "Not for:" in convert.description


async def test_call_tool_forwards_arguments_and_returns_structured_content(fake):
    async with local_server(fake, api_key="k1") as (session, _):
        result = await session.call_tool("fe_util_convert", CONVERT)
    assert not result.is_error
    assert result.structured_content["value"] == pytest.approx(2 * HP_TO_KW)
    forwarded = fake.calls[-1]
    assert forwarded["message"]["params"]["arguments"] == CONVERT
    assert forwarded["headers"]["authorization"] == "Bearer k1"


async def test_quota_error_reaches_the_model_as_a_tool_error(fake):
    async with local_server(fake) as (session, _):
        fake.force(429, {"error": "Rate limit exceeded", "detail": "Hourly quota used.", "retry_after_seconds": 600})
        result = await session.call_tool("fe_util_convert", CONVERT)
        fake.release()
        again = await session.call_tool("fe_util_convert", CONVERT)
    assert result.is_error
    text = result.content[0].text
    assert "Hourly quota used." in text and "Retry in 600 s" in text and "login" in text
    assert not again.is_error


async def test_unknown_tool_is_reported_and_the_server_survives(fake):
    async with local_server(fake) as (session, _):
        result = await session.call_tool("fe_nope", {})
        assert result.is_error
        assert "fe_nope" in result.content[0].text
        # the server is still alive afterwards
        assert len((await session.list_tools()).tools) == 2


async def test_prompts_are_forwarded(fake):
    async with local_server(fake) as (session, _):
        prompts = await session.list_prompts()
        got = await session.get_prompt("size_a_cable", {"load_kw": "75"})
    assert [p.name for p in prompts.prompts] == ["size_a_cable"]
    assert "75 kW" in got.messages[0].content.text


async def test_fallback_instructions_when_hosted_endpoint_is_down(fake):
    from fast_engineer_mcp.api_client import Remote

    remote = Remote("http://127.0.0.1:9/mcp", None, connect_timeout=2)
    server = build_server(remote, instructions=await remote.instructions())
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.run, server_streams[0], server_streams[1], server.create_initialization_options())
            async with ClientSession(*client_streams) as session:
                init = await session.initialize()
                result = await session.call_tool("fe_util_convert", CONVERT)
                with pytest.raises(MCPError) as info:
                    await session.list_tools()
            tg.cancel_scope.cancel()
    assert init.instructions == FALLBACK_INSTRUCTIONS
    assert result.is_error and "Cannot reach fast.engineer" in result.content[0].text
    assert "Cannot reach fast.engineer" in info.value.message
