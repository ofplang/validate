"""Scheduling policy checks (spec 23, 24).

Intent: scheduling policies are best-effort preferences, so policy *misses* and
conflicts are never validation errors. What v0 does constrain is placement and
payload shape: policies live only on composite processes, and each preference
kind fixes whether an `object` target is required or forbidden. This pass
validates the `prefer.kind` name and its object-target rule.

Placement on the wrong process kind (`scheduling` on an atomic) is reported by
the shape pass with `scheduling_on_atomic`; to avoid double-reporting, this pass
only inspects composite processes.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.objects import ProcSig
from ofplang.validate.validator import EXTENSION_TOLERANT
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode

# Object-target rule per v0 preference kind (spec 23.4): gaps forbid an object
# target, temperature requires one.
_GAP_KINDS = {"max_gap", "min_gap"}
_OBJECT_REQUIRED = {"temperature"}
_V0_KINDS = _GAP_KINDS | _OBJECT_REQUIRED


def _valid_temporal(text: str, node_ids: set[str]) -> bool:
    """A temporal reference is self.start/self.end or <known node>.start/.end
    (spec 23.1). Process names and other endpoints are not temporal refs."""
    parts = text.split(".")
    if len(parts) != 2 or parts[1] not in ("start", "end"):
        return False
    return parts[0] == "self" or parts[0] in node_ids


def _payload_ok(prefer: YMap) -> bool:
    """v0 gap/temperature payloads need a numeric `value` and a non-empty string
    `unit` (spec 23.4). We check presence and scalar shape only; unit semantics
    are implementation-defined."""
    value = prefer.get("value")
    if not (isinstance(value, YScalar) and (value.is_int or value.is_float)):
        return False
    unit = prefer.get("unit")
    if not (isinstance(unit, YScalar) and unit.is_str and unit.text.strip()):
        return False
    return True


def _object_bearing_target(ref_text: str, comp_sig: ProcSig, nodes_by_id, sigs) -> bool | None:
    """Whether object.from names an Object-bearing value; None if unresolvable."""
    parts = ref_text.split(".")
    if len(parts) != 2:
        return None
    owner, name = parts
    if owner == "inputs":
        port = comp_sig.inputs.get(name)
        return port.object_bearing if port else None
    node = nodes_by_id.get(owner)
    if isinstance(node, YMap):
        proc = node.get("process")
        if isinstance(proc, YScalar) and proc.text in sigs:
            out = sigs[proc.text].outputs.get(name)
            return out.object_bearing if out else None
    return None


def _check_policy(
    diags: Diagnostics, policy: YMap, base: str, mode: str,
    node_ids: set[str], comp_sig: ProcSig, nodes_by_id, sigs,
) -> None:
    prefer = policy.get("prefer")
    kind = None
    if isinstance(prefer, YMap):
        kind_node = prefer.get("kind")
        if isinstance(kind_node, YScalar):
            kind = kind_node.text

    has_object = policy.get("object") is not None

    # Unknown preference kind: only x- extension kinds are tolerated, and only in
    # extension-tolerant mode (spec 23.4).
    if kind is None or (kind not in _V0_KINDS and not kind.startswith("x-")):
        diags.add(errors.UNKNOWN_PREFER_KIND, f"unknown prefer kind {kind!r}", f"{base}.prefer.kind", at=prefer)
        return
    if kind.startswith("x-"):
        if mode != EXTENSION_TOLERANT:
            diags.add(errors.UNKNOWN_PREFER_KIND, f"extension kind {kind!r}", f"{base}.prefer.kind", at=prefer)
        return

    # Object-target rules with dedicated codes so the author sees the exact rule.
    if kind in _GAP_KINDS and has_object:
        diags.add(errors.GAP_WITH_OBJECT, f"{kind} must not target an object", base, at=policy)
    if kind in _OBJECT_REQUIRED and not has_object:
        diags.add(errors.TEMPERATURE_WITHOUT_OBJECT, f"{kind} requires an object target", base, at=policy)

    # Preference payload shape (value + unit) for v0 kinds (spec 23.4).
    if isinstance(prefer, YMap) and not _payload_ok(prefer):
        diags.add(errors.MALFORMED_PREFER_PAYLOAD, f"{kind} payload requires numeric value and unit", f"{base}.prefer", at=prefer)

    # Temporal interval endpoints must be valid temporal references (spec 23.1).
    during = policy.get("during")
    if isinstance(during, YMap):
        for endpoint in ("from", "to"):
            ep = during.get(endpoint)
            if isinstance(ep, YScalar) and not _valid_temporal(ep.text, node_ids):
                diags.add(errors.BAD_TEMPORAL_REF, f"invalid temporal reference {ep.text!r}", f"{base}.during.{endpoint}", at=ep)

    # An object target that is required/allowed must be Object-bearing (spec 24.1).
    # Skipped for gap kinds, where the object was already rejected outright.
    if has_object and kind not in _GAP_KINDS:
        obj = policy.get("object")
        frm = obj.get("from") if isinstance(obj, YMap) else None
        if isinstance(frm, YScalar):
            ob = _object_bearing_target(frm.text, comp_sig, nodes_by_id, sigs)
            if ob is False:
                diags.add(errors.NON_OBJECT_BEARING_TARGET, f"object target {frm.text!r} is Pure Data", f"{base}.object", at=frm)


def check_scheduling(doc: YMap, diags: Diagnostics, mode: str, sigs: dict[str, ProcSig]) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        # Only composites: atomic placement is the shape pass's concern.
        kind_node = proc.get("kind")
        if not (isinstance(kind_node, YScalar) and kind_node.text == "composite"):
            continue
        scheduling = proc.get("scheduling")
        if not isinstance(scheduling, YMap):
            continue
        policies = scheduling.get("policies")
        if not isinstance(policies, YSeq):
            continue

        # Temporal references may name direct child node ids of this body, plus
        # `self`; object.from resolution needs this composite's signature.
        node_ids: set[str] = set()
        nodes_by_id: dict[str, YMap] = {}
        body = proc.get("body")
        if isinstance(body, YMap):
            body_nodes = body.get("nodes")
            if isinstance(body_nodes, YSeq):
                for n in body_nodes.items:
                    if isinstance(n, YMap) and isinstance(n.get("id"), YScalar):
                        node_ids.add(n.get("id").text)
                        nodes_by_id[n.get("id").text] = n
        comp_sig = sigs.get(pname) or ProcSig(kind="composite")

        for i, policy in enumerate(policies.items):
            if isinstance(policy, YMap):
                _check_policy(
                    diags, policy, f"processes.{pname}.scheduling.policies[{i}]",
                    mode, node_ids, comp_sig, nodes_by_id, sigs,
                )
