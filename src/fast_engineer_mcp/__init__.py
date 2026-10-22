"""fast-engineer-mcp: a local MCP server that forwards to fast.engineer.

The package exposes fast.engineer's electrical engineering calculation tools
to any MCP client that launches local (stdio) servers. Nothing is computed
here: every tool call is forwarded over HTTPS to the hosted endpoint, which
authenticates the caller, enforces quotas and runs the calculation engines.
"""

__version__ = "0.1.0"

DEFAULT_URL = "https://fast.engineer/mcp"
SIGNUP_URL = "https://fast.engineer/mcp/"
