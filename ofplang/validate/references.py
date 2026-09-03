"""Body dataflow reference and binding checks (spec 2.6, 6, 12).

Intent: these checks operate on a composite body's binding graph — the part of
validation that needs the whole node/port picture rather than a single process.
They cover:

  * **binding source arity** — a source entry has exactly one of `from`/`value`
    (spec 2.6.6);
  * **reference resolution** — a `from` naming a body value must resolve to a
    composite input or a direct child node output (spec 2.6.1);
  * **binding correspondence** — a node's binding entries and its target's input
    ports are one-to-one, whatever the node kind (spec 11), distinguishing Pure
    Data (`data_indegree`) from Object (`object_input_no_source`) on the port
    side and reporting `binding_port_not_found` on the entry side; and
  * **phase-flow** — a value may only flow into an equal-or-later phase
    (spec 6): data -> run/graph and run -> graph are errors.

Structured nodes (map/fold/do_while/branch) reshape/route values in kind-specific
ways, so phase-flow is checked only for ordinary nodes; reference resolution and
binding correspondence apply to every node kind.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.objects import DO_WHILE_RESERVED_OUTPUT, ProcSig
from ofplang.validate.yamlnode import YMap, YScalar, YSeq

# Phase order graph < run < data (spec 6). Rank lets us compare "earlier".
_PHASE_RANK = {"graph": 0, "run": 1, "data": 2}

# Binding sections that carry `from`/`value` source entries.
_BINDING_SECTIONS = ("state", "bind", "carry", "args", "each")


def _node_output_names(node: YMap, sigs: dict[str, ProcSig]) -> set[str]:
    """Output names a node exposes, for reference-existence purposes.

    Approximate but sound for existence: ordinary/map/fold/do_while expose their
    target's output names; a branch exposes the union of its arms' outputs. This
    is a superset of what is actually exposed, so it never produces a false
    `unknown_reference`, only (rarely) misses one — which is acceptable here.
    """
    kind_node = node.get("kind")
    kind = kind_node.text if isinstance(kind_node, YScalar) else None
    if kind == "branch":
        names: set[str] = set()
        for arm in ("then", "else"):
            arm_node = node.get(arm)
            if isinstance(arm_node, YMap):
                proc = arm_node.get("process")
                if isinstance(proc, YScalar) and proc.text in sigs:
                    names |= set(sigs[proc.text].outputs)
        return names
    names = set()
    proc = node.get("process")
    if isinstance(proc, YScalar) and proc.text in sigs:
        names |= set(sigs[proc.text].outputs)
    if kind == "do_while":
        # The node's own reserved output, always defined (spec 19.3).
        names.add(DO_WHILE_RESERVED_OUTPUT)
    return names


# The binding sections that supply a target's input ports, per node kind
# (spec 11, 21.0). `None` is an ordinary node. Sections outside a kind's set are
# reported by the shape pass, so a stray one here simply supplies nothing.
_INPUT_SECTIONS: dict[str | None, tuple[str, ...]] = {
    None: ("state", "bind"),
    "map": ("each", "bind"),
    "fold": ("each", "carry", "bind"),
    "do_while": ("carry", "bind"),
    "branch": ("args",),
}


def _targets_of(
    node: YMap, kind: str | None, target: ProcSig | None, sigs: dict[str, ProcSig]
) -> list[tuple[str, ProcSig]]:
    """The processes a node invokes, each with a label for diagnostics.

    A `branch` invokes one process per arm and binds both through one `args`
    section, so the correspondence is answered per arm. An omitted `else` is an
    implicit identity arm with no ports of its own (spec 20.3), so it names no
    target here.
    """
    if kind == "branch":
        out = []
        for arm in ("then", "else"):
            arm_node = node.get(arm)
            if not isinstance(arm_node, YMap):
                continue
            proc = arm_node.get("process")
            if isinstance(proc, YScalar) and proc.text in sigs:
                out.append((arm, sigs[proc.text]))
        return out
    return [("", target)] if target is not None else []


def _check_binding_correspondence(
    diags: Diagnostics,
    node: YMap,
    nid: str,
    arm: str,
    target: ProcSig,
    npath: str,
    kind: str | None,
) -> None:
    """One node's bindings against one target's input ports, both directions."""
    sections = _INPUT_SECTIONS.get(kind, ())
    where = f" of the {arm} arm" if arm else ""

    for iname, isig in target.inputs.items():
        count = 0
        for section in sections:
            bound = node.get(section)
            if isinstance(bound, YMap) and iname in bound.keys():
                count += 1
        if count == 1:
            continue
        if count == 0:
            code = (
                errors.OBJECT_INPUT_NO_SOURCE if isig.object_bearing else errors.DATA_INDEGREE
            )
            message = f"input {iname!r}{where} has no source"
        else:
            code = (
                errors.OBJECT_INPUT_MULTI_SOURCE
                if isig.object_bearing
                else errors.DATA_INDEGREE
            )
            message = f"input {iname!r}{where} has multiple sources"
        diags.add(code, message, f"{npath}.{iname}", at=node)

    # The other direction: an entry that names no input port of this target.
    for section in sections:
        m = node.get(section)
        if not isinstance(m, YMap):
            continue
        for portname in m.keys():
            if portname not in target.inputs:
                diags.add(
                    errors.BINDING_PORT_NOT_FOUND,
                    f"{section} {portname!r} names no input port{where}",
                    f"{npath}.{section}.{portname}",
                    at=m.key_node(portname),
                )


def _parse_ref(text: str) -> tuple[str, str] | None:
    parts = text.split(".")
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _source_phase(ref: tuple[str, str], sig: ProcSig, nodes_by_id, sigs) -> str | None:
    """Phase of the value a reference denotes, if determinable for an ordinary
    source (composite input, or an ordinary child node output)."""
    owner, name = ref
    if owner == "inputs":
        port = sig.inputs.get(name)
        return port.phase if port else None
    node = nodes_by_id.get(owner)
    if node is None:
        return None
    kind = node.get("kind")
    if kind is not None:  # structured source: reshaped phase, skip
        return None
    proc = node.get("process")
    if isinstance(proc, YScalar) and proc.text in sigs:
        out = sigs[proc.text].outputs.get(name)
        return out.phase if out else None
    return None


def _source_object_bearing(ref: tuple[str, str], sig: ProcSig, nodes_by_id, sigs) -> bool:
    """Whether the value a reference denotes is Object-bearing.

    Used to enforce that `bind` carries only Pure Data (spec 11): an Object
    value must travel through `state`/`carry`/`args`/`each`, never `bind`.
    Unresolvable sources default to False so this rule never fabricates an error
    on top of an already-reported unknown reference.
    """
    owner, name = ref
    if owner == "inputs":
        port = sig.inputs.get(name)
        return bool(port and port.object_bearing)
    node = nodes_by_id.get(owner)
    if isinstance(node, YMap):
        proc = node.get("process")
        if isinstance(proc, YScalar) and proc.text in sigs:
            out = sigs[proc.text].outputs.get(name)
            return bool(out and out.object_bearing)
    return False


def _node_from_refs(node: YMap) -> list[str]:
    """Every `from` text in a node's binding and control sections (spec 21.0).

    The dependency graph of a body is built from these (spec 10.2, rule 21b).
    `branch.condition` and `do_while.max_iterations` are control sections that
    carry a `from`; `do_while.condition` names a target output rather than a
    reference (spec 2.6.7), `then`/`else` name processes, and `outputs` names
    modes, so none of those contributes one.
    """
    out: list[str] = []
    for section in _BINDING_SECTIONS:
        m = node.get(section)
        if isinstance(m, YMap):
            for portname in m.keys():
                entry = m.get(portname)
                if isinstance(entry, YMap):
                    frm = entry.get("from")
                    if isinstance(frm, YScalar):
                        out.append(frm.text)
    for section in ("condition", "max_iterations"):
        m = node.get(section)
        if isinstance(m, YMap):
            frm = m.get("from")
            if isinstance(frm, YScalar):
                out.append(frm.text)
    return out


def _find_cycle(deps: dict[str, set[str]]) -> list[str]:
    """One cycle of ``deps``, as the nodes on it closing back on the first, or [].

    ``deps[n]`` holds the nodes ``n`` depends on. Iteration is over sorted keys
    so the reported cycle is the same on every run, which a diagnostic that a
    test matches has to be.
    """
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(deps, white)
    path: list[str] = []

    def visit(n: str) -> list[str]:
        color[n] = grey
        path.append(n)
        for m in sorted(deps.get(n, ())):
            if color.get(m, black) == grey:
                return path[path.index(m) :] + [m]
            if color.get(m, black) == white:
                found = visit(m)
                if found:
                    return found
        path.pop()
        color[n] = black
        return []

    for n in sorted(deps):
        if color[n] == white:
            found = visit(n)
            if found:
                return found
    return []


def _check_composite(
    diags: Diagnostics, pname: str, proc: YMap, sig: ProcSig, sigs: dict[str, ProcSig]
) -> None:
    body = proc.get("body")
    if not isinstance(body, YMap):
        return
    base = f"processes.{pname}.body"

    nodes = body.get("nodes")
    node_items = [
        n for n in (nodes.items if isinstance(nodes, YSeq) else []) if isinstance(n, YMap)
    ]

    # Build the set of resolvable body sources and an id->node index.
    input_names = set(sig.inputs)
    node_out: set[tuple[str, str]] = set()
    nodes_by_id: dict[str, YMap] = {}
    for node in node_items:
        id_node = node.get("id")
        if isinstance(id_node, YScalar):
            nodes_by_id[id_node.text] = node
            for oname in _node_output_names(node, sigs):
                node_out.add((id_node.text, oname))

    def _resolves(ref: tuple[str, str]) -> bool:
        owner, name = ref
        if owner == "inputs":
            return name in input_names
        return (owner, name) in node_out

    # The body's node dependency graph must be acyclic (spec 10.2, 27 rule 21b).
    # A different graph from the one `recursive_process_dependency` reports:
    # that one is about which process invokes which, this one about the order of
    # nodes within one body. An `inputs.p` reference creates no edge, and an
    # unresolvable one creates none either, so a cycle is never fabricated on
    # top of an unknown reference.
    deps: dict[str, set[str]] = {}
    for node in node_items:
        id_node = node.get("id")
        if not isinstance(id_node, YScalar):
            continue
        preds = deps.setdefault(id_node.text, set())
        for text in _node_from_refs(node):
            ref = _parse_ref(text)
            if ref is not None and ref[0] != "inputs" and ref[0] in nodes_by_id:
                preds.add(ref[0])
    cycle = _find_cycle(deps)
    if cycle:
        diags.add(
            errors.NODE_DEPENDENCY_CYCLE,
            "node dependency graph contains a cycle: " + " -> ".join(cycle),
            f"{base}.nodes",
            at=nodes,
        )

    def _check_source_entry(entry: YMap, path: str, target_input_phase: str | None) -> None:
        """Arity + reference resolution + phase-flow for one source entry."""
        has_from = entry.get("from") is not None
        has_value = entry.get("value") is not None
        # Exactly one of from/value (spec 2.6.6).
        if has_from == has_value:  # both present, or both absent
            diags.add(
                errors.BINDING_SOURCE_ARITY,
                "source needs exactly one of from/value",
                path,
                at=entry,
            )
            return
        if not has_from:
            return  # a literal `value`: no reference to resolve
        frm = entry.get("from")
        if not isinstance(frm, YScalar):
            return
        ref = _parse_ref(frm.text)
        if ref is None:
            diags.add(errors.MALFORMED_REFERENCE, f"malformed reference {frm.text!r}", path, at=frm)
            return
        if not _resolves(ref):
            diags.add(errors.UNKNOWN_REFERENCE, f"unresolved reference {frm.text!r}", path, at=frm)
            return
        # Phase-flow: source phase must be earlier-or-equal to the target port.
        if target_input_phase is not None:
            src_phase = _source_phase(ref, sig, nodes_by_id, sigs)
            if (
                src_phase in _PHASE_RANK
                and target_input_phase in _PHASE_RANK
                and _PHASE_RANK[src_phase] > _PHASE_RANK[target_input_phase]
            ):
                diags.add(
                    errors.INVALID_PHASE_FLOW,
                    f"{src_phase} value flows into a {target_input_phase} port",
                    path,
                )

    for node in node_items:
        nid_node = node.get("id")
        nid = nid_node.text if isinstance(nid_node, YScalar) else "?"
        kind_node = node.get("kind")
        kind = kind_node.text if isinstance(kind_node, YScalar) else None
        proc_ref = node.get("process")
        target = sigs.get(proc_ref.text) if isinstance(proc_ref, YScalar) else None

        # Reference/arity checks over every binding section. For ordinary nodes
        # we also know the target input phase (for phase-flow); for `bind`/`state`
        # the section key is the target input port name.
        for section in _BINDING_SECTIONS:
            m = node.get(section)
            if not isinstance(m, YMap):
                continue
            for portname in m.keys():
                entry = m.get(portname)
                if not isinstance(entry, YMap):
                    continue
                tgt_phase = None
                if kind is None and target is not None and portname in target.inputs:
                    tgt_phase = target.inputs[portname].phase
                epath = f"{base}.nodes.{nid}.{section}.{portname}"
                _check_source_entry(entry, epath, tgt_phase)

                # `bind` is Pure Data only: an Object-bearing value must be
                # routed through state/carry/args/each instead (spec 11). Only
                # flag resolvable sources, so this never stacks on an unknown
                # reference already reported above.
                if section == "bind":
                    frm = entry.get("from")
                    if isinstance(frm, YScalar):
                        ref = _parse_ref(frm.text)
                        if (
                            ref
                            and _resolves(ref)
                            and _source_object_bearing(ref, sig, nodes_by_id, sigs)
                        ):
                            diags.add(
                                errors.OBJECT_VIA_BIND,
                                "Object-bearing value passed through bind",
                                epath,
                                at=frm,
                            )

        # `max_iterations` is a constant slot, not a binding section (spec 11.2,
        # 21.0). A slot is treated as an input port whose declared phase is the
        # slot's upper bound -- `run` for this one (spec 19 requirement 5) -- so
        # the arity, resolution and phase-flow rules of a binding apply to it
        # unchanged. Its slot type is `Int`, matched with the other types in
        # `bindings.py`; the `value` literal half is in `nodes.py`.
        if kind == "do_while":
            mi = node.get("max_iterations")
            if isinstance(mi, YMap):
                mpath = f"{base}.nodes.{nid}.max_iterations"
                _check_source_entry(mi, mpath, "run")
                # A constant slot is Pure Data (spec 11.2). Only a resolvable
                # source is flagged, so this never stacks on an unknown
                # reference already reported above.
                frm = mi.get("from")
                if isinstance(frm, YScalar):
                    ref = _parse_ref(frm.text)
                    if (
                        ref
                        and _resolves(ref)
                        and _source_object_bearing(ref, sig, nodes_by_id, sigs)
                    ):
                        diags.add(
                            errors.OBJECT_IN_CONSTANT_SLOT,
                            "Object-bearing value fills a constant slot",
                            mpath,
                            at=frm,
                        )

        # A branch condition is itself a body dataflow reference.
        if kind == "branch":
            cond = node.get("condition")
            if isinstance(cond, YMap):
                frm = cond.get("from")
                if isinstance(frm, YScalar):
                    ref = _parse_ref(frm.text)
                    if ref is None:
                        diags.add(
                            errors.MALFORMED_REFERENCE,
                            "malformed condition reference",
                            f"{base}.nodes.{nid}.condition",
                            at=frm,
                        )
                    elif not _resolves(ref):
                        diags.add(
                            errors.UNKNOWN_REFERENCE,
                            f"unresolved condition {frm.text!r}",
                            f"{base}.nodes.{nid}.condition",
                            at=frm,
                        )

        # A node's binding entries and its target's input ports are in one-to-one
        # correspondence (spec 11), whatever the node kind: every input port is
        # bound exactly once across the sections the kind allows, and every entry
        # names an input port. v0 gives an input port no default, so nothing is
        # exempt. A `branch` has two targets and one `args` section, so the
        # correspondence is checked against each arm.
        for arm, arm_target in _targets_of(node, kind, target, sigs):
            _check_binding_correspondence(
                diags, node, nid, arm, arm_target, f"{base}.nodes.{nid}", kind
            )

    # returns entries are body dataflow references too.
    returns = body.get("returns")
    if isinstance(returns, YMap):
        for rname in returns.keys():
            entry = returns.get(rname)
            if isinstance(entry, YMap):
                frm = entry.get("from")
                if isinstance(frm, YScalar):
                    ref = _parse_ref(frm.text)
                    if ref is None:
                        diags.add(
                            errors.MALFORMED_REFERENCE,
                            "malformed return reference",
                            f"{base}.returns.{rname}",
                            at=frm,
                        )
                    elif not _resolves(ref):
                        diags.add(
                            errors.UNKNOWN_REFERENCE,
                            f"unresolved return {frm.text!r}",
                            f"{base}.returns.{rname}",
                            at=frm,
                        )


def check_references(doc: YMap, diags: Diagnostics, sigs: dict[str, ProcSig]) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    for pname in processes.keys():
        proc = processes.get(pname)
        sig = sigs.get(pname)
        if isinstance(proc, YMap) and sig is not None and sig.kind == "composite":
            _check_composite(diags, pname, proc, sig, sigs)
