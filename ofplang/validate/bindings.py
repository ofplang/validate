"""Binding type compatibility and generic instantiation (spec 8.1, 11.1, 20, 21).

Intent: every binding connects a value to a port, and one walk answers everything
that needs both ends of it. Two questions that look separate are the same question:

  * does this value's type match the port (spec 11.1)? and
  * what does this invocation instantiate the target's type parameters to (spec 8.1)?

v0 defines the second and derives the first from it — matching *is* the structural
relation of spec 8.1, which reduces to identity of type expressions when no type
parameter is involved. So a binding to an ordinary process and a binding to a
generic one differ only in whether the parameter map handed to
:func:`~ofplang.validate.matching.match` is empty, and doing them in one pass is
what keeps a single mistake from being reported twice — a value that disagrees with
an already-inferred parameter is a `conflicting_inference`, not also a mismatch.

What this pass reports, by where the value sits:

    state / bind / carry   binding_type_mismatch
    each                   binding_type_mismatch, or each_source_not_array
    args                   arg_type_mismatch          (matched against every arm)
    branch condition       bad_condition_type
    body.returns           return_type_mismatch
    a `value` literal      literal_type_mismatch, or literal_on_object_port

and, per invocation of a generic process:

    conflicting_inference / uninferable_type_param / constraint_not_satisfied

Reshaping is accounted for on both sides. An `each` source is traversed
element-wise, so what must match the port is its element type — equivalently, the
source must match `Array<port type>`, which is how it is written here so that
inference through a traversal (`Array<T>` against `Array<Cup>`) falls out of the
same recursion. A structured node's *output* is shaped by its mode, so a `map`
output is an Array and a dropped one is not a value at all (spec 21, resolved by
`matching.source_type`).

What is left alone, deliberately:

  * a value produced by a generic process — its declared type names that process's
    own parameter, which says nothing until *that* invocation is instantiated, and
    substituting through it is not modelled;
  * a nominal Data port's literal, whose YAML shape is not decidable here; and
  * an arm that declares no port of an argument's name at all, which is a missing
    port rather than a type mismatch and has no code yet.
"""

from __future__ import annotations

from collections.abc import Callable

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.matching import (
    MatchResult,
    arm_sig,
    is_primitive_only,
    literal_conforms,
    match,
    parse_constraint,
    producer_is_generic,
    satisfies,
    source_type,
)
from ofplang.validate.objects import ProcSig
from ofplang.validate.types import (
    ArrayT,
    Atom,
    TypeEnv,
    TypeExpr,
    is_object_bearing,
    process_type_params,
    resolve_error,
)
from ofplang.validate.yamlnode import YMap, YNode, YScalar, YSeq

# Binding sections that carry `from`/`value` source entries, in a fixed order so
# inference visits an invocation's ports the same way every run.
_SECTIONS = ("state", "bind", "carry", "each", "args")


def _show(expr: TypeExpr) -> str:
    """A type expression back in v0 source form, for a diagnostic message."""
    return f"Array<{_show(expr.elem)}>" if isinstance(expr, ArrayT) else expr.name


def _usable(expr: TypeExpr | None, env: TypeEnv, params: dict[str, str]) -> bool:
    """Whether a type can be matched against: present, and resolving to real names.

    `params` are the type parameters in scope *for that type* — a target port's are
    the target's own plus the enclosing composite's, a source's are only the
    enclosing composite's. Getting that wrong silently disables the check, since an
    unresolvable type is skipped: it was already reported by the type pass, and
    comparing it would only add a second, less useful diagnostic for one mistake."""
    return expr is not None and resolve_error(expr, env, params) is None


class _Invocation:
    """One target of one node: its parameters, and what they have been inferred to.

    A branch has two of these, one per arm (spec 20): each arm declares its own type
    parameters, so an argument matched against both must not let one arm's inference
    leak into the other's."""

    def __init__(self, target: ProcSig | None, definition: YNode | None) -> None:
        self.sig = target
        self.definition = definition
        self.flexible = process_type_params(definition) if isinstance(definition, YMap) else {}
        self.bindings: dict[str, TypeExpr] = {}
        self.conflicts: set[str] = set()
        self.mismatched = False
        # Ports whose value is a literal, and so determines no type argument
        # (spec 8.1, 11.1.1); and ports whose source this pass cannot resolve.
        self.literal_ports: set[str] = set()
        self.indeterminate_ports: set[str] = set()


def _rigid_where(proc: YMap, rigid: dict[str, str]) -> set[tuple[str, str]]:
    """The constraints the enclosing process declares over its own parameters.

    A flexible parameter inferred to be a rigid one has no type to look a trait up
    on, so its constraint is discharged from this instead (spec 8.1)."""
    where = proc.get("where")
    if not isinstance(where, YSeq):
        return set()
    out: set[tuple[str, str]] = set()
    for item in where.items:
        if isinstance(item, YScalar):
            parsed = parse_constraint(item.text)
            if parsed is not None and parsed[1] in rigid:
                out.add(parsed)
    return out


def _check_entry(
    diags: Diagnostics,
    entry: YMap,
    want: TypeExpr,
    path: str,
    code: str,
    *,
    env: TypeEnv,
    rigid: dict[str, str],
    resolve: Callable[[str], TypeExpr | None],
    inv: _Invocation | None = None,
    port: str = "",
    array_code: str | None = None,
) -> None:
    """Match one source entry against the port type `want`.

    `inv`, when given, is the invocation this port belongs to: the match may infer
    one of its type parameters, and what happened is recorded there so the
    per-invocation checks afterwards know whether a parameter went uninferred
    because nothing could determine it or because the binding was simply wrong.

    `array_code` replaces `code` when `want` is an Array and the source is not one at
    all. That is the `each` case: "this is not a collection" is a different mistake
    from "this collection holds the wrong thing".
    """
    value = entry.get("value")
    if value is not None:
        if inv is not None:
            inv.literal_ports.add(port)
        # A literal is Pure Data and cannot introduce an Object identity (spec 13),
        # so no literal is right for an Object-bearing port -- a different failure
        # from writing the wrong one, hence its own code.
        if is_object_bearing(want, env, rigid):
            diags.add(
                errors.LITERAL_ON_OBJECT_PORT,
                f"a literal cannot be bound to the Object-bearing port type {_show(want)}",
                path,
                at=value,
            )
        # Otherwise: only a primitive-only port type has a decidable YAML shape
        # (spec 11.1.1); a nominal Data port's literal is left unchecked.
        elif is_primitive_only(want) and not literal_conforms(want, value):
            diags.add(
                errors.LITERAL_TYPE_MISMATCH,
                f"literal does not conform to the port type {_show(want)}",
                path,
                at=value,
            )
        return

    frm = entry.get("from")
    if not isinstance(frm, YScalar):
        return
    got = resolve(frm.text)
    if not _usable(got, env, rigid):
        if inv is not None:
            inv.indeterminate_ports.add(port)
        return
    assert got is not None  # _usable

    flexible = inv.flexible if inv is not None else {}
    bindings = inv.bindings if inv is not None else {}
    result = match(want, got, env=env, flexible=flexible, rigid=rigid, bindings=bindings)
    if result is MatchResult.OK:
        return
    if result is MatchResult.CONFLICT:
        # The parameter would have to be two things at once. That is a fact about the
        # invocation, not about this one binding, and it is reported once below.
        assert inv is not None  # only a flexible parameter can conflict
        for name in _params_in(want, inv.flexible):
            if inv.bindings.get(name) != got:
                inv.conflicts.add(name)
        return
    if inv is not None:
        inv.mismatched = True
    if array_code is not None and isinstance(want, ArrayT) and not isinstance(got, ArrayT):
        diags.add(
            array_code,
            f"an each source must be an Array; {_show(got)} cannot be traversed",
            path,
            at=frm,
        )
        return
    diags.add(
        code,
        f"value of type {_show(got)} bound to a port of type {_show(want)}",
        path,
        at=frm,
    )


def _params_in(expr: TypeExpr, flexible: dict[str, str]) -> set[str]:
    """The flexible parameters a port type mentions."""
    if isinstance(expr, ArrayT):
        return _params_in(expr.elem, flexible)
    return {expr.name} if expr.name in flexible else set()


def _report_instantiation(
    diags: Diagnostics,
    inv: _Invocation,
    path: str,
    at,
    *,
    env: TypeEnv,
    rigid: dict[str, str],
    rigid_where: set[tuple[str, str]],
) -> None:
    """The per-invocation findings of spec 8.1, once every port has been matched."""
    if inv.sig is None or not inv.flexible:
        return

    for param in sorted(inv.conflicts):
        diags.add(
            errors.CONFLICTING_INFERENCE,
            f"type parameter {param!r} infers incompatible types",
            path,
            at=at,
        )

    # Which input ports mention each parameter, so an uninferred one can be
    # explained by what its ports were bound to.
    param_ports: dict[str, set[str]] = {}
    for portname, port in inv.sig.inputs.items():
        if port.type_expr is None:
            continue
        for name in _params_in(port.type_expr, inv.flexible):
            param_ports.setdefault(name, set()).add(portname)

    for param in sorted(inv.flexible):
        if param in inv.bindings or param in inv.conflicts:
            continue
        ports = param_ports.get(param, set())
        # No port at all is `type_param_not_in_input`, reported at the definition.
        # A port this pass could not resolve leaves the answer unknown, and a port
        # that mismatched already has its own diagnostic. What remains is a
        # parameter every one of whose ports was given a literal, which determines
        # no type argument (spec 8.1) -- so it genuinely cannot be inferred.
        if not ports or ports & inv.indeterminate_ports or inv.mismatched:
            continue
        if ports <= inv.literal_ports:
            diags.add(
                errors.UNINFERABLE_TYPE_PARAM,
                f"type parameter {param!r} cannot be inferred: "
                f"port(s) {sorted(ports)} are bound to literals, which determine no type",
                path,
                at=at,
            )

    # `where` constraints, against what each parameter turned out to be. Skipped
    # entirely for an invocation that mismatched: the inference is then partial, and
    # one mistake should not produce a second, derived diagnostic.
    where = inv.definition.get("where") if isinstance(inv.definition, YMap) else None
    if inv.mismatched or not isinstance(where, YSeq):
        return
    for item in where.items:
        if not isinstance(item, YScalar):
            continue
        parsed = parse_constraint(item.text)
        if parsed is None:
            continue  # malformed: reported at the definition
        trait, param = parsed
        if param in inv.conflicts:
            continue  # ambiguous inference already reported
        concrete = inv.bindings.get(param)
        if concrete is None:
            continue  # never inferred; reported above, or indeterminate
        if not satisfies(
            trait,
            concrete,
            implements=env.implements,
            rigid=rigid,
            rigid_where=rigid_where,
        ):
            detail = (
                f"the enclosing process does not declare {trait}<{_show(concrete)}>"
                if isinstance(concrete, Atom) and concrete.name in rigid
                else f"{_show(concrete)} does not implement {trait}"
            )
            diags.add(
                errors.CONSTRAINT_NOT_SATISFIED,
                f"{detail}, so {trait}<{param}> is not satisfied",
                path,
                at=at,
            )


def _targets(node: YMap, kind: str | None, sigs, processes: YMap) -> list[_Invocation]:
    """The invocation(s) a node makes: one per branch arm (spec 20), otherwise one."""
    if kind == "branch":
        out = []
        for arm in ("then", "else"):
            arm_node = node.get(arm)
            proc = arm_node.get("process") if isinstance(arm_node, YMap) else None
            if isinstance(proc, YScalar):
                out.append(_Invocation(arm_sig(node, arm, sigs), processes.get(proc.text)))
        return out
    proc = node.get("process")
    if not isinstance(proc, YScalar):
        return []
    return [_Invocation(sigs.get(proc.text), processes.get(proc.text))]


def _check_composite(
    diags: Diagnostics,
    pname: str,
    proc: YMap,
    sig: ProcSig,
    sigs: dict[str, ProcSig],
    processes: YMap,
    env: TypeEnv,
) -> None:
    body = proc.get("body")
    if not isinstance(body, YMap):
        return
    base = f"processes.{pname}.body"
    # This composite's own type parameters are *rigid* inside its body (spec 8.1):
    # they stand for types already fixed by whoever instantiates it, so they compare
    # by name rather than being inferred -- and the constraints it declares over them
    # are what a callee's constraints get discharged against.
    rigid = process_type_params(proc)
    rigid_where = _rigid_where(proc, rigid)

    nodes = body.get("nodes")
    node_items = [
        n for n in (nodes.items if isinstance(nodes, YSeq) else []) if isinstance(n, YMap)
    ]
    nodes_by_id: dict[str, YMap] = {}
    for node in node_items:
        id_node = node.get("id")
        if isinstance(id_node, YScalar):
            nodes_by_id[id_node.text] = node

    def resolve(text: str) -> TypeExpr | None:
        """The type a reference denotes, or None where matching must stand down."""
        if producer_is_generic(text, sigs, nodes_by_id):
            return None
        return source_type(text, sig, sigs, nodes_by_id)

    for node in node_items:
        nid_node = node.get("id")
        nid = nid_node.text if isinstance(nid_node, YScalar) else "?"
        kind_node = node.get("kind")
        kind = kind_node.text if isinstance(kind_node, YScalar) else None

        # A branch argument is matched against every arm, but reported once: the
        # author has one thing to fix whether the value is wrong or the two arms
        # disagree about the port it feeds.
        reported_args: set[str] = set()

        for inv in _targets(node, kind, sigs, processes):
            if inv.sig is None:
                continue
            # `args` belongs to a branch and is matched against every arm; every
            # other section belongs to the node's single target.
            sections = ("args",) if kind == "branch" else _SECTIONS
            for section in sections:
                m = node.get(section)
                if not isinstance(m, YMap):
                    continue
                for portname in m.keys():
                    entry = m.get(portname)
                    port = inv.sig.inputs.get(portname)
                    if not isinstance(entry, YMap) or port is None:
                        continue
                    if section == "args" and portname in reported_args:
                        continue
                    # An `each` source is traversed element-wise, so the source
                    # itself must be Array<port type> (spec 11.1, 17, 18). Writing
                    # it that way also makes inference through a traversal fall out
                    # of the same recursion.
                    elementwise = section == "each"
                    want = port.type_expr
                    if not _usable(want, env, {**inv.flexible, **rigid}):
                        continue
                    assert want is not None  # _usable
                    before = len(diags.items)
                    _check_entry(
                        diags,
                        entry,
                        ArrayT(want) if elementwise else want,
                        f"{base}.nodes.{nid}.{section}.{portname}",
                        errors.ARG_TYPE_MISMATCH if section == "args"
                        else errors.BINDING_TYPE_MISMATCH,
                        env=env,
                        rigid=rigid,
                        resolve=resolve,
                        inv=inv,
                        port=portname,
                        array_code=errors.EACH_SOURCE_NOT_ARRAY if elementwise else None,
                    )
                    if section == "args" and len(diags.items) > before:
                        reported_args.add(portname)
            _report_instantiation(
                diags,
                inv,
                f"{base}.nodes.{nid}",
                node,
                env=env,
                rigid=rigid,
                rigid_where=rigid_where,
            )

        # A branch selects an arm, so its condition must be Boolean Data (spec 20).
        if kind == "branch":
            cond = node.get("condition")
            frm = cond.get("from") if isinstance(cond, YMap) else None
            if isinstance(frm, YScalar):
                got = resolve(frm.text)
                if got is not None and got != Atom("Bool"):
                    diags.add(
                        errors.BAD_CONDITION_TYPE,
                        f"branch condition is {_show(got)}, not Bool",
                        f"{base}.nodes.{nid}.condition",
                        at=frm,
                    )

    # A returned value must match the composite output port it is connected to
    # (spec 11.1, 12.3). That port may be typed by one of this composite's own
    # (rigid) parameters, which is why `rigid` is in scope for the match.
    returns = body.get("returns")
    if isinstance(returns, YMap):
        for rname in returns.keys():
            entry = returns.get(rname)
            port = sig.outputs.get(rname)
            if not isinstance(entry, YMap) or port is None:
                continue
            if not _usable(port.type_expr, env, rigid):
                continue
            assert port.type_expr is not None  # _usable
            _check_entry(
                diags,
                entry,
                port.type_expr,
                f"{base}.returns.{rname}",
                errors.RETURN_TYPE_MISMATCH,
                env=env,
                rigid=rigid,
                resolve=resolve,
            )


def check_bindings(
    doc: YMap, diags: Diagnostics, sigs: dict[str, ProcSig], env: TypeEnv
) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    for pname in processes.keys():
        proc = processes.get(pname)
        sig = sigs.get(pname)
        if isinstance(proc, YMap) and sig is not None and sig.kind == "composite":
            _check_composite(diags, pname, proc, sig, sigs, processes, env)
