"""Tests for public `$import` expansion: `expand()` and `validate(expand=True)`.

Intent: the front door of a downstream tool (scheduler/runner) validates a
document and, in the same pass, obtains the exact import-expanded document it
will execute — so "what was validated" equals "what runs". These tests pin the
two public surfaces that provide that:

  * `expand(source)` — structural load + `$import` resolution + plain-Python
    conversion, with no v0 validation.
  * `validate(source, expand=True)` — validation plus `ValidationResult.document`.

The load-bearing guarantee is fidelity with `yaml.safe_load`: for an
import-free document, `expand()` must be byte-for-byte the value `safe_load`
would produce, including less-common scalar tags (a bare date is a `datetime`,
not a string). A hand-rolled tag map would silently diverge here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from ofplang.validate import EXTENSION_TOLERANT, expand, validate
from ofplang.validate.yamlnode import YamlError

CASES = Path(__file__).parent / "conformance" / "cases"
BASIC = CASES / "imports" / "basic" / "main.yaml"


def _import_free_docs() -> list[Path]:
    """Every conformance root document that has no `$import` and loads to a
    mapping — the corpus for the expand==safe_load invariant."""
    out: list[Path] = []
    for path in CASES.rglob("*.yaml"):
        if "expected" in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        if "$import" in text:
            continue
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if isinstance(loaded, dict):
            out.append(path)
    return out


@pytest.mark.parametrize("path", _import_free_docs(), ids=lambda p: p.name)
def test_expand_matches_safe_load_for_import_free_docs(path: Path) -> None:
    # The core fidelity invariant: with no imports to resolve, expansion is a
    # pure round-trip and must equal safe_load exactly.
    assert expand(str(path)) == yaml.safe_load(path.read_text(encoding="utf-8"))


def test_expand_scalar_fidelity_covers_non_string_tags(tmp_path: Path) -> None:
    # Scalars are reconstructed via PyYAML's own SafeConstructor keyed on the
    # retained tag, so int/float/bool/null AND timestamps resolve identically to
    # safe_load — a hand-rolled str-only map would turn the date into a string.
    doc = tmp_path / "scalars.yaml"
    doc.write_text(
        "d: 2026-07-29\n"
        "i: 1_000\n"
        "hex: 0x1f\n"
        "f: .inf\n"
        "b: yes\n"
        'quoted: "123"\n',
        encoding="utf-8",
    )
    got = expand(str(doc))
    assert got == yaml.safe_load(doc.read_text(encoding="utf-8"))
    assert got["d"] == date(2026, 7, 29)
    assert got["i"] == 1000 and got["hex"] == 31
    assert got["quoted"] == "123"  # quoted stays a string, not the int 123


def test_expand_resolves_import_merge() -> None:
    # A mapping-position `$import` is replaced by the imported mapping's entries;
    # the `$import` key itself is gone from the result.
    got = expand(str(BASIC))
    assert "$import" not in yaml.dump(got)
    assert got["types"] == {"Image": {"domain": "data"}}


def test_expand_raises_on_structural_import_failure(tmp_path: Path) -> None:
    doc = tmp_path / "main.yaml"
    doc.write_text("types:\n  $import: ./missing_fragment.yaml\n", encoding="utf-8")
    with pytest.raises(YamlError) as exc:
        expand(str(doc))
    assert exc.value.code == "unreadable_import"


def test_expand_base_dir_overrides_root_import_anchor(tmp_path: Path) -> None:
    # A relocated copy of the root cannot see its fragment next to it; base_dir
    # pointing back at the fragment's real directory resolves the import.
    real_dir = BASIC.parent
    relocated = tmp_path / "main.yaml"
    relocated.write_text(BASIC.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(YamlError):
        expand(str(relocated))
    assert expand(str(relocated), base_dir=str(real_dir)) == expand(str(BASIC))


def test_expand_returns_non_mapping_root_as_is(tmp_path: Path) -> None:
    # A syntactically valid but non-mapping root is returned as-is (structural
    # expansion only); the mapping check is the caller's responsibility.
    doc = tmp_path / "scalar.yaml"
    doc.write_text("just a string\n", encoding="utf-8")
    assert expand(str(doc)) == "just a string"


def test_validate_expand_true_populates_document() -> None:
    result = validate(str(BASIC), mode=EXTENSION_TOLERANT, expand=True)
    assert result.document == expand(str(BASIC))


def test_validate_expand_false_leaves_document_none() -> None:
    # The default path does no conversion and stays behavior-identical.
    result = validate(str(BASIC), mode=EXTENSION_TOLERANT)
    assert result.document is None


def test_validate_document_is_none_on_import_failure(tmp_path: Path) -> None:
    # A structural import failure is fatal: a single diagnostic, no document.
    doc = tmp_path / "main.yaml"
    doc.write_text("types:\n  $import: ./missing_fragment.yaml\n", encoding="utf-8")
    result = validate(str(doc), mode=EXTENSION_TOLERANT, expand=True)
    assert not result.ok
    assert result.document is None


def test_validate_populates_document_even_when_invalid(tmp_path: Path) -> None:
    # `document` reflects a successful load+expand independent of later passes:
    # a document that loads but fails validation still yields its expanded form.
    doc = tmp_path / "bad.yaml"
    doc.write_text("spec_version: \"0.0\"\nentry: nonexistent\n", encoding="utf-8")
    result = validate(str(doc), mode=EXTENSION_TOLERANT, expand=True)
    assert not result.ok  # no such entry process
    assert result.document == {"spec_version": "0.0", "entry": "nonexistent"}
