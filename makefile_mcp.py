#!/usr/bin/env python3
# /// script
# dependencies = [
#   "fastmcp>=3.4.5,<4.0.0"
# ]
# ///

"""
Makefile MCP Server

A Model Context Protocol (MCP) server that exposes Makefile targets as tools.
AI assistants can discover and execute make targets through this server.

Usage: uv run makefile_mcp.py [--makefile PATH] [--include TARGET1,TARGET2] [--exclude TARGET1,TARGET2]
"""

import argparse
import pathlib
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from fastmcp import FastMCP

# search_output() repeats a context window for every match it returns, so an
# unbounded result list would defeat the bounded inline-output design. Callers
# that need more can raise max_results or page with get_output().
DEFAULT_MAX_SEARCH_RESULTS = 20


@dataclass
class CachedExecution:
    """Cached output from a make target execution."""

    execution_id: int
    target: str
    command: str
    stdout: str
    stderr: str
    exit_code: int
    timestamp: float


class OutputCache:
    """Cache for make target execution outputs with eviction.

    Tool bodies run concurrently in FastMCP's worker threadpool, so every access
    to the cache is serialized by a lock.
    """

    def __init__(self, max_entries: int = 20):
        self.max_entries = max_entries
        self._cache: Dict[int, CachedExecution] = {}
        self._next_id: int = 1
        self._lock = threading.Lock()

    def add(self, target: str, command: str, stdout: str, stderr: str, exit_code: int) -> CachedExecution:
        """Store an execution result and return it. Evicts oldest if over limit."""
        with self._lock:
            execution_id = self._next_id
            self._next_id += 1

            entry = CachedExecution(
                execution_id=execution_id,
                target=target,
                command=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timestamp=time.time(),
            )
            self._cache[execution_id] = entry

            # Evict oldest entries if over limit
            while len(self._cache) > self.max_entries:
                oldest_id = min(self._cache.keys())
                del self._cache[oldest_id]

            return entry

    def get(self, execution_id: int) -> Optional[CachedExecution]:
        """Retrieve a cached execution by ID."""
        with self._lock:
            return self._cache.get(execution_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


@dataclass(frozen=True)
class ServerConfig:
    """Immutable runtime configuration resolved from CLI arguments.

    Everything a registered tool needs to run is bound here at initialization
    time; no tool reads module state afterwards.
    """

    makefile_path: pathlib.Path
    working_dir: pathlib.Path
    include_targets: Optional[Set[str]]
    exclude_targets: Set[str]
    max_cached_executions: int
    tail_lines: int
    timeout_seconds: int


def positive_int(value: str) -> int:
    """Parse a strictly positive integer for CLI limits."""
    parsed_value = int(value)
    if parsed_value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed_value


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """
    Parse command-line arguments for Makefile configuration.

    Args:
        argv: Argument tokens to parse; defaults to sys.argv, the process's own
            arguments. Tests and embedders pass an explicit list instead.

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Makefile MCP Server")
    parser.add_argument(
        "--makefile", type=str, default="Makefile", help="Path to the Makefile (default: Makefile in current directory)"
    )
    parser.add_argument("--include", type=str, help="Comma-separated list of targets to include (default: all targets)")
    parser.add_argument("--exclude", type=str, help="Comma-separated list of targets to exclude")
    parser.add_argument(
        "--working-dir", type=str, help="Working directory for make commands (default: directory containing Makefile)"
    )
    parser.add_argument(
        "--max-cached-executions",
        type=positive_int,
        default=20,
        help="Maximum number of cached execution outputs to keep (default: 20)",
    )
    parser.add_argument(
        "--tail-lines",
        type=positive_int,
        default=50,
        help="Number of tail lines to include in make tool responses (default: 50)",
    )
    parser.add_argument(
        "--timeout",
        type=positive_int,
        default=300,
        help="Seconds to wait before a running make target is killed (default: 300)",
    )

    return parser.parse_args(argv)


def _parse_target_list(value: Optional[str]) -> Optional[Set[str]]:
    """Parse a comma-separated target list. None means the filter is not set."""
    if not value:
        return None
    return {target.strip() for target in value.split(",")}


def build_config(cli_args: argparse.Namespace) -> ServerConfig:
    """Resolve parsed CLI arguments into an immutable server configuration."""
    makefile_path = pathlib.Path(cli_args.makefile)
    if not makefile_path.is_absolute():
        makefile_path = pathlib.Path.cwd() / makefile_path

    if cli_args.working_dir:
        working_dir = pathlib.Path(cli_args.working_dir).resolve()
    else:
        working_dir = makefile_path.parent.resolve()

    return ServerConfig(
        makefile_path=makefile_path,
        working_dir=working_dir,
        include_targets=_parse_target_list(cli_args.include),
        exclude_targets=_parse_target_list(cli_args.exclude) or set(),
        max_cached_executions=cli_args.max_cached_executions,
        tail_lines=cli_args.tail_lines,
        timeout_seconds=cli_args.timeout,
    )


class MakefileParser:
    """Parser for extracting targets and descriptions from Makefiles."""

    def __init__(self, makefile_path: pathlib.Path):
        self.makefile_path = makefile_path
        self.targets: Dict[str, str] = {}
        self._parse()

    def _parse(self):
        """Parse the Makefile to extract targets and their descriptions."""
        try:
            with open(self.makefile_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(self.makefile_path, "r", encoding="latin-1") as f:
                content = f.read()

        lines = content.split("\n")
        current_comment = ""

        for _i, line in enumerate(lines):
            line = line.rstrip()

            # Track comments that might describe the next target
            if line.startswith("#"):
                comment = line[1:].strip()
                if comment and not comment.startswith("#"):  # Skip comment headers like "###"
                    current_comment = comment
                continue

            # Skip empty lines but reset comment
            if not line.strip():
                current_comment = ""
                continue

            # Look for target definitions (target: dependencies). A single rule
            # may declare several space-separated targets, e.g. "start stop restart:".
            target_match = re.match(r"^([a-zA-Z0-9_.-][a-zA-Z0-9_.\- ]*?)\s*:(?![:=])", line)
            if target_match:
                for target_name in target_match.group(1).split():
                    # Skip special targets that start with . or contain %
                    if target_name.startswith(".") or "%" in target_name:
                        continue

                    # Apply the preceding comment to every target on the rule,
                    # or generate a default description per target.
                    if current_comment:
                        description = current_comment
                    else:
                        description = f"Execute the '{target_name}' target"

                    self.targets[target_name] = description

                current_comment = ""
                continue

            # If line doesn't start with tab/space, reset comment
            if line and not line.startswith(("\t", " ")):
                current_comment = ""

    def get_targets(self) -> Dict[str, str]:
        """Get all discovered targets with their descriptions."""
        return self.targets.copy()

    def get_filtered_targets(self, include: Optional[Set[str]], exclude: Set[str]) -> Dict[str, str]:
        """Get targets filtered by include/exclude lists."""
        targets = self.get_targets()

        # Apply include filter
        if include is not None:
            targets = {name: desc for name, desc in targets.items() if name in include}

        # Apply exclude filter
        if exclude:
            targets = {name: desc for name, desc in targets.items() if name not in exclude}

        return targets


def get_makefile_targets(config: ServerConfig) -> Dict[str, str]:
    """Parse the Makefile and return filtered targets."""
    if not config.makefile_path.exists():
        return {}

    parser = MakefileParser(config.makefile_path)
    filtered_targets = parser.get_filtered_targets(config.include_targets, config.exclude_targets)

    if not filtered_targets:
        print("Warning: No targets found or all targets filtered out", file=sys.stderr)

    return filtered_targets


def _tail_lines(text: str, n: int) -> tuple[str, bool]:
    """Return the last n lines of text. Returns (tail_text, was_truncated)."""
    if not text:
        return text, False
    lines = text.splitlines(keepends=True)
    if len(lines) <= n:
        return text, False
    return "".join(lines[-n:]), True


def _bounded_output_fields(stdout: str, stderr: str, tail_n: int, execution_id: int) -> Dict[str, Any]:
    """Build the bounded output fields shared by completed and timed-out executions."""
    stdout_tail, stdout_truncated = _tail_lines(stdout, tail_n)
    stderr_tail, stderr_truncated = _tail_lines(stderr, tail_n)

    fields: Dict[str, Any] = {
        "execution_id": execution_id,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "stdout_total_lines": len(stdout.splitlines()),
        "stdout_total_chars": len(stdout),
        "stderr_total_lines": len(stderr.splitlines()),
        "stderr_total_chars": len(stderr),
    }

    if stdout_truncated or stderr_truncated:
        fields["truncation_note"] = (
            "Output was truncated to the last "
            f"{tail_n} lines. Use get_output(execution_id={execution_id}) "
            "to paginate or search_output() to search the full output."
        )

    return fields


def _as_text(stream: Any) -> str:
    """Normalize a captured subprocess stream to text.

    subprocess.TimeoutExpired carries the raw bytes read before the timeout even
    when the call used text=True, and either stream may be absent entirely.
    """
    if stream is None:
        return ""
    if isinstance(stream, (bytes, bytearray)):
        return bytes(stream).decode("utf-8", errors="replace")
    return str(stream)


def _lookup_cached_stream(
    cache: OutputCache, execution_id: int, stream: str
) -> tuple[Optional[CachedExecution], Optional[Dict[str, Any]]]:
    """Resolve the preconditions shared by get_output() and search_output().

    Returns (entry, None) when the execution is cached and the stream name is
    valid, or (None, error_response) describing the failure otherwise.
    """
    cached = cache.get(execution_id)
    if cached is None:
        return None, {
            "status": "error",
            "message": f"Execution ID {execution_id} not found in cache.",
        }
    if stream not in ("stdout", "stderr"):
        return None, {
            "status": "error",
            "message": f"Invalid stream '{stream}'. Must be 'stdout' or 'stderr'.",
        }
    return cached, None


def make_tool_name(target_name: str) -> str:
    """Return the MCP tool name for a make target."""
    return f"make_{target_name.replace('-', '_').replace('.', '_')}"


def validate_tool_names(targets: Dict[str, str]) -> None:
    """Reject targets that would generate duplicate MCP tool names."""
    targets_by_tool_name: Dict[str, List[str]] = {}
    for target_name in targets:
        targets_by_tool_name.setdefault(make_tool_name(target_name), []).append(target_name)

    collisions = {name: names for name, names in targets_by_tool_name.items() if len(names) > 1}
    if collisions:
        details = "; ".join(f"{tool_name}: {', '.join(target_names)}" for tool_name, target_names in collisions.items())
        raise ValueError(f"Conflicting make targets generate the same MCP tool name: {details}")


# A command-line make variable assignment: NAME=value, NAME:=value, NAME::=value,
# NAME+=value, NAME?=value, NAME!=value. These override variables and cannot select
# another target, load another makefile, or change directory. Their values are still
# make source, so the operator and the value are constrained further below.
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*(::|:|\+|\?|!)?=")

# make expands a command-line variable's value itself, so a function reference such
# as $(shell ...) runs a command even when no recipe reads the variable. shell=False
# does not constrain that: it is make evaluating make syntax, not a shell.
_EXPANSION_RE = re.compile(r"\$[({]")

# '!=' is make's shell-assignment operator (GNU make >= 4.0): the whole value is run
# through a shell regardless of any expansion reference in it.
_SHELL_ASSIGNMENT_OPERATOR = "!"

# Short option letters that only tune execution and cannot select another target,
# load/evaluate another makefile, or change directory. Dangerous letters are absent
# on purpose (e.g. -f/--file, -C/--directory, -I/--include-dir, -o, -W).
_ALLOWED_SHORT_FLAGS = set("BdeijklnOqRrsw")
# Short flags whose value may be attached (-j4) or separated (-j 4).
_SHORT_FLAGS_WITH_VALUE = set("jl")
# Short flags whose value is optional and only ever attached (-Oline).
_SHORT_FLAGS_OPTIONAL_VALUE = set("O")

# Long options that take no value.
_ALLOWED_LONG_FLAGS_NO_VALUE = {
    "keep-going",
    "silent",
    "quiet",
    "ignore-errors",
    "dry-run",
    "just-print",
    "recon",
    "print-directory",
    "no-print-directory",
    "always-make",
    "no-builtin-rules",
    "no-builtin-variables",
    "environment-overrides",
    "question",
    "trace",
    "warn-undefined-variables",
}
# Long options that require a value, attached (--jobs=4) or separated (--jobs 4).
_ALLOWED_LONG_FLAGS_WITH_VALUE = {
    "jobs",
    "load-average",
    "max-load",
}
# Long options whose value is optional and only ever attached (--debug=b).
_ALLOWED_LONG_FLAGS_OPTIONAL_VALUE = {
    "output-sync",
    "debug",
}


def validate_additional_args(tokens: List[str]) -> Optional[str]:
    """Return an error message if any token falls outside the safe allowlist.

    Accepts make variable assignments with literal values and an explicit set of
    execution-tuning options. Rejects bare targets, end-of-options markers, any
    option that could select another target, load/evaluate another makefile, or
    change directory, and any assignment value carrying a make expansion
    reference. Returns None when every token is allowed.
    """
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            return "'--' is not allowed; following tokens would be treated as targets"

        if not tok.startswith("-"):
            assignment = _ASSIGNMENT_RE.match(tok)
            if assignment:
                if assignment.group(1) == _SHELL_ASSIGNMENT_OPERATOR:
                    return f"'{tok}' uses make's '!=' shell-assignment operator; its value would be run by a shell"
                if _EXPANSION_RE.search(tok[assignment.end() :]):
                    return (
                        f"'{tok}' contains a make expansion reference ('$(' or '${{'); "
                        "assignment values must be literal"
                    )
                i += 1
                continue
            return f"'{tok}' is not an allowed variable assignment or option (it would select another target)"

        if tok.startswith("--"):
            name, sep, _value = tok[2:].partition("=")
            if name in _ALLOWED_LONG_FLAGS_NO_VALUE:
                if sep:
                    return f"option '--{name}' does not take a value"
                i += 1
                continue
            if name in _ALLOWED_LONG_FLAGS_OPTIONAL_VALUE:
                i += 1
                continue
            if name in _ALLOWED_LONG_FLAGS_WITH_VALUE:
                i += 1 if sep else 2  # consume a separated value token
                continue
            return f"option '--{name}' is not in the allowed make option set"

        # Short option cluster: -j4, -ks, -j 4
        body = tok[1:]
        if not body:
            return "'-' is not an allowed option"
        consumed_separated = False
        j = 0
        while j < len(body):
            ch = body[j]
            if ch not in _ALLOWED_SHORT_FLAGS:
                return f"option '-{ch}' is not in the allowed make option set"
            if ch in _SHORT_FLAGS_WITH_VALUE:
                if not body[j + 1 :]:  # value is the next token
                    consumed_separated = True
                break  # remainder of the cluster is this flag's value
            if ch in _SHORT_FLAGS_OPTIONAL_VALUE:
                break  # remainder, if any, is an attached optional value
            j += 1
        i += 2 if consumed_separated else 1

    return None


class MakefileServer:
    """An MCP server exposing one Makefile's targets, bound to one configuration.

    Instances are created only by initialize_makefile_mcp(): the constructor owns
    every piece of mutable state (the FastMCP instance, the output cache, the
    discovered targets), so independent instances — including servers built by
    tests — cannot leak configuration or registrations into each other.
    """

    def __init__(self, config: ServerConfig):
        self.config = config
        self.mcp_server = FastMCP("MakefileMCP")
        self.output_cache = OutputCache(max_entries=config.max_cached_executions)
        self.filtered_targets: Dict[str, str] = get_makefile_targets(config)
        self._register_utility_tools()

    def _register_utility_tools(self) -> None:
        """Register the four utility tools every server exposes."""
        self.mcp_server.tool()(self.list_available_targets)
        self.mcp_server.tool()(self.get_makefile_info)
        self.mcp_server.tool()(self.get_output)
        self.mcp_server.tool()(self.search_output)

    def create_make_tool(self, target_name: str, description: str):
        """Create an MCP tool for a specific make target."""
        config = self.config

        def make_target(additional_args: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
            """Execute the make target with optional arguments and dry-run capability."""
            extra_args: List[str] = []
            if additional_args:
                try:
                    extra_args = shlex.split(additional_args)
                except ValueError as e:
                    return {
                        "target": target_name,
                        "status": "error",
                        "message": f"Invalid additional_args for target '{target_name}': {str(e)}",
                        "exit_code": -1,
                    }

                arg_error = validate_additional_args(extra_args)
                if arg_error is not None:
                    return {
                        "target": target_name,
                        "status": "error",
                        "message": f"Rejected additional_args for target '{target_name}': {arg_error}",
                        "exit_code": -1,
                    }

            try:
                # Build the make command
                cmd = ["make", "-C", str(config.working_dir), "-f", str(config.makefile_path), target_name]

                if dry_run:
                    cmd.append("-n")  # Dry run flag for make

                cmd.extend(extra_args)

                # Execute the command - safe execution with list of args, no shell injection risk
                result = subprocess.run(  # noqa: S603
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=config.timeout_seconds,
                )

                # Cache full output
                command_str = " ".join(cmd)
                cached = self.output_cache.add(
                    target=target_name,
                    command=command_str,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.returncode,
                )

                response = {
                    "target": target_name,
                    "command": command_str,
                    "working_directory": str(config.working_dir),
                    "exit_code": result.returncode,
                    **_bounded_output_fields(result.stdout, result.stderr, config.tail_lines, cached.execution_id),
                }

                if dry_run:
                    response["note"] = "This was a dry run - no commands were actually executed"

                if result.returncode == 0:
                    response["status"] = "success"
                    response["message"] = f"Successfully executed target '{target_name}'"
                else:
                    response["status"] = "error"
                    response["message"] = f"Target '{target_name}' failed with exit code {result.returncode}"

                return response

            except subprocess.TimeoutExpired as e:
                # The killed process may still have printed the decisive diagnostic, so
                # cache whatever was captured and expose it like a completed execution.
                partial_stdout = _as_text(e.stdout)
                partial_stderr = _as_text(e.stderr)
                command_str = " ".join(cmd)
                cached = self.output_cache.add(
                    target=target_name,
                    command=command_str,
                    stdout=partial_stdout,
                    stderr=partial_stderr,
                    exit_code=-1,
                )

                return {
                    "target": target_name,
                    "command": command_str,
                    "working_directory": str(config.working_dir),
                    "status": "error",
                    "message": f"Target '{target_name}' timed out after {config.timeout_seconds} seconds",
                    "exit_code": -1,
                    **_bounded_output_fields(partial_stdout, partial_stderr, config.tail_lines, cached.execution_id),
                }
            except (subprocess.SubprocessError, OSError) as e:
                # Subprocess failures and OS-level execution errors (e.g. the make
                # binary itself missing or not executable) are reported structurally;
                # anything else is a bug and must surface instead of being masked.
                return {
                    "target": target_name,
                    "status": "error",
                    "message": f"Failed to execute target '{target_name}': {str(e)}",
                    "exit_code": -1,
                }

        # Set the function name and docstring dynamically
        tool_name = make_tool_name(target_name)
        make_target.__name__ = tool_name
        make_target.__doc__ = (
            f"{description}.\n\nExecutes: make -C {config.working_dir} -f {config.makefile_path} {target_name}"
        )

        # Register the tool with the MCP server
        self.mcp_server.tool()(make_target)

        return make_target

    def register_make_tools(self) -> List[tuple[str, str]]:
        """Validate and register MCP tools for the discovered make targets."""
        validate_tool_names(self.filtered_targets)
        return [
            (target_name, self.create_make_tool(target_name, description))
            for target_name, description in self.filtered_targets.items()
        ]

    def list_available_targets(self) -> Dict[str, Any]:
        """
        List all available make targets that can be executed through this server.

        Returns:
            dict: Information about available targets and server configuration.
        """
        config = self.config
        return {
            "makefile_path": str(config.makefile_path),
            "working_directory": str(config.working_dir),
            "total_targets_in_makefile": (
                len(MakefileParser(config.makefile_path).get_targets()) if config.makefile_path.exists() else 0
            ),
            "available_targets": len(self.filtered_targets),
            "targets": [
                {"name": name, "description": desc, "tool_name": make_tool_name(name)}
                for name, desc in self.filtered_targets.items()
            ],
            "include_filter": list(config.include_targets) if config.include_targets else None,
            "exclude_filter": list(config.exclude_targets) if config.exclude_targets else None,
        }

    def get_makefile_info(self) -> Dict[str, Any]:
        """
        Get detailed information about the Makefile and its targets.

        Returns:
            dict: Comprehensive information about the Makefile.
        """
        config = self.config
        all_targets = MakefileParser(config.makefile_path).get_targets() if config.makefile_path.exists() else {}

        return {
            "makefile_path": str(config.makefile_path),
            "makefile_exists": config.makefile_path.exists(),
            "working_directory": str(config.working_dir),
            "all_targets": {
                "count": len(all_targets),
                "targets": [{"name": name, "description": desc} for name, desc in all_targets.items()],
            },
            "filtered_targets": {
                "count": len(self.filtered_targets),
                "targets": [{"name": name, "description": desc} for name, desc in self.filtered_targets.items()],
            },
            "filters": {
                "include": list(config.include_targets) if config.include_targets else None,
                "exclude": list(config.exclude_targets) if config.exclude_targets else None,
            },
        }

    def get_output(
        self, execution_id: int, stream: str = "stdout", start_line: int = 0, end_line: int = 100
    ) -> Dict[str, Any]:
        """
        Retrieve a page of cached output from a previous make target execution.

        Args:
            execution_id: The execution ID returned by a make target tool.
            stream: Which output stream to read — "stdout" or "stderr".
            start_line: First line to return (0-indexed, inclusive).
            end_line: Last line to return (exclusive).

        Returns:
            dict: The requested lines and metadata.
        """
        cached, error = _lookup_cached_stream(self.output_cache, execution_id, stream)
        if error is not None:
            return error

        text = cached.stdout if stream == "stdout" else cached.stderr
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)

        # Clamp range
        start_line = max(0, start_line)
        end_line = max(start_line, min(end_line, total_lines))

        selected = lines[start_line:end_line]

        return {
            "status": "success",
            "execution_id": execution_id,
            "target": cached.target,
            "stream": stream,
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "content": "".join(selected),
        }

    def search_output(
        self,
        execution_id: int,
        pattern: str,
        stream: str = "stdout",
        context_lines: int = 3,
        max_results: int = DEFAULT_MAX_SEARCH_RESULTS,
    ) -> Dict[str, Any]:
        """
        Search cached output from a previous make target execution.

        Args:
            execution_id: The execution ID returned by a make target tool.
            pattern: Non-empty substring to search for (case-insensitive, literal).
            stream: Which output stream to search — "stdout" or "stderr".
            context_lines: Number of surrounding lines to include with each match (>= 0).
            max_results: Maximum number of matches to return (>= 1, defaults to
                DEFAULT_MAX_SEARCH_RESULTS = 20). Every match is still counted in
                total_matches; use get_output() around the returned line numbers to
                read past the cap.

        Returns:
            dict: Matching lines with context and line numbers.
        """
        if not pattern:
            return {
                "status": "error",
                "message": "Search pattern must not be empty. Provide a literal substring to search for.",
            }

        if context_lines < 0:
            return {
                "status": "error",
                "message": f"Invalid context_lines {context_lines}. Must be 0 or greater.",
            }

        if max_results < 1:
            return {
                "status": "error",
                "message": f"Invalid max_results {max_results}. Must be 1 or greater.",
            }

        cached, error = _lookup_cached_stream(self.output_cache, execution_id, stream)
        if error is not None:
            return error

        text = cached.stdout if stream == "stdout" else cached.stderr
        lines = text.splitlines()
        total_lines = len(lines)
        pattern_lower = pattern.lower()

        # Find matching line indices
        match_indices = [i for i, line in enumerate(lines) if pattern_lower in line.lower()]
        total_matches = len(match_indices)

        # Build matches with context, bounded to the first max_results matches so one
        # response cannot repeat a context window for every line of a large log.
        matches: List[Dict[str, Any]] = []
        for idx in match_indices[:max_results]:
            ctx_start = max(0, idx - context_lines)
            ctx_end = min(total_lines, idx + context_lines + 1)
            context = [{"line_number": i, "text": lines[i], "is_match": i == idx} for i in range(ctx_start, ctx_end)]
            matches.append(
                {
                    "line_number": idx,
                    "text": lines[idx],
                    "context": context,
                }
            )

        result: Dict[str, Any] = {
            "status": "success",
            "execution_id": execution_id,
            "target": cached.target,
            "stream": stream,
            "pattern": pattern,
            "total_lines": total_lines,
            "total_matches": total_matches,
            "returned_matches": len(matches),
            "max_results": max_results,
            "truncated": total_matches > len(matches),
            "matches": matches,
        }

        if result["truncated"]:
            result["truncation_note"] = (
                f"Returned the first {len(matches)} of {total_matches} matches. "
                "Narrow the pattern, raise max_results, or use "
                f"get_output(execution_id={execution_id}) around the returned line numbers."
            )

        return result


def initialize_makefile_mcp(argv: Optional[List[str]] = None) -> MakefileServer:
    """Initialize the makefile MCP server: the only place server state is created.

    Parses arguments (sys.argv by default), validates the resolved paths, and
    builds a server with every tool — the four utilities plus one per discovered
    make target — registered on its own FastMCP instance. Exits the process when
    the configuration is invalid or the targets cannot be exposed as tools, the
    same way argparse exits on bad arguments. Importing this module performs
    none of this work.
    """
    config = build_config(parse_cli_args(argv))

    if not config.makefile_path.exists():
        print(f"Error: Makefile not found at {config.makefile_path}", file=sys.stderr)
        sys.exit(1)

    if not config.working_dir.is_dir():
        print(f"Error: Working directory not found: {config.working_dir}", file=sys.stderr)
        sys.exit(1)

    server = MakefileServer(config)

    if not server.filtered_targets:
        print("Error: No make targets available to expose as tools", file=sys.stderr)
        sys.exit(1)

    try:
        server.register_make_tools()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    return server


def main(argv: Optional[List[str]] = None) -> MakefileServer:
    """Entry point for the Makefile MCP server. Returns the initialized server."""
    server = initialize_makefile_mcp(argv)

    print("Starting Makefile MCP server", file=sys.stderr)
    print(f"  Makefile: {server.config.makefile_path}", file=sys.stderr)
    print(f"  Working directory: {server.config.working_dir}", file=sys.stderr)
    print(f"  Available targets: {', '.join(server.filtered_targets.keys())}", file=sys.stderr)

    if server.config.include_targets:
        print(f"  Include filter: {', '.join(server.config.include_targets)}", file=sys.stderr)
    if server.config.exclude_targets:
        print(f"  Exclude filter: {', '.join(server.config.exclude_targets)}", file=sys.stderr)

    server.mcp_server.run()
    return server


if __name__ == "__main__":
    main()
