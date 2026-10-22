# Changelog

## 0.1.0 — unreleased

- First release: stdio MCP server forwarding tools and prompts to `https://fast.engineer/mcp`.
- `login` / `logout` store a free key per user; `FAST_ENGINEER_API_KEY` and `--key` override it.
- `check` connects and prints the live tool catalogue.
- Quota (429), outage (503) and credential (401/403) replies are turned into readable tool errors.
