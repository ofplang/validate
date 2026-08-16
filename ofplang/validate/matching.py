"""Type matching for bindings and generic instantiation (spec 8.1, 11.1).

Intent: every binding connects a value to a port, and the value's resolved type must
*match* the port's (spec 11.1). v0 defines that match once, in spec 8.1, as the
structural matching relation used for generic instantiation; when no type parameter
is involved it reduces to identity of type expressions. This module is that one
relation, plus the two things a caller needs to use it:

  * :func:`source_type` — the resolved type a body dataflow reference denotes; and
  * :func:`literal_conforms` — whether a written-out YAML value fits a type, which
    spec 11.1.1 defines by pointing at the static-view-value rule (spec 7.4), so the
    check lives here once and the view pass calls it too.

Two kinds of type parameter can meet in one match and they behave differently
(spec 8.1). A parameter declared by the *target* process is **flexible**: matching is
what determines it. A parameter declared by the *enclosing* process — whose body
holds the invocation — is **rigid**: it stands for a type already fixed by whoever
instantiates the enclosing process, so matching may not choose it, and it matches
only itself. Passing ``flexible={}`` therefore gives the plain identity match that
the non-generic case needs.
"""

from __future__ import annotations

from enum import Enum

from ofplang.validate.objects import ProcSig
from ofplang.validate.types import (
    PRIMITIVE_TYPES,
    ArrayT,
    Atom,
    TypeEnv,
    TypeExpr,
)
from ofplang.validate.yamlnode import YMap, YNode, YScalar, YSeq


class MatchResult(Enum):
    """Outcome of one match. ``CONFLICT`` is distinct from ``MISMATCH`` because a
    flexible parameter that would have to be two different things is a defect of the
    *invocation* as a whole (spec 8.1 `conflicting_inference`), not of the one binding
    being matched; the caller that owns inference reports it, a per-binding caller
    treats it as "already reported elsewhere"."""

    OK = "ok"
    MISMATCH = "mismatch"
    CONFLICT = "conflict"


def domain_of(expr: TypeExpr, env: TypeEnv, rigid: dict[str, str]) -> str | None:
    """The domain ('data' / 'object') of an atomic type, or None if it does not
    resolve. Only atoms have a domain here: spec 8.1 never binds a parameter to an
    Array, so an Array never needs one."""
    if not isinstance(expr, Atom):
        return None
    if expr.name in rigid:
        return rigid[expr.name]
    if expr.name in PRIMITIVE_TYPES:
        return "data"
    return env.user_types.get(expr.name)


def match(
    target: TypeExpr,
    source: TypeExpr,
    *,
    env: TypeEnv,
    flexible: dict[str, str] | None = None,
    rigid: dict[str, str] | None = None,
    bindings: dict[str, TypeExpr] | None = None,
) -> MatchResult:
    """Match a bound value's `source` type against its `target` port type (spec 8.1).

    ``flexible`` / ``rigid`` map a type parameter name to its declared domain, for the
    target process and the enclosing process respectively. ``bindings`` accumulates
    the flexible parameters inferred so far and is *mutated*, so one invocation's ports
    can be matched in sequence and disagree on the second one (``CONFLICT``).

    With ``flexible`` empty this is identity matching, which is what a binding to a
    non-generic process needs.
    """
    if bindings is None:
        bindings = {}
    return _match(target, source, env, flexible or {}, rigid or {}, bindings)


def _match(
    target: TypeExpr,
    source: TypeExpr,
    env: TypeEnv,
    flexible: dict[str, str],
    rigid: dict[str, str],
    bindings: dict[str, TypeExpr],
) -> MatchResult:
    # A flexible parameter: bind it, or check it against what it is already bound to.
    if isinstance(target, Atom) and target.name in flexible:
        existing = bindings.get(target.name)
        if existing is not None:
            # "An already-bound flexible parameter matches only what it is already
            # bound to" (spec 8.1).
            return MatchResult.OK if existing == source else MatchResult.CONFLICT
        if not isinstance(source, Atom):
            # No Array-to-type-parameter binding (spec 8.1).
            return MatchResult.MISMATCH
        # The concrete atom -- or the rigid parameter -- must have the declared domain.
        if domain_of(source, env, rigid) != flexible[target.name]:
            return MatchResult.MISMATCH
        bindings[target.name] = source
        return MatchResult.OK

    # Array<X> matches Array<Y> by recursively matching X and Y (spec 8.1).
    if isinstance(target, ArrayT):
        if not isinstance(source, ArrayT):
            return MatchResult.MISMATCH
        return _match(target.elem, source.elem, env, flexible, rigid, bindings)

    # Everything else is a nominal match by name. This covers a built-in primitive, a
    # user-defined nominal, and a rigid parameter -- a rigid one "matches only that
    # same rigid parameter", and name equality is exactly that, because a parameter
    # may shadow neither a user type nor a built-in (spec 2.5).
    if not isinstance(source, Atom):
        return MatchResult.MISMATCH
    return MatchResult.OK if target.name == source.name else MatchResult.MISMATCH


def satisfies(
    trait: str,
    concrete: TypeExpr,
    *,
    implements: dict[str, set[str]],
    rigid: dict[str, str],
    rigid_where: set[tuple[str, str]],
) -> bool:
    """Whether `concrete` -- the type a flexible parameter was inferred to be --
    satisfies the `where` constraint `trait` (spec 8.1).

    For a real type this is trait membership: `Numeric` is the two primitive numeric
    types, a document trait is what the type `implements`. For a **rigid** parameter
    there is no type to look anything up on, so the constraint is discharged from what
    the enclosing process already promises: it holds only if that process declares the
    same constraint over the same parameter in its own `where` (`rigid_where`)."""
    if not isinstance(concrete, Atom):
        return False
    if concrete.name in rigid:
        return (trait, concrete.name) in rigid_where
    if trait == "Numeric":
        return concrete.name in ("Int", "Float")
    return trait in implements.get(concrete.name, set())


# --- resolving what a reference denotes -------------------------------------


def source_type(
    ref: str,
    comp_sig: ProcSig,
    sigs: dict[str, ProcSig],
    nodes_by_id: dict[str, YMap],
) -> TypeExpr | None:
    """The resolved type a body dataflow reference denotes, or None when it cannot be
    determined from the declarations alone.

    `inputs.X` is an input port of the enclosing composite; `<node>.<out>` is an output
    of a direct child. None means "no answer here", never "no type": a structured node
    reshapes its target's outputs (spec 21) and that shaping is not modelled yet, and a
    malformed or dangling reference is reported by the reference checks instead.
    """
    parts = ref.split(".")
    if len(parts) != 2:
        return None
    owner, name = parts
    if owner == "inputs":
        port = comp_sig.inputs.get(name)
        return port.type_expr if port else None
    node = nodes_by_id.get(owner)
    if not isinstance(node, YMap):
        return None
    if node.get("kind") is not None:
        return None  # structured node output shaping (spec 21) is not modelled yet
    proc = node.get("process")
    if not isinstance(proc, YScalar) or proc.text not in sigs:
        return None
    out = sigs[proc.text].outputs.get(name)
    return out.type_expr if out else None


def producer_is_generic(ref: str, sigs: dict[str, ProcSig], nodes_by_id: dict[str, YMap]) -> bool:
    """Whether a `<node>.<out>` reference is produced by a *generic* process.

    Such an output's declared type names the producer's own type parameter, which says
    nothing until that invocation is instantiated (spec 8.1). A caller matching types
    structurally must therefore leave it alone rather than compare the parameter name.
    """
    parts = ref.split(".")
    if len(parts) != 2 or parts[0] == "inputs":
        return False
    node = nodes_by_id.get(parts[0])
    if not isinstance(node, YMap):
        return False
    proc = node.get("process")
    if not isinstance(proc, YScalar):
        return False
    target = sigs.get(proc.text)
    return target is not None and target.generic


# --- literal values ----------------------------------------------------------


def is_primitive_only(expr: TypeExpr) -> bool:
    """Whether a resolved type is a primitive or an Array recursively of primitives --
    the shapes a written-out YAML value can be checked against (spec 7.4, 11.1.1)."""
    if isinstance(expr, ArrayT):
        return is_primitive_only(expr.elem)
    return isinstance(expr, Atom) and expr.name in PRIMITIVE_TYPES


def literal_conforms(expr: TypeExpr, value: YNode) -> bool:
    """Whether a written-out YAML value conforms to a primitive-only type.

    This is the rule spec 7.4 gives for a static view value, which spec 11.1.1 then
    reuses verbatim for a binding literal -- hence one function for both. Float
    intentionally accepts an integer scalar (a document should not have to write `3.0`
    where `3` is meant) but requires the value to be *finite*: NaN and infinity are not
    portable v0 values.
    """
    if isinstance(expr, ArrayT):
        if not isinstance(value, YSeq):
            return False
        return all(literal_conforms(expr.elem, item) for item in value.items)
    if not isinstance(value, YScalar) or not isinstance(expr, Atom):
        return False
    name = expr.name
    if name == "Bool":
        return value.is_bool
    if name == "Int":
        return value.is_int
    if name == "Float":
        return (value.is_float or value.is_int) and value.is_finite
    if name == "String":
        return value.is_str
    return False
