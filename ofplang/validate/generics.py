"""Generic type parameters and `where` constraints, at the definition (spec 8, 8.1).

Intent: v0 generics are deliberately minimal — parameters carry only a domain, and
constraints are nominal trait memberships. This pass validates what is decidable
from a process definition alone:

  * each type parameter declares a valid domain, and shadows nothing;
  * every type parameter appears in at least one input port type (spec 8.1), since
    inference has nothing to bind it to otherwise; and
  * each `where` constraint is a well-formed `TraitName<Param>` naming a known trait
    and a declared parameter.

What an invocation *instantiates* those parameters to is a property of the binding,
not of the definition, so it lives with the other binding checks in
:mod:`ofplang.validate.bindings` — matching a value against a port and inferring a
type argument from it are the same operation (spec 8.1), and splitting them would
mean two passes walking the same bindings and reporting one mistake twice.
"""

from __future__ import annotations

import contextlib

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.matching import parse_constraint
from ofplang.validate.types import (
    BUILTIN_TYPE_NAMES,
    PRIMITIVE_TYPES,
    ArrayT,
    Atom,
    TypeEnv,
    TypeExpr,
    TypeParseError,
    parse_type,
    process_type_params,
)
from ofplang.validate.yamlnode import YMap, YScalar, YSeq


def _atoms(expr: TypeExpr) -> set[str]:
    """All atom names occurring in a type expression (recursing into Array)."""
    if isinstance(expr, ArrayT):
        return _atoms(expr.elem)
    if isinstance(expr, Atom):
        return {expr.name}
    return set()


def _input_atoms(proc: YMap) -> set[str]:
    """Collect every type-atom name used across the process's input ports."""
    names: set[str] = set()
    inputs = proc.get("inputs")
    if isinstance(inputs, YMap):
        for pname in inputs.keys():
            port = inputs.get(pname)
            if isinstance(port, YMap):
                tnode = port.get("type")
                if isinstance(tnode, YScalar) and tnode.is_str:
                    with contextlib.suppress(TypeParseError):
                        names |= _atoms(parse_type(tnode.text))
    return names


def check_generics(doc: YMap, diags: Diagnostics, env: TypeEnv) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return

    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        base = f"processes.{pname}"
        tp_node = proc.get("type_params")
        if not isinstance(tp_node, YMap):
            continue

        tp = process_type_params(proc)  # only well-formed 'data'/'object' params

        # A type parameter must not shadow a top-level user type name (spec 2.5),
        # which would make an atom in a port type ambiguous.
        for name in tp_node.keys():
            if name in env.user_types:
                diags.add(
                    errors.TYPE_PARAM_SHADOW,
                    f"type parameter {name!r} shadows a user type",
                    f"{base}.type_params.{name}",
                    at=tp_node.key_node(name),
                )

        # Each declared parameter must have a valid domain (spec 8). A missing or
        # bad domain is reported so the parameter is visibly rejected.
        for name in tp_node.keys():
            decl = tp_node.get(name)
            dom = decl.get("domain") if isinstance(decl, YMap) else None
            if not isinstance(dom, YScalar):
                diags.add(
                    errors.MISSING_TYPE_PARAM_DOMAIN,
                    f"{name!r} needs a domain",
                    f"{base}.type_params.{name}",
                    at=tp_node.key_node(name),
                )
            elif dom.text not in ("data", "object"):
                diags.add(
                    errors.BAD_TYPE_PARAM_DOMAIN,
                    f"invalid domain {dom.text!r}",
                    f"{base}.type_params.{name}",
                    at=dom,
                )

        # Every parameter must appear in an input port type so inference can bind
        # it (spec 8.1). Parameters used only in outputs/where are errors.
        used = _input_atoms(proc)
        for name in tp:
            if name not in used:
                diags.add(
                    errors.TYPE_PARAM_NOT_IN_INPUT,
                    f"type parameter {name!r} not used by any input port",
                    f"{base}.type_params.{name}",
                    at=tp_node.key_node(name),
                )

        # `where` constraints: well-formed, known trait, declared parameter.
        where = proc.get("where")
        if isinstance(where, YSeq):
            for i, item in enumerate(where.items):
                cpath = f"{base}.where[{i}]"
                if not isinstance(item, YScalar) or not item.is_str:
                    diags.add(
                        errors.MALFORMED_CONSTRAINT, "constraint must be a string", cpath, at=item
                    )
                    continue
                parsed = parse_constraint(item.text)
                if parsed is None:
                    diags.add(
                        errors.MALFORMED_CONSTRAINT,
                        f"malformed constraint {item.text!r}",
                        cpath,
                        at=item,
                    )
                    continue
                trait, param = parsed
                # The constraint must target a declared parameter of this process.
                if param not in tp:
                    # Distinguish "constrained a concrete type" (a real, if
                    # disallowed, type name) from arbitrary garbage (spec 8.1).
                    if (
                        param in env.user_types
                        or param in PRIMITIVE_TYPES
                        or param in BUILTIN_TYPE_NAMES
                    ):
                        diags.add(
                            errors.CONSTRAINT_ON_CONCRETE,
                            f"constraint over concrete type {param!r}",
                            cpath,
                            at=item,
                        )
                    else:
                        diags.add(
                            errors.MALFORMED_CONSTRAINT,
                            f"{param!r} is not a type parameter",
                            cpath,
                            at=item,
                        )
                    continue
                # The trait must be `Numeric` (built-in) or a declared trait.
                if trait != "Numeric" and trait not in env.traits:
                    diags.add(errors.UNKNOWN_TRAIT, f"unknown trait {trait!r}", cpath, at=item)
                elif trait == "Numeric" and tp[param] == "object":
                    # `Numeric` is satisfied only by the primitive Data types Int
                    # and Float, so it may be written only over a `domain: data`
                    # parameter (spec 8). A constraint over an object-domain one
                    # can never hold, which makes it a *definition*-level error --
                    # reported here rather than waiting for a call site, since an
                    # uninvoked generic process would otherwise never be checked.
                    diags.add(
                        errors.MALFORMED_CONSTRAINT,
                        "Numeric applies only to a data-domain type parameter",
                        cpath,
                        at=item,
                    )
