"""Object tracking completeness and linearity (spec 12, 13, 14, 15).

Intent: this is the central well-formedness property of v0 — every Object slot
must have exactly one explicit fate (input) or provenance (output), so Objects
are never implicitly created, lost, duplicated, or discarded. Two mechanisms:

  * **Atomic** processes declare Object behavior explicitly via an `objects`
    section (map / consume / create / transform), or via the
    `object_identity_map` inference when `objects` is omitted entirely
    (spec 15). That marker is declared under a process's `behavior`, a
    vocabulary of its own that this module also closes.
  * **Composite** processes derive Object behavior from the body graph and
    `returns` (spec 10.2, 13): every Object-bearing value must flow to exactly
    one consumer (outdegree 1), which is the linearity rule (spec 12.2), and
    every Object-bearing output port must be returned, or its provenance is
    unknown.

An Object-bearing output port is one of three things (spec 12.2): an ordinary
node's output, a structured node's *exposed* output, or the composite boundary
itself. All three are accounted for here; which outputs a structured node
exposes is :func:`structured_exposed_port`, shared with the type layer so the
two cannot disagree about what a mode exposes.

Granularity note: v0 defines fate/provenance at the *Object slot* level. This
implementation currently accounts at *port* level, which is exact for scalar
Object ports and whole-container transforms (the shapes v0 workflows use in
practice). Sub-slot correspondence for partial/nested-array rewiring is a
future refinement; where it matters the transform role-typing below still
guards the container structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.types import (
    ArrayT,
    Atom,
    TypeEnv,
    TypeExpr,
    TypeParseError,
    is_object_bearing,
    parse_type,
    process_type_params,
)
from ofplang.validate.yamlnode import YMap, YNode, YScalar, YSeq

# The one behavior marker v0 defines (spec 15). A process that omits `objects`
# entirely and declares it gets a same-name identity map inferred for its
# top-level Object-bearing ports.
OBJECT_IDENTITY_MAP = "object_identity_map"
BEHAVIOR_MARKERS = frozenset({OBJECT_IDENTITY_MAP})


def _has_behavior(proc: YMap, marker: str) -> bool:
    section = proc.get("behavior")
    return isinstance(section, YSeq) and any(
        isinstance(item, YScalar) and item.text == marker for item in section.items
    )


def check_behavior(doc: YMap, diags: Diagnostics) -> None:
    """Every `behavior` entry must name a marker v0 defines (spec 15)."""
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        section = proc.get("behavior")
        if not isinstance(section, YSeq):
            continue
        for i, item in enumerate(section.items):
            if isinstance(item, YScalar) and item.text not in BEHAVIOR_MARKERS:
                diags.add(
                    errors.UNKNOWN_BEHAVIOR,
                    f"unknown behavior marker {item.text!r}",
                    f"processes.{pname}.behavior[{i}]",
                    at=item,
                )


# --- Per-process signature -------------------------------------------------
@dataclass
class PortSig:
    """A resolved port: its parsed type, Object-bearing flag, and phase.

    ``phase`` is retained so the reference/graph layer can check phase-flow
    (a value may only flow to an equal-or-later phase, spec 6) without
    re-reading the tree.
    """

    type_expr: TypeExpr | None
    object_bearing: bool
    phase: str | None = None


@dataclass
class ProcSig:
    kind: str | None
    inputs: dict[str, PortSig] = field(default_factory=dict)
    outputs: dict[str, PortSig] = field(default_factory=dict)
    # Whether the process declares `type_params` (spec 8). A generic process's port
    # types name its own parameters, so they mean nothing until an invocation is
    # instantiated (spec 8.1) -- passes that match types structurally use this to tell
    # "compare these" from "leave this to the generics pass".
    generic: bool = False


def _port_sigs(ports: YNode | None, env: TypeEnv, tp: dict[str, str]) -> dict[str, PortSig]:
    """Resolve a ports mapping into name -> PortSig.

    Malformed/absent types resolve to non-Object-bearing so that Object tracking
    never cascades off a type error already reported by the type pass.
    """
    out: dict[str, PortSig] = {}
    if not isinstance(ports, YMap):
        return out
    for name in ports.keys():
        port = ports.get(name)
        expr = None
        ob = False
        phase = None
        if isinstance(port, YMap):
            tnode = port.get("type")
            if isinstance(tnode, YScalar) and tnode.is_str:
                try:
                    expr = parse_type(tnode.text)
                    ob = is_object_bearing(expr, env, tp)
                except TypeParseError:
                    expr = None
            pnode = port.get("phase")
            if isinstance(pnode, YScalar) and not pnode.is_null:
                phase = pnode.text
        out[name] = PortSig(type_expr=expr, object_bearing=ob, phase=phase)
    return out


def build_signatures(doc: YMap, env: TypeEnv) -> dict[str, ProcSig]:
    """Build every process's port signature once, for reuse by graph checks."""
    sigs: dict[str, ProcSig] = {}
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return sigs
    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        tp = process_type_params(proc)
        kind_node = proc.get("kind")
        kind = kind_node.text if isinstance(kind_node, YScalar) else None
        sigs[pname] = ProcSig(
            kind=kind,
            inputs=_port_sigs(proc.get("inputs"), env, tp),
            outputs=_port_sigs(proc.get("outputs"), env, tp),
            generic=proc.get("type_params") is not None,
        )
    return sigs


# The Boolean output a `do_while` node exposes of its own, true when it
# terminated by reaching `max_iterations` (spec 19.3). It is not a target
# process output: `exhausted` is a reserved name (2.4), so no target declares
# one, and the `outputs` section that shapes target outputs never lists it.
DO_WHILE_RESERVED_OUTPUT = "exhausted"


# --- Path parsing for objects declarations --------------------------------
def _parse_path(text: str) -> tuple[str, str] | None:
    """Parse an Object path 'inputs.X' / 'outputs.X' into (side, port).

    Returns ``None`` for anything not of that exact two-segment shape; callers
    map that to an `objects_path_not_found`-style error.
    """
    parts = text.split(".")
    if len(parts) == 2 and parts[0] in ("inputs", "outputs"):
        return parts[0], parts[1]
    return None


# --- Transform role tables (spec 14.4) -------------------------------------
# Each kind fixes an exact input-role and output-role set, and a role typing.
# The number against a role is how many Array layers that role wraps the shared
# element type T in, which is the whole of what v0's two kinds differ by: both
# are regroupings, so one side is nested one level deeper than the other and
# every role's T must unify within one entry.
_TRANSFORM_ROLES = {
    "array_flatten": ({"xss": 2}, {"xs": 1}),
    "array_unflatten": ({"xs": 1}, {"xss": 2}),
}


def _element_type(depth: int, expr: TypeExpr | None) -> TypeExpr | None:
    """Strip `depth` Array layers to get the T a role contributes to unification.

    ``None`` when the type is not an Array that deep -- the role's shape is wrong,
    which is a role type mismatch (spec 14.4.1)."""
    for _ in range(depth):
        if not isinstance(expr, ArrayT):
            return None
        expr = expr.elem
    return expr


def _validate_transform_entry(
    diags: Diagnostics,
    entry: YMap,
    sig: ProcSig,
    base: str,
    claimed_inputs: set[str],
    claimed_outputs: set[str],
) -> None:
    """Validate one transform entry and record which Object ports it accounts for.

    Ordering is deliberate: kind, then exact role set, then Object-bearing
    paths, then role typing. Each earlier failure short-circuits later checks so
    a single mistake yields a single, specific code. Regardless of validity we
    record the referenced Object ports as "claimed" so completeness does not
    *also* flag them as unaccounted.
    """
    kind_node = entry.get("kind")
    kind = kind_node.text if isinstance(kind_node, YScalar) else None

    inputs = entry.get("inputs")
    outputs = entry.get("outputs")
    in_roles = {k: inputs.get(k) for k in inputs.keys()} if isinstance(inputs, YMap) else {}
    out_roles = {k: outputs.get(k) for k in outputs.keys()} if isinstance(outputs, YMap) else {}

    # Record claimed Object ports from every path this entry mentions, up front,
    # so completeness accounting is stable even when the entry is invalid.
    def _record(role_map, claimed: set[str], side: str) -> dict[str, TypeExpr | None]:
        types: dict[str, TypeExpr | None] = {}
        for role, val in role_map.items():
            if isinstance(val, YScalar):
                parsed = _parse_path(val.text)
                if parsed and parsed[0] == side:
                    port = parsed[1]
                    ports = sig.inputs if side == "inputs" else sig.outputs
                    if port in ports:
                        if ports[port].object_bearing:
                            claimed.add(port)
                        types[role] = ports[port].type_expr
        return types

    in_types = _record(in_roles, claimed_inputs, "inputs")
    out_types = _record(out_roles, claimed_outputs, "outputs")

    # 1. Kind must be a defined v0 transform.
    if kind not in _TRANSFORM_ROLES:
        diags.add(
            errors.UNKNOWN_TRANSFORM_KIND,
            f"unknown transform kind {kind!r}",
            f"{base}.kind",
            at=entry,
        )
        return

    exp_in, exp_out = _TRANSFORM_ROLES[kind]

    # 2. Role names must match the kind's required set exactly (no missing/extra).
    if set(in_roles) != set(exp_in) or set(out_roles) != set(exp_out):
        diags.add(errors.INVALID_TRANSFORM_ROLES, f"invalid roles for {kind}", base, at=entry)
        return

    # 2.5 Every referenced path must name a declared port (spec 14.4.1, 4.4). A
    # malformed or non-existent path is reported here and short-circuits the
    # Object-bearing / typing checks, which cannot be judged against a path that
    # does not resolve to a port.
    any_missing = False
    for role_map, side in ((in_roles, "inputs"), (out_roles, "outputs")):
        for role, val in role_map.items():
            if isinstance(val, YScalar):
                parsed = _parse_path(val.text)
                if parsed is None or parsed[1] not in (
                    sig.inputs if parsed[0] == "inputs" else sig.outputs
                ):
                    diags.add(
                        errors.OBJECTS_PATH_NOT_FOUND,
                        f"objects path {val.text!r} does not name a declared port",
                        f"{base}.{side}.{role}",
                        at=val,
                    )
                    any_missing = True
    if any_missing:
        return

    # 3. Every referenced path must be Object-bearing (spec 14.4.1).
    all_ports_ob = True
    for role_map, side in ((in_roles, "inputs"), (out_roles, "outputs")):
        ports = sig.inputs if side == "inputs" else sig.outputs
        for val in role_map.values():
            if isinstance(val, YScalar):
                parsed = _parse_path(val.text)
                if parsed and parsed[1] in ports and not ports[parsed[1]].object_bearing:
                    all_ports_ob = False
    if not all_ports_ob:
        diags.add(errors.PURE_DATA_IN_TRANSFORM, "transform path is Pure Data", base, at=entry)
        return

    # 4. Role typing (spec 14.4.1): each role must be bound to an Array nested to
    # the depth the kind gives it, and every T left after stripping those layers
    # must unify to one type. A type that is not nested that deep, or a
    # unification conflict, is a role type mismatch.
    ts: list[TypeExpr] = []
    role_mismatch = False
    for role_types, exp in ((in_types, exp_in), (out_types, exp_out)):
        for role, depth in exp.items():
            expr = role_types.get(role)
            if expr is None:
                continue
            t = _element_type(depth, expr)
            if t is None:
                role_mismatch = True
                continue
            ts.append(t)
    if role_mismatch or any(t != ts[0] for t in ts[1:]):
        diags.add(
            errors.TRANSFORM_ROLE_TYPE_MISMATCH,
            f"inconsistent element type in {kind}",
            base,
            at=entry,
        )


# --- Atomic Object completeness --------------------------------------------
def _check_atomic(diags: Diagnostics, pname: str, proc: YMap, sig: ProcSig) -> None:
    base = f"processes.{pname}"

    obj_inputs = {n for n, s in sig.inputs.items() if s.object_bearing}
    obj_outputs = {n for n, s in sig.outputs.items() if s.object_bearing}

    # Resolve a port's declaration node for positioning diagnostics; fall back
    # to the process node when the port map is absent.
    inputs_map = proc.get("inputs")
    outputs_map = proc.get("outputs")

    def _in_at(name: str):
        return inputs_map.get(name) if isinstance(inputs_map, YMap) else proc

    def _out_at(name: str):
        return outputs_map.get(name) if isinstance(outputs_map, YMap) else proc

    objects = proc.get("objects")

    # `object_identity_map` inference applies only when `objects` is omitted
    # entirely (spec 15): infer a same-name identity map for top-level Object
    # ports. The marker is declared under `behavior`, which is a process's own
    # vocabulary and not the top-level `traits` that declare type traits.
    if objects is None:
        if _has_behavior(proc, OBJECT_IDENTITY_MAP):
            # Same-name Object input/output pairs are accounted; a leftover
            # Object port with no counterpart falls through to "incomplete".
            for name in list(obj_inputs):
                if name in obj_outputs:
                    obj_inputs.discard(name)
                    obj_outputs.discard(name)
        # Whatever remains is unaccounted.
        for name in sorted(obj_inputs):
            diags.add(
                errors.INCOMPLETE_OBJECTS,
                f"input {name!r} has no fate",
                f"{base}.inputs.{name}",
                at=_in_at(name),
            )
        for name in sorted(obj_outputs):
            diags.add(
                errors.INCOMPLETE_OBJECTS,
                f"output {name!r} has no provenance",
                f"{base}.outputs.{name}",
                at=_out_at(name),
            )
        return

    # Count fates (per Object input) and provenances (per Object output) across
    # the four declaration mechanisms. Counting (rather than boolean) lets us
    # distinguish "none" (incomplete) from "more than one" (conflicting).
    fates: dict[str, int] = dict.fromkeys(obj_inputs, 0)
    provs: dict[str, int] = dict.fromkeys(obj_outputs, 0)

    def _exists(parsed: tuple[str, str] | None) -> bool:
        """Whether a parsed path names a declared port on the side it names.

        A malformed path (``None``) or a path whose port is absent from the named
        side's port map is an `objects_path_not_found` error (spec 14.4.1, 4.4).
        A well-formed path to a real port on the *wrong* side is not "not found"
        here; it simply fails to account for its Object slot and surfaces as an
        incomplete fate/provenance below.
        """
        if parsed is None:
            return False
        side, port = parsed
        ports = sig.inputs if side == "inputs" else sig.outputs
        return port in ports

    def _not_found(text: str, path: str, at) -> None:
        diags.add(
            errors.OBJECTS_PATH_NOT_FOUND,
            f"objects path {text!r} does not name a declared port",
            path,
            at=at,
        )

    if isinstance(objects, YMap):
        # map: outputs.X (provenance) <- inputs.Y (fate). Cross-wiring allowed.
        map_node = objects.get("map")
        if isinstance(map_node, YMap):
            for out_path in map_node.keys():
                src = map_node.get(out_path)
                op = _parse_path(out_path)
                if not _exists(op):
                    _not_found(
                        out_path,
                        f"{base}.objects.map.{out_path}",
                        map_node.key_node(out_path),
                    )
                elif op is not None and op[0] == "outputs" and op[1] in provs:
                    provs[op[1]] += 1
                if isinstance(src, YScalar):
                    ip = _parse_path(src.text)
                    if not _exists(ip):
                        _not_found(src.text, f"{base}.objects.map.{out_path}", src)
                    elif ip is not None and ip[0] == "inputs" and ip[1] in fates:
                        fates[ip[1]] += 1

        # consume: input Object identities terminated here.
        consume = objects.get("consume")
        if isinstance(consume, YSeq):
            for item in consume.items:
                if isinstance(item, YScalar):
                    ip = _parse_path(item.text)
                    if not _exists(ip):
                        _not_found(item.text, f"{base}.objects.consume", item)
                    elif ip is not None and ip[0] == "inputs" and ip[1] in fates:
                        fates[ip[1]] += 1

        # create: new output Object identities.
        create = objects.get("create")
        if isinstance(create, YSeq):
            for item in create.items:
                if isinstance(item, YScalar):
                    op = _parse_path(item.text)
                    if not _exists(op):
                        _not_found(item.text, f"{base}.objects.create", item)
                    elif op is not None and op[0] == "outputs" and op[1] in provs:
                        provs[op[1]] += 1

        # transform: validated in detail, and its Object ports counted once.
        transform = objects.get("transform")
        if isinstance(transform, YSeq):
            claimed_in: set[str] = set()
            claimed_out: set[str] = set()
            for i, entry in enumerate(transform.items):
                if isinstance(entry, YMap):
                    _validate_transform_entry(
                        diags, entry, sig, f"{base}.objects.transform[{i}]", claimed_in, claimed_out
                    )
            for name in claimed_in:
                if name in fates:
                    fates[name] += 1
            for name in claimed_out:
                if name in provs:
                    provs[name] += 1

    # Emit completeness diagnostics. "map + consume on the same input" surfaces
    # here as a fate count of 2 -> multiple_fates (spec 13.1 example).
    for name in sorted(fates):
        if fates[name] == 0:
            diags.add(
                errors.INCOMPLETE_OBJECTS,
                f"input {name!r} has no fate",
                f"{base}.inputs.{name}",
                at=_in_at(name),
            )
        elif fates[name] > 1:
            diags.add(
                errors.MULTIPLE_FATES,
                f"input {name!r} has multiple fates",
                f"{base}.inputs.{name}",
                at=_in_at(name),
            )
    for name in sorted(provs):
        if provs[name] == 0:
            diags.add(
                errors.INCOMPLETE_OBJECTS,
                f"output {name!r} has no provenance",
                f"{base}.outputs.{name}",
                at=_out_at(name),
            )
        elif provs[name] > 1:
            diags.add(
                errors.MULTIPLE_PROVENANCES,
                f"output {name!r} has multiple provenances",
                f"{base}.outputs.{name}",
                at=_out_at(name),
            )


# --- Structured node output exposure (spec 17-21) --------------------------
# How a structured node reshapes the target output it exposes: as the target's
# own type, or collected into an Array. Two questions read this table -- what
# type a downstream binding sees (the matching layer) and whether the exposed
# value is an Object-bearing output port that linearity governs (below) -- so
# the rule lives in one place and both ask it the same way.
EXPOSED_ELEMENT = "element"
EXPOSED_ARRAY = "array"


def _text_of(node: YNode | None) -> str | None:
    return node.text if isinstance(node, YScalar) else None


def _output_mode(outputs: YNode | None, name: str) -> str | None:
    """The `mode` an `outputs` section gives port `name` (spec 21)."""
    if not isinstance(outputs, YMap):
        return None
    entry = outputs.get(name)
    return _text_of(entry.get("mode")) if isinstance(entry, YMap) else None


def _arm_target(node: YMap, arm: str, sigs: dict[str, ProcSig]) -> ProcSig | None:
    proc = node.get(arm)
    name = _text_of(proc.get("process")) if isinstance(proc, YMap) else None
    return sigs.get(name) if name is not None else None


def _branch_common(node: YMap, name: str, port: PortSig, sigs: dict[str, ProcSig]) -> bool:
    """Whether both arms declare output `name` -- a branch exposes only a *common*
    output (spec 20.1), so a one-sided one is not a value downstream at all. That it
    is one-sided is reported by the node pass; treating it as exposed here would only
    add a second complaint about the same mistake.

    An arm that does not resolve to a process is left as common: the arm itself is
    what is wrong, and hiding the output would suppress the checks that say so.
    """
    else_arm = node.get("else")
    if isinstance(else_arm, YMap):
        else_target = _arm_target(node, "else", sigs)
        return else_target is None or name in else_target.outputs
    # An omitted `else` is an implicit identity arm: it re-exposes each
    # Object-bearing branch argument as a same-name output, and nothing else
    # (spec 20.3).
    args = node.get("args")
    return port.object_bearing and isinstance(args, YMap) and name in args.keys()


def structured_exposed_port(
    node: YMap, name: str, sigs: dict[str, ProcSig]
) -> tuple[str, PortSig] | None:
    """How a structured node exposes its target's output `name`, and that port.

    ``None`` means the output is exposed as nothing at all: it is dropped, it is
    unlisted under an omitted `outputs` section (spec 18.3, 19.2, 20.3), or the
    target does not declare it. There is then no value downstream, so neither a
    type nor an outdegree applies to it.
    """
    kind = _text_of(node.get("kind"))
    outputs = node.get("outputs")

    if kind == "do_while" and name == DO_WHILE_RESERVED_OUTPUT:
        # The node's own output rather than the target's, so no mode shapes it
        # and no `outputs` entry lists it (spec 19.3).
        return (EXPOSED_ELEMENT, PortSig(type_expr=Atom("Bool"), object_bearing=False,
                                         phase="data"))

    if kind == "branch":
        # Both arms declare the output with the same type (spec 20.1 rule 4,
        # pinned by the node pass), so the `then` arm's declaration stands for
        # it. With `outputs` omitted only the Object-bearing commons are
        # exposed; Data outputs are dropped (spec 20.3).
        target = _arm_target(node, "then", sigs)
        port = target.outputs.get(name) if target is not None else None
        if port is None or not _branch_common(node, name, port, sigs):
            return None
        if isinstance(outputs, YMap):
            exposed = _output_mode(outputs, name) == "common"
        else:
            exposed = port.object_bearing
        return (EXPOSED_ELEMENT, port) if exposed else None

    target = sigs.get(_text_of(node.get("process")) or "")
    port = target.outputs.get(name) if target is not None else None
    if port is None:
        return None

    if kind == "map":
        # Every target output p: T is collected as Array<T>; v0 defines no
        # `map.outputs` to shape it with (spec 17, 21).
        return (EXPOSED_ARRAY, port)

    if kind not in ("fold", "do_while"):
        return None

    if isinstance(outputs, YMap):
        mode = _output_mode(outputs, name)
    else:
        # Defaults: carry outputs are exposed as carry, everything else is
        # dropped (spec 18.3, 19.2).
        carry = node.get("carry")
        mode = "carry" if isinstance(carry, YMap) and name in carry.keys() else None

    if mode == "collect":
        return (EXPOSED_ARRAY, port)
    if mode in ("carry", "last"):
        # A carry output is the threaded value itself, which spec 16 requires to
        # be the carried port's type; `last` is one per-invocation value.
        return (EXPOSED_ELEMENT, port)
    return None


def _exposed_object_outputs(node: YMap, sigs: dict[str, ProcSig]) -> list[str]:
    """The names of the Object-bearing output ports a structured node exposes.

    An Object-bearing output a node exposes *nothing* for is not silently lost:
    the node pass reports it where the kind forbids it (spec 18.3, 19.1, 20.1),
    which is a different mistake from an exposed output nobody connects.
    """
    kind = _text_of(node.get("kind"))
    target = (
        _arm_target(node, "then", sigs)
        if kind == "branch"
        else sigs.get(_text_of(node.get("process")) or "")
    )
    if target is None:
        return []
    names = []
    for name in target.outputs:
        exposed = structured_exposed_port(node, name, sigs)
        if exposed is not None and exposed[1].object_bearing:
            names.append(name)
    return names


# --- Composite linearity ---------------------------------------------------
def _ref_target(text: str) -> tuple[str, str] | None:
    """Parse a body dataflow reference 'inputs.X' or 'node.output' (spec 2.6.1)."""
    parts = text.split(".")
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


def _collect_refs(body: YMap) -> list[tuple[str, str]]:
    """Every body dataflow source referenced by node bindings and returns.

    Each reference contributes one unit of outdegree to its source. We gather
    from all binding sections (state/bind/carry/args/each), branch conditions,
    and returns — anywhere a `from:` can name a body-visible value.
    """
    refs: list[tuple[str, str]] = []

    def _scan_binding_map(m: YNode | None) -> None:
        # A binding section maps port name -> {from|value}; only `from` refers
        # to another value (a `value` literal is a fresh Pure Data constant).
        if not isinstance(m, YMap):
            return
        for k in m.keys():
            entry = m.get(k)
            if isinstance(entry, YMap):
                frm = entry.get("from")
                if isinstance(frm, YScalar):
                    tgt = _ref_target(frm.text)
                    if tgt:
                        refs.append(tgt)

    nodes = body.get("nodes")
    if isinstance(nodes, YSeq):
        for item in nodes.items:
            if not isinstance(item, YMap):
                continue
            for section in ("state", "bind", "carry", "args", "each"):
                _scan_binding_map(item.get(section))
            # branch condition is itself a body dataflow reference.
            cond = item.get("condition")
            if isinstance(cond, YMap):
                frm = cond.get("from")
                if isinstance(frm, YScalar):
                    tgt = _ref_target(frm.text)
                    if tgt:
                        refs.append(tgt)

    # returns: internal source -> composite boundary (counts as a use).
    returns = body.get("returns")
    if isinstance(returns, YMap):
        for k in returns.keys():
            entry = returns.get(k)
            if isinstance(entry, YMap):
                frm = entry.get("from")
                if isinstance(frm, YScalar):
                    tgt = _ref_target(frm.text)
                    if tgt:
                        refs.append(tgt)

    return refs


def _check_composite(
    diags: Diagnostics, pname: str, proc: YMap, sig: ProcSig, sigs: dict[str, ProcSig]
) -> None:
    base = f"processes.{pname}"
    body = proc.get("body")
    if not isinstance(body, YMap):
        return

    # Enumerate Object-bearing value sources available in this body: the
    # composite's own Object inputs, plus every node's Object outputs. A
    # structured node contributes the outputs it *exposes*, reshaped by its mode
    # (spec 12.2 lists such an output as a connection target in its own right);
    # what it exposes nothing for is not a value here at all.
    # (owner, name) -> (display path, the node to point a diagnostic at). The node is
    # what the reader has to go and change: the port declaration for a composite's own
    # input, the producing node for a node output (the output itself is declared on the
    # target process, which is elsewhere).
    sources: dict[tuple[str, str], tuple[str, YNode | None]] = {}
    declared_inputs = proc.get("inputs")
    for name, s in sig.inputs.items():
        if s.object_bearing:
            at = declared_inputs.get(name) if isinstance(declared_inputs, YMap) else None
            sources[("inputs", name)] = (f"{base}.inputs.{name}", at)

    nodes = body.get("nodes")
    if isinstance(nodes, YSeq):
        for item in nodes.items:
            if not isinstance(item, YMap):
                continue
            nid = item.get("id")
            proc_ref = item.get("process")
            kind = item.get("kind")
            if not isinstance(nid, YScalar):
                continue
            if kind is not None:
                # A structured node reshapes its target's outputs, so which of
                # them are Object-bearing values here follows from the exposure.
                onames = _exposed_object_outputs(item, sigs)
            elif isinstance(proc_ref, YScalar) and proc_ref.text in sigs:
                onames = [
                    n for n, s in sigs[proc_ref.text].outputs.items() if s.object_bearing
                ]
            else:
                continue
            for oname in onames:
                sources[(nid.text, oname)] = (
                    f"{base}.body.nodes.{nid.text}.{oname}",
                    nid,
                )

    # Count outdegree of each Object source. Linearity requires exactly one use
    # (spec 12.2): zero is an unused Object output, more than one is fan-out.
    refs = _collect_refs(body)
    counts: dict[tuple[str, str], int] = dict.fromkeys(sources, 0)
    for tgt in refs:
        if tgt in counts:
            counts[tgt] += 1

    for src, n in counts.items():
        path, at = sources[src]
        if n == 0:
            diags.add(errors.OBJECT_OUTPUT_UNUSED, "Object-bearing value is unused", path, at=at)
        elif n > 1:
            diags.add(errors.OBJECT_FANOUT, "Object-bearing value fans out", path, at=at)

    # Provenance at the boundary: a composite has no `objects` section to declare
    # a `create` with (spec 10.2), so an Object-bearing output port is explained
    # only by the `body.returns` entry that connects it. Without one the
    # composite promises an Object nothing in the body produced -- unknown
    # provenance (spec 13). The Pure Data side is deliberately untouched: v0
    # states this requirement for Object tracking completeness only.
    returns = body.get("returns")
    returned = set(returns.keys()) if isinstance(returns, YMap) else set()
    declared_outputs = proc.get("outputs")
    for name, s in sig.outputs.items():
        if s.object_bearing and name not in returned:
            diags.add(
                errors.INCOMPLETE_OBJECTS,
                f"output {name!r} has no provenance",
                f"{base}.outputs.{name}",
                at=declared_outputs.get(name) if isinstance(declared_outputs, YMap) else proc,
            )


def check_objects(
    doc: YMap, diags: Diagnostics, env: TypeEnv, sigs: dict[str, ProcSig] | None = None
) -> None:
    """`sigs` are the process signatures the validator already built for the other
    graph-level passes; they are rebuilt here only when a caller does not have them."""
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    if sigs is None:
        sigs = build_signatures(doc, env)
    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        sig = sigs.get(pname)
        if sig is None:
            continue
        # Script processes are Pure Data and do not participate in Object
        # tracking (spec 22.1); the script pass owns their port validation, so
        # skip them here to avoid reporting their Object ports as incompleteness.
        if proc.get("script") is not None:
            continue
        if sig.kind == "atomic":
            _check_atomic(diags, pname, proc, sig)
        elif sig.kind == "composite":
            _check_composite(diags, pname, proc, sig, sigs)
