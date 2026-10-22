"""Where the API key comes from, and how it is stored.

Resolution order (first hit wins):

1. ``--key`` on the command line.
2. The ``FAST_ENGINEER_API_KEY`` environment variable.
3. The credentials file written by ``fast-engineer-mcp login``.

No key means the anonymous tier of the free endpoint, which is limited per
IP address. The key is sent as ``Authorization: Bearer <key>`` and never
logged; ``redact`` is the only way a key should ever reach a log line.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_API_KEY = "FAST_ENGINEER_API_KEY"
ENV_URL = "FAST_ENGINEER_MCP_URL"
ENV_CONFIG_DIR = "FAST_ENGINEER_CONFIG_DIR"
CREDENTIALS_FILE = "credentials.json"


def config_dir() -> Path:
    """The per-user directory that holds the credentials file."""
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "fast-engineer"


def credentials_path() -> Path:
    return config_dir() / CREDENTIALS_FILE


@dataclass(frozen=True)
class Credentials:
    api_key: str | None
    source: str  # "flag", "env", "file" or "none"


def _read_stored_key() -> str | None:
    path = credentials_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    key = payload.get("api_key") if isinstance(payload, dict) else None
    return key.strip() if isinstance(key, str) and key.strip() else None


def resolve_credentials(explicit_key: str | None = None) -> Credentials:
    """The key to use, and where it came from."""
    if explicit_key and explicit_key.strip():
        return Credentials(explicit_key.strip(), "flag")
    env_key = os.environ.get(ENV_API_KEY, "").strip()
    if env_key:
        return Credentials(env_key, "env")
    stored = _read_stored_key()
    if stored:
        return Credentials(stored, "file")
    return Credentials(None, "none")


def store_key(api_key: str) -> Path:
    """Write the key to the credentials file, readable by the current user only."""
    key = api_key.strip()
    if not key:
        raise ValueError("The API key is empty.")
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"api_key": key}, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows has no POSIX modes; the per-user profile directory is the protection there.
    os.replace(tmp, path)
    return path


def clear_key() -> bool:
    """Delete the credentials file. Returns True if there was one."""
    path = credentials_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def redact(api_key: str | None) -> str:
    """A loggable form of a key: its first characters only."""
    if not api_key:
        return "(none)"
    return api_key[:6] + "…" if len(api_key) > 6 else "…"


def resolve_url(explicit_url: str | None = None) -> str:
    from . import DEFAULT_URL

    if explicit_url and explicit_url.strip():
        return explicit_url.strip()
    return os.environ.get(ENV_URL, "").strip() or DEFAULT_URL
