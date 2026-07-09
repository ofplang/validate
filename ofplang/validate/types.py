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

from ofplang.validate.yamlnode import YMap, YScalar, YNode

# Built-in primitive Data types (spec 7.1) and the reserved constructor/trait
# names. `Numeric` is a trait, not a type, but shares the "reserved, cannot be
# redeclared" property (spec 7.3).
PRIMITIVE_TYPES = frozenset({"Bool", "Int", "Float", "String"})
BUILTIN_TYPE_NAMES = frozenset({"Bool", "Int", "Float", "String", "Array"})
RESERVED_TYPE_LIKE = BUILTIN_TYPE_NAMES | {"Numeric"}

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# --- Parsed type representation -------------------------------------------
@dataclass(frozen=True)
class Atom:
    """A leaf type name (primitive, user type, or type parameter)."""

    name: str


@dataclass(frozen=True)
class ArrayT:
    """``Array<elem>`` — the only v0 type constructor."""

    elem: "TypeExpr"


TypeExpr = Atom | ArrayT


class TypeParseError(Exception):
    """Malformed type expression (maps to `malformed_type_expr`)."""


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

    def parse_expr(self) -> TypeExpr:
        name = self._read_ident()
        if name == "Array":
            # No whitespace is permitted between `Array` and `<` (spec 2.5), so
            # we require `<` to be the very next character.
            if self._peek() != "<":
                raise TypeParseError("Array must be immediately followed by '<'")
            self.i += 1  # consume '<'
            self._skip_inner_ws()
            elem = self.parse_expr()  # exactly one argument (recursion handles nesting)
            self._skip_inner_ws()
            # Anything other than the closing '>' here (e.g. a comma) means a
            # second type argument or junk: Array has arity exactly one.
            if self._peek() != ">":
                raise TypeParseError("Array takes exactly one argument")
            self.i += 1  # consume '>'
            return ArrayT(elem)
        # A bare `Array` (no `<`) fell through above; any other identifier is an
        # atom. `Numeric`/unknown names parse fine and are rejected at resolution.
        return Atom(name)

    def parse(self) -> TypeExpr:
        expr = self.parse_expr()
        if self.i != len(self.s):
            # Trailing characters (including outer whitespace) are illegal.
            raise TypeParseError(f"unexpected trailing text {self.s[self.i:]!r}")
        return expr


def parse_type(text: str) -> TypeExpr:
    return _Parser(text).parse()


# --- Document type environment --------------------------------------------
@dataclass
class TypeEnv:
    """Resolution context built once per document.

    ``user_types`` maps a user-defined type name to its domain ('data'/'object')
    and only contains structurally well-formed declarations, so resolution can
    trust it. ``traits`` is the set of declared trait names (built-in `Numeric`
    is handled separately and is not listed here).
    """

    user_types: dict[str, str] = field(default_factory=dict)
    traits: set[str] = field(default_factory=set)


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
            # Only record recognized domains; unknown/missing domain is an error
            # reported elsewhere and simply leaves the type unresolvable here.
            if domain in ("data", "object"):
                env.user_types[name] = domain

    traits = doc.get("traits")
    if isinstance(traits, YMap):
        for name in traits.keys():
            env.traits.add(name)

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
