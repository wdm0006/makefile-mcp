#!/usr/bin/env python3
"""
Comprehensive test suite for the Makefile MCP Server

Tests Makefile parsing, target filtering, tool creation, and command execution.
"""

import os
import pathlib
import re
import subprocess

# Import the makefile MCP components
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMakefileParser:
    """Test the MakefileParser class functionality."""

    def test_simple_makefile_parsing(self):
        """Test parsing a simple Makefile with basic targets."""
        from makefile_mcp import MakefileParser

        makefile_content = """# Build the project
build:
	echo "Building..."

# Run tests
test:
	pytest

# Clean up build artifacts
clean:
	rm -rf build/

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
	echo "Building..."

# This is a test target
test:
	pytest

install:
	pip install -e .
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
	echo "All"

%.o: %.c
	gcc -c $< -o $@

clean:
	rm -f *.o

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

    @pytest.fixture
    def test_makefile(self):
        """Create a test Makefile for testing."""
        makefile_content = """# Build the project
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            f.flush()  # Ensure content is written to disk
            yield f.name

        os.unlink(f.name)

    def test_make_tool_creation(self, test_makefile):
        """Test that make tools are created correctly."""
        # Mock the CLI args and reimport
        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            # Clear the module cache
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            # Manually trigger target parsing with the test makefile
            makefile_mcp.MAKEFILE_PATH = pathlib.Path(test_makefile)
            makefile_mcp.WORKING_DIR = pathlib.Path(test_makefile).parent
            makefile_mcp.INCLUDE_TARGETS = None  # Include all targets
            makefile_mcp.EXCLUDE_TARGETS = set()  # Exclude nothing
            makefile_mcp.filtered_targets = makefile_mcp.get_makefile_targets()

            # Check that targets were parsed
            assert len(makefile_mcp.filtered_targets) == 3
            assert "build" in makefile_mcp.filtered_targets
            assert "test" in makefile_mcp.filtered_targets
            assert "clean" in makefile_mcp.filtered_targets

    def test_startup_rejects_colliding_tool_names(self, tmp_path, capsys):
        """Startup rejects targets that normalize to the same tool name."""
        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text("foo-bar:\n\techo hyphen\n\nfoo.bar:\n\techo period\n")

        with patch("sys.argv", ["makefile_mcp.py", "--makefile", str(makefile_path)]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            with patch.object(makefile_mcp, "create_make_tool") as create_make_tool:
                with pytest.raises(SystemExit) as exc_info:
                    makefile_mcp.main()

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

        argv = [
            "makefile_mcp.py",
            "--makefile",
            str(makefile_path),
            "--include",
            "build,test",
            "--exclude",
            "test",
        ]
        with patch("sys.argv", argv):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            with patch.object(makefile_mcp.mcp_server, "run") as run:
                makefile_mcp.main()

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

    def test_tool_name_normalization_is_shared(self, test_makefile):
        """Registration and target metadata use the same name generator."""
        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            makefile_mcp.filtered_targets = {"test-coverage.xml": "Coverage"}
            tool = makefile_mcp.create_make_tool("test-coverage.xml", "Coverage")
            listed_target = makefile_mcp.list_available_targets()["targets"][0]

        assert tool.__name__ == "make_test_coverage_xml"
        assert listed_target["tool_name"] == tool.__name__

    @patch("subprocess.run")
    def test_make_tool_execution_success(self, mock_run, test_makefile):
        """Test successful execution of a make target."""
        # Mock successful subprocess execution
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Building project...\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            # Create a make tool for testing
            make_tool = makefile_mcp.create_make_tool("build", "Build the project")

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
    def test_make_tool_execution_failure(self, mock_run, test_makefile):
        """Test failed execution of a make target."""
        # Mock failed subprocess execution
        mock_result = MagicMock()
        mock_result.returncode = 2
        mock_result.stdout = ""
        mock_result.stderr = "make: *** No rule to make target 'invalid'. Stop.\n"
        mock_run.return_value = mock_result

        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("invalid", "Invalid target")
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

        with patch(
            "sys.argv",
            [
                "makefile_mcp.py",
                "--makefile",
                str(custom_makefile),
                "--working-dir",
                str(working_dir),
            ],
        ):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("custom", "Run custom target")
            result = make_tool()

        expected_command = ["make", "-C", str(working_dir), "-f", str(custom_makefile), "custom"]
        mock_run.assert_called_once_with(expected_command, capture_output=True, text=True, timeout=300)
        assert result["command"] == " ".join(expected_command)

    @patch("subprocess.run")
    def test_make_tool_dry_run(self, mock_run, test_makefile):
        """Test dry run execution of a make target."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'echo "Building project..."\n'
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("build", "Build the project")
            result = make_tool(dry_run=True)

            assert result["status"] == "success"
            assert result["note"] == "This was a dry run - no commands were actually executed"

            # Verify -n flag was added for dry run
            call_args = mock_run.call_args[0][0]
            assert "-n" in call_args

    @patch("subprocess.run")
    def test_make_tool_with_additional_args(self, mock_run, test_makefile):
        """Test make tool execution with additional arguments."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Running tests with verbose output...\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("test", "Run tests")
            result = make_tool(additional_args="-j4 VERBOSE=1")

            assert result["status"] == "success"

            call_args = mock_run.call_args[0][0]
            assert call_args[-2:] == ["-j4", "VERBOSE=1"]

    @patch("subprocess.run")
    def test_make_tool_additional_args_preserve_quoting(self, mock_run, test_makefile):
        """Quoted values and escaped spaces stay a single argument."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("test", "Run tests")
            result = make_tool(additional_args='MESSAGE="hello world" PATH_ARG=my\\ file.txt -j4')

            assert result["status"] == "success"

            call_args = mock_run.call_args[0][0]
            assert call_args[-3:] == ["MESSAGE=hello world", "PATH_ARG=my file.txt", "-j4"]

    @patch("subprocess.run")
    def test_make_tool_invalid_additional_args(self, mock_run, test_makefile):
        """Malformed quoting is reported as an error without invoking make."""
        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("test", "Run tests")
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
        ],
    )
    @patch("subprocess.run")
    def test_make_tool_rejects_boundary_bypass_args(self, mock_run, additional_args, test_makefile):
        """Target/makefile/directory/eval bypass attempts never reach subprocess.run."""
        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("safe", "Run the safe target")
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
    def test_make_tool_accepts_safe_args(self, mock_run, additional_args, expected_tail, test_makefile):
        """Safe execution flags and variable assignments still reach make unchanged."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("safe", "Run the safe target")
            result = make_tool(additional_args=additional_args)

            assert result["status"] == "success"

            call_args = mock_run.call_args[0][0]
            assert call_args[-len(expected_tail) :] == expected_tail

    def test_list_available_targets_tool(self, test_makefile):
        """Test the list_available_targets tool."""
        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            # Manually trigger target parsing with the test makefile
            makefile_mcp.MAKEFILE_PATH = pathlib.Path(test_makefile)
            makefile_mcp.WORKING_DIR = pathlib.Path(test_makefile).parent
            makefile_mcp.INCLUDE_TARGETS = None  # Include all targets
            makefile_mcp.EXCLUDE_TARGETS = set()  # Exclude nothing
            makefile_mcp.filtered_targets = makefile_mcp.get_makefile_targets()

            result = makefile_mcp.list_available_targets()

            assert "makefile_path" in result
            assert "working_directory" in result
            assert "available_targets" in result
            assert result["available_targets"] == 3
            assert "targets" in result

            target_names = [t["name"] for t in result["targets"]]
            assert "build" in target_names
            assert "test" in target_names
            assert "clean" in target_names

    def test_get_makefile_info_tool(self, test_makefile):
        """Test the get_makefile_info tool."""
        with patch("sys.argv", ["makefile_mcp.py", "--makefile", test_makefile]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            # Manually trigger target parsing with the test makefile
            makefile_mcp.MAKEFILE_PATH = pathlib.Path(test_makefile)
            makefile_mcp.WORKING_DIR = pathlib.Path(test_makefile).parent
            makefile_mcp.INCLUDE_TARGETS = None  # Include all targets
            makefile_mcp.EXCLUDE_TARGETS = set()  # Exclude nothing
            makefile_mcp.filtered_targets = makefile_mcp.get_makefile_targets()

            result = makefile_mcp.get_makefile_info()

            assert result["makefile_exists"] is True
            assert result["all_targets"]["count"] == 3
            assert result["filtered_targets"]["count"] == 3
            assert result["filters"]["include"] is None
            assert result["filters"]["exclude"] is None


class TestCommandLineArguments:
    """Test command-line argument parsing and filtering."""

    def test_include_filter(self):
        """Test --include command line argument."""
        test_args = ["makefile_mcp.py", "--include", "build,test"]

        with patch("sys.argv", test_args):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            # Just test the arg parsing function directly
            from makefile_mcp import parse_cli_args

            args = parse_cli_args()

            assert args.include == "build,test"
            assert args.exclude is None

    def test_exclude_filter(self):
        """Test --exclude command line argument."""
        test_args = ["makefile_mcp.py", "--exclude", "clean,deploy"]

        with patch("sys.argv", test_args):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            from makefile_mcp import parse_cli_args

            args = parse_cli_args()

            assert args.exclude == "clean,deploy"
            assert args.include is None

    def test_custom_makefile_path(self):
        """Test --makefile command line argument."""
        test_args = ["makefile_mcp.py", "--makefile", "/custom/path/Makefile"]

        with patch("sys.argv", test_args):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            from makefile_mcp import parse_cli_args

            args = parse_cli_args()

            assert args.makefile == "/custom/path/Makefile"

    def test_working_directory(self):
        """Test --working-dir command line argument."""
        test_args = ["makefile_mcp.py", "--working-dir", "/custom/work/dir"]

        with patch("sys.argv", test_args):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            from makefile_mcp import parse_cli_args

            args = parse_cli_args()

            assert args.working_dir == "/custom/work/dir"

    @pytest.mark.parametrize("option", ["--max-cached-executions", "--tail-lines"])
    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_output_limits_must_be_positive(self, option, value, capsys):
        """Test output limits reject zero and negative values."""
        with patch("sys.argv", ["makefile_mcp.py"]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            from makefile_mcp import parse_cli_args

        with patch("sys.argv", ["makefile_mcp.py", option, value]):
            with pytest.raises(SystemExit) as exc_info:
                parse_cli_args()

        assert exc_info.value.code == 2
        error = capsys.readouterr().err
        assert f"argument {option}" in error
        assert "must be a positive integer" in error

    def test_positive_output_limits_are_accepted(self):
        """Test positive custom output limits are preserved."""
        test_args = ["makefile_mcp.py", "--max-cached-executions", "3", "--tail-lines", "5"]

        with patch("sys.argv", test_args):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            from makefile_mcp import parse_cli_args

            args = parse_cli_args()

        assert args.max_cached_executions == 3
        assert args.tail_lines == 5

    def test_import_tolerates_host_process_arguments(self):
        """Importing the module ignores arguments owned by the host process."""
        with patch("sys.argv", ["host-process", "--host-option", "value"]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

        assert makefile_mcp.cli_args.makefile == "Makefile"

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
    def test_subprocess_timeout(self, mock_run):
        """Test handling of subprocess timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("make", 300)

        # Create a temporary makefile for this test
        makefile_content = "test:\n\techo 'test'"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            with patch("sys.argv", ["makefile_mcp.py", "--makefile", makefile_path]):
                if "makefile_mcp" in sys.modules:
                    del sys.modules["makefile_mcp"]

                import makefile_mcp

                make_tool = makefile_mcp.create_make_tool("test", "Test target")
                result = make_tool()

                assert result["status"] == "error"
                assert "timed out" in result["message"]
                assert result["exit_code"] == -1
        finally:
            os.unlink(makefile_path)

    @patch("subprocess.run")
    def test_subprocess_error(self, mock_run):
        """Test handling of subprocess errors."""
        mock_run.side_effect = subprocess.SubprocessError("Command failed")

        makefile_content = "test:\n\techo 'test'"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(makefile_content)
            makefile_path = f.name

        try:
            with patch("sys.argv", ["makefile_mcp.py", "--makefile", makefile_path]):
                if "makefile_mcp" in sys.modules:
                    del sys.modules["makefile_mcp"]

                import makefile_mcp

                make_tool = makefile_mcp.create_make_tool("test", "Test target")
                result = make_tool()

                assert result["status"] == "error"
                assert "Failed to execute" in result["message"]
                assert result["exit_code"] == -1
        finally:
            os.unlink(makefile_path)


class TestTimeoutPartialOutput:
    """Test that partial output captured before a timeout is preserved."""

    def _run_timed_out_target(self, tmp_path, timeout_error, extra_argv=()):
        """Import a fresh module, run a target that times out, return (module, result)."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("slow:\n\tsleep 600\n")

        argv = ["makefile_mcp.py", "--makefile", str(makefile), *extra_argv]
        with patch("subprocess.run", side_effect=timeout_error), patch("sys.argv", argv):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]

            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("slow", "Slow target")
            return makefile_mcp, make_tool()

    def test_bytes_partial_output_is_cached(self, tmp_path):
        """Byte-valued captured output (what POSIX actually reports) is decoded and cached."""
        error = subprocess.TimeoutExpired(
            "make",
            300,
            output=b"compiling\nlinking\n",
            stderr=b"ld: undefined symbol _main\n",
        )
        makefile_mcp, result = self._run_timed_out_target(tmp_path, error)

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

        cached = makefile_mcp.output_cache.get(result["execution_id"])
        assert cached.stdout == "compiling\nlinking\n"
        assert cached.stderr == "ld: undefined symbol _main\n"
        assert cached.exit_code == -1
        assert cached.target == "slow"

    def test_str_partial_output_is_cached(self, tmp_path):
        """Already-decoded captured output is preserved without a secondary exception."""
        error = subprocess.TimeoutExpired("make", 300, output="running tests\n", stderr="warning: slow\n")
        _makefile_mcp, result = self._run_timed_out_target(tmp_path, error)

        assert result["stdout_tail"] == "running tests\n"
        assert result["stderr_tail"] == "warning: slow\n"
        assert result["stdout_total_lines"] == 1
        assert result["stderr_total_lines"] == 1

    def test_undecodable_bytes_do_not_raise(self, tmp_path):
        """Invalid UTF-8 in the partial stream is replaced rather than raising."""
        error = subprocess.TimeoutExpired("make", 300, output=b"ok\n\xff\n", stderr=None)
        _makefile_mcp, result = self._run_timed_out_target(tmp_path, error)

        assert result["exit_code"] == -1
        assert result["stdout_total_lines"] == 2
        assert "�" in result["stdout_tail"]

    def test_partial_output_is_retrievable_by_execution_id(self, tmp_path):
        """get_output and search_output can read the cached partial streams."""
        stdout = "".join(f"step {i}\n" for i in range(10)) + "FATAL: disk full\n"
        error = subprocess.TimeoutExpired("make", 300, output=stdout.encode(), stderr=b"make: *** [slow] Error 1\n")
        makefile_mcp, result = self._run_timed_out_target(tmp_path, error)
        eid = result["execution_id"]

        paged = makefile_mcp.get_output(eid, stream="stdout", start_line=0, end_line=3)
        assert paged["status"] == "success"
        assert paged["total_lines"] == 11
        assert paged["content"].splitlines() == ["step 0", "step 1", "step 2"]

        stderr_page = makefile_mcp.get_output(eid, stream="stderr", start_line=0, end_line=100)
        assert stderr_page["content"].strip() == "make: *** [slow] Error 1"

        found = makefile_mcp.search_output(eid, "FATAL")
        assert found["total_matches"] == 1
        assert found["matches"][0]["line_number"] == 10
        assert found["matches"][0]["text"] == "FATAL: disk full"

    def test_partial_output_is_tail_bounded(self, tmp_path):
        """Long partial output is truncated inline and points at the log tools."""
        stdout = "".join(f"line{i}\n" for i in range(100))
        error = subprocess.TimeoutExpired("make", 300, output=stdout.encode(), stderr=b"")
        makefile_mcp, result = self._run_timed_out_target(tmp_path, error, extra_argv=["--tail-lines", "5"])

        assert result["stdout_tail"].splitlines() == ["line95", "line96", "line97", "line98", "line99"]
        assert result["stdout_total_lines"] == 100
        assert "truncation_note" in result
        assert f"get_output(execution_id={result['execution_id']})" in result["truncation_note"]

        full = makefile_mcp.get_output(result["execution_id"], stream="stdout", start_line=0, end_line=1000)
        assert full["total_lines"] == 100

    def test_empty_partial_output_has_zero_metadata(self, tmp_path):
        """A timeout with nothing captured still reports a clear error and zero-valued totals."""
        error = subprocess.TimeoutExpired("make", 300)
        makefile_mcp, result = self._run_timed_out_target(tmp_path, error)

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

        cached = makefile_mcp.output_cache.get(result["execution_id"])
        assert cached.stdout == ""
        assert cached.stderr == ""

    def test_timeout_response_reports_command_context(self, tmp_path):
        """The timeout response carries the same command context as a completed run."""
        error = subprocess.TimeoutExpired("make", 300, output=b"partial\n")
        makefile_mcp, result = self._run_timed_out_target(tmp_path, error)

        expected_command = " ".join(
            ["make", "-C", str(makefile_mcp.WORKING_DIR), "-f", str(makefile_mcp.MAKEFILE_PATH), "slow"]
        )
        assert result["command"] == expected_command
        assert result["working_directory"] == str(makefile_mcp.WORKING_DIR)
        assert makefile_mcp.output_cache.get(result["execution_id"]).command == expected_command


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


class TestTailTruncation:
    """Test the tail-line truncation behavior in make tool responses."""

    @patch("subprocess.run")
    def test_short_output_not_truncated(self, mock_run):
        """Output shorter than tail_lines should not be truncated."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "line1\nline2\nline3\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("sys.argv", ["makefile_mcp.py", "--tail-lines", "50"]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]
            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("build", "Build")
            result = make_tool()

            assert result["stdout_tail"] == "line1\nline2\nline3\n"
            assert "truncation_note" not in result

    @patch("subprocess.run")
    def test_long_output_truncated(self, mock_run):
        """Output longer than tail_lines should be truncated to last N lines."""
        lines = [f"line{i}" for i in range(100)]
        full_output = "\n".join(lines) + "\n"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = full_output
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("sys.argv", ["makefile_mcp.py", "--tail-lines", "5"]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]
            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("build", "Build")
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
    def test_execution_id_in_response(self, mock_run):
        """Response should include execution_id for cache retrieval."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("sys.argv", ["makefile_mcp.py"]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]
            import makefile_mcp

            make_tool = makefile_mcp.create_make_tool("test", "Test")
            result = make_tool()
            assert "execution_id" in result
            assert isinstance(result["execution_id"], int)


class TestGetOutput:
    """Test the get_output MCP tool."""

    def _setup(self):
        """Set up a module with cached output."""
        with patch("sys.argv", ["makefile_mcp.py"]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]
            import makefile_mcp

            # Directly add to cache
            lines = [f"line{i}" for i in range(20)]
            full_output = "\n".join(lines) + "\n"
            entry = makefile_mcp.output_cache.add("test", "make test", full_output, "err0\nerr1\n", 0)
            return makefile_mcp, entry.execution_id

    def test_basic_pagination(self):
        """Test retrieving a range of lines."""
        makefile_mcp, eid = self._setup()
        result = makefile_mcp.get_output(eid, stream="stdout", start_line=0, end_line=5)

        assert result["status"] == "success"
        assert result["execution_id"] == eid
        content_lines = result["content"].splitlines()
        assert len(content_lines) == 5
        assert content_lines[0] == "line0"
        assert content_lines[4] == "line4"
        assert result["total_lines"] == 20

    def test_middle_range(self):
        """Test retrieving lines from the middle."""
        makefile_mcp, eid = self._setup()
        result = makefile_mcp.get_output(eid, stream="stdout", start_line=10, end_line=13)
        content_lines = result["content"].splitlines()
        assert content_lines[0] == "line10"
        assert content_lines[2] == "line12"

    def test_stderr_stream(self):
        """Test reading from stderr."""
        makefile_mcp, eid = self._setup()
        result = makefile_mcp.get_output(eid, stream="stderr", start_line=0, end_line=100)
        assert result["status"] == "success"
        assert "err0" in result["content"]
        assert result["total_lines"] == 2

    def test_out_of_range_clamped(self):
        """Test that out-of-range line numbers are clamped."""
        makefile_mcp, eid = self._setup()
        result = makefile_mcp.get_output(eid, stream="stdout", start_line=0, end_line=9999)
        assert result["status"] == "success"
        assert result["end_line"] == result["total_lines"]

    def test_missing_execution_id(self):
        """Test error for missing execution ID."""
        makefile_mcp, _eid = self._setup()
        result = makefile_mcp.get_output(99999)
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_invalid_stream(self):
        """Test error for invalid stream name."""
        makefile_mcp, eid = self._setup()
        result = makefile_mcp.get_output(eid, stream="invalid")
        assert result["status"] == "error"
        assert "Invalid stream" in result["message"]


class TestSearchOutput:
    """Test the search_output MCP tool."""

    def _setup(self):
        """Set up a module with cached output."""
        with patch("sys.argv", ["makefile_mcp.py"]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]
            import makefile_mcp

            output = "Starting build\nCompiling main.c\nWARNING: deprecated function\nCompiling util.c\nLinking...\nWARNING: unused variable\nBuild complete\n"
            entry = makefile_mcp.output_cache.add("build", "make build", output, "", 0)
            return makefile_mcp, entry.execution_id

    def test_basic_search(self):
        """Test basic substring search."""
        makefile_mcp, eid = self._setup()
        result = makefile_mcp.search_output(eid, "WARNING")

        assert result["status"] == "success"
        assert result["total_matches"] == 2
        assert result["matches"][0]["line_number"] == 2
        assert "deprecated" in result["matches"][0]["text"]
        assert result["matches"][1]["line_number"] == 5

    def test_case_insensitive(self):
        """Test that search is case-insensitive."""
        makefile_mcp, eid = self._setup()
        result = makefile_mcp.search_output(eid, "warning")
        assert result["total_matches"] == 2

    def test_context_lines(self):
        """Test that context lines are included."""
        makefile_mcp, eid = self._setup()
        result = makefile_mcp.search_output(eid, "WARNING", context_lines=1)
        match = result["matches"][0]
        context = match["context"]

        # Should have line before, the match, and line after
        assert len(context) == 3
        assert context[0]["is_match"] is False
        assert context[1]["is_match"] is True
        assert context[2]["is_match"] is False

    def test_no_matches(self):
        """Test search with no results."""
        makefile_mcp, eid = self._setup()
        result = makefile_mcp.search_output(eid, "NONEXISTENT_PATTERN")
        assert result["status"] == "success"
        assert result["total_matches"] == 0
        assert result["matches"] == []

    def test_missing_execution_id(self):
        """Test error for missing execution ID."""
        makefile_mcp, _eid = self._setup()
        result = makefile_mcp.search_output(99999, "test")
        assert result["status"] == "error"

    def test_search_stderr(self):
        """Test searching stderr stream."""
        with patch("sys.argv", ["makefile_mcp.py"]):
            if "makefile_mcp" in sys.modules:
                del sys.modules["makefile_mcp"]
            import makefile_mcp

            entry = makefile_mcp.output_cache.add("t", "make t", "", "error: foo\nwarning: bar\n", 1)
            result = makefile_mcp.search_output(entry.execution_id, "error", stream="stderr")
            assert result["total_matches"] == 1
            assert result["matches"][0]["line_number"] == 0

    def test_line_numbers_for_followup(self):
        """Test that match line numbers can be used with get_output."""
        makefile_mcp, eid = self._setup()
        search_result = makefile_mcp.search_output(eid, "WARNING")

        # Use first match line number with get_output
        line_num = search_result["matches"][0]["line_number"]
        get_result = makefile_mcp.get_output(eid, start_line=line_num, end_line=line_num + 1)
        assert "WARNING" in get_result["content"]


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
