---
name: local-dev
description: How to stand up and verify the makefile-mcp dev environment (uv venv, tests, lint, MCP stdio client check) on a sandbox computer.
version: 1
---

# Local Dev — makefile-mcp

Durable record of the LOCAL-DEV onboarding run (2026-09-06, sandbox `i7udzej394irdg8yemvsz`, computer `cmp_VuIVlfOc`). Outcome: **dev stack healthy**, snapshot `mfxcuk1ure329m8tqtpd:default`.

## Environment

- Python 3.10+ required (sandbox used 3.13.14). `uv` must be on PATH — on a fresh sandbox install it first: `pip install uv`.
- No external services (no DB/Redis), no env vars, no ports. The "app" is a stdio MCP server.
- Setup (canonical): `make install` → creates `.venv`, installs `-e ".[dev]"` (fastmcp, ruff, pytest, pre-commit).

## Warm-venv gotcha

`make install` runs `uv venv .venv --seed` unconditionally and **errors if `.venv` already exists**. Fixes: `make clean` first, `export UV_VENV_CLEAR=1`, or (preferred on a warm venv) skip make and run `uv run pytest tests/ -v` / `uv run ruff check .` directly, exactly like CI. Every Makefile target re-runs `install`, so slow `make test` invocations are normal.

## Verification sequence (all must pass)

1. `make test` (or `uv run pytest tests/ -v`) → expect **117 passed** (~4s).
2. `make lint-check` → `ruff check .` → all checks passed.
3. `make format-check` → `ruff format --check .` → 4 files already formatted.
4. `.venv/bin/python makefile_mcp.py --help` → argparse usage.
5. MCP end-to-end (primary flow) — client against the server over stdio:

```python
# /tmp/mcp-verify/verify.py in the snapshot session
import asyncio, pathlib
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

REPO = pathlib.Path("/home/user/work/makefile-mcp")


async def main():
    t = StdioTransport(
        command=str(REPO / ".venv/bin/python"), args=[str(REPO / "makefile_mcp.py"), "--makefile", "/path/to/Makefile"]
    )
    async with Client(t) as client:
        tools = sorted(x.name for x in await client.list_tools())
        r = await client.call_tool("make_hello", {})  # a target from your Makefile
        print(r.content[0].text)


asyncio.run(main())
```

Expect tool names `make_<target>` plus `list_available_targets`, `get_makefile_info`, `get_output`, `search_output`; a successful call returns JSON with `exit_code`, `execution_id`, `stdout_tail`; `get_output(execution_id=N)` replays cached stdout. Filters: `--include a,b` / `--exclude a,b` verified to hide the corresponding tools. Tool names use **underscores**, not the README's dashes.

## Notes

- Server banners ("Starting Makefile MCP server", target list) go to **stderr**; JSON-RPC is stdout-only. Pipe stderr away when scripting (`2>/dev/null`).
- `make clean` deletes `.venv` — re-run `make install` after it.
- Evidence captured during onboarding: pytest 117 passed; ruff clean; MCP client transcript (tools list, execution, cached-output retrieval, exclude filter, dry-run).
