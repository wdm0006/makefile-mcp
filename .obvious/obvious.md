# makefile-mcp — Agent Guide

Repo: **wdm0006/makefile-mcp** (default branch: `main`)
A Model Context Protocol (MCP) server that parses a Makefile and exposes each target as an executable tool for AI assistants. Single-module Python project — no web app, no ports, no external services, no required env vars. The server speaks MCP over **stdio** (`mcp_server.run()`).

## Stack

| Component | Detail |
|---|---|
| Language | Python `>=3.10` (CI matrix: 3.10 / 3.11 / 3.12; sandbox venv uses 3.13) |
| Package manager | `uv` (Makefile + CI both drive `uv venv` / `uv pip install -e ".[dev]"` / `uv run`) |
| Core dependency | `fastmcp>=3.4.5,<4.0.0` (installed: 3.4.7) |
| Dev dependencies | `ruff` (lint + format), `pytest`, `pre-commit` |
| Build backend | hatchling (`makefile-mcp` 0.1.0) |
| Entry point | console script `makefile-mcp` → `makefile_mcp:main`; also `python makefile_mcp.py` |
| Transport | stdio (no HTTP port) |
| External services | none |
| Required env vars | none |

## Commands

```bash
make install        # create .venv and install -e ".[dev]" (fails if .venv exists — see Quirks)
make test           # uv run pytest tests/
make lint-check     # uv run ruff check .
make format-check   # uv run ruff format --check .
make lint           # ruff check --fix .   (mutates files)
make format         # ruff format .        (mutates files)
make clean          # remove .venv and caches
```

Run the server directly (what an MCP client launches):

```bash
uv run makefile_mcp.py --makefile /path/to/Makefile [--include a,b] [--exclude a,b] \
  [--working-dir DIR] [--max-cached-executions N] [--tail-lines N]
```

On a warm venv, prefer the CI-style direct form (`uv venv .venv && uv pip install -e ".[dev]"` once, then `uv run pytest tests/ -v`, `uv run ruff check .`) instead of re-running `make install`.

## Codebase Map

See [codebase-map.md](codebase-map.md). Everything of substance lives in `makefile_mcp.py` (867 lines): `MakefileParser` (target/description parsing), `OutputCache` (bounded, locked execution cache), `make_tool_name()` + `register_make_tools()` (dynamic per-target tools `make_<target>`), utility tools (`list_available_targets`, `get_makefile_info`, `get_output`, `search_output`), and `main()` (CLI parse → filter → register → `mcp_server.run()`).

Note: registered MCP tool names use **underscores** (`make_hello`, `list_available_targets`); the README's dashed spellings (`list-available-targets`) are prose, not wire names.

## Local Verification Summary

Verified 2026-09-06 on sandbox `i7udzej394irdg8yemvsz` (see snapshot below):

- `make test` → **117 passed** in 3.99s (pytest, `tests/test_makefile_mcp.py`)
- `make lint-check` → `ruff check .` — all checks passed
- `make format-check` → `ruff format --check .` — 4 files already formatted
- `python makefile_mcp.py --help` → argparse usage printed correctly
- **Primary flow (MCP client, end-to-end over stdio)**: launched the server against a scratch Makefile with `fastmcp.Client` + `StdioTransport`; handshake OK; `list_tools` returned `make_hello`, `make_greet`, `list_available_targets`, `get_makefile_info`, `get_output`, `search_output`; `make_hello` executed (exit 0, output captured, `execution_id: 1`); `get_output(1)` returned the cached stdout; `--exclude test,clean` against the repo's own Makefile correctly hid `make_test`/`make_clean`; `make_lint` with `dry_run: true` echoed the command without executing.

Dev stack: **healthy**. No external services to start; the "app" is the stdio server itself, verified by driving it as a client (script preserved at `/tmp/mcp-verify/verify.py` in the sandbox session).

## Sandbox Snapshot

- Snapshot (E2B template): `mfxcuk1ure329m8tqtpd:default`, built `2026-09-06T20:22:56.969Z`
- Live session captured: `i7udzej394irdg8yemvsz` on computer `cmp_VuIVlfOc`
- Baked in: `uv` 0.12.10 on PATH (installed via `pip install uv`), repo `.venv` with `-e ".[dev]"` (fastmcp 3.4.7, ruff, pytest, pre-commit), warm-verified test/lint/MCP flow.

## Quirks / Gotchas

1. **`uv` is not preinstalled** on a fresh sandbox — `pip install uv` first (or `curl -LsSf https://astral.sh/uv/install.sh | sh`).
2. **`make install` errors on a warm venv** (`uv venv` refuses to overwrite `.venv`). Either `make clean` first, export `UV_VENV_CLEAR=1`, or skip `make install` and use `uv run` directly.
3. Every Makefile target depends on `install`, so each `make <target>` re-runs the venv setup — expected, not a failure.
4. Server startup banners go to **stderr**; JSON-RPC traffic is on stdout. When scripting the server, filter stderr.
