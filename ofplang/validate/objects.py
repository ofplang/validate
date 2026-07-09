"""Object tracking completeness and linearity (spec 12, 13, 14, 15).

Intent: this is the central well-formedness property of v0 — every Object slot
must have exactly one explicit fate (input) or provenance (output), so Objects
are never implicitly created, lost, duplicated, or discarded. Two mechanisms:

  * **Atomic** processes declare Object behavior explicitly via an `objects`
    section (map / consume / create / transform), or via the `elidable_iso`
    inference when `objects` is omitted entirely (spec 15).
  * **Composite** processes derive Object behavior from the body graph: every
    Object-bearing value must flow to exactly one consumer (outdegree 1), which
    is the linearity rule (spec 12.2).

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
    TypeEnv,
    TypeExpr,
    TypeParseError,
    is_object_bearing,
    parse_type,
    process_type_params,
)
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode


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
        )
    return sigs


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
# 'array' marks a role whose type must be Array<T>; 'elem' marks a bare T. All
# 'elem'/element-of-'array' types must unify to the same T within one entry.
_TRANSFORM_ROLES = {
    "array_uncons": ({"xs": "array"}, {"head": "elem", "tail": "array"}),
    "array_cons": ({"head": "elem", "tail": "array"}, {"xs": "array"}),
    "array_reverse": ({"xs": "array"}, {"ys": "array"}),
}


def _element_type(role_kind: str, expr: TypeExpr | None) -> TypeExpr | None:
    """Extract the T that a role contributes to unification, or None on shape
    mismatch (e.g. an 'array' role whose path is not actually an Array)."""
    if expr is None:
        return None
    if role_kind == "array":
        return expr.elem if isinstance(expr, ArrayT) else None
    return expr  # 'elem' role: the type itself is T


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
        diags.add(errors.UNKNOWN_TRANSFORM_KIND, f"unknown transform kind {kind!r}", f"{base}.kind", at=entry)
        return

    exp_in, exp_out = _TRANSFORM_ROLES[kind]

    # 2. Role names must match the kind's required set exactly (no missing/extra).
    if set(in_roles) != set(exp_in) or set(out_roles) != set(exp_out):
        diags.add(errors.INVALID_TRANSFORM_ROLES, f"invalid roles for {kind}", base, at=entry)
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

    # 4. Role typing: all element types must unify to a single T (spec 14.4.1).
    ts: list[TypeExpr] = []
    for role, rkind in exp_in.items():
        t = _element_type(rkind, in_types.get(role))
        if t is not None:
            ts.append(t)
    for role, rkind in exp_out.items():
        t = _element_type(rkind, out_types.get(role))
        if t is not None:
            ts.append(t)
    if any(t != ts[0] for t in ts[1:]):
        diags.add(errors.TRANSFORM_ROLE_TYPE_MISMATCH, f"inconsistent element type in {kind}", base, at=entry)


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

    # `elidable_iso` inference applies only when `objects` is omitted entirely
    # (spec 15): infer a same-name identity map for top-level Object ports.
    if objects is None:
        traits_node = proc.get("traits")
        is_elidable = isinstance(traits_node, YSeq) and any(
            isinstance(t, YScalar) and t.text == "elidable_iso" for t in traits_node.items
        )
        if is_elidable:
            # Same-name Object input/output pairs are accounted; a leftover
            # Object port with no counterpart falls through to "incomplete".
            for name in list(obj_inputs):
                if name in obj_outputs:
                    obj_inputs.discard(name)
                    obj_outputs.discard(name)
        # Whatever remains is unaccounted.
        for name in sorted(obj_inputs):
            diags.add(errors.INCOMPLETE_OBJECTS, f"input {name!r} has no fate", f"{base}.inputs.{name}", at=_in_at(name))
        for name in sorted(obj_outputs):
            diags.add(errors.INCOMPLETE_OBJECTS, f"output {name!r} has no provenance", f"{base}.outputs.{name}", at=_out_at(name))
        return

    # Count fates (per Object input) and provenances (per Object output) across
    # the four declaration mechanisms. Counting (rather than boolean) lets us
    # distinguish "none" (incomplete) from "more than one" (conflicting).
    fates: dict[str, int] = {n: 0 for n in obj_inputs}
    provs: dict[str, int] = {n: 0 for n in obj_outputs}

    if isinstance(objects, YMap):
        # map: outputs.X (provenance) <- inputs.Y (fate). Cross-wiring allowed.
        map_node = objects.get("map")
        if isinstance(map_node, YMap):
            for out_path in map_node.keys():
                src = map_node.get(out_path)
                op = _parse_path(out_path)
                if op and op[0] == "outputs" and op[1] in provs:
                    provs[op[1]] += 1
                if isinstance(src, YScalar):
                    ip = _parse_path(src.text)
                    if ip and ip[0] == "inputs" and ip[1] in fates:
                        fates[ip[1]] += 1

        # consume: input Object identities terminated here.
        consume = objects.get("consume")
        if isinstance(consume, YSeq):
            for item in consume.items:
                if isinstance(item, YScalar):
                    ip = _parse_path(item.text)
                    if ip and ip[0] == "inputs" and ip[1] in fates:
                        fates[ip[1]] += 1

        # create: new output Object identities.
        create = objects.get("create")
        if isinstance(create, YSeq):
            for item in create.items:
                if isinstance(item, YScalar):
                    op = _parse_path(item.text)
                    if op and op[0] == "outputs" and op[1] in provs:
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
            diags.add(errors.INCOMPLETE_OBJECTS, f"input {name!r} has no fate", f"{base}.inputs.{name}", at=_in_at(name))
        elif fates[name] > 1:
            diags.add(errors.MULTIPLE_FATES, f"input {name!r} has multiple fates", f"{base}.inputs.{name}", at=_in_at(name))
    for name in sorted(provs):
        if provs[name] == 0:
            diags.add(errors.INCOMPLETE_OBJECTS, f"output {name!r} has no provenance", f"{base}.outputs.{name}", at=_out_at(name))
        elif provs[name] > 1:
            diags.add(errors.MULTIPLE_PROVENANCES, f"output {name!r} has multiple provenances", f"{base}.outputs.{name}", at=_out_at(name))


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
    # composite's own Object inputs, plus each ordinary node's Object outputs.
    # (Structured node output shaping is handled in the node layer; those nodes
    # are skipped here so we do not misjudge their outdegree.)
    sources: dict[tuple[str, str], str] = {}  # (owner, name) -> display path
    for name, s in sig.inputs.items():
        if s.object_bearing:
            sources[("inputs", name)] = f"{base}.inputs.{name}"

    nodes = body.get("nodes")
    if isinstance(nodes, YSeq):
        for item in nodes.items:
            if not isinstance(item, YMap):
                continue
            nid = item.get("id")
            proc_ref = item.get("process")
            kind = item.get("kind")
            if not isinstance(nid, YScalar) or not isinstance(proc_ref, YScalar):
                continue
            # Only ordinary (unkinded) nodes have plain target-output typing.
            if kind is not None:
                continue
            target = sigs.get(proc_ref.text)
            if target is None:
                continue
            for oname, osig in target.outputs.items():
                if osig.object_bearing:
                    sources[(nid.text, oname)] = f"{base}.body.nodes.{nid.text}.{oname}"

    # Count outdegree of each Object source. Linearity requires exactly one use
    # (spec 12.2): zero is an unused Object output, more than one is fan-out.
    refs = _collect_refs(body)
    counts: dict[tuple[str, str], int] = {src: 0 for src in sources}
    for tgt in refs:
        if tgt in counts:
            counts[tgt] += 1

    for src, n in counts.items():
        if n == 0:
            diags.add(errors.OBJECT_OUTPUT_UNUSED, "Object-bearing value is unused", sources[src])
        elif n > 1:
            diags.add(errors.OBJECT_FANOUT, "Object-bearing value fans out", sources[src])


def check_objects(doc: YMap, diags: Diagnostics, env: TypeEnv) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
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
