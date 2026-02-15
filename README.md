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
```

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

## Development

```bash
make install   # Set up venv and install deps
make test      # Run tests
make lint      # Lint with ruff
make format    # Format with ruff
```

## License

MIT License. See [LICENSE](LICENSE) for details.
