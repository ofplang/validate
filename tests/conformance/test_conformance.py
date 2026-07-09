"""The conformance test: run every discovered case through the validator and
compare the produced error codes against the case's expected outcome.

Each case is a separate parametrized test so failures point at a single fixture.

While the validator is unimplemented, ``validate`` raises ``NotImplementedError``
and cases are reported as ``xfail`` ("pending implementation"). Set the env var
``OFPLANG_STRICT_TESTS=1`` to turn that escape hatch off so a finished validator
is held to the full contract (and any remaining ``NotImplementedError`` fails).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ofplang.validate import validate
from tests.conformance.cases import (
    INVALID,
    MATCH_EXACT,
    VALID,
    Case,
    discover_cases,
)

CASES_ROOT = Path(__file__).parent / "cases"
_STRICT = os.environ.get("OFPLANG_STRICT_TESTS") == "1"

_CASES = discover_cases(CASES_ROOT) if CASES_ROOT.exists() else []

# Categories whose validation passes are implemented. The validator is built
# milestone by milestone; a case in a not-yet-implemented category is reported
# xfail ("pending") rather than failed, so partial progress keeps a green suite.
# Add categories here as their passes land. `OFPLANG_STRICT_TESTS=1` ignores
# this gate and holds the whole suite to the full contract (final acceptance).
IMPLEMENTED_CATEGORIES = {
    "shape",
    "metadata",
    "identifiers",
    "entry",
    "extensions",
    "types",
    "traits",
    "views",
    "phases",
    "features",
    "imports",
    "objects",
    "transforms",
    "linearity",
    "generics",
    "script",
    "nodes",
    "contracts",
    "scheduling",
    "references",
    "aggregation",
}


def _category(case: Case) -> str:
    return case.id.split("/", 1)[0]


def _run(case: Case):
    if not _STRICT and _category(case) not in IMPLEMENTED_CATEGORIES:
        pytest.xfail(f"category '{_category(case)}' not implemented yet")
    try:
        return validate(case.root_doc, mode=case.mode)
    except NotImplementedError:
        if _STRICT:
            raise
        pytest.xfail("validator not implemented yet")


def _assert_outcome(case: Case, result) -> None:
    produced = set(result.codes)
    expected = set(case.expected_codes)

    if case.outcome == VALID:
        assert result.ok, (
            f"expected valid, got errors {sorted(produced)}"
            + (f"\nnote: {case.notes}" if case.notes else "")
        )
        return

    # INVALID
    assert not result.ok, "expected validation errors, got none"
    if case.match == MATCH_EXACT:
        assert produced == expected, (
            f"error code set mismatch\n  expected: {sorted(expected)}\n"
            f"  produced: {sorted(produced)}"
            + (f"\nnote: {case.notes}" if case.notes else "")
        )
    else:  # superset
        missing = expected - produced
        assert not missing, (
            f"missing expected error codes: {sorted(missing)}\n"
            f"  produced: {sorted(produced)}"
        )


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.id)
def test_conformance(case: Case, request: pytest.FixtureRequest) -> None:
    # `pending` cases document behavior the validator does not satisfy yet
    # (unimplemented spec areas, or known false positives). Mark them xfail
    # (non-strict, so an unexpected pass shows up as XPASS) so the suite stays
    # green while these tests are added ahead of implementation. Remove the
    # `pending` field from a case's expected file once the behavior lands.
    if case.pending:
        request.node.add_marker(
            pytest.mark.xfail(reason=f"pending: {case.pending}", strict=False)
        )
    result = _run(case)
    _assert_outcome(case, result)


def test_at_least_one_case_discovered() -> None:
    assert _CASES, f"no conformance cases found under {CASES_ROOT}"
