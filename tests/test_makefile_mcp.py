#!/usr/bin/env python3
"""
Comprehensive test suite for the Makefile MCP Server

Tests Makefile parsing, target filtering, tool creation, and command execution.

Every server in this suite is built through explicit initialization
(initialize_makefile_mcp), which returns an independent MakefileServer. There is
no module-level state to reset, so no test deletes the module from sys.modules
or reimports it to isolate itself.
"""

import contextlib
import dataclasses
import os
import pathlib
import re
import runpy
import shutil
import subprocess

# Import the makefile MCP components once: importing the module has no side
# effects, so a single top-level import is safe to share across all tests.
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import makefile_mcp  # noqa: E402

THREE_TARGET_MAKEFILE = """# Build the project
build:
\techo "Building project..."

# Run tests
test:
\techo "Running tests..."

# Clean up
clean:
\techo "Cleaning up..."

.PHONY: build test clean
"""


@pytest.fixture
def server_factory(tmp_path):
    """Create fresh servers through explicit initialization.

    This replaces the old reset pattern, which deleted makefile_mcp from
    sys.modules and reimported it under a patched sys.argv to rebuild
    import-time globals. Each call builds a new MakefileServer with its own
    FastMCP instance, output cache, and discovered targets, so nothing leaks
    between tests — and no module reloading is involved.
    """

    def _create(makefile_text=THREE_TARGET_MAKEFILE, extra_args=()):
        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text(makefile_text)
        return makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile_path), *extra_args])

    return _create


class TestMakefileParser:
    """Test the MakefileParser class functionality."""

    def test_simple_makefile_parsing(self):
        """Test parsing a simple Makefile with basic targets."""
        from makefile_mcp import MakefileParser

        makefile_content = """# Build the project
build:
\techo "Building..."

# Run tests
test:
\tpytest

# Clean up build artifacts
clean:
\trm -rf build/

.PHONY: build test clean
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            parser = MakefileParser(pathlib.Path(makefile_path))
            targets = parser.get_targets()

            assert len(targets) == 3
            assert "build" in targets
            assert "test" in targets
            assert "clean" in targets
            assert targets["build"] == "Build the project"
            assert targets["test"] == "Run tests"
            assert targets["clean"] == "Clean up build artifacts"

        finally:
            os.unlink(makefile_path)

    def test_targets_without_comments(self):
        """Test parsing targets that don't have comment descriptions."""
        from makefile_mcp import MakefileParser

        makefile_content = """build:
\techo "Building..."

# This is a test target
test:
\tpytest

install:
\tpip install -e .
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            parser = MakefileParser(pathlib.Path(makefile_path))
            targets = parser.get_targets()

            assert targets["build"] == "Execute the 'build' target"  # Default description
            assert targets["test"] == "This is a test target"  # From comment
            assert targets["install"] == "Execute the 'install' target"  # Default description

        finally:
            os.unlink(makefile_path)

    def test_special_targets_ignored(self):
        """Test that special targets (.PHONY, patterns) are ignored."""
        from makefile_mcp import MakefileParser

        makefile_content = """.PHONY: all clean
.DEFAULT_GOAL := all

all:
\techo "All"

%.o: %.c
\tgcc -c $< -o $@

clean:
\trm -f *.o

.SUFFIXES: .c .o
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            parser = MakefileParser(pathlib.Path(makefile_path))
            targets = parser.get_targets()

            # Should only include regular targets
            assert len(targets) == 2
            assert "all" in targets
            assert "clean" in targets

            # Special targets should be excluded
            assert ".PHONY" not in targets
            assert ".DEFAULT_GOAL" not in targets
            assert "%.o" not in targets
            assert ".SUFFIXES" not in targets

        finally:
            os.unlink(makefile_path)

    def test_variable_assignments_not_targets(self):
        """Simply-expanded (:=) and ::= variable assignments must not be parsed as targets."""
        from makefile_mcp import MakefileParser

        makefile_content = """CC := gcc
PREFIX := /usr/local
OBJS ::= a.o b.o
VERSION ?= 1.0
CFLAGS = -O2

build: deps
\t$(CC) -o app

deps:
\techo "deps"
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            parser = MakefileParser(pathlib.Path(makefile_path))
            targets = parser.get_targets()

            # Variable assignments should never become targets
            assert "CC" not in targets
            assert "PREFIX" not in targets
            assert "OBJS" not in targets
            assert "VERSION" not in targets
            assert "CFLAGS" not in targets

            # Real targets are still discovered
            assert "build" in targets
            assert "deps" in targets
            assert len(targets) == 2

        finally:
            os.unlink(makefile_path)

    def test_filtering_targets(self):
        """Test include/exclude filtering of targets."""
        from makefile_mcp import MakefileParser

        makefile_content = """build:
\techo "Building..."

test:
\tpytest

clean:
\trm -rf build/

deploy:
\techo "Deploying..."

format:
\tblack .
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            parser = MakefileParser(pathlib.Path(makefile_path))

            # Test include filter
            include_set = {"build", "test"}
            filtered = parser.get_filtered_targets(include_set, set())
            assert len(filtered) == 2
            assert "build" in filtered
            assert "test" in filtered
            assert "clean" not in filtered

            # Test exclude filter
            exclude_set = {"deploy", "format"}
            filtered = parser.get_filtered_targets(None, exclude_set)
            assert len(filtered) == 3
            assert "build" in filtered
            assert "test" in filtered
            assert "clean" in filtered
            assert "deploy" not in filtered
            assert "format" not in filtered

            # Test both include and exclude
            include_set = {"build", "test", "deploy"}
            exclude_set = {"deploy"}
            filtered = parser.get_filtered_targets(include_set, exclude_set)
            assert len(filtered) == 2
            assert "build" in filtered
            assert "test" in filtered
            assert "deploy" not in filtered

        finally:
            os.unlink(makefile_path)

    def test_complex_makefile_parsing(self):
        """Test parsing a more complex, realistic Makefile."""
        from makefile_mcp import MakefileParser

        makefile_content = """# Development Makefile for Python project

# Set up development environment
setup:
\tpython -m venv venv
\t. venv/bin/activate && pip install -e .[dev]

# Install dependencies
install:
\tpip install -e .

# Run linting checks
lint:
\truff check --fix .
\tmypy src/

# Format code
format:
\truff format .
\tisort src/

# Run the test suite
test:
\tpytest tests/ -v

# Run tests with coverage
test-coverage:
\tpytest tests/ --cov=src --cov-report=html

# Build the package
build: clean
\tpython -m build

# Clean build artifacts
clean:
\trm -rf dist/ build/ *.egg-info/
\tfind . -name __pycache__ -exec rm -rf {} +

# Deploy to PyPI
deploy: build
\ttwine upload dist/*

.PHONY: setup install lint format test test-coverage build clean deploy
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            parser = MakefileParser(pathlib.Path(makefile_path))
            targets = parser.get_targets()

            expected_targets = {
                "setup": "Set up development environment",
                "install": "Install dependencies",
                "lint": "Run linting checks",
                "format": "Format code",
                "test": "Run the test suite",
                "test-coverage": "Run tests with coverage",
                "build": "Build the package",
                "clean": "Clean build artifacts",
                "deploy": "Deploy to PyPI",
            }

            assert len(targets) == len(expected_targets)
            for target, expected_desc in expected_targets.items():
                assert target in targets
                assert targets[target] == expected_desc

        finally:
            os.unlink(makefile_path)

    def test_multi_target_rule(self):
        """Rules declaring several targets on one line expose each as a target."""
        from makefile_mcp import MakefileParser

        makefile_content = """# Control the service
start stop restart:
\t@echo "$@"

install uninstall: build
\t@echo "$@"
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            parser = MakefileParser(pathlib.Path(makefile_path))
            targets = parser.get_targets()

            # Every target on a multi-target rule is discovered.
            for name in ("start", "stop", "restart", "install", "uninstall"):
                assert name in targets

            # The preceding comment applies to each target on that rule.
            assert targets["start"] == "Control the service"
            assert targets["stop"] == "Control the service"
            assert targets["restart"] == "Control the service"

            # A rule with no preceding comment gets a per-target default.
            assert targets["install"] == "Execute the 'install' target"
            assert targets["uninstall"] == "Execute the 'uninstall' target"

        finally:
            os.unlink(makefile_path)

    def test_multi_target_rule_excludes_special_and_pattern(self):
        """Special targets on a multi-target rule are skipped while siblings are kept."""
        from makefile_mcp import MakefileParser

        makefile_content = """.hidden start:
\t@echo "$@"

%.o: %.c
\tgcc -c $< -o $@

CONFIG := build
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            parser = MakefileParser(pathlib.Path(makefile_path))
            targets = parser.get_targets()

            # A dot-prefixed sibling on a multi-target rule is excluded; the
            # ordinary sibling is still discovered.
            assert "start" in targets
            assert ".hidden" not in targets

            # Pattern rules and variable assignments remain excluded.
            assert "%.o" not in targets
            assert "CONFIG" not in targets
            assert len(targets) == 1

        finally:
            os.unlink(makefile_path)


class TestMakefileMCPServer:
    """Test the MCP server functionality."""

    def test_make_tool_creation(self, server_factory):
        """Test that make tools are created correctly."""
        server = server_factory()

        # Initialization discovered the Makefile's targets
        assert len(server.filtered_targets) == 3
        assert "build" in server.filtered_targets
        assert "test" in server.filtered_targets
        assert "clean" in server.filtered_targets

    def test_server_config_is_immutable(self, server_factory):
        """The resolved configuration cannot be mutated after initialization."""
        server = server_factory()

        with pytest.raises(dataclasses.FrozenInstanceError):
            server.config.makefile_path = pathlib.Path("/somewhere/else/Makefile")

    def test_startup_rejects_colliding_tool_names(self, tmp_path, capsys):
        """Startup rejects targets that normalize to the same tool name."""
        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text("foo-bar:\n\techo hyphen\n\nfoo.bar:\n\techo period\n")

        with patch.object(makefile_mcp.MakefileServer, "create_make_tool") as create_make_tool:
            with pytest.raises(SystemExit) as exc_info:
                makefile_mcp.main(["--makefile", str(makefile_path)])

        assert exc_info.value.code == 1
        create_make_tool.assert_not_called()
        error = capsys.readouterr().err
        assert "make_foo_bar" in error
        assert "foo-bar" in error
        assert "foo.bar" in error

    def test_startup_status_uses_stderr(self, tmp_path, capsys):
        """Startup keeps stdout clean for the MCP stdio transport."""
        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text("build:\n\techo build\n\ntest:\n\techo test\n")

        with patch.object(makefile_mcp.FastMCP, "run") as run:
            makefile_mcp.main(["--makefile", str(makefile_path), "--include", "build,test", "--exclude", "test"])

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Starting Makefile MCP server" in captured.err
        assert f"Makefile: {makefile_path.resolve()}" in captured.err
        assert f"Working directory: {tmp_path.resolve()}" in captured.err
        assert "Available targets: build" in captured.err
        include_line = next(line for line in captured.err.splitlines() if "Include filter:" in line)
        assert {target.strip() for target in include_line.split(":", 1)[1].split(",")} == {"build", "test"}
        assert "Exclude filter: test" in captured.err
        run.assert_called_once_with()

    def test_tool_name_normalization_is_shared(self, tmp_path):
        """Registration and target metadata use the same name generator."""
        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text("# Coverage\ntest-coverage.xml:\n\techo coverage\n")

        server = makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile_path)])
        tool = server.create_make_tool("test-coverage.xml", "Coverage")
        listed_target = server.list_available_targets()["targets"][0]

        assert tool.__name__ == "make_test_coverage_xml"
        assert listed_target["tool_name"] == tool.__name__

    @patch("subprocess.run")
    def test_make_tool_execution_success(self, mock_run, server_factory):
        """Test successful execution of a make target."""
        # Mock successful subprocess execution
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Building project...\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        server = server_factory()

        # Create a make tool for testing
        make_tool = server.create_make_tool("build", "Build the project")

        # Execute the tool
        result = make_tool()

        assert result["status"] == "success"
        assert result["target"] == "build"
        assert result["exit_code"] == 0
        assert result["stdout_tail"] == "Building project...\n"
        assert result["execution_id"] >= 1
        assert result["stdout_total_lines"] == 1
        assert result["stdout_total_chars"] == len("Building project...\n")
        assert "Successfully executed target 'build'" in result["message"]

        # Verify subprocess was called correctly
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "make" in call_args
        assert "build" in call_args

    @patch("subprocess.run")
    def test_make_tool_execution_failure(self, mock_run, server_factory):
        """Test failed execution of a make target."""
        # Mock failed subprocess execution
        mock_result = MagicMock()
        mock_result.returncode = 2
        mock_result.stdout = ""
        mock_result.stderr = "make: *** No rule to make target 'invalid'. Stop.\n"
        mock_run.return_value = mock_result

        server = server_factory()

        make_tool = server.create_make_tool("invalid", "Invalid target")
        result = make_tool()

        assert result["status"] == "error"
        assert result["target"] == "invalid"
        assert result["exit_code"] == 2
        assert "failed with exit code 2" in result["message"]

    @patch("subprocess.run")
    def test_make_tool_uses_custom_makefile(self, mock_run, tmp_path):
        """Test execution explicitly uses a custom makefile path."""
        custom_makefile = tmp_path / "custom.mk"
        custom_makefile.write_text("custom:\n\techo custom\n")
        working_dir = tmp_path / "work"
        working_dir.mkdir()
        (working_dir / "Makefile").write_text("default:\n\techo default\n")

        mock_result = MagicMock(returncode=0, stdout="custom\n", stderr="")
        mock_run.return_value = mock_result

        server = makefile_mcp.initialize_makefile_mcp(
            ["--makefile", str(custom_makefile), "--working-dir", str(working_dir)]
        )
        make_tool = server.create_make_tool("custom", "Run custom target")
        result = make_tool()

        expected_command = ["make", "-C", str(working_dir), "-f", str(custom_makefile), "custom"]
        mock_run.assert_called_once_with(expected_command, capture_output=True, text=True, timeout=300)
        assert result["command"] == " ".join(expected_command)

    @patch("subprocess.run")
    def test_make_tool_dry_run(self, mock_run, server_factory):
        """Test dry run execution of a make target."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'echo "Building project..."\n'
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        server = server_factory()

        make_tool = server.create_make_tool("build", "Build the project")
        result = make_tool(dry_run=True)

        assert result["status"] == "success"
        assert result["note"] == "This was a dry run - no commands were actually executed"

        # Verify -n flag was added for dry run
        call_args = mock_run.call_args[0][0]
        assert "-n" in call_args

    @patch("subprocess.run")
    def test_make_tool_with_additional_args(self, mock_run, server_factory):
        """Test make tool execution with additional arguments."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Running tests with verbose output...\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        server = server_factory()

        make_tool = server.create_make_tool("test", "Run tests")
        result = make_tool(additional_args="-j4 VERBOSE=1")

        assert result["status"] == "success"

        call_args = mock_run.call_args[0][0]
        assert call_args[-2:] == ["-j4", "VERBOSE=1"]

    @patch("subprocess.run")
    def test_make_tool_additional_args_preserve_quoting(self, mock_run, server_factory):
        """Quoted values and escaped spaces stay a single argument."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        server = server_factory()

        make_tool = server.create_make_tool("test", "Run tests")
        result = make_tool(additional_args='MESSAGE="hello world" PATH_ARG=my\\ file.txt -j4')

        assert result["status"] == "success"

        call_args = mock_run.call_args[0][0]
        assert call_args[-3:] == ["MESSAGE=hello world", "PATH_ARG=my file.txt", "-j4"]

    @patch("subprocess.run")
    def test_make_tool_invalid_additional_args(self, mock_run, server_factory):
        """Malformed quoting is reported as an error without invoking make."""
        server = server_factory()

        make_tool = server.create_make_tool("test", "Run tests")
        result = make_tool(additional_args='MESSAGE="unclosed')

        assert result["status"] == "error"
        assert result["target"] == "test"
        assert result["exit_code"] == -1
        assert "Invalid additional_args" in result["message"]
        mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "additional_args",
        [
            "blocked",  # bare secondary target
            "-f /path/to/other.mk other",  # short attached makefile + target
            "-f/path/to/other.mk",  # short attached makefile
            "--file=other.mk",  # long makefile with value
            "--makefile other.mk",  # long makefile, separated value
            "-C /tmp",  # short change-directory
            "--directory=/tmp",  # long change-directory
            "-I /some/include/dir",  # include-dir loads makefiles
            "--include-dir=/some/dir",
            "--eval=$(shell touch pwned)",  # evaluate makefile syntax
            "-j4 sneaky",  # safe flag but trailing bare target
            "VAR=1 anothertarget",  # assignment followed by a target
            "--",  # end-of-options marker
            "-- target",
            # Quoted so shlex yields ONE token: the unquoted spelling splits and is
            # already caught by the bare-target rule, so it would not exercise this guard.
            'X="$(shell touch pwned)"',  # make expands the value itself
            'X="${shell touch pwned}"',  # brace form of the same expansion
            'X:="$(shell touch pwned)"',  # simply-expanded assignment
            'X="prefix $(shell touch pwned) suffix"',  # expansion embedded mid-value
            'X="$(wildcard *)"',  # a function taking no spaces at all
            "X!='touch pwned'",  # make's shell-assignment operator
        ],
    )
    @patch("subprocess.run")
    def test_make_tool_rejects_boundary_bypass_args(self, mock_run, additional_args, server_factory):
        """Target/makefile/directory/eval bypass attempts never reach subprocess.run."""
        server = server_factory()

        make_tool = server.create_make_tool("safe", "Run the safe target")
        result = make_tool(additional_args=additional_args)

        assert result["status"] == "error"
        assert result["target"] == "safe"
        assert result["exit_code"] == -1
        assert "Rejected additional_args" in result["message"]
        mock_run.assert_not_called()

    @pytest.mark.parametrize(
        ("additional_args", "expected_tail"),
        [
            ("-j4 VERBOSE=1", ["-j4", "VERBOSE=1"]),
            ("-j 4", ["-j", "4"]),
            ("--jobs=4", ["--jobs=4"]),
            ("--jobs 4", ["--jobs", "4"]),
            ("-k -s", ["-k", "-s"]),
            ("-ks", ["-ks"]),
            ('MESSAGE="hello world"', ["MESSAGE=hello world"]),
            ("PATH_ARG=my\\ file.txt", ["PATH_ARG=my file.txt"]),
            ("NAME:=value", ["NAME:=value"]),
            ("--keep-going", ["--keep-going"]),
            ("--load-average 2.5", ["--load-average", "2.5"]),
        ],
    )
    @patch("subprocess.run")
    def test_make_tool_accepts_safe_args(self, mock_run, additional_args, expected_tail, server_factory):
        """Safe execution flags and variable assignments still reach make unchanged."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        server = server_factory()

        make_tool = server.create_make_tool("safe", "Run the safe target")
        result = make_tool(additional_args=additional_args)

        assert result["status"] == "success"

        call_args = mock_run.call_args[0][0]
        assert call_args[-len(expected_tail) :] == expected_tail

    @pytest.mark.parametrize(
        ("additional_args", "expected_fragment"),
        [
            ('X="$(shell touch pwned)"', "make expansion reference"),
            ('X="${shell touch pwned}"', "make expansion reference"),
            ("X!='touch pwned'", "shell-assignment operator"),
        ],
    )
    @patch("subprocess.run")
    def test_make_tool_rejection_names_the_expansion(
        self, mock_run, additional_args, expected_fragment, server_factory
    ):
        """The rejection is specific, not the closure's generic unexpected-error fallback."""
        server = server_factory()

        make_tool = server.create_make_tool("safe", "Run the safe target")
        result = make_tool(additional_args=additional_args)

        assert result["status"] == "error"
        assert result["exit_code"] == -1
        assert "Rejected additional_args" in result["message"]
        assert expected_fragment in result["message"]
        assert "Unexpected error" not in result["message"]
        mock_run.assert_not_called()

    def test_list_available_targets_tool(self, server_factory):
        """Test the list_available_targets tool."""
        server = server_factory()
        result = server.list_available_targets()

        assert "makefile_path" in result
        assert "working_directory" in result
        assert "available_targets" in result
        assert result["available_targets"] == 3
        assert "targets" in result

        target_names = [t["name"] for t in result["targets"]]
        assert "build" in target_names
        assert "test" in target_names
        assert "clean" in target_names

    def test_get_makefile_info_tool(self, server_factory):
        """Test the get_makefile_info tool."""
        server = server_factory()
        result = server.get_makefile_info()

        assert result["makefile_exists"] is True
        assert result["all_targets"]["count"] == 3
        assert result["filtered_targets"]["count"] == 3
        assert result["filters"]["include"] is None
        assert result["filters"]["exclude"] is None


class TestCommandLineArguments:
    """Test command-line argument parsing and filtering."""

    def test_include_filter(self):
        """Test --include command line argument."""
        args = makefile_mcp.parse_cli_args(["--include", "build,test"])

        assert args.include == "build,test"
        assert args.exclude is None

    def test_exclude_filter(self):
        """Test --exclude command line argument."""
        args = makefile_mcp.parse_cli_args(["--exclude", "clean,deploy"])

        assert args.exclude == "clean,deploy"
        assert args.include is None

    def test_custom_makefile_path(self):
        """Test --makefile command line argument."""
        args = makefile_mcp.parse_cli_args(["--makefile", "/custom/path/Makefile"])

        assert args.makefile == "/custom/path/Makefile"

    def test_working_directory(self):
        """Test --working-dir command line argument."""
        args = makefile_mcp.parse_cli_args(["--working-dir", "/custom/work/dir"])

        assert args.working_dir == "/custom/work/dir"

    @pytest.mark.parametrize("option", ["--max-cached-executions", "--tail-lines"])
    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_output_limits_must_be_positive(self, option, value, capsys):
        """Test output limits reject zero and negative values."""
        with pytest.raises(SystemExit) as exc_info:
            makefile_mcp.parse_cli_args([option, value])

        assert exc_info.value.code == 2
        error = capsys.readouterr().err
        assert f"argument {option}" in error
        assert "must be a positive integer" in error

    def test_positive_output_limits_are_accepted(self):
        """Test positive custom output limits are preserved."""
        args = makefile_mcp.parse_cli_args(["--max-cached-executions", "3", "--tail-lines", "5"])

        assert args.max_cached_executions == 3
        assert args.tail_lines == 5

    def test_import_tolerates_host_process_arguments(self):
        """Executing the module without running main() ignores host-process arguments.

        runpy re-executes the file under a non-__main__ name to observe the import
        contract itself; no test resets state this way — state is built by calling
        initialize_makefile_mcp(). The re-execution must parse no arguments and
        leave no module-level state behind.
        """
        argv = ["host-process", "--host-option", "value", "--makefile", "/nonexistent/Makefile"]
        module_path = pathlib.Path(__file__).resolve().parent.parent / "makefile_mcp.py"
        with patch("sys.argv", argv):
            module_globals = runpy.run_path(str(module_path), run_name="makefile_mcp_import")

        assert "cli_args" not in module_globals
        assert "output_cache" not in module_globals
        assert "filtered_targets" not in module_globals

    @pytest.mark.parametrize(
        "command",
        [
            [str(pathlib.Path(sys.executable).parent / "makefile-mcp")],
            [sys.executable, str(pathlib.Path(__file__).parents[1] / "makefile_mcp.py")],
        ],
        ids=["console-entry-point", "direct-script"],
    )
    def test_executable_startup_rejects_unknown_arguments(self, command):
        """Executable launch paths reject unknown arguments before starting the server."""
        result = subprocess.run(
            [*command, "--exlude", "deploy"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2
        assert "unrecognized arguments: --exlude deploy" in result.stderr
        assert "Starting Makefile MCP server" not in result.stderr


class TestErrorHandling:
    """Test error handling scenarios."""

    @patch("subprocess.run")
    def test_subprocess_timeout(self, mock_run, server_factory):
        """Test handling of subprocess timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("make", 300)

        server = server_factory(makefile_text="test:\n\techo 'test'")
        make_tool = server.create_make_tool("test", "Test target")
        result = make_tool()

        assert result["status"] == "error"
        assert "timed out" in result["message"]
        assert result["exit_code"] == -1

    @patch("subprocess.run")
    def test_subprocess_error(self, mock_run, server_factory):
        """Test handling of subprocess errors."""
        mock_run.side_effect = subprocess.SubprocessError("Command failed")

        server = server_factory(makefile_text="test:\n\techo 'test'")
        make_tool = server.create_make_tool("test", "Test target")
        result = make_tool()

        assert result["status"] == "error"
        assert "Failed to execute" in result["message"]
        assert result["exit_code"] == -1

    @patch("subprocess.run")
    def test_os_error_is_reported_structurally(self, mock_run, server_factory):
        """A missing make binary is a tool-level failure, not a crash."""
        mock_run.side_effect = FileNotFoundError(2, "No such file or directory", "make")

        server = server_factory(makefile_text="test:\n\techo 'test'")
        make_tool = server.create_make_tool("test", "Test target")
        result = make_tool()

        assert result["status"] == "error"
        assert "Failed to execute" in result["message"]
        assert result["exit_code"] == -1

    @patch("subprocess.run")
    def test_unexpected_errors_propagate(self, mock_run, server_factory):
        """Non-execution errors are bugs: they surface instead of being masked."""
        mock_run.side_effect = RuntimeError("programming bug")

        server = server_factory(makefile_text="test:\n\techo 'test'")
        make_tool = server.create_make_tool("test", "Test target")

        with pytest.raises(RuntimeError, match="programming bug"):
            make_tool()


class TestTimeoutPartialOutput:
    """Test that partial output captured before a timeout is preserved."""

    def _run_timed_out_target(self, tmp_path, timeout_error, extra_argv=()):
        """Initialize a server, run a target that times out, and return (server, result)."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("slow:\n\tsleep 600\n")

        with patch("subprocess.run", side_effect=timeout_error):
            server = makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile), *extra_argv])
            make_tool = server.create_make_tool("slow", "Slow target")
            return server, make_tool()

    def test_bytes_partial_output_is_cached(self, tmp_path):
        """Byte-valued captured output (what POSIX actually reports) is decoded and cached."""
        error = subprocess.TimeoutExpired(
            "make",
            300,
            output=b"compiling\nlinking\n",
            stderr=b"ld: undefined symbol _main\n",
        )
        server, result = self._run_timed_out_target(tmp_path, error)

        assert result["status"] == "error"
        assert "timed out" in result["message"]
        assert result["exit_code"] == -1
        assert result["execution_id"] == 1
        assert result["stdout_tail"] == "compiling\nlinking\n"
        assert result["stderr_tail"] == "ld: undefined symbol _main\n"
        assert result["stdout_total_lines"] == 2
        assert result["stdout_total_chars"] == len("compiling\nlinking\n")
        assert result["stderr_total_lines"] == 1
        assert result["stderr_total_chars"] == len("ld: undefined symbol _main\n")

        cached = server.output_cache.get(result["execution_id"])
        assert cached.stdout == "compiling\nlinking\n"
        assert cached.stderr == "ld: undefined symbol _main\n"
        assert cached.exit_code == -1
        assert cached.target == "slow"

    def test_str_partial_output_is_cached(self, tmp_path):
        """Already-decoded captured output is preserved without a secondary exception."""
        error = subprocess.TimeoutExpired("make", 300, output="running tests\n", stderr="warning: slow\n")
        _server, result = self._run_timed_out_target(tmp_path, error)

        assert result["stdout_tail"] == "running tests\n"
        assert result["stderr_tail"] == "warning: slow\n"
        assert result["stdout_total_lines"] == 1
        assert result["stderr_total_lines"] == 1

    def test_undecodable_bytes_do_not_raise(self, tmp_path):
        """Invalid UTF-8 in the partial stream is replaced rather than raising."""
        error = subprocess.TimeoutExpired("make", 300, output=b"ok\n\xff\n", stderr=None)
        _server, result = self._run_timed_out_target(tmp_path, error)

        assert result["exit_code"] == -1
        assert result["stdout_total_lines"] == 2
        assert "\ufffd" in result["stdout_tail"]

    def test_partial_output_is_retrievable_by_execution_id(self, tmp_path):
        """get_output and search_output can read the cached partial streams."""
        stdout = "".join(f"step {i}\n" for i in range(10)) + "FATAL: disk full\n"
        error = subprocess.TimeoutExpired("make", 300, output=stdout.encode(), stderr=b"make: *** [slow] Error 1\n")
        server, result = self._run_timed_out_target(tmp_path, error)
        eid = result["execution_id"]

        paged = server.get_output(eid, stream="stdout", start_line=0, end_line=3)
        assert paged["status"] == "success"
        assert paged["total_lines"] == 11
        assert paged["content"].splitlines() == ["step 0", "step 1", "step 2"]

        stderr_page = server.get_output(eid, stream="stderr", start_line=0, end_line=100)
        assert stderr_page["content"].strip() == "make: *** [slow] Error 1"

        found = server.search_output(eid, "FATAL")
        assert found["total_matches"] == 1
        assert found["matches"][0]["line_number"] == 10
        assert found["matches"][0]["text"] == "FATAL: disk full"

    def test_partial_output_is_tail_bounded(self, tmp_path):
        """Long partial output is truncated inline and points at the log tools."""
        stdout = "".join(f"line{i}\n" for i in range(100))
        error = subprocess.TimeoutExpired("make", 300, output=stdout.encode(), stderr=b"")
        server, result = self._run_timed_out_target(tmp_path, error, extra_argv=["--tail-lines", "5"])

        assert result["stdout_tail"].splitlines() == ["line95", "line96", "line97", "line98", "line99"]
        assert result["stdout_total_lines"] == 100
        assert "truncation_note" in result
        assert f"get_output(execution_id={result['execution_id']})" in result["truncation_note"]

        full = server.get_output(result["execution_id"], stream="stdout", start_line=0, end_line=1000)
        assert full["total_lines"] == 100

    def test_empty_partial_output_has_zero_metadata(self, tmp_path):
        """A timeout with nothing captured still reports a clear error and zero-valued totals."""
        error = subprocess.TimeoutExpired("make", 300)
        server, result = self._run_timed_out_target(tmp_path, error)

        assert result["status"] == "error"
        assert "timed out" in result["message"]
        assert result["exit_code"] == -1
        assert result["stdout_tail"] == ""
        assert result["stderr_tail"] == ""
        assert result["stdout_total_lines"] == 0
        assert result["stdout_total_chars"] == 0
        assert result["stderr_total_lines"] == 0
        assert result["stderr_total_chars"] == 0
        assert "truncation_note" not in result

        cached = server.output_cache.get(result["execution_id"])
        assert cached.stdout == ""
        assert cached.stderr == ""

    def test_timeout_response_reports_command_context(self, tmp_path):
        """The timeout response carries the same command context as a completed run."""
        error = subprocess.TimeoutExpired("make", 300, output=b"partial\n")
        server, result = self._run_timed_out_target(tmp_path, error)

        expected_command = " ".join(
            ["make", "-C", str(server.config.working_dir), "-f", str(server.config.makefile_path), "slow"]
        )
        assert result["command"] == expected_command
        assert result["working_directory"] == str(server.config.working_dir)
        assert server.output_cache.get(result["execution_id"]).command == expected_command


class TestConfigurableTimeout:
    """Test the --timeout flag: a run exceeding it is killed and reported."""

    @patch("subprocess.run")
    def test_timeout_flag_reaches_subprocess(self, mock_run, tmp_path):
        """The configured timeout value is passed to subprocess.run, not hard-coded."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("build:\n\techo building\n")
        mock_run.return_value = MagicMock(returncode=0, stdout="building\n", stderr="")

        server = makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile), "--timeout", "42"])
        assert server.config.timeout_seconds == 42

        make_tool = server.create_make_tool("build", "Build")
        result = make_tool()

        assert result["status"] == "success"
        assert mock_run.call_args.kwargs["timeout"] == 42

    @patch("subprocess.run")
    def test_default_timeout_is_300_seconds(self, mock_run, server_factory):
        """Without the flag, the historical 300-second default applies."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        server = server_factory()
        make_tool = server.create_make_tool("build", "Build")
        result = make_tool()

        assert result["status"] == "success"
        assert mock_run.call_args.kwargs["timeout"] == 300

    @pytest.mark.skipif(shutil.which("make") is None, reason="requires a make executable")
    def test_run_exceeding_timeout_is_killed_and_reported(self, tmp_path):
        """An unmocked run longer than --timeout is killed and reported as a timeout error.

        The target sleeps far longer than the configured timeout; the response must come
        back quickly with the timeout report, and the killed run must still be cached so
        get_output/search_output can address it.
        """
        makefile = tmp_path / "Makefile"
        makefile.write_text("slow:\n\tsleep 30\n")

        server = makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile), "--timeout", "1"])
        make_tool = server.create_make_tool("slow", "Slow target")

        started = time.monotonic()
        result = make_tool()
        elapsed = time.monotonic() - started

        assert elapsed < 10, f"run took {elapsed:.1f}s; the 1s timeout did not kill the target"
        assert result["status"] == "error"
        assert result["target"] == "slow"
        assert result["exit_code"] == -1
        assert "timed out after 1 seconds" in result["message"]

        cached = server.output_cache.get(result["execution_id"])
        assert cached is not None
        assert cached.target == "slow"
        assert cached.exit_code == -1


class TestOutputCache:
    """Test the OutputCache class."""

    def _get_cache(self, max_entries=20):
        from makefile_mcp import OutputCache

        return OutputCache(max_entries=max_entries)

    def test_add_and_get(self):
        """Test adding and retrieving entries."""
        cache = self._get_cache()
        entry = cache.add("build", "make build", "hello\nworld\n", "warn\n", 0)

        assert entry.execution_id == 1
        assert entry.target == "build"
        assert entry.stdout == "hello\nworld\n"
        assert entry.stderr == "warn\n"
        assert entry.exit_code == 0

        retrieved = cache.get(1)
        assert retrieved is entry

    def test_auto_increment_id(self):
        """Test that execution IDs auto-increment."""
        cache = self._get_cache()
        e1 = cache.add("a", "make a", "", "", 0)
        e2 = cache.add("b", "make b", "", "", 0)
        e3 = cache.add("c", "make c", "", "", 0)
        assert e1.execution_id == 1
        assert e2.execution_id == 2
        assert e3.execution_id == 3

    def test_eviction(self):
        """Test that oldest entries are evicted when over limit."""
        cache = self._get_cache(max_entries=3)
        cache.add("a", "make a", "out_a", "", 0)
        cache.add("b", "make b", "out_b", "", 0)
        cache.add("c", "make c", "out_c", "", 0)
        assert len(cache) == 3

        # Adding a 4th should evict the oldest (id=1)
        cache.add("d", "make d", "out_d", "", 0)
        assert len(cache) == 3
        assert cache.get(1) is None
        assert cache.get(2) is not None
        assert cache.get(4) is not None

    def test_get_missing_id(self):
        """Test that getting a non-existent ID returns None."""
        cache = self._get_cache()
        assert cache.get(999) is None

    def test_concurrent_add_keeps_ids_and_payloads_together(self):
        """Concurrent add() calls must return unique IDs that resolve to their own payload."""
        threads_count = 12
        adds_per_thread = 40
        cache = self._get_cache(max_entries=threads_count * adds_per_thread)

        results = []
        results_lock = threading.Lock()
        start = threading.Barrier(threads_count)

        def worker(thread_index):
            start.wait()
            local = []
            for call_index in range(adds_per_thread):
                payload = f"t{thread_index}-{call_index}"
                entry = cache.add(payload, f"make {payload}", payload, "", 0)
                local.append((entry.execution_id, payload))
            with results_lock:
                results.extend(local)

        original_switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            sys.setswitchinterval(original_switch_interval)

        expected_total = threads_count * adds_per_thread
        assert len(results) == expected_total

        # The property that actually breaks without a lock: the execution_id handed
        # back to a caller must address the entry that same call stored.
        mismatched = [
            (execution_id, payload)
            for execution_id, payload in results
            if cache.get(execution_id) is None or cache.get(execution_id).stdout != payload
        ]
        assert mismatched == [], f"{len(mismatched)} execution_ids resolve to another run's output"

        returned_ids = [execution_id for execution_id, _ in results]
        assert len(set(returned_ids)) == expected_total, "add() handed the same execution_id to two callers"

        assert len(cache) == expected_total


class TestTailTruncation:
    """Test the tail-line truncation behavior in make tool responses."""

    @patch("subprocess.run")
    def test_short_output_not_truncated(self, mock_run, server_factory):
        """Output shorter than tail_lines should not be truncated."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "line1\nline2\nline3\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        server = server_factory(extra_args=["--tail-lines", "50"])
        make_tool = server.create_make_tool("build", "Build")
        result = make_tool()

        assert result["stdout_tail"] == "line1\nline2\nline3\n"
        assert "truncation_note" not in result

    @patch("subprocess.run")
    def test_long_output_truncated(self, mock_run, server_factory):
        """Output longer than tail_lines should be truncated to last N lines."""
        lines = [f"line{i}" for i in range(100)]
        full_output = "\n".join(lines) + "\n"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = full_output
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        server = server_factory(extra_args=["--tail-lines", "5"])
        make_tool = server.create_make_tool("build", "Build")
        result = make_tool()

        # Should only have the last 5 lines
        tail_lines = result["stdout_tail"].splitlines()
        assert len(tail_lines) == 5
        assert tail_lines[0] == "line95"
        assert tail_lines[4] == "line99"

        assert result["stdout_total_lines"] == 100
        assert "truncation_note" in result
        assert "get_output" in result["truncation_note"]

    @patch("subprocess.run")
    def test_execution_id_in_response(self, mock_run, server_factory):
        """Response should include execution_id for cache retrieval."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        server = server_factory()
        make_tool = server.create_make_tool("test", "Test")
        result = make_tool()
        assert "execution_id" in result
        assert isinstance(result["execution_id"], int)


class TestGetOutput:
    """Test the get_output MCP tool."""

    def _setup(self, server_factory):
        """Set up a server with cached output."""
        server = server_factory()

        # Directly add to cache
        lines = [f"line{i}" for i in range(20)]
        full_output = "\n".join(lines) + "\n"
        entry = server.output_cache.add("test", "make test", full_output, "err0\nerr1\n", 0)
        return server, entry.execution_id

    def test_basic_pagination(self, server_factory):
        """Test retrieving a range of lines."""
        server, eid = self._setup(server_factory)
        result = server.get_output(eid, stream="stdout", start_line=0, end_line=5)

        assert result["status"] == "success"
        assert result["execution_id"] == eid
        content_lines = result["content"].splitlines()
        assert len(content_lines) == 5
        assert content_lines[0] == "line0"
        assert content_lines[4] == "line4"
        assert result["total_lines"] == 20

    def test_middle_range(self, server_factory):
        """Test retrieving lines from the middle."""
        server, eid = self._setup(server_factory)
        result = server.get_output(eid, stream="stdout", start_line=10, end_line=13)
        content_lines = result["content"].splitlines()
        assert content_lines[0] == "line10"
        assert content_lines[2] == "line12"

    def test_stderr_stream(self, server_factory):
        """Test reading from stderr."""
        server, eid = self._setup(server_factory)
        result = server.get_output(eid, stream="stderr", start_line=0, end_line=100)
        assert result["status"] == "success"
        assert "err0" in result["content"]
        assert result["total_lines"] == 2

    def test_out_of_range_clamped(self, server_factory):
        """Test that out-of-range line numbers are clamped."""
        server, eid = self._setup(server_factory)
        result = server.get_output(eid, stream="stdout", start_line=0, end_line=9999)
        assert result["status"] == "success"
        assert result["end_line"] == result["total_lines"]

    def test_missing_execution_id(self, server_factory):
        """Test error for missing execution ID."""
        server, _eid = self._setup(server_factory)
        result = server.get_output(99999)
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_invalid_stream(self, server_factory):
        """Test error for invalid stream name."""
        server, eid = self._setup(server_factory)
        result = server.get_output(eid, stream="invalid")
        assert result["status"] == "error"
        assert "Invalid stream" in result["message"]


class TestSearchOutput:
    """Test the search_output MCP tool."""

    def _setup(self, server_factory):
        """Set up a server with cached output."""
        server = server_factory()

        output = "Starting build\nCompiling main.c\nWARNING: deprecated function\nCompiling util.c\nLinking...\nWARNING: unused variable\nBuild complete\n"
        entry = server.output_cache.add("build", "make build", output, "", 0)
        return server, entry.execution_id

    def test_basic_search(self, server_factory):
        """Test basic substring search."""
        server, eid = self._setup(server_factory)
        result = server.search_output(eid, "WARNING")

        assert result["status"] == "success"
        assert result["total_matches"] == 2
        assert result["matches"][0]["line_number"] == 2
        assert "deprecated" in result["matches"][0]["text"]
        assert result["matches"][1]["line_number"] == 5

    def test_case_insensitive(self, server_factory):
        """Test that search is case-insensitive."""
        server, eid = self._setup(server_factory)
        result = server.search_output(eid, "warning")
        assert result["total_matches"] == 2

    def test_context_lines(self, server_factory):
        """Test that context lines are included."""
        server, eid = self._setup(server_factory)
        result = server.search_output(eid, "WARNING", context_lines=1)
        match = result["matches"][0]
        context = match["context"]

        # Should have line before, the match, and line after
        assert len(context) == 3
        assert context[0]["is_match"] is False
        assert context[1]["is_match"] is True
        assert context[2]["is_match"] is False

    def test_no_matches(self, server_factory):
        """Test search with no results."""
        server, eid = self._setup(server_factory)
        result = server.search_output(eid, "NONEXISTENT_PATTERN")
        assert result["status"] == "success"
        assert result["total_matches"] == 0
        assert result["matches"] == []

    def test_missing_execution_id(self, server_factory):
        """Test error for missing execution ID."""
        server, _eid = self._setup(server_factory)
        result = server.search_output(99999, "test")
        assert result["status"] == "error"

    def test_search_stderr(self, server_factory):
        """Test searching stderr stream."""
        server = server_factory()
        entry = server.output_cache.add("t", "make t", "", "error: foo\nwarning: bar\n", 1)
        result = server.search_output(entry.execution_id, "error", stream="stderr")
        assert result["total_matches"] == 1
        assert result["matches"][0]["line_number"] == 0

    def test_line_numbers_for_followup(self, server_factory):
        """Test that match line numbers can be used with get_output."""
        server, eid = self._setup(server_factory)
        search_result = server.search_output(eid, "WARNING")

        # Use first match line number with get_output
        line_num = search_result["matches"][0]["line_number"]
        get_result = server.get_output(eid, start_line=line_num, end_line=line_num + 1)
        assert "WARNING" in get_result["content"]


class TestSearchOutputBounds:
    """Test search_output input validation and result bounding."""

    def _setup(self, server_factory, warning_count=40):
        """Cache an output where every other line matches 'WARNING'."""
        server = server_factory()

        lines = []
        for i in range(warning_count):
            lines.append(f"Compiling file{i}.c")
            lines.append(f"WARNING: issue {i}")
        output = "\n".join(lines) + "\n"
        entry = server.output_cache.add("build", "make build", output, "", 0)
        return server, entry.execution_id

    def test_empty_pattern_rejected(self, server_factory):
        """An empty pattern would match every cached line and is rejected."""
        server, eid = self._setup(server_factory)
        result = server.search_output(eid, "")
        assert result["status"] == "error"
        assert "must not be empty" in result["message"]
        assert "matches" not in result

    def test_negative_context_lines_rejected(self, server_factory):
        """Negative context sizes produce incoherent ranges and are rejected."""
        server, eid = self._setup(server_factory)
        result = server.search_output(eid, "WARNING", context_lines=-1)
        assert result["status"] == "error"
        assert "context_lines" in result["message"]
        assert "matches" not in result

    def test_zero_context_lines_allowed(self, server_factory):
        """Zero context is valid and returns only the matching line."""
        server, eid = self._setup(server_factory)
        result = server.search_output(eid, "WARNING: issue 0", context_lines=0)
        assert result["status"] == "success"
        assert result["total_matches"] == 1
        assert result["matches"][0]["context"] == [{"line_number": 1, "text": "WARNING: issue 0", "is_match": True}]

    def test_non_positive_max_results_rejected(self, server_factory):
        """max_results must be positive."""
        server, eid = self._setup(server_factory)
        for bad in (0, -5):
            result = server.search_output(eid, "WARNING", max_results=bad)
            assert result["status"] == "error"
            assert "max_results" in result["message"]
            assert "matches" not in result

    def test_default_cap_truncates_and_reports_full_count(self, server_factory):
        """The default cap bounds returned matches while counting them all."""
        server, eid = self._setup(server_factory, warning_count=40)
        result = server.search_output(eid, "WARNING", context_lines=1)

        cap = makefile_mcp.DEFAULT_MAX_SEARCH_RESULTS
        assert cap == 20
        assert result["status"] == "success"
        assert result["total_matches"] == 40
        assert result["returned_matches"] == cap
        assert len(result["matches"]) == cap
        assert result["max_results"] == cap
        assert result["truncated"] is True
        assert "20 of 40 matches" in result["truncation_note"]
        assert f"get_output(execution_id={eid})" in result["truncation_note"]

        # The bounded prefix is the first matches in line order.
        assert [m["line_number"] for m in result["matches"]] == [2 * i + 1 for i in range(cap)]
        assert result["matches"][0]["text"] == "WARNING: issue 0"
        assert result["matches"][-1]["text"] == f"WARNING: issue {cap - 1}"

    def test_explicit_max_results_caps_matches(self, server_factory):
        """An explicit max_results overrides the default cap."""
        server, eid = self._setup(server_factory, warning_count=40)
        result = server.search_output(eid, "WARNING", max_results=3)

        assert result["total_matches"] == 40
        assert result["returned_matches"] == 3
        assert result["max_results"] == 3
        assert result["truncated"] is True
        assert [m["line_number"] for m in result["matches"]] == [1, 3, 5]

    def test_uncapped_search_returns_every_match(self, server_factory):
        """A search below the cap returns all matches and reports no truncation."""
        server, eid = self._setup(server_factory, warning_count=5)
        result = server.search_output(eid, "WARNING", context_lines=1)

        assert result["total_matches"] == 5
        assert result["returned_matches"] == 5
        assert result["truncated"] is False
        assert "truncation_note" not in result
        assert [m["line_number"] for m in result["matches"]] == [1, 3, 5, 7, 9]
        assert [m["text"] for m in result["matches"]] == [f"WARNING: issue {i}" for i in range(5)]
        assert result["matches"][0]["context"] == [
            {"line_number": 0, "text": "Compiling file0.c", "is_match": False},
            {"line_number": 1, "text": "WARNING: issue 0", "is_match": True},
            {"line_number": 2, "text": "Compiling file1.c", "is_match": False},
        ]

    def test_no_matches_is_not_truncated(self, server_factory):
        """A zero-match search reports no truncation."""
        server, eid = self._setup(server_factory, warning_count=5)
        result = server.search_output(eid, "NONEXISTENT_PATTERN")
        assert result["total_matches"] == 0
        assert result["returned_matches"] == 0
        assert result["truncated"] is False
        assert result["matches"] == []


try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 has no stdlib tomllib
    tomllib = None

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# PEP 723: the metadata block is delimited by `# /// script` and a closing `# ///`,
# and every content line is prefixed with `# ` (or is a bare `#`).
_PEP723_BLOCK_RE = re.compile(r"(?m)^# /// script\s*$\n(?P<body>(?:^#(?: .*)?$\n)*)^# ///\s*$")


def _read_script_dependencies(script_path):
    """Extract the `dependencies` list from a file's PEP 723 inline metadata block."""
    match = _PEP723_BLOCK_RE.search(script_path.read_text(encoding="utf-8"))
    assert match is not None, f"no PEP 723 script block found in {script_path}"
    content = "".join(line[2:] if line.startswith("# ") else line[1:] for line in match.group("body").splitlines(True))
    return tomllib.loads(content)["dependencies"]


def _find_requirement(dependencies, name):
    """Return the single requirement string for `name` from a PEP 508 dependency list."""
    matches = [dep for dep in dependencies if re.match(rf"^{name}\b", dep.strip())]
    assert len(matches) == 1, f"expected exactly one '{name}' requirement, got {matches!r}"
    return matches[0].strip()


@pytest.mark.skipif(tomllib is None, reason="tomllib is stdlib only from Python 3.11")
class TestDependencyDeclarationsAgree:
    """The fastmcp requirement is declared twice; the two must not drift apart."""

    def test_script_block_matches_pyproject(self):
        """The PEP 723 block and pyproject.toml must declare the same fastmcp requirement.

        `uv run makefile_mcp.py` builds its environment from the inline block, while the
        installed console script uses pyproject.toml. If these disagree, the two documented
        entry points run against different major versions of fastmcp.
        """
        with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
            project_dependencies = tomllib.load(handle)["project"]["dependencies"]
        script_dependencies = _read_script_dependencies(REPO_ROOT / "makefile_mcp.py")

        assert _find_requirement(script_dependencies, "fastmcp") == _find_requirement(project_dependencies, "fastmcp")

    def test_script_block_requires_fastmcp_3(self):
        """Guard the major version, which equality alone would not.

        Setting both declarations back to fastmcp 2 would satisfy the equality check above
        while reintroducing the blocking-sync-tool behavior the 3.x upgrade fixed.
        """
        requirement = _find_requirement(_read_script_dependencies(REPO_ROOT / "makefile_mcp.py"), "fastmcp")
        assert re.search(r">=\s*3\.", requirement), requirement
        assert re.search(r"<\s*4(\.|,|$)", requirement), requirement


def _make_major_version() -> int:
    """Return the installed make's major version, or 0 when it cannot be determined."""
    if shutil.which("make") is None:
        return 0
    try:
        banner = subprocess.run(["make", "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    match = re.search(r"(\d+)\.\d+", banner)
    return int(match.group(1)) if match else 0


@pytest.mark.skipif(shutil.which("make") is None, reason="requires a make executable")
class TestRealMakeExpansionRegression:
    """Prove against a real make that assignment values cannot execute commands."""

    MARKER = "pwned"

    @pytest.fixture
    def harness(self, tmp_path, monkeypatch):
        """A Makefile whose only target ignores every variable the caller can set.

        The process cwd moves to the same directory because make evaluates a '!='
        shell assignment in its invoking directory, not in the '-C' directory, so a
        marker could otherwise be written outside the space this test inspects.
        """
        makefile = tmp_path / "Makefile"
        makefile.write_text('safe:\n\t@echo "safe target ran"\n')
        monkeypatch.chdir(tmp_path)
        return makefile

    def _marker(self, makefile):
        return makefile.parent / self.MARKER

    @pytest.mark.parametrize(
        "assignment",
        [
            "X=$(shell touch pwned)",
            "X=${shell touch pwned}",
        ],
    )
    def test_real_make_would_execute_the_assignment(self, harness, assignment):
        """Control: the pre-fix argv (validation skipped) really does create the marker.

        Without this the regression below could pass because make ignores the token,
        rather than because the guard rejected it.
        """
        subprocess.run(
            ["make", "-C", str(harness.parent), "-f", str(harness), "safe", assignment],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert self._marker(harness).exists(), (
            "make did not expand the assignment; the control is not measuring the bug"
        )

    @pytest.mark.skipif(_make_major_version() < 4, reason="'!=' shell assignment requires GNU make >= 4.0")
    def test_real_make_would_run_a_shell_assignment(self, harness):
        """Control for the '!=' operator, which older make releases do not implement."""
        subprocess.run(
            ["make", "-C", str(harness.parent), "-f", str(harness), "safe", "X!=touch pwned"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert self._marker(harness).exists(), "make did not run the shell assignment; the control is not measuring it"

    @pytest.mark.parametrize(
        "additional_args",
        [
            'X="$(shell touch pwned)"',
            'X="${shell touch pwned}"',
            "X!='touch pwned'",
        ],
    )
    def test_project_path_never_executes_the_assignment(self, harness, additional_args):
        """The same input through the generated tool is rejected and runs no command."""
        server = makefile_mcp.initialize_makefile_mcp(["--makefile", str(harness)])
        make_tool = server.create_make_tool("safe", "Run the safe target")
        result = make_tool(additional_args=additional_args)

        # The marker assertion carries the security claim, so it runs before the
        # weaker response-shape assertions and cannot be masked by them.
        assert not self._marker(harness).exists()
        assert result["status"] == "error"
        assert result["exit_code"] == -1

    def test_literal_assignment_still_reaches_real_make(self, harness):
        """A plain value is not collateral damage: it runs and stays a single argv token."""
        server = makefile_mcp.initialize_makefile_mcp(["--makefile", str(harness)])
        make_tool = server.create_make_tool("safe", "Run the safe target")
        result = make_tool(additional_args='VERBOSE=1 MESSAGE="hello world"')

        assert result["status"] == "success"
        assert "safe target ran" in result["stdout_tail"]
        assert result["command"].endswith("safe VERBOSE=1 MESSAGE=hello world")


class TestSingleToolRegistration:
    """Every startup path registers each discovered target exactly once."""

    MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "makefile_mcp.py"
    MAKEFILE_CONTENT = "# Build it\nbuild:\n\techo build\n\n# Test it\ntest:\n\techo test\n"

    @staticmethod
    @contextlib.contextmanager
    def _recorded_registrations():
        """Record the name of every function registered as a tool, keeping registration real."""
        import fastmcp

        registered = []
        original_tool = fastmcp.FastMCP.tool

        def recording_tool(self, *args, **kwargs):
            decorator = original_tool(self, *args, **kwargs)

            def record(fn):
                registered.append(fn.__name__)
                return decorator(fn)

            return record

        with patch.object(fastmcp.FastMCP, "tool", recording_tool):
            yield registered

    def test_direct_script_registers_each_target_once(self, tmp_path, capsys):
        """`uv run makefile_mcp.py` executes the module as __main__ and registers one tool per target."""
        import fastmcp

        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text(self.MAKEFILE_CONTENT)

        argv = ["makefile_mcp.py", "--makefile", str(makefile_path)]
        with patch("sys.argv", argv), self._recorded_registrations() as registered:
            with patch.object(fastmcp.FastMCP, "run") as run:
                runpy.run_path(str(self.MODULE_PATH), run_name="__main__")

        # The registration counts carry the regression claim, so they run before the
        # weaker lifecycle assertions and cannot be masked by them.
        assert registered.count("make_build") == 1
        assert registered.count("make_test") == 1
        for utility in ("list_available_targets", "get_makefile_info", "get_output", "search_output"):
            assert registered.count(utility) == 1
        assert "Available targets: build, test" in capsys.readouterr().err
        run.assert_called_once_with()

    def test_imported_main_registers_each_target_once(self, tmp_path):
        """The console-script entry point registers one tool per target too."""
        import fastmcp

        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text(self.MAKEFILE_CONTENT)

        with self._recorded_registrations() as registered:
            with patch.object(fastmcp.FastMCP, "run") as run:
                server = makefile_mcp.main(["--makefile", str(makefile_path)])

        assert registered.count("make_build") == 1
        assert registered.count("make_test") == 1
        assert server.filtered_targets == {"build": "Build it", "test": "Test it"}
        run.assert_called_once_with()

    def test_import_alone_registers_no_tools(self):
        """Executing the module without main() registers nothing and exposes no server.

        runpy re-executes the file under a non-__main__ name to observe the import
        contract itself; no test resets state this way — state is built by calling
        initialize_makefile_mcp().
        """
        with self._recorded_registrations() as registered:
            module_globals = runpy.run_path(str(self.MODULE_PATH), run_name="makefile_mcp_import")

        assert registered == []
        assert "mcp_server" not in module_globals
        assert "filtered_targets" not in module_globals

    def test_reinitialization_resets_state_without_reimport(self, tmp_path):
        """Calling initialize_makefile_mcp() again yields a fully independent server.

        This is the reset pattern that replaced deleting the module from sys.modules
        and reimporting it: a new init call builds fresh state, and nothing from the
        previous server — registrations, cache, execution IDs — leaks into it.
        """
        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text(self.MAKEFILE_CONTENT)

        first = makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile_path)])
        first.output_cache.add("build", "make build", "first run\n", "", 0)

        with self._recorded_registrations() as registered:
            second = makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile_path)])

        # The fresh server re-registers every tool exactly once, on its own FastMCP
        # instance, rather than doubling up registrations on shared module state.
        assert registered.count("make_build") == 1
        for utility in ("list_available_targets", "get_makefile_info", "get_output", "search_output"):
            assert registered.count(utility) == 1

        assert second.mcp_server is not first.mcp_server
        assert second.output_cache is not first.output_cache
        assert len(second.output_cache) == 0
        assert second.output_cache.get(1) is None
        assert second.filtered_targets == {"build": "Build it", "test": "Test it"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
