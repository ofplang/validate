"""Generic type parameters and `where` constraints (spec 8, 8.1).

Intent: v0 generics are deliberately minimal — parameters carry only a domain,
constraints are nominal trait memberships, and type arguments are *inferred*
from input bindings (there is no explicit type-argument syntax). This pass
validates the parts that are decidable from a process definition alone:

  * each type parameter declares a valid domain;
  * every type parameter appears in at least one input port type (spec 8.1),
    since inference has nothing to bind it to otherwise; and
  * each `where` constraint is a well-formed `TraitName<Param>` naming a known
    trait and a declared parameter.

Constraint *satisfaction* (checking the inferred concrete type implements the
trait) requires an invocation site and is performed during graph validation by
the node layer; it is out of scope here.
"""

from __future__ import annotations

import re

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.objects import ProcSig
from ofplang.validate.types import (
    ArrayT,
    Atom,
    BUILTIN_TYPE_NAMES,
    PRIMITIVE_TYPES,
    TypeEnv,
    TypeExpr,
    TypeParseError,
    parse_type,
    process_type_params,
)
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode

# A `where` constraint: TraitName<Param>, whitespace allowed only inside the
# angle brackets (spec 8.1) — mirrors the type-expression whitespace rule.
_CONSTRAINT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)<[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*>$")


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
                    try:
                        names |= _atoms(parse_type(tnode.text))
                    except TypeParseError:
                        pass
    return names


def _implements_map(doc: YMap) -> dict[str, set[str]]:
    """user type name -> set of trait names it declares via `implements`."""
    out: dict[str, set[str]] = {}
    types = doc.get("types")
    if not isinstance(types, YMap):
        return out
    for tname in types.keys():
        decl = types.get(tname)
        traits: set[str] = set()
        if isinstance(decl, YMap):
            impls = decl.get("implements")
            if isinstance(impls, YSeq):
                for item in impls.items:
                    if isinstance(item, YScalar):
                        traits.add(item.text)
        out[tname] = traits
    return out


def _unify(pexpr: TypeExpr, sexpr: TypeExpr | None, params: set[str], bindings: dict[str, TypeExpr]) -> None:
    """Infer type-parameter bindings by structural matching (spec 8.1).

    Only the shapes v0 inference needs: a parameter atom binds to the concrete
    source type; Array matches Array recursively; concrete-vs-concrete is left
    alone (mismatches there are ordinary type errors handled elsewhere).
    """
    if sexpr is None:
        return
    if isinstance(pexpr, Atom) and pexpr.name in params:
        bindings.setdefault(pexpr.name, sexpr)
        return
    if isinstance(pexpr, ArrayT) and isinstance(sexpr, ArrayT):
        _unify(pexpr.elem, sexpr.elem, params, bindings)


def _source_type(ref_text: str, comp_sig: ProcSig, sigs: dict[str, ProcSig], nodes_by_id) -> TypeExpr | None:
    """Resolve a `from` reference to the concrete type of the value it names."""
    parts = ref_text.split(".")
    if len(parts) != 2:
        return None
    owner, name = parts
    if owner == "inputs":
        port = comp_sig.inputs.get(name)
        return port.type_expr if port else None
    node = nodes_by_id.get(owner)
    if node is None:
        return None
    proc = node.get("process")
    if isinstance(proc, YScalar) and proc.text in sigs:
        out = sigs[proc.text].outputs.get(name)
        return out.type_expr if out else None
    return None


def _check_instantiations(
    doc: YMap, diags: Diagnostics, env: TypeEnv, sigs: dict[str, ProcSig]
) -> None:
    """Infer type arguments at ordinary call sites and check `where` (spec 8.1).

    Scope: ordinary nodes invoking a generic process, with sources whose types
    we can resolve. This is enough to enforce trait satisfaction on inferred
    concrete types; broader inference (partial/uninferable) is future work.
    """
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    implements = _implements_map(doc)

    for pname in processes.keys():
        proc = processes.get(pname)
        comp_sig = sigs.get(pname)
        if not isinstance(proc, YMap) or comp_sig is None:
            continue
        body = proc.get("body")
        if not isinstance(body, YMap):
            continue
        nodes = body.get("nodes")
        if not isinstance(nodes, YSeq):
            continue

        nodes_by_id = {
            n.get("id").text: n
            for n in nodes.items
            if isinstance(n, YMap) and isinstance(n.get("id"), YScalar)
        }

        for node in nodes.items:
            if not isinstance(node, YMap) or node.get("kind") is not None:
                continue  # ordinary nodes only
            proc_ref = node.get("process")
            if not isinstance(proc_ref, YScalar):
                continue
            target_def = processes.get(proc_ref.text)
            target_sig = sigs.get(proc_ref.text)
            if not isinstance(target_def, YMap) or target_sig is None:
                continue
            params = set(process_type_params(target_def))
            if not params:
                continue  # not a generic process

            # Infer bindings from each bound input port's source value type.
            bindings: dict[str, TypeExpr] = {}
            for section in ("state", "bind"):
                m = node.get(section)
                if not isinstance(m, YMap):
                    continue
                for portname in m.keys():
                    port = target_sig.inputs.get(portname)
                    entry = m.get(portname)
                    if port is None or port.type_expr is None or not isinstance(entry, YMap):
                        continue
                    frm = entry.get("from")
                    if isinstance(frm, YScalar):
                        src = _source_type(frm.text, comp_sig, sigs, nodes_by_id)
                        _unify(port.type_expr, src, params, bindings)

            # Check each where-constraint against the inferred concrete type.
            where = target_def.get("where")
            if not isinstance(where, YSeq):
                continue
            for item in where.items:
                if not isinstance(item, YScalar):
                    continue
                m = _CONSTRAINT_RE.match(item.text)
                if not m:
                    continue  # malformed constraint reported at definition
                trait, param = m.group(1), m.group(2)
                concrete = bindings.get(param)
                if not isinstance(concrete, Atom):
                    continue  # could not infer a concrete atom; skip
                cname = concrete.name
                if trait == "Numeric":
                    satisfied = cname in ("Int", "Float")
                else:
                    satisfied = trait in implements.get(cname, set())
                if not satisfied:
                    diags.add(
                        errors.CONSTRAINT_NOT_SATISFIED,
                        f"{cname} does not satisfy {trait}<{param}>",
                        f"processes.{pname}.body.nodes.{node.get('id').text}",
                        at=node,
                    )


def check_generics(doc: YMap, diags: Diagnostics, env: TypeEnv, sigs: dict[str, ProcSig]) -> None:
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
                diags.add(errors.MISSING_TYPE_PARAM_DOMAIN, f"{name!r} needs a domain", f"{base}.type_params.{name}", at=tp_node.key_node(name))
            elif dom.text not in ("data", "object"):
                diags.add(errors.BAD_TYPE_PARAM_DOMAIN, f"invalid domain {dom.text!r}", f"{base}.type_params.{name}", at=dom)

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
                    diags.add(errors.MALFORMED_CONSTRAINT, "constraint must be a string", cpath, at=item)
                    continue
                m = _CONSTRAINT_RE.match(item.text)
                if not m:
                    diags.add(errors.MALFORMED_CONSTRAINT, f"malformed constraint {item.text!r}", cpath, at=item)
                    continue
                trait, param = m.group(1), m.group(2)
                # The constraint must target a declared parameter of this process.
                if param not in tp:
                    # Distinguish "constrained a concrete type" (a real, if
                    # disallowed, type name) from arbitrary garbage (spec 8.1).
                    if param in env.user_types or param in PRIMITIVE_TYPES or param in BUILTIN_TYPE_NAMES:
                        diags.add(errors.CONSTRAINT_ON_CONCRETE, f"constraint over concrete type {param!r}", cpath, at=item)
                    else:
                        diags.add(errors.MALFORMED_CONSTRAINT, f"{param!r} is not a type parameter", cpath, at=item)
                    continue
                # The trait must be `Numeric` (built-in) or a declared trait.
                if trait != "Numeric" and trait not in env.traits:
                    diags.add(errors.UNKNOWN_TRAIT, f"unknown trait {trait!r}", cpath, at=item)

    # Call-site instantiation: infer type arguments and check where-constraints.
    _check_instantiations(doc, diags, env, sigs)
