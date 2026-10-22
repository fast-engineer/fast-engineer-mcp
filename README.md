# fast-engineer-mcp

A local [MCP](https://modelcontextprotocol.io) server that gives AI assistants
fast.engineer's verified electrical engineering calculations: cable sizing,
fault current, maximum demand, unit conversion and more, each result with a
provenance envelope (method, inputs, assumptions, warnings).

Nothing is calculated on your machine. The package starts a small server over
stdio, lists the tools the hosted service offers, and forwards every call over
HTTPS to `https://fast.engineer/mcp`. Accounts, quotas and the calculation
engines live on the service.

## Install

```
python -m pip install fast-engineer-mcp
```

or run it without installing, with [uv](https://docs.astral.sh/uv/):

```
uvx fast-engineer-mcp
```

Python 3.11 or newer.

## Free key (optional)

The service works anonymously with a small per-IP quota. A free key raises it.
Enter your email at <https://fast.engineer/mcp/>, then store the key that is
emailed to you:

```
fast-engineer-mcp login
```

The key is written to a file only your user can read
(`~/.config/fast-engineer/credentials.json` on Linux,
`~/Library/Application Support/fast-engineer/` on macOS,
`%APPDATA%\fast-engineer\` on Windows). `FAST_ENGINEER_API_KEY` in the
environment, or `--key`, takes precedence over the file. `fast-engineer-mcp
logout` removes it.

Check the connection and see the current tool catalogue:

```
fast-engineer-mcp check
```

## Connect a client

**Claude Desktop** — `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fast-engineer": {
      "command": "fast-engineer-mcp"
    }
  }
}
```

(Use `"command": "uvx", "args": ["fast-engineer-mcp"]` to run it with uv.)

**Claude Code**:

```
claude mcp add fast-engineer -- fast-engineer-mcp
```

**Cursor** — `~/.cursor/mcp.json`, **VS Code** — `.vscode/mcp.json`, and any
other client that launches stdio servers: the command is `fast-engineer-mcp`
with no arguments. Pass the key through the environment instead of `login` if
the client manages secrets for you:

```json
{
  "mcpServers": {
    "fast-engineer": {
      "command": "fast-engineer-mcp",
      "env": { "FAST_ENGINEER_API_KEY": "YOUR_KEY" }
    }
  }
}
```

Clients that can connect to remote MCP servers directly do not need this
package: point them at `https://fast.engineer/mcp` (see the
[connection guide](https://fast.engineer/mcp/help/connect/)). This package is
for clients that only launch local servers, or that cannot send an
`Authorization` header.

## Using it

Ask the assistant an engineering question, for example:

> Size a copper cable for a 40 A three-phase load over 100 m at 400 V with a
> 5 % voltage-drop limit.

The assistant calls the matching tool, and the result comes back with the
calculation method, the inputs used, the assumptions made and any warnings.
Quote them. Tools are listed with full input and output schemas, so the
assistant knows which parameters it must ask you for.

Limits are per hour and per day. When a quota is used up the tool returns a
message saying so, how long to wait, and how to get a key. Listing tools never
uses quota.

## Command reference

```
fast-engineer-mcp                 serve over stdio (what clients run)
fast-engineer-mcp login [--key K] store a key (prompts when --key is omitted)
fast-engineer-mcp logout          forget the stored key
fast-engineer-mcp check           connect and print the server and its tools
fast-engineer-mcp --version
```

Serve and check accept `--url` (another endpoint, also `FAST_ENGINEER_MCP_URL`),
`--key`, `--anonymous` (ignore any configured key) and `--verbose` (debug
logging on stderr).

## How it works

```
AI client ──stdio──▶ fast-engineer-mcp ──HTTPS──▶ https://fast.engineer/mcp ──▶ calculation engines
                      (this package)               (auth, quotas, audit)
```

- `tools/list`, `tools/call`, `prompts/list` and `prompts/get` are forwarded
  as-is. The catalogue is whatever the service releases; nothing is hard-coded
  here, so an installed client never offers a withdrawn tool.
- The key travels as `Authorization: Bearer …`. It is never logged.
- HTTP-level replies from the service (429 quota, 503 paused, 401/403
  credentials) become readable tool errors instead of a bare failure.
- If the service is unreachable the server still starts; each call reports the
  problem, and the connection is retried on the next call.

## Development

```
git clone https://github.com/fast-engineer/fast-engineer-mcp
cd fast-engineer-mcp
python -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
```

The tests run a fake endpoint in-process (and, for the end-to-end test, on a
local port with uvicorn); they never contact fast.engineer.

Releases: bump `__version__` in `src/fast_engineer_mcp/__init__.py`, add a
changelog entry, tag `vX.Y.Z` and publish a GitHub release. The
`publish.yml` workflow tests, builds and uploads to PyPI through trusted
publishing.

## Licence

MIT, see [LICENSE](LICENSE). The licence covers this client only; the
calculation engines and the fast.engineer service are governed by the
service's own terms of use.
