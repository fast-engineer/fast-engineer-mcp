import json
import os
import stat
import sys

import pytest

from fast_engineer_mcp import DEFAULT_URL
from fast_engineer_mcp.auth import (
    clear_key,
    credentials_path,
    redact,
    resolve_credentials,
    resolve_url,
    store_key,
)

pytestmark = pytest.mark.usefixtures("isolated_config")


def test_no_key_anywhere_is_anonymous():
    creds = resolve_credentials()
    assert creds.api_key is None
    assert creds.source == "none"


def test_flag_beats_env_beats_file(monkeypatch):
    def resolved(explicit=None):
        creds = resolve_credentials(explicit)
        return creds.api_key, creds.source

    store_key("file-key")
    assert resolved() == ("file-key", "file")
    monkeypatch.setenv("FAST_ENGINEER_API_KEY", "env-key")
    assert resolved() == ("env-key", "env")
    assert resolved("flag-key") == ("flag-key", "flag")
    assert resolved("   ") == ("env-key", "env")


def test_store_key_writes_private_json_file(isolated_config):
    path = store_key("  abc123  ")
    assert path == credentials_path() == isolated_config / "credentials.json"
    assert json.loads(path.read_text()) == {"api_key": "abc123"}
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not path.with_suffix(".tmp").exists()


def test_store_rejects_empty_key():
    with pytest.raises(ValueError):
        store_key("   ")


def test_clear_key():
    assert clear_key() is False
    store_key("k")
    assert clear_key() is True
    assert resolve_credentials().source == "none"


def test_corrupt_credentials_file_is_ignored():
    path = credentials_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert resolve_credentials().source == "none"
    path.write_text(json.dumps({"api_key": "   "}))
    assert resolve_credentials().source == "none"


def test_redact_never_shows_the_whole_key():
    assert redact(None) == "(none)"
    assert redact("abcdefghijkl") == "abcdef…"
    assert "abcdefghijkl" not in redact("abcdefghijkl")
    assert redact("abc") == "…"


def test_resolve_url(monkeypatch):
    assert resolve_url() == DEFAULT_URL
    monkeypatch.setenv("FAST_ENGINEER_MCP_URL", "http://localhost:8000/mcp")
    assert resolve_url() == "http://localhost:8000/mcp"
    assert resolve_url("http://x/mcp") == "http://x/mcp"
