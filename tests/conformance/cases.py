"""Discovery and modeling of conformance test cases.

A *case* is a spec-derived example paired with its expected validation outcome.
Two on-disk layouts are supported:

1. **Sidecar** (single-file document):
   ``<name>.yaml`` + ``<name>.expected.yaml`` in the same directory.
   The root document under test is ``<name>.yaml``.

2. **Directory** (multi-file, e.g. ``$import`` cases):
   a directory containing ``expected.yaml`` and ``main.yaml`` (plus any
   imported fragments). The root document under test is ``main.yaml``.

Expected-outcome schema (``*.expected.yaml`` / ``expected.yaml``)::

    mode: strict            # optional: strict | extension-tolerant (default strict)
    outcome: invalid        # required: valid | invalid
    match: exact            # optional: exact | superset (default exact)
    errors:                 # required iff outcome == invalid
      - code: unknown_key   # required; must be a member of ofplang.validate.errors.ERROR_CODES
        path: "..."         # optional location hint (not matched by default)
    warnings:               # optional, and independent of `outcome`: a warning never
      - code: ...           #   makes a document invalid. Omit it and warnings are not
                            #   checked at all, so existing cases need no change. Present
                            #   (even as an empty list) it is matched exactly, which is
                            #   how a case pins "this draws exactly these warnings" or
                            #   "this draws none".
    pending: "reason"       # optional: this case documents behavior the validator
                            #   does not satisfy yet (spec area not implemented, or a
                            #   known false positive). Marked xfail so the suite stays
                            #   green until the behavior lands; remove `pending` then.
    notes: "..."            # optional free-text rationale
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ofplang.validate.errors import ERROR_CODES, WARNING_CODES

VALID = "valid"
INVALID = "invalid"
_OUTCOMES = {VALID, INVALID}

MATCH_EXACT = "exact"
MATCH_SUPERSET = "superset"
_MATCHES = {MATCH_EXACT, MATCH_SUPERSET}

_DEFAULT_MODE = "strict"
_MODES = {"strict", "extension-tolerant"}


@dataclass(frozen=True)
class Case:
    """One conformance case: a root document plus its expected outcome."""

    id: str
    root_doc: Path
    mode: str
    outcome: str
    match: str
    expected_codes: tuple[str, ...]
    # None when the fixture omits `warnings` entirely, which means "do not check
    # them". A tuple (possibly empty) means "these exactly", so a case can pin
    # either the warnings a document draws or that it draws none.
    expected_warnings: tuple[str, ...] | None
    pending: str  # non-empty when this case is a known-not-yet-satisfied target
    notes: str


class CaseError(Exception):
    """A structurally malformed case fixture (a test-authoring bug)."""


def _load_expected(expected_path: Path, case_id: str) -> dict:
    try:
        data = yaml.safe_load(expected_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - authoring guard
        raise CaseError(f"[{case_id}] expected file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise CaseError(f"[{case_id}] expected file must be a mapping")
    return data


def _build_case(case_id: str, root_doc: Path, expected_path: Path) -> Case:
    data = _load_expected(expected_path, case_id)

    unknown = set(data) - {
        "mode", "outcome", "match", "errors", "warnings", "pending", "notes"
    }
    if unknown:
        raise CaseError(f"[{case_id}] unknown expected keys: {sorted(unknown)}")

    mode = data.get("mode", _DEFAULT_MODE)
    if mode not in _MODES:
        raise CaseError(f"[{case_id}] invalid mode: {mode!r}")

    outcome = data.get("outcome")
    if outcome not in _OUTCOMES:
        raise CaseError(f"[{case_id}] outcome must be one of {sorted(_OUTCOMES)}")

    match = data.get("match", MATCH_EXACT)
    if match not in _MATCHES:
        raise CaseError(f"[{case_id}] match must be one of {sorted(_MATCHES)}")

    errors = data.get("errors", [])
    if outcome == VALID and errors:
        raise CaseError(f"[{case_id}] valid case must not list errors")
    if outcome == INVALID and not errors:
        raise CaseError(f"[{case_id}] invalid case must list at least one error")

    codes: list[str] = []
    for entry in errors:
        if not isinstance(entry, dict) or "code" not in entry:
            raise CaseError(f"[{case_id}] each error entry needs a 'code'")
        code = entry["code"]
        if code not in ERROR_CODES:
            raise CaseError(
                f"[{case_id}] unknown error code {code!r}; "
                f"add it to ofplang.validate.errors if it is a real spec error"
            )
        codes.append(code)

    # `warnings` is independent of `outcome`: a warning never makes a document
    # invalid, so a valid case may list some and an invalid one may too. Absent
    # means "not checked"; present means "exactly these".
    warnings: tuple[str, ...] | None = None
    if "warnings" in data:
        entries = data["warnings"] or []
        if not isinstance(entries, list):
            raise CaseError(f"[{case_id}] 'warnings' must be a list")
        wcodes: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict) or "code" not in entry:
                raise CaseError(f"[{case_id}] each warning entry needs a 'code'")
            code = entry["code"]
            if code not in WARNING_CODES:
                raise CaseError(
                    f"[{case_id}] unknown warning code {code!r}; "
                    f"add it to ofplang.validate.errors.WARNING_CODES if it is a real one"
                )
            wcodes.append(code)
        warnings = tuple(wcodes)

    if not root_doc.exists():
        raise CaseError(f"[{case_id}] missing root document: {root_doc}")

    return Case(
        id=case_id,
        root_doc=root_doc,
        mode=mode,
        outcome=outcome,
        match=match,
        expected_codes=tuple(codes),
        expected_warnings=warnings,
        pending=str(data.get("pending", "")),
        notes=str(data.get("notes", "")),
    )


def discover_cases(cases_root: Path) -> list[Case]:
    """Find every conformance case under ``cases_root``, sorted by id."""
    cases: list[Case] = []
    seen_ids: set[str] = set()

    for expected_path in sorted(cases_root.rglob("*.expected.yaml")):
        root_doc = expected_path.with_name(
            expected_path.name[: -len(".expected.yaml")] + ".yaml"
        )
        case_id = str(expected_path.relative_to(cases_root)).replace("\\", "/")
        case_id = case_id[: -len(".expected.yaml")]
        cases.append(_build_case(case_id, root_doc, expected_path))
        seen_ids.add(case_id)

    for expected_path in sorted(cases_root.rglob("expected.yaml")):
        case_dir = expected_path.parent
        root_doc = case_dir / "main.yaml"
        case_id = str(case_dir.relative_to(cases_root)).replace("\\", "/")
        if case_id in seen_ids:
            raise CaseError(f"duplicate case id: {case_id}")
        cases.append(_build_case(case_id, root_doc, expected_path))
        seen_ids.add(case_id)

    return sorted(cases, key=lambda c: c.id)
