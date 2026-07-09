"""Tests for source-position reporting (file:line:col).

Intent: verify positions propagate from the node tree into diagnostics, that an
imported fragment's diagnostic names the *fragment* file (not the root), and
that the CLI renders positions in both text and JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

from ofplang.validate import validate
from ofplang.validate.cli import main, EXIT_INVALID

CASES = Path(__file__).parent / "conformance" / "cases"


def test_position_propagates_to_diagnostic() -> None:
    result = validate(str(CASES / "types" / "unknown_type.yaml"))
    diag = next(d for d in result.diagnostics if d.code == "unknown_type")
    # A concrete position is attached (1-based) and names the source file.
    assert isinstance(diag.line, int) and diag.line >= 1
    assert isinstance(diag.col, int) and diag.col >= 1
    assert diag.file and diag.file.endswith("unknown_type.yaml")
    assert diag.location and ":" in diag.location


def test_import_diagnostic_names_the_fragment(tmp_path: Path) -> None:
    # An error located inside an imported fragment must report the fragment
    # file, not the root — that is why positions carry a file.
    (tmp_path / "frag.yaml").write_text(
        "main:\n"
        "  kind: atomic\n"
        "  inputs:\n"
        "    x:\n"
        "      type: Nope\n"
        "      phase: data\n"
        "  outputs: {}\n",
        encoding="utf-8",
    )
    (tmp_path / "main.yaml").write_text(
        'spec_version: "0.0"\n'
        "processes:\n"
        "  $import: ./frag.yaml\n"
        "entry: main\n",
        encoding="utf-8",
    )
    result = validate(str(tmp_path / "main.yaml"))
    diag = next(d for d in result.diagnostics if d.code == "unknown_type")
    assert diag.file and diag.file.endswith("frag.yaml")
    assert diag.line == 5  # the `type: Nope` line within the fragment


def test_import_cycle_points_at_the_closing_import() -> None:
    # The cycle a -> b -> a is reported at the $import in b.yaml that re-enters
    # the stack, so the position names the fragment where it closes.
    result = validate(str(CASES / "imports" / "cycle" / "main.yaml"))
    diag = next(d for d in result.diagnostics if d.code == "import_cycle")
    assert diag.file and diag.file.endswith("b.yaml")
    assert isinstance(diag.line, int)


def test_duplicate_key_points_at_the_key() -> None:
    result = validate(str(CASES / "imports" / "duplicate_key" / "main.yaml"))
    diag = next(d for d in result.diagnostics if d.code == "duplicate_key_after_import")
    assert diag.file and diag.file.endswith("main.yaml")
    assert isinstance(diag.line, int)


def test_unreadable_import_points_at_the_import_site() -> None:
    result = validate(str(CASES / "imports" / "unreadable" / "main.yaml"))
    diag = next(d for d in result.diagnostics if d.code == "unreadable_import")
    # No target file to point at, so it anchors at the importing $import line.
    assert diag.file and diag.file.endswith("main.yaml")
    assert isinstance(diag.line, int)


def test_multidoc_import_points_at_the_import_site() -> None:
    # A multi-document target has no single position; anchor at the $import.
    result = validate(str(CASES / "imports" / "multidoc" / "main.yaml"))
    diag = next(d for d in result.diagnostics if d.code == "multidoc_import")
    assert diag.file and diag.file.endswith("main.yaml")
    assert isinstance(diag.line, int)


def test_uri_fragment_import_points_at_the_import_site() -> None:
    result = validate(str(CASES / "imports" / "uri_fragment" / "main.yaml"))
    diag = next(d for d in result.diagnostics if d.code == "uri_fragment_import")
    assert diag.file and diag.file.endswith("main.yaml")
    assert isinstance(diag.line, int)


def test_cli_text_shows_position(capsys) -> None:
    assert main([str(CASES / "types" / "unknown_type.yaml")]) == EXIT_INVALID
    out = capsys.readouterr().out
    # The locator carries a line:col (e.g. "...unknown_type.yaml:7:15:").
    assert ":7:" in out


def test_cli_json_includes_position(capsys) -> None:
    assert main(["--format", "json", str(CASES / "types" / "unknown_type.yaml")]) == EXIT_INVALID
    payload = json.loads(capsys.readouterr().out)
    diag = payload["results"][0]["diagnostics"][0]
    assert isinstance(diag["line"], int)
    assert diag["file"].endswith("unknown_type.yaml")
