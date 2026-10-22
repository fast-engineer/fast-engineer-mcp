"""Request handlers for the local server: each forwards to the hosted endpoint.

The tool catalogue is never defined here. fast.engineer decides which tools
exist and which are released, so the local server lists whatever the hosted
endpoint lists, schemas included. That keeps an installed client from ever
offering a tool the service has withdrawn, or hiding one it has added.

Protocol errors (unknown tool, bad params) pass through as JSON-RPC errors.
A ``tools/call`` that fails at the HTTP layer (quota, outage, bad key) is
returned as a tool error instead, so the model can read the message and
tell the engineer what to do.
"""

from __future__ import annotations

from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
)

from .api_client import Remote


class ProxyHandlers:
    def __init__(self, remote: Remote):
        self.remote = remote

    async def list_tools(self, ctx: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
        return await self.remote.call(lambda s: s.list_tools(params=params))

    async def call_tool(self, ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        try:
            result = await self.remote.call(lambda s: s.call_tool(params.name, params.arguments))
        except MCPError as exc:
            return CallToolResult(content=[TextContent(type="text", text=exc.message)], is_error=True)
        if not isinstance(result, CallToolResult):
            # fast.engineer never asks for input mid-call; anything else is a contract change.
            return CallToolResult(
                content=[TextContent(type="text", text="fast.engineer returned an unexpected result shape.")],
                is_error=True,
            )
        return result

    async def list_prompts(
        self, ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListPromptsResult:
        return await self.remote.call(lambda s: s.list_prompts(params=params))

    async def get_prompt(self, ctx: ServerRequestContext, params: GetPromptRequestParams) -> GetPromptResult:
        result = await self.remote.call(lambda s: s.get_prompt(params.name, params.arguments))
        if not isinstance(result, GetPromptResult):
            raise MCPError(code=-32603, message="fast.engineer returned an unexpected prompt result.")
        return result
