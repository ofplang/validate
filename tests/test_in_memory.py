"""Validating a document that is already loaded (no file to point at).

`validate()` takes a path or a mapping. The mapping form is for a caller that built
or holds a document in memory -- the shape the sibling tools accept and a generator
or a notebook produces -- which until now had no front door at all: the schedulers
and runners accept in-memory documents, but validation only accepted paths, so the
one document nobody could check was the one nobody wrote to disk.

What has to hold: the two forms must agree on every finding, since one is meant to
substitute for the other. What differs is what a document in memory cannot carry --
source positions, duplicate mapping keys -- and what it must not carry: an
unexpanded `$import` (nothing to resolve it against) or a value v0 has no spelling
for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ofplang.validate import EXTENSION_TOLERANT, validate
from ofplang.validate.yamlnode import from_object, to_plain

CASES = Path(__file__).parent / "conformance" / "cases"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _codes(result) -> list[str]:
    return sorted({d.code for d in result.diagnostics})


# --- the two forms agree ------------------------------------------------------


def _has_duplicate_key(text: str) -> bool:
    """Whether the document repeats a mapping key anywhere.

    Such a document cannot be held in a dict at all -- one value per key -- so the
    two forms are not comparable for it, and the case is left out of the agreement
    test rather than papered over.
    """

    def walk(node) -> bool:
        if isinstance(node, yaml.MappingNode):
            keys = [getattr(k, "value", None) for k, _ in node.value]
            if len(keys) != len(set(keys)):
                return True
            return any(walk(v) for _, v in node.value)
        if isinstance(node, yaml.SequenceNode):
            return any(walk(item) for item in node.value)
        return False

    try:
        return any(walk(node) for node in yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError:
        return False


def _case_files() -> list[Path]:
    """Every conformance case document the in-memory form can be handed: a single
    mapping, no `$import` (refused there), no duplicate key (inexpressible there)."""
    out = []
    for path in sorted(CASES.rglob("*.yaml")):
        if path.name.endswith(".expected.yaml") or "imports" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            continue  # a deliberately unparseable fixture: nothing to load in memory
        if not isinstance(doc, dict) or "$import" in yaml.safe_dump(doc):
            continue
        if _has_duplicate_key(text):
            continue
        out.append(path)
    return out


@pytest.mark.parametrize("path", _case_files(), ids=lambda p: p.stem)
def test_a_document_in_memory_gets_the_same_findings_as_its_file(path: Path) -> None:
    from_file = validate(str(path), mode=EXTENSION_TOLERANT)
    in_memory = validate(_load(path), mode=EXTENSION_TOLERANT)
    assert _codes(in_memory) == _codes(from_file)
    assert in_memory.ok == from_file.ok


def test_findings_locate_by_path_when_there_is_no_file() -> None:
    result = validate(_load(CASES / "types" / "unknown_type.yaml"))
    diag = next(d for d in result.diagnostics if d.code == "unknown_type")
    # No position to report, so the logical path is the whole locator.
    assert diag.line is None and diag.file is None
    assert diag.location is None
    assert diag.path


# --- what the in-memory form is more faithful about --------------------------


def test_a_string_that_reads_like_a_number_stays_a_string() -> None:
    """Round-tripping through YAML text would resolve `"0.0"` to a float; taking the
    value as it stands keeps the type the caller actually built."""
    doc = {
        "spec_version": "0.0",
        "processes": {"main": {"kind": "atomic", "inputs": {}, "outputs": {}}},
        "entry": "main",
    }
    assert validate(doc).ok

    root = from_object(doc)
    version = root.get("spec_version")
    assert version.is_str and version.text == "0.0"


def test_a_non_finite_float_reads_as_the_yaml_spelling() -> None:
    """v0 excludes infinity wherever a number is written (spec 7.4). YAML spells it
    `.inf`, and Python's `float()` does not accept that spelling, so the wrapper has to
    write the YAML one for the finiteness check to see what it is looking at."""
    doc = {
        "spec_version": "0.0",
        "types": {
            "Plate": {
                "domain": "object",
                "view": {"od": {"type": "Float", "value": float("inf")}},
            }
        },
        "processes": {"main": {"kind": "atomic", "inputs": {}, "outputs": {}}},
        "entry": "main",
    }
    value = from_object(doc).get("types").get("Plate").get("view").get("od").get("value")
    assert value.is_float and not value.is_finite
    assert value.text == ".inf"

    # And the document is rejected the same way its file form is.
    in_memory = _codes(validate(doc))
    assert in_memory and in_memory == validate_text(yaml.safe_dump(doc, sort_keys=False))


def test_a_duplicate_key_cannot_arise_in_memory() -> None:
    """Not a gap: a dict has one value per key, so the document a caller holds cannot
    express the error. Stated as a test so the absence is deliberate."""
    text = "spec_version: \"0.0\"\nspec_version: \"0.0\"\nprocesses: {}\nentry: main\n"
    from_text = validate_text(text)
    assert "duplicate_key" in from_text
    assert "duplicate_key" not in _codes(validate(yaml.safe_load(text)))


def validate_text(text: str) -> list[str]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "doc.yaml"
        path.write_text(text, encoding="utf-8")
        return _codes(validate(str(path)))


# --- what it must not carry ---------------------------------------------------


def test_an_unexpanded_import_is_refused() -> None:
    # The document is not at fault -- v0 allows imports -- but this entry point has no
    # directory to resolve one against, so it is a caller error like an unknown mode.
    doc = {"spec_version": "0.0", "types": {"$import": "types.yaml"}, "entry": "main"}
    with pytest.raises(ValueError) as excinfo:
        validate(doc)
    assert "import-expanded" in str(excinfo.value)
    assert "types.$import" in str(excinfo.value)


def test_a_value_v0_has_no_spelling_for_is_refused() -> None:
    import datetime

    doc = {
        "spec_version": "0.0",
        "processes": {"main": {"kind": "atomic", "inputs": {}, "outputs": {}}},
        "entry": "main",
        "when": datetime.date(2026, 8, 20),
    }
    with pytest.raises(ValueError) as excinfo:
        validate(doc)
    assert "date" in str(excinfo.value) and "<root>.when" in str(excinfo.value)


# --- the expanded document it hands back --------------------------------------


def test_the_returned_document_does_not_alias_the_input() -> None:
    doc = _load(CASES / "shape" / "valid_minimal.yaml")
    result = validate(doc, expand=True)
    assert result.ok
    assert result.document == doc
    assert result.document is not doc
    result.document["processes"]["main"]["kind"] = "composite"
    assert doc["processes"]["main"]["kind"] == "atomic"


def test_wrapping_and_unwrapping_round_trips() -> None:
    doc = _load(CASES / "shape" / "valid_minimal.yaml")
    assert to_plain(from_object(doc)) == doc
