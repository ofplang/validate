"""The `units` feature: normal form, type identity, and the contract unit rules.

Intent: a unit is part of the type (spec 28), which is what lets every rule that
requires two types to be the same require their units to agree without being
restated. These pin the three things that claim rests on and that the
conformance suite can only observe indirectly:

  * the normal form, so `Float[mg/mL]` and `Float[mg*mL^-1]` *are* one type and
    `Float[1]` *is* `Float` (28.3, 28.4, 28.5);
  * the two places that compared type names and would otherwise have let a unit
    through -- the matching relation and the `Numeric` constraint (8.1, 7.3); and
  * the literal expression of a contract (28.11), whose boundary is where a
    negative literal sits: spec 9.2 parses `-30.0` as unary minus applied to a
    literal, so a rule written over `Lit` alone would reject `x >= -30.0`.
"""

from __future__ import annotations

import pytest
import yaml

from ofplang.validate import validate
from ofplang.validate.matching import MatchResult, match, satisfies
from ofplang.validate.types import (
    ArrayArityError,
    Atom,
    TypeEnv,
    TypeParseError,
    UnitExprError,
    parse_type,
    show_type,
    unit_error,
)

# --- normal form (spec 28.3) -------------------------------------------------


@pytest.mark.parametrize(
    "written,canonical",
    [
        ("Float[s]", "Float[s]"),
        ("Float[mg/mL]", "Float[mL^-1*mg]"),
        ("Float[mg*mL^-1]", "Float[mL^-1*mg]"),
        ("Float[m*s^-2]", "Float[m*s^-2]"),
        ("Float[uL/mL]", "Float[mL^-1*uL]"),  # not dimensionless: atoms are opaque
        ("Float[a/b*c]", "Float[a*b^-1*c]"),  # `*` and `/` are left-associative
        ("Float[1]", "Float"),
        ("Float[s/s]", "Float"),
        ("Float[v/v]", "Float"),  # parses as a quotient, hence the advice in 28.13
        ("Int[count_]", "Int[count_]"),
        ("Array<Float[mg/mL]>", "Array<Float[mL^-1*mg]>"),
        ("Array< Float[uL] >", "Array<Float[uL]>"),  # 2.5's whitespace rule is unchanged
    ],
)
def test_normal_form(written: str, canonical: str) -> None:
    assert show_type(parse_type(written)) == canonical


@pytest.mark.parametrize(
    "written,code",
    [
        ("Float[mg / mL]", "unit"),  # no whitespace inside a suffix
        ("Float[2s]", "unit"),  # an atom must not begin with a digit
        ("Float[s^0]", "unit"),  # a nonzero exponent
        ("Float[s^01]", "unit"),  # without a leading zero
        ("Float[1*s]", "unit"),  # `1` only as the whole expression
        ("Float[%]", "unit"),
        ("Float[]", "unit"),
        ("Float[s", "unit"),
        ("Float [s]", "type"),  # whitespace before the suffix is not in the grammar
        ("Array<Int>[s]", "type"),  # nor is a suffix on an Array
    ],
)
def test_malformed(written: str, code: str) -> None:
    with pytest.raises(UnitExprError if code == "unit" else TypeParseError) as exc:
        parse_type(written)
    # A unit error is a TypeParseError too, so the type cases must not be one.
    if code == "type":
        assert not isinstance(exc.value, (UnitExprError, ArrayArityError))


# --- type identity (spec 28.4, 28.5) -----------------------------------------


def test_dimensionless_is_the_plain_type() -> None:
    assert parse_type("Float[1]") == Atom("Float")
    assert parse_type("Int[1]") == Atom("Int")
    assert parse_type("Float[s/s]") == parse_type("Float")


@pytest.mark.parametrize(
    "a,b",
    [
        ("Float[s]", "Float"),  # a unit is never added or dropped implicitly
        ("Float[uL]", "Float[mL]"),
        ("Float[mg/mL]", "Float[g/L]"),
        ("Float[s]", "Int[s]"),
        ("Float[s]", "Float[s^2]"),
        ("Array<Float[s]>", "Array<Float>"),
    ],
)
def test_distinct_types(a: str, b: str) -> None:
    assert parse_type(a) != parse_type(b)


# --- the matching relation (spec 8.1, 28.7) ----------------------------------

ENV = TypeEnv(user_types={"Cup": "object"}, units={"s", "m", "uL", "mL"})


def _match(target: str, source: str) -> MatchResult:
    return match(
        parse_type(target), parse_type(source), env=ENV, flexible={}, rigid={}, bindings={}
    )


def test_matching_requires_the_unit() -> None:
    # The relation compared type *names*, so without this a `Float[s]` value
    # satisfied a `Float` port and every rule built on it (11.1, 16, 21) let the
    # unit through.
    assert _match("Float[s]", "Float[s]") is MatchResult.OK
    assert _match("Float[s]", "Float") is MatchResult.UNIT_MISMATCH
    assert _match("Float", "Float[s]") is MatchResult.UNIT_MISMATCH
    assert _match("Float[s]", "Float[m]") is MatchResult.UNIT_MISMATCH
    assert _match("Array<Float[s]>", "Array<Float>") is MatchResult.UNIT_MISMATCH


def test_unit_mismatch_is_not_a_base_type_mismatch() -> None:
    # Two codes because the fix differs: a conversion process, or a different
    # port. `matching` is the only place that knows which it saw.
    assert _match("Float[s]", "Int[s]") is MatchResult.MISMATCH
    assert _match("Float[s]", "Cup") is MatchResult.MISMATCH


def test_a_unit_annotated_type_instantiates_a_data_parameter() -> None:
    # A unit-annotated numeric type is a concrete atomic type of domain data
    # (spec 28.7), so a flexible parameter of that domain takes it.
    bindings: dict = {}
    assert (
        match(
            Atom("T"),
            parse_type("Float[s]"),
            env=ENV,
            flexible={"T": "data"},
            rigid={},
            bindings=bindings,
        )
        is MatchResult.OK
    )
    assert bindings["T"] == parse_type("Float[s]")
    # And, being already bound, it matches only itself -- unit included.
    assert (
        match(
            Atom("T"),
            parse_type("Float[m]"),
            env=ENV,
            flexible={"T": "data"},
            rigid={},
            bindings=bindings,
        )
        is MatchResult.CONFLICT
    )


# --- Numeric (spec 7.3, 28.12) -----------------------------------------------


@pytest.mark.parametrize(
    "written,satisfied",
    [
        ("Float", True),
        ("Int", True),
        ("Float[1]", True),  # which is Float (28.5)
        ("Float[s]", False),
        ("Int[s]", False),
    ],
)
def test_numeric_takes_only_a_dimensionless_type(written: str, satisfied: bool) -> None:
    assert (
        satisfies(
            "Numeric", parse_type(written), implements={}, rigid={}, rigid_where=set()
        )
        is satisfied
    )


# --- declaration (spec 28.1, 28.2) -------------------------------------------


@pytest.mark.parametrize(
    "written,code",
    [
        ("Float[s]", None),
        ("Array<Float[uL]>", None),
        ("Float", None),
        ("Float[sec]", "undeclared_unit_atom"),
        ("Float[s*sec]", "undeclared_unit_atom"),
        ("Cup[s]", "unit_suffix_not_numeric"),
        ("Bool[s]", "unit_suffix_not_numeric"),
        ("String[s]", "unit_suffix_not_numeric"),
        # A suffix on a type parameter is the same mistake: only `Int` and
        # `Float` carry one, and v0 cannot abstract over a unit (28.12).
        ("T[s]", "unit_suffix_not_numeric"),
        # Position before declaration: a suffix that cannot be there says
        # nothing useful about the atoms inside it.
        ("Cup[sec]", "unit_suffix_not_numeric"),
    ],
)
def test_unit_error(written: str, code: str | None) -> None:
    assert unit_error(parse_type(written), ENV) == code


# --- contract unit rules (spec 28.11) ----------------------------------------

DOC = """\
spec_version: "0.3"
units:
  s: {}
  uL: {}
  mL: {}
  mg: {}
processes:
  main:
    kind: atomic
    inputs:
      t: {type: "Float[s]", phase: data}
      v: {type: "Float[uL]", phase: data}
      c: {type: "Float[mg/uL]", phase: data}
      m: {type: "Float[mg]", phase: data}
      n: {type: Float, phase: data}
    outputs: {}
    contracts:
      requires:
        - expr: "%s"
entry: main
"""


def _codes(expr: str) -> list[str]:
    """The error codes one contract expression draws, in-memory (no file needed)."""
    return sorted(validate(yaml.safe_load(DOC % expr)).codes)


@pytest.mark.parametrize(
    "expr",
    [
        "inputs.t.view >= 30.0",  # a literal takes the unit of the port
        "inputs.t.view >= -30.0",  # ... including a negative one (9.2 parses it as unary minus)
        "inputs.t.view > 1.0 + 2.0",  # ... and an expression of literals alone
        "inputs.t.view + 1.0 > inputs.t.view",
        "inputs.v.view * inputs.c.view <= inputs.m.view",  # uL * mg/uL is mg
        "inputs.v.view / inputs.v.view > 0.5",  # uL/uL is dimensionless
        "inputs.t.view * 2.0 > inputs.t.view",  # a literal factor is dimensionless
        "inputs.n.view > 1.0",  # an unannotated port is unaffected
        "not (inputs.t.view > 1.0)",
        "inputs.t.view > 1.0 and inputs.v.view > 1.0",
    ],
)
def test_contract_accepted(expr: str) -> None:
    assert _codes(expr) == []


@pytest.mark.parametrize(
    "expr",
    [
        "inputs.t.view > inputs.v.view",  # s against uL
        "inputs.v.view + inputs.m.view > 1.0",  # uL against mg
        "inputs.t.view > inputs.n.view",  # s against dimensionless
        "inputs.v.view * inputs.c.view <= inputs.v.view",  # mg against uL
        "inputs.v.view / inputs.c.view > inputs.m.view",  # uL^2/mg against mg
    ],
)
def test_contract_unit_mismatch(expr: str) -> None:
    assert _codes(expr) == ["contract_unit_mismatch"]


# --- the units section (spec 28.1) -------------------------------------------

SECTION = """\
spec_version: "0.3"
units:
%s
processes:
  main:
    kind: atomic
    inputs: {}
    outputs: {}
entry: main
"""


def test_a_yaml_integer_key_is_not_a_unit_atom_name() -> None:
    # `1:` is why the dimensionless unit needs no rule forbidding its
    # declaration: every mapping key is carried as text, so it arrives as "1"
    # and fails the identifier grammar of 28.2 like any other non-identifier.
    doc = yaml.safe_load(SECTION % "  1: {}")
    assert sorted(validate(doc).codes) == ["malformed_unit_atom"]


def test_expand_renders_a_non_string_key_as_text() -> None:
    # Stated here because the conformance corpus holds the invariant that
    # `expand` equals `safe_load` for an import-free document, and this is the
    # one shape where it does not: a YAML integer key is normalized to text.
    # Only an invalid document can show it -- v0 keys are identifiers (spec 2.4).
    from ofplang.validate import expand

    text = SECTION % "  1: {}"
    assert yaml.safe_load(text)["units"] == {1: {}}
    assert expand(_written(text))["units"] == {"1": {}}


def _written(text: str) -> str:
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "d.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_an_omitted_units_section_admits_only_the_dimensionless_unit() -> None:
    # An omitted section is an empty mapping (spec 2.3, 28.1), so `Float[1]` --
    # which is `Float` -- can still be written and nothing else can.
    ok = yaml.safe_load(
        'spec_version: "0.3"\nprocesses:\n  main:\n    kind: atomic\n'
        '    inputs:\n      t: {type: "Float[1]", phase: data}\n    outputs: {}\nentry: main\n'
    )
    assert validate(ok).ok
    bad = yaml.safe_load(
        'spec_version: "0.3"\nprocesses:\n  main:\n    kind: atomic\n'
        '    inputs:\n      t: {type: "Float[s]", phase: data}\n    outputs: {}\nentry: main\n'
    )
    assert sorted(validate(bad).codes) == ["undeclared_unit_atom"]
