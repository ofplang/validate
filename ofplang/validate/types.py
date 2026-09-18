"""Type expressions and the document type environment (spec 2.5, 5.2, 7.1).

Intent: v0's type language is tiny on purpose — atoms plus a single ``Array<T>``
constructor — so we hand-write a small recursive parser. Hand-rolling (rather
than a grammar library) lets us enforce v0's exact whitespace rule (space is
legal *only* immediately inside the angle brackets) and single-argument arity
with precise error classification, and keeps the dependency surface at zero.

This module provides three things the type layer and later passes reuse:
  * :func:`parse_type` — text -> :class:`TypeExpr`, raising on malformed input;
  * :func:`is_object_bearing` — whether a resolved type has Object slots, the
    property that drives linear Object tracking (spec 5.2); and
  * :class:`TypeEnv` / :func:`build_env` — the document-wide map of user type
    domains and declared traits used to resolve atoms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ofplang.validate.yamlnode import YMap, YScalar, YSeq

# Built-in primitive Data types (spec 7.1) and the reserved constructor/trait
# names. `Numeric` is a trait, not a type, but shares the "reserved, cannot be
# redeclared" property (spec 7.3).
PRIMITIVE_TYPES = frozenset({"Bool", "Int", "Float", "String"})
#: The primitive types a unit suffix may be attached to (spec 28.2).
NUMERIC_PRIMITIVES = frozenset({"Int", "Float"})
BUILTIN_TYPE_NAMES = frozenset({"Bool", "Int", "Float", "String", "Array"})
RESERVED_TYPE_LIKE = BUILTIN_TYPE_NAMES | {"Numeric"}

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: A unit exponent: a nonzero decimal without a leading zero (spec 28.2).
_EXPONENT_RE = re.compile(r"[1-9][0-9]*")

#: A unit in normal form (spec 28.3): atom/exponent pairs sorted by atom name in
#: ASCII order, every exponent nonzero. The empty tuple is the dimensionless
#: unit, so `Atom("Float")` and a parsed `Float[1]` are the same value, which is
#: spec 28.5. A tuple rather than a mapping because `Atom` is frozen and must
#: stay hashable, and because the normal form is then the only representation.
Unit = tuple[tuple[str, int], ...]


def normalize_unit(pairs: list[tuple[str, int]]) -> Unit:
    """The normal form of a list of atom/exponent pairs (spec 28.3)."""
    acc: dict[str, int] = {}
    for name, exponent in pairs:
        acc[name] = acc.get(name, 0) + exponent
    return tuple(sorted((n, e) for n, e in acc.items() if e != 0))


def show_unit(unit: Unit) -> str:
    """The canonical written form of a normal form (spec 28.3)."""
    if not unit:
        return "1"
    return "*".join(name if exp == 1 else f"{name}^{exp}" for name, exp in unit)


# --- Parsed type representation -------------------------------------------
@dataclass(frozen=True)
class Atom:
    """A leaf type name (primitive, user type, or type parameter).

    ``unit`` is the type's unit in normal form (spec 28.3), empty for every type
    written without a suffix. It is part of the type, so two atoms are equal only
    when their units agree (spec 28.4) -- and because this dataclass is frozen,
    every pass that already compared type expressions with ``==`` compares units
    without being changed. The default is what makes `Atom("Int")` mean the
    dimensionless `Int`, which is spec 28.5.
    """

    name: str
    unit: Unit = ()


@dataclass(frozen=True)
class ArrayT:
    """``Array<elem>`` — the only v0 type constructor."""

    elem: TypeExpr


TypeExpr = Atom | ArrayT


class TypeParseError(Exception):
    """Malformed type expression (maps to `malformed_type_expr`)."""


class ArrayArityError(TypeParseError):
    """`Array` given other than exactly one argument (maps to `array_arity`).

    A subclass of `TypeParseError`, so callers that only care about "unparsable"
    (e.g. Object-tracking's port-type resolver) still catch it; callers that want
    the precise code (the type pass) catch this first."""


class UnitExprError(TypeParseError):
    """Malformed unit expression inside a suffix (maps to `malformed_unit_expr`).

    A subclass for the same reason `ArrayArityError` is one: the reader is
    looking at a different part of what they wrote. Only what is inside the
    brackets raises this. Whitespace *before* the bracket, or a suffix after
    `Array<...>`, is not in the grammar of spec 2.5 at all and stays a
    `malformed_type_expr`."""


# --- Parser ---------------------------------------------------------------
class _Parser:
    """Character-level recursive-descent parser for a single type expression.

    The parser is intentionally strict: it never trims outer whitespace and only
    tolerates spaces/tabs immediately inside ``Array< ... >`` (spec 2.5). Any
    other stray character is a parse error rather than being silently repaired.
    """

    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def _peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def _read_ident(self) -> str:
        m = _IDENT_RE.match(self.s, self.i)
        if not m:
            raise TypeParseError(f"expected a type name at offset {self.i}")
        self.i = m.end()
        return m.group()

    def _skip_inner_ws(self) -> None:
        # Only spaces and tabs, and only where this is called (inside <>).
        while self._peek() in (" ", "\t"):
            self.i += 1

    def _read_unit_ident(self) -> str:
        m = _IDENT_RE.match(self.s, self.i)
        if not m:
            raise UnitExprError(f"expected a unit atom at offset {self.i}")
        self.i = m.end()
        return m.group()

    def _read_exponent(self) -> int:
        # `^` already consumed. A nonzero decimal without a leading zero, so
        # `s^0` and `s^01` are malformed rather than silently accepted (28.2).
        negative = self._peek() == "-"
        if negative:
            self.i += 1
        m = _EXPONENT_RE.match(self.s, self.i)
        if not m:
            raise UnitExprError(f"expected a nonzero exponent at offset {self.i}")
        self.i = m.end()
        value = int(m.group())
        return -value if negative else value

    def _read_unit_suffix(self) -> Unit:
        """The unit of a `[...]` suffix, in normal form. `[` is the next character.

        No whitespace is allowed anywhere inside the brackets (28.2), which is
        simply what not skipping any gives. `*` and `/` are left-associative with
        equal precedence, so the separator decides the sign of the term that
        follows it: `a/b*c` is `(a/b)*c`.
        """
        self.i += 1  # consume '['
        # `1` is the dimensionless unit and may appear only as the whole
        # expression, so `1*s` and `1^-3` are malformed (28.2).
        if self._peek() == "1":
            self.i += 1
            if self._peek() != "]":
                raise UnitExprError("1 may appear only as the whole unit expression")
            self.i += 1
            return ()
        pairs: list[tuple[str, int]] = []
        sign = 1
        while True:
            name = self._read_unit_ident()
            exponent = 1
            if self._peek() == "^":
                self.i += 1
                exponent = self._read_exponent()
            pairs.append((name, sign * exponent))
            nxt = self._peek()
            if nxt == "*":
                sign = 1
            elif nxt == "/":
                sign = -1
            else:
                break
            self.i += 1  # consume the separator
        if self._peek() != "]":
            raise UnitExprError(f"unexpected {self._peek()!r} in a unit expression")
        self.i += 1  # consume ']'
        return normalize_unit(pairs)

    def parse_expr(self) -> TypeExpr:
        name = self._read_ident()
        if name == "Array":
            # No whitespace is permitted between `Array` and `<` (spec 2.5), so
            # we require `<` to be the very next character.
            if self._peek() != "<":
                raise TypeParseError("Array must be immediately followed by '<'")
            self.i += 1  # consume '<'
            self._skip_inner_ws()
            # `Array<>` (no argument) is an arity error, distinct from a malformed
            # inner type: Array has arity exactly one.
            if self._peek() == ">":
                raise ArrayArityError("Array takes exactly one argument")
            elem = self.parse_expr()  # exactly one argument (recursion handles nesting)
            self._skip_inner_ws()
            # Anything other than the closing '>' here (e.g. a comma) means a
            # second type argument or junk: Array has arity exactly one.
            if self._peek() != ">":
                raise ArrayArityError("Array takes exactly one argument")
            self.i += 1  # consume '>'
            return ArrayT(elem)
        # A bare `Array` (no `<`) fell through above; any other identifier is an
        # atom. `Numeric`/unknown names parse fine and are rejected at resolution.
        # A unit suffix is accepted on any atom and rejected at resolution where
        # the atom cannot carry one: the grammar of 28.2 is `TypeAtom
        # UnitSuffix?`, so `Cup[s]` is well-formed and wrong, and saying so with
        # its own code is what 4.4 promises. A suffix after `Array<...>` is not
        # in the grammar and falls out of `parse` as trailing text.
        unit: Unit = ()
        if self._peek() == "[":
            unit = self._read_unit_suffix()
        return Atom(name, unit)

    def parse(self) -> TypeExpr:
        expr = self.parse_expr()
        if self.i != len(self.s):
            # Trailing characters (including outer whitespace) are illegal.
            raise TypeParseError(f"unexpected trailing text {self.s[self.i:]!r}")
        return expr


def parse_type(text: str) -> TypeExpr:
    return _Parser(text).parse()


def show_type(expr: TypeExpr) -> str:
    """A type expression back in v0 source form, for a diagnostic message.

    A unit is written in its canonical normal form (spec 28.3) rather than as the
    document wrote it, so a diagnostic about `Float[mg/mL]` and one about
    `Float[mg*mL^-1]` name the same type the same way.
    """
    if isinstance(expr, ArrayT):
        return f"Array<{show_type(expr.elem)}>"
    return f"{expr.name}[{show_unit(expr.unit)}]" if expr.unit else expr.name


# --- Document type environment --------------------------------------------
@dataclass
class TypeEnv:
    """Resolution context built once per document.

    ``user_types`` maps a user-defined type name to its domain ('data'/'object')
    and only contains structurally well-formed declarations, so resolution can
    trust it. ``traits`` is the set of declared trait names (built-in `Numeric`
    is handled separately and is not listed here). ``implements`` maps a user type
    to the traits it declares, which is what a `where` constraint is checked
    against (spec 8.1) -- recorded as written, so a trait that was never declared
    still appears here and the trait pass reports it once, where it is.
    """

    user_types: dict[str, str] = field(default_factory=dict)
    traits: set[str] = field(default_factory=set)
    implements: dict[str, set[str]] = field(default_factory=dict)
    #: Unit atom names declared in the top-level `units` section (spec 28.1),
    #: their own namespace. Only names matching the identifier grammar are
    #: recorded: a malformed one is reported where it is declared, and nothing
    #: can reference it from a suffix anyway.
    units: set[str] = field(default_factory=set)


def process_type_params(proc: YMap) -> dict[str, str]:
    """Extract a process's type parameters as name -> domain.

    Shared by every pass that resolves types inside a process body, since a type
    atom may legitimately be a type parameter of the enclosing process
    (spec 2.5). Only well-formed 'data'/'object' domains are recorded.
    """
    out: dict[str, str] = {}
    tp = proc.get("type_params")
    if isinstance(tp, YMap):
        for name in tp.keys():
            decl = tp.get(name)
            if isinstance(decl, YMap):
                dom = decl.get("domain")
                if isinstance(dom, YScalar) and dom.text in ("data", "object"):
                    out[name] = dom.text
    return out


def build_env(doc: YMap) -> TypeEnv:
    """Collect user type domains and trait names from the document."""
    env = TypeEnv()

    types = doc.get("types")
    if isinstance(types, YMap):
        for name in types.keys():
            decl = types.get(name)
            domain = None
            if isinstance(decl, YMap):
                dom_node = decl.get("domain")
                if isinstance(dom_node, YScalar):
                    domain = dom_node.text
                impls = decl.get("implements")
                if isinstance(impls, YSeq):
                    env.implements[name] = {
                        item.text for item in impls.items if isinstance(item, YScalar)
                    }
            # Only record recognized domains; unknown/missing domain is an error
            # reported elsewhere and simply leaves the type unresolvable here.
            if domain in ("data", "object"):
                env.user_types[name] = domain

    traits = doc.get("traits")
    if isinstance(traits, YMap):
        for name in traits.keys():
            env.traits.add(name)

    units = doc.get("units")
    if isinstance(units, YMap):
        for name in units.keys():
            if _IDENT_RE.fullmatch(name):
                env.units.add(name)

    return env


# --- Resolution & Object-slot computation ---------------------------------
def resolve_error(expr: TypeExpr, env: TypeEnv, type_params: dict[str, str]) -> str | None:
    """Return `unknown_type` if any atom fails to resolve, else ``None``.

    An atom resolves to exactly one of: a built-in primitive, a top-level user
    type, or a type parameter of the current process (spec 2.5). `Numeric` and
    other unknown names do not resolve as types.
    """
    from ofplang.validate import errors

    if isinstance(expr, ArrayT):
        return resolve_error(expr.elem, env, type_params)
    name = expr.name
    if name in PRIMITIVE_TYPES:
        return None
    if name in env.user_types:
        return None
    if name in type_params:
        return None
    return errors.UNKNOWN_TYPE


def unit_error(expr: TypeExpr, env: TypeEnv) -> str | None:
    """Return a unit code if any suffix in `expr` is misplaced or undeclared.

    Two conditions, in this order (spec 28.1, 28.2). A suffix may sit only on
    `Int` or `Float`, so a suffix on a nominal type, on `Bool`, on `String` or on
    a type parameter is reported first -- it is the more specific mistake, and
    the atoms inside a suffix that should not be there say nothing useful. Then
    every atom must be declared in the top-level `units` section, which is what
    makes a misspelling an error where it is written rather than where the value
    is finally connected.
    """
    from ofplang.validate import errors

    if isinstance(expr, ArrayT):
        return unit_error(expr.elem, env)
    if not expr.unit:
        return None
    if expr.name not in NUMERIC_PRIMITIVES:
        return errors.UNIT_SUFFIX_NOT_NUMERIC
    for name, _ in expr.unit:
        if name not in env.units:
            return errors.UNDECLARED_UNIT_ATOM
    return None


def is_object_bearing(expr: TypeExpr, env: TypeEnv, type_params: dict[str, str]) -> bool:
    """Whether a type has one or more Object slots (spec 5.2).

    Recurses through ``Array<T>`` because an Array is Object-bearing iff its
    element type is. Unresolvable atoms are treated as non-Object-bearing; the
    unknown-type error is reported separately, and this keeps Object tracking
    from cascading off a name error.
    """
    if isinstance(expr, ArrayT):
        return is_object_bearing(expr.elem, env, type_params)
    name = expr.name
    if name in env.user_types:
        return env.user_types[name] == "object"
    if name in type_params:
        return type_params[name] == "object"
    return False
