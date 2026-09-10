"""Warning severity, and the one warning v0 defines.

Intent: a warning reports something the specification states as a *condition*
rather than as a rule, so it must not behave like an error anywhere -- not in
`ok`, not in `codes`, not in the CLI's exit code. The conformance suite pins
which documents draw the warning; these pin what a warning *is*, which is a
public API change and is otherwise only implied by fixtures.

The warning itself is `unbounded_array_output` (spec 1.1): an atomic process's
`Array` output port whose length nothing relates to its inputs.
"""

from __future__ import annotations

from pathlib import Path

from ofplang.validate import validate
from ofplang.validate.cli import EXIT_OK, main
from ofplang.validate.errors import ERROR_CODES, UNBOUNDED_ARRAY_OUTPUT, WARNING_CODES
from ofplang.validate.validator import ERROR, WARNING

CASES = Path(__file__).parent / "conformance" / "cases"
# `create` on an `Array<Cup>` port: valid v0, but the count is reachable from
# nothing in the document.
UNBOUNDED = str(CASES / "objects" / "valid_create_array_port.yaml")
# A fold carrying an Object-bearing collection: every length traces to a
# traversal, so the condition of 1.1 holds and nothing is reported.
BOUNDED = str(CASES / "nodes" / "valid_collection_carry.yaml")


def test_warning_vocabulary_is_disjoint_from_errors() -> None:
    # A fixture names a code in exactly one of the two sets, so the runner can
    # tell which list it belongs in without a severity of its own.
    assert UNBOUNDED_ARRAY_OUTPUT in WARNING_CODES
    assert not (WARNING_CODES & ERROR_CODES)


def test_warning_does_not_make_a_document_invalid() -> None:
    result = validate(UNBOUNDED)
    assert result.ok
    assert result.codes == []  # errors only
    assert result.warning_codes == [UNBOUNDED_ARRAY_OUTPUT]
    assert [d.severity for d in result.warnings] == [WARNING]


def test_warning_carries_a_position_and_the_port_path() -> None:
    (diag,) = validate(UNBOUNDED).warnings
    assert diag.path == "processes.cups_create.outputs.cups"
    assert diag.line is not None and diag.col is not None


def test_derivable_length_draws_no_warning() -> None:
    result = validate(BOUNDED)
    assert result.ok
    assert result.warnings == []


def test_diagnostics_split_by_severity() -> None:
    # An invalid document: its findings are errors, and the two views agree.
    result = validate(str(CASES / "types" / "unknown_type.yaml"))
    assert not result.ok
    assert result.warnings == []
    assert result.errors == result.diagnostics
    assert all(d.severity == ERROR for d in result.errors)


def test_cli_reports_a_warning_without_failing(capsys) -> None:
    assert main(["--no-color", UNBOUNDED]) == EXIT_OK
    out = capsys.readouterr().out
    assert "warning unbounded_array_output" in out
    assert "all valid" in out and "1 warning" in out


def test_cli_json_carries_severity(capsys) -> None:
    import json

    assert main(["--format", "json", UNBOUNDED]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    (diag,) = payload["results"][0]["diagnostics"]
    assert diag["severity"] == WARNING
    assert diag["code"] == UNBOUNDED_ARRAY_OUTPUT
