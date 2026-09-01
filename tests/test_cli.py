"""Tests for the ofplang CLI.

Intent: exercise the CLI contract (exit codes and rendered output) end-to-end by
driving ``cli.main`` with argv, reusing real conformance fixtures as inputs so
the CLI is tested against genuine valid/invalid documents rather than mocks.
Stdout is captured (not a TTY), so output is plain text with no ANSI codes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ofplang.validate.cli import EXIT_INVALID, EXIT_OK, EXIT_USAGE, main
from ofplang.validate.version import SPEC_VERSION

CASES = Path(__file__).parent / "conformance" / "cases"
VALID = str(CASES / "shape" / "valid_minimal.yaml")
INVALID = str(CASES / "types" / "unknown_type.yaml")  # yields unknown_type
X_KEY = str(CASES / "extensions" / "x_key_tolerant.yaml")  # x- key: strict-invalid


def test_valid_file_exits_ok(capsys) -> None:
    assert main([VALID]) == EXIT_OK
    assert "all valid" in capsys.readouterr().out


def test_invalid_file_exits_one_and_names_code(capsys) -> None:
    assert main([INVALID]) == EXIT_INVALID
    out = capsys.readouterr().out
    assert "unknown_type" in out


def test_json_format_is_structured(capsys) -> None:
    assert main(["--format", "json", INVALID]) == EXIT_INVALID
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    codes = [d["code"] for r in payload["results"] for d in r["diagnostics"]]
    assert "unknown_type" in codes


def test_mode_changes_outcome(capsys) -> None:
    # An x- key is an error in strict mode but accepted in extension-tolerant.
    assert main([X_KEY]) == EXIT_INVALID
    capsys.readouterr()
    assert main(["--mode", "extension-tolerant", X_KEY]) == EXIT_OK


def test_multiple_files_aggregate_worst_exit(capsys) -> None:
    assert main([VALID, INVALID]) == EXIT_INVALID
    out = capsys.readouterr().out
    # Per-file headers appear when validating more than one file.
    assert INVALID in out or "unknown_type" in out


def test_missing_file_is_usage_error(capsys) -> None:
    assert main([str(CASES / "does_not_exist.yaml")]) == EXIT_USAGE
    assert "cannot open" in capsys.readouterr().err


def test_quiet_suppresses_diagnostic_lines(capsys) -> None:
    assert main(["--quiet", INVALID]) == EXIT_INVALID
    out = capsys.readouterr().out
    # Summary is still shown, but the individual diagnostic line is not.
    assert "error" in out  # summary counts errors
    assert "unknown_type" not in out


def test_version_reports_both_the_package_and_the_specification(capsys):
    """`--version` answers two different questions (spec 2.1): which package this
    is, and which revision of the specification it implements. A user cannot
    infer the second from the first."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "ofp-validate" in out
    assert f"specification {SPEC_VERSION}" in out
