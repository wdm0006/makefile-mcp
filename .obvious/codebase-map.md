# Codebase Map — wdm0006/makefile-mcp

Single-module Python MCP server. Depth cap 2.

| Path | Contents |
|---|---|
| `./` (root) | `makefile_mcp.py` — the entire server (867 lines: `MakefileParser`, `OutputCache`, dynamic `make_*` tool factory, utility tools, `main()`); `pyproject.toml` (hatchling, deps, ruff config); `Makefile` (install/lint/format/test/clean, uv-driven); `README.md`; `LICENSE` (MIT); `.gitignore`; `.pre-commit-config.yaml` |
| `.github/workflows/` | `ci.yml` — lint job (ruff check + format check, py3.12) and test job (pytest, matrix py3.10/3.11/3.12), all via uv |
| `tests/` | `test_makefile_mcp.py` (117 tests: parser, filtering, tool creation, execution, cache, search) + `__init__.py` |

No sub-apps, no services, no other source directories.
