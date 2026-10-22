"""Command line: ``fast-engineer-mcp [serve|login|logout|check]``.

With no sub-command it serves, because that is the command MCP clients run.
"""

from __future__ import annotations

import argparse
import getpass
import sys

import anyio

from . import DEFAULT_URL, SIGNUP_URL, __version__
from .auth import (
    ENV_API_KEY,
    ENV_URL,
    clear_key,
    credentials_path,
    redact,
    resolve_credentials,
    resolve_url,
    store_key,
)

SERVE_COMMANDS = {"serve", None}


def _connection_options(*, for_subcommand: bool) -> argparse.ArgumentParser:
    """The flags serve and check share. On a sub-command they default to SUPPRESS so a value given
    before the sub-command (``fast-engineer-mcp --key K serve``) is not overwritten with None."""
    default = argparse.SUPPRESS if for_subcommand else None
    flag_default = argparse.SUPPRESS if for_subcommand else False
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=default, help=f"MCP endpoint URL (default {DEFAULT_URL})")
    common.add_argument("--key", default=default, help="API key (prefer the environment variable or `login`)")
    common.add_argument("--anonymous", action="store_true", default=flag_default, help="ignore any configured key")
    common.add_argument("-v", "--verbose", action="store_true", default=flag_default, help="debug logging on stderr")
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fast-engineer-mcp",
        description="Local MCP server for fast.engineer's electrical engineering calculations.",
        epilog=(
            f"Keys: --key, then ${ENV_API_KEY}, then the file written by `login`. "
            f"Endpoint: --url, then ${ENV_URL}, then {DEFAULT_URL}."
        ),
        parents=[_connection_options(for_subcommand=False)],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")
    sub_options = [_connection_options(for_subcommand=True)]
    sub.add_parser("serve", parents=sub_options, help="serve MCP over stdio (the default)")
    login = sub.add_parser("login", help="store a free key from fast.engineer")
    login.add_argument("--key", help="the key; prompted for when omitted")
    sub.add_parser("logout", help="forget the stored key")
    sub.add_parser("check", parents=sub_options, help="connect, then print the server and its tools")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "login":
        return cmd_login(args.key)
    if args.command == "logout":
        return cmd_logout()
    if args.command == "check":
        return cmd_check(args)
    return cmd_serve(args)


def _credentials(args):
    if getattr(args, "anonymous", False):
        return None, "anonymous"
    creds = resolve_credentials(getattr(args, "key", None))
    return creds.api_key, creds.source


def cmd_serve(args) -> int:
    from .server import configure_logging, serve

    configure_logging(args.verbose)
    api_key, source = _credentials(args)
    url = resolve_url(args.url)
    print(f"fast-engineer-mcp {__version__}: {url}, key {redact(api_key)} ({source})", file=sys.stderr)
    try:
        anyio.run(serve, url, api_key)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_login(key: str | None) -> int:
    if not key:
        print(f"Get a free key by entering your email at {SIGNUP_URL}", file=sys.stderr)
        key = getpass.getpass("Paste the key (input hidden): ")
    try:
        path = store_key(key)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Stored key {redact(key)} in {path}", file=sys.stderr)
    return 0


def cmd_logout() -> int:
    if clear_key():
        print(f"Removed {credentials_path()}", file=sys.stderr)
    else:
        print("No stored key.", file=sys.stderr)
    return 0


def cmd_check(args) -> int:
    from .api_client import Remote
    from .server import configure_logging

    configure_logging(args.verbose)
    api_key, source = _credentials(args)
    url = resolve_url(args.url)

    async def run() -> int:
        async with Remote(url, api_key) as remote:
            from mcp.shared.exceptions import MCPError

            try:
                tools = await remote.call(lambda s: s.list_tools())
            except MCPError as exc:
                print(f"error: {exc.message}", file=sys.stderr)
                return 1
            info = remote.init_result.server_info if remote.init_result else None
            print(f"Endpoint:  {url}")
            print(f"Key:       {redact(api_key)} ({source})")
            if info is not None:
                print(f"Server:    {info.name} {info.version}")
            print(f"Tools:     {len(tools.tools)}")
            for tool in tools.tools:
                summary = (tool.description or "").split(". ", 1)[0].strip()
                print(f"  {tool.name:<36} {summary[:70]}")
            return 0

    return anyio.run(run)
