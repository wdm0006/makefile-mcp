# Makefile MCP Server

A Model Context Protocol (MCP) server that exposes Makefile targets as executable tools for AI assistants.

## Features

- **Dynamic make target tools**: every Makefile target becomes an executable tool named `make_<target>` (e.g. `make_build`, `make_test`, `make_clean`)
- **`list_available_targets`**: list every make target the server exposes, with descriptions and tool names
- **`get_makefile_info`**: detailed information about the Makefile and the include/exclude filtering configuration
- **`get_output`**: page through the full cached stdout/stderr of any previous execution by `execution_id`
- **`search_output`**: search a previous execution's full cached output for a literal substring, with surrounding context
- **Target discovery**: Makefiles are parsed automatically to discover targets and descriptions
- **Comment-based descriptions**: comments above a target become its tool description
- **Include/exclude filtering**: `--include` and `--exclude` control which targets are exposed as tools
- **Dry-run mode**: call any target tool with `dry_run=true` to see the commands make would run without executing them (a tool parameter — the server itself takes no `--dry-run` CLI flag)
- **Bounded responses, full output retained**: inline responses carry a configurable tail of each stream plus line/char totals; the complete output stays addressable through the `execution_id`

## Install

```bash
# Run directly from GitHub (no install needed)
uvx --from git+https://github.com/wdm0006/makefile-mcp makefile-mcp --makefile /path/to/Makefile

# Or install from source
git clone https://github.com/wdm0006/makefile-mcp
cd makefile-mcp
uv sync
uv run makefile_mcp.py --makefile /path/to/Makefile
```

## Usage

```bash
# Use default Makefile in current directory
makefile-mcp

# Use specific Makefile
makefile-mcp --makefile /path/to/Makefile

# Include only specific targets
makefile-mcp --include build,test,clean

# Exclude specific targets
makefile-mcp --exclude deploy,publish

# Custom working directory
makefile-mcp --working-dir /path/to/project

# Keep 50 cached executions instead of the default 20
makefile-mcp --max-cached-executions 50

# Include only the last 10 lines of each stream inline
makefile-mcp --tail-lines 10

# Kill targets that run longer than 60 seconds
makefile-mcp --timeout 60
```

### CLI reference

| Flag | Default | Meaning |
|---|---|---|
| `--makefile PATH` | `Makefile` | Makefile to parse and execute |
| `--include a,b` | all targets | expose only these targets |
| `--exclude a,b` | none | hide these targets |
| `--working-dir PATH` | the Makefile's directory | directory make runs in |
| `--max-cached-executions N` | `20` | how many executions stay cached; the oldest is evicted first, so old `execution_id`s eventually expire |
| `--tail-lines N` | `50` | lines of each stream included inline in make tool responses |
| `--timeout SECONDS` | `300` | kill a running target after this many seconds; the timeout is reported as an error and any partial output is still cached |

## MCP Client Configuration

```json
{
  "mcpServers": {
    "makefile": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/wdm0006/makefile-mcp",
        "makefile-mcp",
        "--makefile", "/path/to/your/project/Makefile",
        "--exclude", "deploy,publish"
      ]
    }
  }
}
```

## Tools

Every server exposes four utility tools plus one `make_<target>` tool per discovered make target (after include/exclude filtering). All tool names are snake_case.

### `make_<target>(additional_args=None, dry_run=False)`

Executes `make -C <working-dir> -f <makefile> <target>`.

- `additional_args` — a string of extra make arguments (e.g. `"-j4 VERBOSE=1"`). Validated against the security allowlist below before make runs.
- `dry_run` — when `true`, make runs with `-n`: it prints the commands it would execute without running them. This is a **tool parameter**, not a CLI flag of the server process.
- The response carries `status`, `exit_code`, the exact `command`, `working_directory`, a bounded tail of stdout/stderr with line/char totals, and an `execution_id` addressing the full cached output.
- A target killed by the timeout returns `status: "error"` with a timeout message and `exit_code: -1`; whatever it printed before the kill is cached and readable via `get_output`/`search_output`.

### `list_available_targets()`

Returns the Makefile path, working directory, total and exposed target counts, and each exposed target's `name`, `description`, and `tool_name`.

### `get_makefile_info()`

Returns all targets (including filtered-out ones) versus the exposed set, plus the active include/exclude filters.

### `get_output(execution_id, stream="stdout", start_line=0, end_line=100)`

Pages through a cached execution's full output. Lines are 0-indexed and `end_line` is exclusive; the response includes `total_lines` and the selected `content`. Use this to read past the inline tail.

### `search_output(execution_id, pattern, stream="stdout", context_lines=3, max_results=20)`

Case-insensitive literal substring search over a cached execution's full output. Each match comes with `context_lines` of surrounding context and its line number. Every match is counted in `total_matches`, but at most `max_results` matches are returned; when `truncated` is true, page through the rest with `get_output` around the returned line numbers.

### Execution lifecycle

- **`execution_id`**: every execution — success, failure, or timeout — is cached in full and gets a sequential `execution_id` in its response. `get_output` and `search_output` address the complete stdout/stderr through it.
- **Cache eviction**: the cache holds the most recent `--max-cached-executions` executions (default 20). Older entries are evicted; an expired `execution_id` returns a "not found in cache" error.
- **Timeout**: a target running longer than `--timeout` seconds is killed and reported as an error; partial output captured before the kill is cached like any other run.

## Security: the `additional_args` allowlist

`additional_args` is the only channel through which a caller can shape the make command line, so every token is validated (`validate_additional_args`) before make is invoked. Anything not listed here is rejected with an error response — make never runs.

**Allowed:**

- Execution-tuning short options: `-B -d -e -i -j -k -l -n -O -q -R -r -s -w` (`-j` and `-l` may take a value, attached like `-j4` or separated like `-j 4`; `-O` takes an attached value like `-Oline`)
- Execution-tuning long options, no value: `--keep-going --silent --quiet --ignore-errors --dry-run --just-print --recon --print-directory --no-print-directory --always-make --no-builtin-rules --no-builtin-variables --environment-overrides --question --trace --warn-undefined-variables`
- Long options with a value: `--jobs`, `--load-average`, `--max-load` (attached `--jobs=4` or separated `--jobs 4`); optional attached value: `--output-sync`, `--debug`
- Variable assignments with literal values: `NAME=value`, `NAME:=value`, `NAME::=value`, `NAME+=value`, `NAME?=value`

**Rejected** (structured error returned to the caller):

- Any bare word — it would select another target (e.g. `clean`)
- `--` — the end-of-options marker would turn later tokens into targets
- Any option outside the sets above, including options that change what make builds or where it runs: `-f`/`--file`, `-C`/`--directory`, `-I`/`--include-dir`, `-o`, `-W`
- Assignments using make's `!=` shell-assignment operator — its value would be run through a shell
- Assignment values containing make expansion references (`$(...)` or `${...}`) — make evaluates these itself, so `X=$(shell ...)` would execute a command even with a literal shell disabled

The test suite proves these guards against real GNU make, including control tests showing that make does execute `X=$(shell ...)` and `X!=...` values if validation were bypassed.

## Development

```bash
make install   # Set up venv and install deps
make test      # Run tests
make lint      # Lint with ruff
make format    # Format with ruff
```

## License

MIT License. See [LICENSE](LICENSE) for details.
