"""The local MCP server: stdio in front, fast.engineer behind.

``build_server`` wires the forwarding handlers into the SDK's low-level
``Server``; ``serve`` runs it over the process's stdin/stdout, which is how
MCP clients launch local servers. All logging goes to stderr: stdout is the
protocol channel and a stray print would corrupt it.
"""

from __future__ import annotations

import logging

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .api_client import Remote
from .tools import ProxyHandlers

SERVER_NAME = "fast-engineer"
SERVER_TITLE = "fast.engineer — electrical engineering"
WEBSITE_URL = "https://fast.engineer/mcp/"

FALLBACK_INSTRUCTIONS = (
    "Verified electrical engineering calculations (cable sizing, fault current, maximum demand and more) "
    "computed by fast.engineer. Every result carries a provenance envelope: quote the method, the inputs "
    "and the warnings when presenting a number."
)


def build_server(remote: Remote, *, instructions: str | None = None) -> Server:
    handlers = ProxyHandlers(remote)
    return Server(
        SERVER_NAME,
        version=__version__,
        title=SERVER_TITLE,
        instructions=instructions or FALLBACK_INSTRUCTIONS,
        website_url=WEBSITE_URL,
        on_list_tools=handlers.list_tools,
        on_call_tool=handlers.call_tool,
        on_list_prompts=handlers.list_prompts,
        on_get_prompt=handlers.get_prompt,
    )


async def serve(
    url: str,
    api_key: str | None,
    *,
    stdin: anyio.AsyncFile[str] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
) -> None:
    """Serve one stdio connection until the client closes it."""
    async with Remote(url, api_key) as remote:
        instructions = await remote.instructions()
        server = build_server(remote, instructions=instructions)
        async with stdio_server(stdin, stdout) as (read, write):
            await server.run(read, write, server.create_initialization_options())


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        stream=__import__("sys").stderr,
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not verbose:
        logging.getLogger("fast_engineer_mcp").setLevel(logging.INFO)
