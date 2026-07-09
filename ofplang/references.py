"""Body dataflow reference and binding checks (spec 2.6, 6, 12).

Intent: these checks operate on a composite body's binding graph — the part of
validation that needs the whole node/port picture rather than a single process.
They cover:

  * **binding source arity** — a source entry has exactly one of `from`/`value`
    (spec 2.6.6);
  * **reference resolution** — a `from` naming a body value must resolve to a
    composite input or a direct child node output (spec 2.6.1);
  * **node input indegree** — an ordinary node's input ports must each be bound
    exactly once (spec 12.1/12.2), distinguishing Pure Data (`data_indegree`)
    from Object (`object_input_no_source`); and
  * **phase-flow** — a value may only flow into an equal-or-later phase
    (spec 6): data -> run/graph and run -> graph are errors.

Structured nodes (map/fold/do_while/branch) reshape/route values in kind-specific
ways, so per-port indegree and phase-flow are checked only for ordinary nodes;
reference resolution still applies to all node bindings.
"""

from __future__ import annotations

from ofplang import errors
from ofplang.diagnostics import Diagnostics
from ofplang.objects import ProcSig
from ofplang.yamlnode import YMap, YScalar, YNode

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
    proc = node.get("process")
    if isinstance(proc, YScalar) and proc.text in sigs:
        return set(sigs[proc.text].outputs)
    return set()


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


def _check_composite(
    diags: Diagnostics, pname: str, proc: YMap, sig: ProcSig, sigs: dict[str, ProcSig]
) -> None:
    body = proc.get("body")
    if not isinstance(body, YMap):
        return
    base = f"processes.{pname}.body"

    nodes = body.get("nodes")
    node_items = [n for n in (nodes.items if hasattr(nodes, "items") else []) if isinstance(n, YMap)]

    # Build the set of resolvable body sources and an id->node index.
    input_names = set(sig.inputs)
    node_out: set[tuple[str, str]] = set()
    nodes_by_id: dict[str, YMap] = {}
    for node in node_items:
        nid = node.get("id")
        if isinstance(nid, YScalar):
            nodes_by_id[nid.text] = node
            for oname in _node_output_names(node, sigs):
                node_out.add((nid.text, oname))

    def _resolves(ref: tuple[str, str]) -> bool:
        owner, name = ref
        if owner == "inputs":
            return name in input_names
        return (owner, name) in node_out

    def _check_source_entry(entry: YMap, path: str, target_input_phase: str | None) -> None:
        """Arity + reference resolution + phase-flow for one source entry."""
        has_from = entry.get("from") is not None
        has_value = entry.get("value") is not None
        # Exactly one of from/value (spec 2.6.6).
        if has_from == has_value:  # both present, or both absent
            diags.add(errors.BINDING_SOURCE_ARITY, "source needs exactly one of from/value", path)
            return
        if not has_from:
            return  # a literal `value`: no reference to resolve
        frm = entry.get("from")
        if not isinstance(frm, YScalar):
            return
        ref = _parse_ref(frm.text)
        if ref is None:
            diags.add(errors.MALFORMED_REFERENCE, f"malformed reference {frm.text!r}", path)
            return
        if not _resolves(ref):
            diags.add(errors.UNKNOWN_REFERENCE, f"unresolved reference {frm.text!r}", path)
            return
        # Phase-flow: source phase must be earlier-or-equal to the target port.
        if target_input_phase is not None:
            src_phase = _source_phase(ref, sig, nodes_by_id, sigs)
            if src_phase in _PHASE_RANK and target_input_phase in _PHASE_RANK:
                if _PHASE_RANK[src_phase] > _PHASE_RANK[target_input_phase]:
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
                        if ref and _resolves(ref) and _source_object_bearing(ref, sig, nodes_by_id, sigs):
                            diags.add(errors.OBJECT_VIA_BIND, "Object-bearing value passed through bind", epath)

        # A branch condition is itself a body dataflow reference.
        if kind == "branch":
            cond = node.get("condition")
            if isinstance(cond, YMap):
                frm = cond.get("from")
                if isinstance(frm, YScalar):
                    ref = _parse_ref(frm.text)
                    if ref is None:
                        diags.add(errors.MALFORMED_REFERENCE, "malformed condition reference", f"{base}.nodes.{nid}.condition")
                    elif not _resolves(ref):
                        diags.add(errors.UNKNOWN_REFERENCE, f"unresolved condition {frm.text!r}", f"{base}.nodes.{nid}.condition")

        # Node input indegree, ordinary nodes only: every target input port must
        # be bound exactly once via state (Object) or bind (Pure Data).
        if kind is None and target is not None:
            for iname, isig in target.inputs.items():
                count = 0
                for section in ("state", "bind"):
                    m = node.get(section)
                    if isinstance(m, YMap) and iname in m.keys():
                        count += 1
                if count == 0:
                    code = errors.OBJECT_INPUT_NO_SOURCE if isig.object_bearing else errors.DATA_INDEGREE
                    diags.add(code, f"input {iname!r} has no source", f"{base}.nodes.{nid}.{iname}")
                elif count > 1:
                    code = errors.OBJECT_INPUT_MULTI_SOURCE if isig.object_bearing else errors.DATA_INDEGREE
                    diags.add(code, f"input {iname!r} has multiple sources", f"{base}.nodes.{nid}.{iname}")

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
                        diags.add(errors.MALFORMED_REFERENCE, "malformed return reference", f"{base}.returns.{rname}")
                    elif not _resolves(ref):
                        diags.add(errors.UNKNOWN_REFERENCE, f"unresolved return {frm.text!r}", f"{base}.returns.{rname}")


def check_references(doc: YMap, diags: Diagnostics, sigs: dict[str, ProcSig]) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    for pname in processes.keys():
        proc = processes.get(pname)
        sig = sigs.get(pname)
        if isinstance(proc, YMap) and sig is not None and sig.kind == "composite":
            _check_composite(diags, pname, proc, sig, sigs)
