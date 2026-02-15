# Makefile MCP Server

A Model Context Protocol (MCP) server that exposes Makefile targets as executable tools for AI assistants.

## Features

- **Dynamic Make Target Tools**: Each Makefile target becomes an executable tool (e.g., `make_build`, `make_test`, `make_clean`)
- **`list-available-targets`**: List all available make targets exposed by the server
- **`get-makefile-info`**: Get detailed information about the Makefile and filtering configuration
- **Target Discovery**: Automatically parses Makefiles to discover targets and descriptions
- **Comment-based Descriptions**: Uses comments above targets as tool descriptions
- **Include/Exclude Filtering**: Filter which targets are exposed as tools via command-line flags
- **Dry Run Support**: Execute targets with `--dry-run` to see what would be executed

## Installation

Requires Python 3.10+ and `uv`.

```bash
uv pip install -e ".[dev]"
```

## Usage

```bash
# Use default Makefile in current directory
uv run makefile_mcp.py

# Use specific Makefile
uv run makefile_mcp.py --makefile /path/to/Makefile

# Include only specific targets
uv run makefile_mcp.py --include build,test,clean

# Exclude specific targets
uv run makefile_mcp.py --exclude deploy,publish

# Custom working directory
uv run makefile_mcp.py --working-dir /path/to/project
```

## MCP Client Configuration

```json
{
  "mcpServers": {
    "makefile": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/makefile-mcp",
        "run", "makefile_mcp.py",
        "--makefile", "/path/to/your/project/Makefile",
        "--exclude", "deploy,publish"
      ]
    }
  }
}
```

## Development

```bash
make install   # Set up venv and install deps
make test      # Run tests
make lint      # Lint with ruff
make format    # Format with ruff
```

## License

MIT License. See [LICENSE](LICENSE) for details.
