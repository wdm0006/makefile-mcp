#!/usr/bin/env python3
"""Mutation-survivor contract tests.

Each test pins behavior that a mutmut campaign showed the original suite left
uncovered: CLI defaults, parser corner semantics, the additional_args option
scanner's token consumption, tool response key sets, initialization failure
modes, and startup banner text. docs/mutation-waivers.md maps surviving
mutants to these tests or records a waiver classification.
"""

import os
import pathlib
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import makefile_mcp  # noqa: E402

THREE_TARGET_MAKEFILE = """# Build the project
build:
\t@echo "Building project..."

# Run tests
test:
\t@echo "Running tests..."

# Clean up
clean:
\t@echo "Cleaning up..."

.PHONY: build test clean
"""


@pytest.fixture
def server_factory(tmp_path):
    """Create fresh servers through explicit initialization (same contract as the main suite)."""

    def _create(makefile_text=THREE_TARGET_MAKEFILE, extra_args=()):
        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text(makefile_text)
        return makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile_path), *extra_args])

    return _create


def write_makefile(tmp_path, text, name="Makefile"):
    path = tmp_path / name
    path.write_text(text)
    return path


class TestCliDefaults:
    """Pin every CLI default and relative-path resolution."""

    def test_cli_defaults_are_pinned(self):
        args = makefile_mcp.parse_cli_args([])
        assert args.makefile == "Makefile"
        assert args.max_cached_executions == 20
        assert args.tail_lines == 50
        assert args.timeout == 300

    def test_relative_makefile_resolves_against_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = makefile_mcp.build_config(makefile_mcp.parse_cli_args(["--makefile", "Makefile"]))
        assert config.makefile_path == tmp_path / "Makefile"
        assert config.working_dir == tmp_path.resolve()

    def test_output_cache_default_capacity_is_20(self):
        cache = makefile_mcp.OutputCache()
        for i in range(1, 21):
            cache.add(target="t", command="c", stdout=f"out {i}", stderr="", exit_code=0)
        assert len(cache) == 20
        first = cache.add(target="t", command="c", stdout="out 21", stderr="", exit_code=0)
        assert first.execution_id == 21
        assert len(cache) == 20
        assert cache.get(1) is None

    def test_positive_int_rejection_message(self, capsys):
        with pytest.raises(SystemExit):
            makefile_mcp.parse_cli_args(["--timeout", "0"])
        assert "must be a positive integer" in capsys.readouterr().err


class TestParserContract:
    """Pin MakefileParser comment/target semantics at the surviving corners."""

    def test_comment_and_target_corners(self, tmp_path):
        makefile = write_makefile(
            tmp_path,
            "#nospace comment\nnospace:\n\ttrue\n"
            "# real description\n### divider\n#\nafter_divider:\n\ttrue\n"
            "BUILD:\n\ttrue\n"
            "%.c:\n\ttrue\n"
            "  indented_fake:\n\ttrue\n"
            "# before blank\n\n\nafter_blank:\n\ttrue\n"
            "# first target only\nfirst:\n\ttrue\nsecond:\n\ttrue\n"
            "# var desc\nVAR = 1\nvar_target:\n\ttrue\n"
            "# spaced desc\n   spaced_var = 1\nspaced_target:\n\ttrue\n"
            "# orphan desc\n\ttrue\norphan_target:\n\ttrue\n",
        )
        targets = makefile_mcp.MakefileParser(makefile).get_targets()
        assert targets["nospace"] == "nospace comment"
        assert targets["after_divider"] == "real description"
        assert targets["BUILD"] == "Execute the 'BUILD' target"
        assert targets["after_blank"] == "Execute the 'after_blank' target"
        assert targets["second"] == "Execute the 'second' target"
        assert targets["var_target"] == "Execute the 'var_target' target"
        assert targets["spaced_target"] == "spaced desc"
        assert targets["orphan_target"] == "orphan desc"
        assert "indented_fake" not in targets
        assert not any("%" in name for name in targets)

    def test_non_utf8_makefile_falls_back_to_latin1(self, tmp_path):
        makefile = tmp_path / "Makefile"
        makefile.write_bytes(b"# caf\xe9 build step\nbuild:\n\ttrue\n")
        targets = makefile_mcp.MakefileParser(makefile).get_targets()
        assert "build" in targets
        assert targets["build"] == "café build step"

    def test_get_makefile_targets_applies_filters(self, tmp_path, capsys):
        makefile = write_makefile(tmp_path, "# Build\nbuild:\n\ttrue\n# Test\ntest:\n\ttrue\n# Clean\nclean:\n\ttrue\n")
        include_config = makefile_mcp.build_config(
            makefile_mcp.parse_cli_args(["--makefile", str(makefile), "--include", "build"])
        )
        assert makefile_mcp.get_makefile_targets(include_config) == {"build": "Build"}
        exclude_config = makefile_mcp.build_config(
            makefile_mcp.parse_cli_args(["--makefile", str(makefile), "--exclude", "test"])
        )
        assert makefile_mcp.get_makefile_targets(exclude_config) == {"build": "Build", "clean": "Clean"}
        assert capsys.readouterr().err == ""

    def test_get_makefile_targets_missing_file_returns_empty(self, tmp_path, capsys):
        config = makefile_mcp.build_config(makefile_mcp.parse_cli_args(["--makefile", str(tmp_path / "absent")]))
        assert makefile_mcp.get_makefile_targets(config) == {}
        assert capsys.readouterr().err == ""

    def test_get_makefile_targets_warning_text(self, tmp_path, capsys):
        makefile = write_makefile(tmp_path, "# only comments, no targets\n")
        config = makefile_mcp.build_config(makefile_mcp.parse_cli_args(["--makefile", str(makefile)]))
        assert makefile_mcp.get_makefile_targets(config) == {}
        captured = capsys.readouterr()
        assert captured.err == "Warning: No targets found or all targets filtered out\n"
        assert captured.out == ""


class TestAllowlistScanner:
    """Pin validate_additional_args token consumption and exact rejections."""

    @pytest.mark.parametrize(
        "tokens",
        [
            ["-k"],
            ["-j4"],
            ["-j", "4"],
            ["-l", "2.5"],
            ["-Oline"],
            ["--jobs=4"],
            ["--jobs", "4"],
            ["--no-print-directory"],
            ["--output-sync"],
            ["--debug"],
            ["--silent", "--trace"],
            ["-ks"],
            ["X=1"],
            ["X:=1"],
            ["X+=1"],
            ["X?=1"],
            ["A=1", "B=2"],
            ["-j", "2", "-k"],
            ["-j", "4", "-k"],
            ["--silent", "--jobs", "4"],
            ["-O", "-k"],
            ["--jobs", "4", "-k"],
        ],
    )
    def test_accepted_shapes(self, tokens):
        assert makefile_mcp.validate_additional_args(tokens) is None

    @pytest.mark.parametrize(
        ("tokens", "message"),
        [
            (["--"], "'--' is not allowed; following tokens would be treated as targets"),
            (["-"], "'-' is not an allowed option"),
            (
                ["X=$(shell echo pwned)"],
                "'X=$(shell echo pwned)' contains a make expansion reference ('$(' or '${'); "
                "assignment values must be literal",
            ),
            (["X!=pwned"], "'X!=pwned' uses make's '!=' shell-assignment operator; its value would be run by a shell"),
            (["-z"], "option '-z' is not in the allowed make option set"),
            (["-skzk"], "option '-z' is not in the allowed make option set"),
            (["--frobnicate"], "option '--frobnicate' is not in the allowed make option set"),
            (["--silent=1"], "option '--silent' does not take a value"),
            (["clean"], "'clean' is not an allowed variable assignment or option (it would select another target)"),
            (["A=1", "-z"], "option '-z' is not in the allowed make option set"),
            (["A=1", "B=2", "-z"], "option '-z' is not in the allowed make option set"),
            (["--silent", "-z"], "option '-z' is not in the allowed make option set"),
            (["--jobs=4", "-z"], "option '-z' is not in the allowed make option set"),
            (["--jobs", "4", "-z"], "option '-z' is not in the allowed make option set"),
            (["-j", "4", "-z"], "option '-z' is not in the allowed make option set"),
            (["-Oj", "2"], "'2' is not an allowed variable assignment or option (it would select another target)"),
            (["-Oj", "-z"], "option '-z' is not in the allowed make option set"),
        ],
    )
    def test_rejected_shapes(self, tokens, message):
        assert makefile_mcp.validate_additional_args(tokens) == message


class TestToolResponseContract:
    """Pin response key sets and values for every tool."""

    def test_make_tool_response_contract(self, server_factory):
        server = server_factory()
        tool = server.create_make_tool("build", "Build the project")
        result = tool(additional_args="--no-print-directory")
        assert set(result) == {
            "target",
            "command",
            "working_directory",
            "exit_code",
            "execution_id",
            "stdout_tail",
            "stderr_tail",
            "stdout_total_lines",
            "stdout_total_chars",
            "stderr_total_lines",
            "stderr_total_chars",
            "status",
            "message",
        }
        assert result["target"] == "build"
        assert (
            result["command"]
            == f"make -C {server.config.working_dir} -f {server.config.makefile_path} build --no-print-directory"
        )
        assert result["working_directory"] == str(server.config.working_dir)
        assert result["exit_code"] == 0
        assert result["status"] == "success"
        assert result["message"] == "Successfully executed target 'build'"
        assert result["stdout_total_lines"] == 1
        assert result["stdout_tail"] == "Building project...\n"

    def test_make_tool_argument_error_contract(self, server_factory):
        server = server_factory()
        tool = server.create_make_tool("build", "Build the project")
        shlex_error = tool(additional_args="'unterminated")
        assert shlex_error["target"] == "build"
        assert shlex_error["status"] == "error"
        assert shlex_error["message"] == "Invalid additional_args for target 'build': No closing quotation"
        rejected = tool(additional_args="clean")
        assert rejected["target"] == "build"
        assert rejected["message"] == (
            "Rejected additional_args for target 'build': "
            "'clean' is not an allowed variable assignment or option (it would select another target)"
        )

    def test_make_tool_os_error_message(self, server_factory):
        server = server_factory()
        tool = server.create_make_tool("build", "Build the project")
        with patch("makefile_mcp.subprocess.run", side_effect=OSError("boom")):
            result = tool()
        assert result["status"] == "error"
        assert result["message"] == "Failed to execute target 'build': boom"

    def test_get_output_contract_and_defaults(self, tmp_path):
        lines = "\n".join(f"\t@echo line {i}" for i in range(1, 151))
        makefile = write_makefile(tmp_path, f"# Emit\nemit:\n{lines}\n")
        server = makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile), "--tail-lines", "5"])
        tool = server.create_make_tool("emit", "Emit lines")
        first = tool(additional_args="--no-print-directory")
        page = server.get_output(first["execution_id"])
        assert set(page) == {
            "status",
            "execution_id",
            "target",
            "stream",
            "start_line",
            "end_line",
            "total_lines",
            "content",
        }
        assert page["start_line"] == 0
        assert page["end_line"] == 100
        assert page["total_lines"] == 150
        assert len(page["content"].splitlines()) == 100
        assert page["target"] == "emit"
        assert page["stream"] == "stdout"

    def test_search_output_contract(self, server_factory):
        server = server_factory()
        tool = server.create_make_tool("build", "Build the project")
        first = tool(additional_args="--no-print-directory")
        found = server.search_output(first["execution_id"], "Building")
        assert set(found) == {
            "status",
            "execution_id",
            "target",
            "stream",
            "pattern",
            "total_lines",
            "total_matches",
            "returned_matches",
            "max_results",
            "truncated",
            "matches",
        }
        assert found["total_matches"] == 1
        assert found["returned_matches"] == 1
        match = found["matches"][0]
        assert match["line_number"] == 0
        assert match["text"] == "Building project..."
        assert len(match["context"]) == 1

    def test_search_output_default_context_is_three_lines(self, tmp_path):
        lines = "\n".join(f"\t@echo line {i}" for i in range(1, 151))
        makefile = write_makefile(tmp_path, f"# Emit\nemit:\n{lines}\n")
        server = makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile), "--tail-lines", "5"])
        tool = server.create_make_tool("emit", "Emit lines")
        first = tool(additional_args="--no-print-directory")
        found = server.search_output(first["execution_id"], "line 40")
        match = found["matches"][0]
        assert match["line_number"] == 39
        assert [c["line_number"] for c in match["context"]] == list(range(36, 43))

    def test_search_output_rejections_are_exact(self, server_factory):
        server = server_factory()
        tool = server.create_make_tool("build", "Build the project")
        first = tool(additional_args="--no-print-directory")
        assert server.search_output(first["execution_id"], "") == {
            "status": "error",
            "message": "Search pattern must not be empty. Provide a literal substring to search for.",
        }
        assert server.search_output(first["execution_id"], "Building", max_results=0) == {
            "status": "error",
            "message": "Invalid max_results 0. Must be 1 or greater.",
        }
        assert server.search_output(first["execution_id"], "Building", max_results=1)["returned_matches"] == 1

    def test_search_output_truncation_note_is_exact(self, tmp_path):
        makefile = write_makefile(tmp_path, "# Emit\nemit:\n\t@echo line 1\n\t@echo target 1\n\t@echo target 2\n")
        server = makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile)])
        tool = server.create_make_tool("emit", "Emit")
        first = tool()
        found = server.search_output(first["execution_id"], "target", max_results=1)
        assert found["truncated"] is True
        assert found["truncation_note"] == (
            f"Returned the first 1 of 2 matches. Narrow the pattern, raise max_results, "
            f"or use get_output(execution_id={first['execution_id']}) around the returned line numbers."
        )

    def test_list_available_targets_contract(self, server_factory):
        server = server_factory(extra_args=("--include", "build", "--exclude", "test"))
        result = server.list_available_targets()
        assert set(result) == {
            "makefile_path",
            "working_directory",
            "total_targets_in_makefile",
            "available_targets",
            "targets",
            "include_filter",
            "exclude_filter",
        }
        assert result["total_targets_in_makefile"] == 3
        assert result["available_targets"] == 1
        assert result["include_filter"] == ["build"]
        assert result["exclude_filter"] == ["test"]
        assert result["targets"] == [{"name": "build", "description": "Build the project", "tool_name": "make_build"}]

    def test_list_available_targets_counts_zero_when_makefile_deleted(self, server_factory):
        server = server_factory()
        server.config.makefile_path.unlink()
        result = server.list_available_targets()
        assert result["total_targets_in_makefile"] == 0
        assert result["available_targets"] == 3

    def test_get_makefile_info_contract(self, server_factory):
        server = server_factory(extra_args=("--include", "build,test", "--exclude", "test"))
        result = server.get_makefile_info()
        assert set(result) == {
            "makefile_path",
            "makefile_exists",
            "working_directory",
            "all_targets",
            "filtered_targets",
            "filters",
        }
        assert result["makefile_exists"] is True
        assert result["all_targets"]["count"] == 3
        assert result["filtered_targets"]["count"] == 1
        # include/exclude are sets at config level, so list order is not deterministic
        assert sorted(result["filters"]["include"]) == ["build", "test"]
        assert sorted(result["filters"]["exclude"]) == ["test"]
        assert result["all_targets"]["targets"][0] == {"name": "build", "description": "Build the project"}

    def test_server_name_and_tool_docstring(self, tmp_path):
        makefile = write_makefile(tmp_path, "# Build\nbuild:\n\ttrue\n")
        config = makefile_mcp.build_config(makefile_mcp.parse_cli_args(["--makefile", str(makefile)]))
        server = makefile_mcp.MakefileServer(config)
        assert server.mcp_server.name == "MakefileMCP"
        registered = server.register_make_tools()
        assert registered[0][0] == "build"
        doc = registered[0][1].__doc__
        assert doc == f"Build.\n\nExecutes: make -C {config.working_dir} -f {config.makefile_path} build"

    def test_validate_tool_names_collision_message(self):
        with pytest.raises(ValueError) as excinfo:
            makefile_mcp.validate_tool_names({"a-b": "First", "a.b": "Second", "c-d": "Third", "c.d": "Fourth"})
        assert str(excinfo.value) == (
            "Conflicting make targets generate the same MCP tool name: make_a_b: a-b, a.b; make_c_d: c-d, c.d"
        )


class TestOutputBounding:
    """Pin tail bounding and the exact truncation note."""

    def test_tail_lines_boundary_at_exactly_n(self):
        text = "a\nb\nc\n"
        assert makefile_mcp._tail_lines(text, 3) == (text, False)
        tail, truncated = makefile_mcp._tail_lines(text, 2)
        assert tail == "b\nc\n"
        assert truncated is True

    def test_bounded_output_fields_truncation_note_is_exact(self):
        fields = makefile_mcp._bounded_output_fields("a\nb\nc\n", "", 2, 7)
        assert fields["truncation_note"] == (
            "Output was truncated to the last 2 lines. "
            "Use get_output(execution_id=7) to paginate or search_output() to search the full output."
        )


class TestInitializationFailures:
    """Pin exit code 1 and the exact stderr message for each failure mode."""

    def test_missing_makefile(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            makefile_mcp.initialize_makefile_mcp(["--makefile", str(tmp_path / "missing")])
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert captured.err == f"Error: Makefile not found at {tmp_path / 'missing'}\n"
        assert captured.out == ""

    def test_missing_working_directory(self, tmp_path, capsys):
        makefile = write_makefile(tmp_path, "# Build\nbuild:\n\ttrue\n")
        with pytest.raises(SystemExit) as excinfo:
            makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile), "--working-dir", str(tmp_path / "nope")])
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert captured.err == f"Error: Working directory not found: {(tmp_path / 'nope').resolve()}\n"

    def test_no_targets(self, tmp_path, capsys):
        makefile = write_makefile(tmp_path, "# only comments, no targets\n")
        with pytest.raises(SystemExit) as excinfo:
            makefile_mcp.initialize_makefile_mcp(["--makefile", str(makefile)])
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert captured.err == (
            "Warning: No targets found or all targets filtered out\n"
            "Error: No make targets available to expose as tools\n"
        )


class TestStartupBanner:
    """Pin the stderr banner main() prints before serving."""

    def test_banner_lines(self, tmp_path):
        makefile = write_makefile(
            tmp_path,
            "# Build\nbuild:\n\ttrue\n# Test\ntest:\n\ttrue\n# Clean\nclean:\n\ttrue\n# Lint\nlint:\n\ttrue\n",
        )
        script_dir = pathlib.Path(makefile_mcp.__file__).parent
        proc = subprocess.run(
            [
                sys.executable,
                str(script_dir / "makefile_mcp.py"),
                "--makefile",
                str(makefile),
                "--exclude",
                "test,clean",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "Starting Makefile MCP server" in proc.stderr
        assert f"  Makefile: {makefile}" in proc.stderr
        assert "  Available targets: build, lint" in proc.stderr
        exclude_line = next(line for line in proc.stderr.splitlines() if line.startswith("  Exclude filter: "))
        # exclude_targets is a set, so the join order is not deterministic
        assert sorted(exclude_line.removeprefix("  Exclude filter: ").split(", ")) == ["clean", "test"]
        assert "Exclude filter" not in proc.stdout
        assert "Starting Makefile MCP server" not in proc.stdout
