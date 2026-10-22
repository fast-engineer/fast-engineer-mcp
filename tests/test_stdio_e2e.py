"""The installed command, as an MCP client runs it: a subprocess over stdio, talking HTTP to a fake endpoint."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from .conftest import FakeRemote

uvicorn = pytest.importorskip("uvicorn")

pytestmark = pytest.mark.anyio


def _subprocess_env(config_dir, **extra) -> dict[str, str]:
    """Environment for a child running the package: an installed or source-path copy, isolated config."""
    env = {"FAST_ENGINEER_CONFIG_DIR": str(config_dir), **extra}
    for name in ("PATH", "PYTHONPATH", "SYSTEMROOT", "HOME", "USERPROFILE", "TEMP", "TMP"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def served_fake():
    """The fake endpoint on a real local port, in a background thread with its own event loop."""
    fake = FakeRemote()
    port = _free_port()

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            # Run the SDK's session manager for the life of the server.
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await stack.__aenter__()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await stack.__aexit__(None, None, None)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        else:
            await fake(scope, receive, send)

    stack = fake.running()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield fake, f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


async def test_stdio_subprocess_round_trip(served_fake, isolated_config):
    fake, url = served_fake
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fast_engineer_mcp", "--url", url],
        env=_subprocess_env(isolated_config, FAST_ENGINEER_API_KEY="e2e-key"),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("fe_util_convert", {"value": 1, "from_unit": "hp", "to_unit": "kW"})
    assert init.server_info.name == "fast-engineer"
    assert [t.name for t in tools.tools] == ["fe_util_convert", "fe_calc_voltage_drop"]
    assert result.structured_content["unit"] == "kW"
    assert fake.calls[-1]["headers"]["authorization"] == "Bearer e2e-key"


def test_check_command_prints_the_catalogue(served_fake, isolated_config):
    _, url = served_fake
    proc = subprocess.run(
        [sys.executable, "-m", "fast_engineer_mcp", "check", "--url", url, "--anonymous"],
        capture_output=True, text=True, timeout=60, env=_subprocess_env(isolated_config),
    )
    assert proc.returncode == 0, proc.stderr
    assert "Tools:     2" in proc.stdout
    assert "fe_util_convert" in proc.stdout
    assert "Key:       (none) (anonymous)" in proc.stdout


def test_check_command_reports_an_unreachable_endpoint(isolated_config):
    proc = subprocess.run(
        [sys.executable, "-m", "fast_engineer_mcp", "check", "--url", "http://127.0.0.1:9/mcp"],
        capture_output=True, text=True, timeout=60, env=_subprocess_env(isolated_config),
    )
    assert proc.returncode == 1, proc.stderr
    assert "Cannot reach fast.engineer" in proc.stderr


def test_login_and_logout_round_trip(isolated_config):
    env = _subprocess_env(isolated_config)
    login = subprocess.run([sys.executable, "-m", "fast_engineer_mcp", "login", "--key", "stored-key"],
                           capture_output=True, text=True, env=env)
    assert login.returncode == 0, login.stderr
    assert "stored-key" not in login.stderr  # never echoed in full
    assert (isolated_config / "credentials.json").exists()
    logout = subprocess.run([sys.executable, "-m", "fast_engineer_mcp", "logout"],
                            capture_output=True, text=True, env=env)
    assert logout.returncode == 0
    assert not (isolated_config / "credentials.json").exists()
