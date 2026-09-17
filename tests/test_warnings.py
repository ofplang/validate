"""Warning severity, and the one warning v0 defines.

Intent: a warning reports something the specification states as a *condition*
rather than as a rule, so it must not behave like an error anywhere -- not in
`ok`, not in `codes`, not in the CLI's exit code. The conformance suite pins
which documents draw the warning; these pin what a warning *is*, which is a
public API change and is otherwise only implied by fixtures.

The warning itself is `array_output_length_not_derivable` (spec 1.1): an atomic
process's `Array` output port whose length nothing relates to its inputs. The
second half of this module pins which *contract* shapes are read as bounding
such a port -- the boundary between them is a judgement about how much algebra
belongs in this reasoning, so it is worth stating case by case rather than
leaving to whatever the implementation happens to accept.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ofplang.validate import ERROR, WARNING, validate
from ofplang.validate.cli import EXIT_OK, main
from ofplang.validate.errors import ARRAY_OUTPUT_LENGTH_NOT_DERIVABLE, ERROR_CODES, WARNING_CODES

CASES = Path(__file__).parent / "conformance" / "cases"
# `create` on an `Array<Cup>` port: valid v0, but the count is reachable from
# nothing in the document.
UNBOUNDED = str(CASES / "objects" / "valid_create_array_port.yaml")
# A fold carrying an Object-bearing collection: every length traces to a
# traversal, so the condition of 1.1 holds and nothing is reported.
BOUNDED = str(CASES / "nodes" / "valid_collection_carry.yaml")


def test_severity_constants_are_public() -> None:
    # A caller that reads `Diagnostic.severity` needs the two values to compare it
    # against, so they belong in the package namespace rather than in `validator`.
    # `ofplang-schedule` exports its own pair this way and the two read alike.
    import ofplang.validate as api

    assert (api.ERROR, api.WARNING) == ("error", "warning")
    assert {"ERROR", "WARNING"} <= set(api.__all__)


def test_warning_vocabulary_is_disjoint_from_errors() -> None:
    # A fixture names a code in exactly one of the two sets, so the runner can
    # tell which list it belongs in without a severity of its own.
    assert ARRAY_OUTPUT_LENGTH_NOT_DERIVABLE in WARNING_CODES
    assert not (WARNING_CODES & ERROR_CODES)


def test_warning_does_not_make_a_document_invalid() -> None:
    result = validate(UNBOUNDED)
    assert result.ok
    assert result.codes == []  # errors only
    assert result.warning_codes == [ARRAY_OUTPUT_LENGTH_NOT_DERIVABLE]
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
    assert "warning array_output_length_not_derivable" in out
    assert "all valid" in out and "1 warning" in out


def test_cli_json_carries_severity(capsys) -> None:
    import json

    assert main(["--format", "json", UNBOUNDED]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    (diag,) = payload["results"][0]["diagnostics"]
    assert diag["severity"] == WARNING
    assert diag["code"] == ARRAY_OUTPUT_LENGTH_NOT_DERIVABLE


# --- Which contract shapes discharge the condition (spec 1.1, clause two) ---
# One process with three inputs to bound against: an Array (whose length the
# bound computation reaches by recursion), a `run` phase scalar (known once the
# arguments are given), and a `data` phase scalar (known to neither).
_DOC = """
spec_version: "0.2"
types:
  Plate96: {{ domain: object }}
processes:
  measure_all:
    kind: atomic
    inputs:
      plates: {{ type: "Array<Plate96>", phase: data }}
      cap:    {{ type: Int, phase: run }}
      later:  {{ type: Int, phase: data }}
    outputs:
      plates:   {{ type: "Array<Plate96>", phase: data }}
      readings: {{ type: "Array<Float>", phase: data }}
    objects:
      map: {{ outputs.plates: inputs.plates }}
{contract}
  main:
    kind: composite
    inputs:
      plates: {{ type: "Array<Plate96>", phase: data }}
      cap:    {{ type: Int, phase: run }}
      later:  {{ type: Int, phase: data }}
    outputs:
      plates:   {{ type: "Array<Plate96>", phase: data }}
      readings: {{ type: "Array<Float>", phase: data }}
    body:
      nodes:
        - id: m
          process: measure_all
          state: {{ plates: {{ from: inputs.plates }} }}
          bind:  {{ cap: {{ from: inputs.cap }}, later: {{ from: inputs.later }} }}
      returns:
        plates:   {{ from: m.plates }}
        readings: {{ from: m.readings }}
entry: main
"""

_LEN = "outputs.readings.view.length"

# Read as bounding `readings`: the reference is one whole side of a comparison
# that caps it, and the other side is built from things the bound computation
# reaches.
_BOUNDING = [
    f"{_LEN} == inputs.plates.view.length",          # an input Array's length
    f"{_LEN} <= inputs.plates.view.length",
    f"{_LEN} < inputs.cap.view",                     # a run-phase scalar
    f"inputs.plates.view.length == {_LEN}",          # mirrored
    f"inputs.cap.view >= {_LEN}",                    # mirrored, and `>=` caps it there
    f"0 < {_LEN} and {_LEN} <= inputs.cap.view",     # a conjunct bounds the whole
    f"{_LEN} <= inputs.plates.view.length * 2 + 1",  # arithmetic over reachable leaves
]

# Not read as bounding it, each for its own reason.
_NOT_BOUNDING = [
    f"{_LEN} >= inputs.cap.view",                    # a lower bound leaves it free above
    f"{_LEN} != inputs.cap.view",
    f"{_LEN} <= inputs.cap.view or inputs.cap.view > 0",  # `or`: neither side is assured
    f"{_LEN} <= inputs.later.view",                  # data phase: not known at run start
    f"{_LEN} == outputs.plates.view.length",         # a chain through another output
    f"{_LEN} * 2 == inputs.plates.view.length",      # would have to be solved for
]


def _warnings_for(contract: str | None, tmp_path: Path) -> list[str]:
    block = (
        ""
        if contract is None
        else f'    contracts:\n      ensures:\n        - expr: "{contract}"\n'
    )
    doc = tmp_path / "doc.yaml"
    doc.write_text(_DOC.format(contract=block), encoding="utf-8")
    result = validate(str(doc))
    assert result.codes == [], f"document should be valid, got {result.codes}"
    return result.warning_codes


def test_no_contract_warns(tmp_path: Path) -> None:
    # The baseline the rest is measured against: `readings` is Pure Data, so no
    # `objects` declaration can relate its length and only a contract can.
    assert _warnings_for(None, tmp_path) == [ARRAY_OUTPUT_LENGTH_NOT_DERIVABLE]


@pytest.mark.parametrize("contract", _BOUNDING)
def test_contract_bounds_the_length(contract: str, tmp_path: Path) -> None:
    assert _warnings_for(contract, tmp_path) == []


@pytest.mark.parametrize("contract", _NOT_BOUNDING)
def test_contract_does_not_bound_the_length(contract: str, tmp_path: Path) -> None:
    assert _warnings_for(contract, tmp_path) == [ARRAY_OUTPUT_LENGTH_NOT_DERIVABLE]


def test_a_malformed_contract_bounds_nothing(tmp_path: Path) -> None:
    # The contracts pass reports the parse failure; this one stays silent about
    # it and simply credits the port with no bound.
    doc = tmp_path / "bad.yaml"
    block = f'    contracts:\n      ensures:\n        - expr: "{_LEN} <= )"\n'
    doc.write_text(_DOC.format(contract=block), encoding="utf-8")
    result = validate(str(doc))
    assert not result.ok  # the malformed expression is an error, reported once
    assert result.warning_codes == [ARRAY_OUTPUT_LENGTH_NOT_DERIVABLE]
