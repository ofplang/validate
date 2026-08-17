"""How many diagnostics an instantiated generic contract produces.

The conformance suite compares error codes as *sets*, so it pins which code a
faulty instantiation gets but not how often it is reported. That count is a
decision in its own right -- a contract faulted by ten call sites that all
instantiate it the same way is one thing to fix -- so it is pinned here.
"""

from __future__ import annotations

from pathlib import Path

from ofplang.validate import validate

_HEADER = """\
spec_version: "0.0"
types:
  Img:
    domain: data
    view:
      width:
        type: Int
  Blob:
    domain: data
    view:
      size:
        type: Int
processes:
  wide_enough:
    kind: atomic
    type_params:
      D: {{ domain: data }}
    inputs:
      a: {{ type: D, phase: data }}
    outputs: {{}}
    contracts:
      requires:
        - expr: "{expr}"
  make_img:
    kind: atomic
    inputs: {{}}
    outputs:
      v: {{ type: Img, phase: data }}
  make_blob:
    kind: atomic
    inputs: {{}}
    outputs:
      v: {{ type: Blob, phase: data }}
  main:
    kind: composite
    inputs: {{}}
    outputs: {{}}
    body:
      nodes:
        - {{ id: mi, process: make_img }}
        - {{ id: mb, process: make_blob }}
"""


def _doc(tmp_path: Path, sources: list[str], expr: str = "inputs.a.view.width > 0") -> str:
    """A document invoking `wide_enough` once per entry of `sources`."""
    nodes = "".join(
        f"        - {{ id: w{i}, process: wide_enough, bind: {{ a: {{ from: {src}.v }} }} }}\n"
        for i, src in enumerate(sources)
    )
    path = tmp_path / "doc.yaml"
    path.write_text(_HEADER.format(expr=expr) + nodes + "      returns: {}\nentry: main\n")
    return str(path)


def test_one_finding_per_set_of_type_arguments(tmp_path: Path) -> None:
    # Five invocations, all instantiating D to Blob, which has no `width`. That is
    # one mistake to fix, so it is reported once.
    result = validate(_doc(tmp_path, ["mb"] * 5))
    assert result.codes == ["unknown_view_field"]


def test_a_different_type_argument_is_a_different_finding(tmp_path: Path) -> None:
    # Blob twice and Img twice: Img satisfies the contract, Blob does not, and
    # collapsing them would hide which argument is the problem.
    result = validate(_doc(tmp_path, ["mb", "mi", "mb", "mi"]))
    assert result.codes == ["unknown_view_field"]
    assert "D := Blob" in result.diagnostics[0].message


def test_two_faulty_type_arguments_are_two_findings(tmp_path: Path) -> None:
    # A bare `.view` faults for both arguments, since neither nominal type has a
    # scalar view. Same code twice, but two invocations to fix, so two findings --
    # and each names the argument it is about.
    result = validate(_doc(tmp_path, ["mb", "mi"], expr="inputs.a.view > 0"))
    assert result.codes == ["contract_invalid_reference"] * 2
    named = {d.message.split("with ")[1][:-1] for d in result.diagnostics}
    assert named == {"D := Blob", "D := Img"}


def test_a_contract_faulty_at_its_definition_is_reported_once(tmp_path: Path) -> None:
    # A parse error belongs to the definition, not to any invocation, so five call
    # sites must not turn it into six diagnostics.
    result = validate(_doc(tmp_path, ["mi"] * 5, expr="inputs.a.view.width >"))
    assert result.codes == ["contract_parse_error"]
