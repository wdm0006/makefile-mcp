# Mutation testing waiver register

Campaign: `mutmut==3.7.0` (config: `[tool.mutmut]`, `source_paths = ["makefile_mcp.py"]`),
pytest-timeout 60s, run directly via `python -m mutmut run` against the full test suite.

| Result | Count |
| --- | --- |
| Total mutants | 945 |
| Killed | 877 |
| Killed by timeout (mutant hung a test) | 5 |
| Survived (waived below) | 63 |

**Detection rate: 93.3%** (877 + 5 of 945). Every survivor is classified below with a
rationale. Re-run the campaign after any source change; new survivors must be killed by a
named committed test or added here with a classification.

`mutant` identifiers use mutmut's mangled names (`x_<function>__mutmut_<n>` for module
functions, `xǁ<Class>ǁ<method>__mutmut_<n>` for methods). Mutant numbers shift whenever the
source changes — re-derive them with `python -m mutmut results` rather than editing counts.

## Survivors — inert (no observable behavior change): 47

All in `x_parse_cli_args`: mutations of argparse `description=...` and `help=...` strings
(marker wraps, case flips, `None`, keyword removal) and `type=str` → `type=None` /
keyword removal on string-typed arguments. Argparse's default type is `str`, and none of
the mutated arguments feed anything but help rendering; every default **value** is pinned
by `tests/test_mutation_contract.py::TestCliDefaults::test_cli_defaults_are_pinned`.

Mutants: `parse_cli_args__mutmut_2, 3, 4, 5, 7, 9, 11, 13, 19, 20, 21, 23, 24, 26, 27, 30,
31, 32, 34, 35, 37, 38, 41, 42, 43, 45, 46, 48, 49, 52, 53, 54, 58, 62, 66, 67, 68, 72, 76,
80, 81, 82, 86, 90, 94, 95, 96`.

## Survivors — equivalent (mutant is semantically identical to the original): 13

| Mutants | Mutation | Why equivalent |
| --- | --- | --- |
| `_parse__mutmut_3, 5, 6` | `open(..., "r", encoding="utf-8")` → mode/encoding dropped | `open` defaults are mode `"r"` and locale-preferred encoding, which is UTF-8 here; the non-UTF-8 fallback path and its decoded high-byte text are pinned by `test_non_utf8_makefile_falls_back_to_latin1` |
| `_parse__mutmut_10, 21` | `"utf-8"` → `"UTF-8"`, `"latin-1"` → `"LATIN-1"` | Python codec lookup is case-insensitive; identical codecs |
| `_parse__mutmut_26, 42, 61, 69` | `current_comment = ""` → `= None` | Every read is guarded by `if current_comment:`; `None` and `""` are both falsy |
| `_as_text__mutmut_5, 9` | `decode("utf-8", errors="replace")` → `decode(errors="replace")` / `"UTF-8"` | Same codec (locale default is UTF-8); `errors="replace"` preserved |
| `validate_additional_args__mutmut_53` | `consumed_separated = False` → `= None` | Only read as a truthiness flag (`2 if consumed_separated else 1`) |

## Survivors — test-cost (behavior pinned by tests mutmut cannot attribute): 3

| Mutants | Mutation | Why waived |
| --- | --- | --- |
| `x_main__mutmut_7` | Banner line `"Starting Makefile MCP server"` → `"XX...XX"` | Pinned by `TestStartupBanner::test_banner_lines`, which runs the server as a **subprocess** and asserts `stderr.startswith("Starting Makefile MCP server\n")` |
| `x_main__mutmut_23` | `', '.join(targets)` → `'XX, XX'.join(...)` in the banner | Pinned by `test_banner_lines` asserting the exact `  Available targets: build, lint` line |
| `x_main__mutmut_35` | `', '.join(exclude)` → `'XX, XX'.join(...)` in the banner | Pinned by `test_banner_lines` asserting the exclude-filter line's exact comma-split content |

`main()` only executes in the subprocess those tests spawn; mutmut's per-function test
selection (function→test stats) cannot attribute subprocess coverage, so these mutants are
reported as survivors even though the suite fails under them. Killing them would require
in-process tests of `main()`, which cannot run (it blocks on `mcp.run()`).

## Killed by timeout — detected, not waived: 5

`validate_additional_args__mutmut_21, 33, 38, 39, 69`: token-index arithmetic mutated
(`i += 1` → `i = 1` / `i -= 1`, `j += 1` → `j = 1`) makes the scanner loop forever on
multi-token inputs. Killed by `TestAllowlistScanner::test_accepted_shapes` cases
(`["A=1", "B=2", "C=3"]`, `["--silent", "--trace", "--debug"]`,
`["--debug", "--output-sync", "--debug"]`, `["-ssk"]`, `["-kss"]`) hanging until
pytest-timeout fires.

## Survivors killed during this PR's campaign (for the record)

Three rounds of contract tests (`tests/test_mutation_contract.py`,
`TestToolResponseContract`, `TestParserContract`, plus assertions in
`tests/test_makefile_mcp.py`) reduced survivors from 237 to 63, killing, among others:
CLI default values, response key sets and exact values on success/error/timeout paths
(`target`, `exit_code`, `makefile_path`, `working_directory`, filtered-target key sets),
cache-entry field contracts (`command`, `exit_code`, `stdout`, `stderr` read-back by
execution id, timestamp contract), allowlist token-consumption accounting
(`i += 2` skips, `continue` → `break` short-circuits, `--debug`/`--output-sync` followed
by an invalid token, short-cluster walks), pattern-rule exclusion, latin-1 fallback
decoding, and the argparse `--timeout` rejection message.
