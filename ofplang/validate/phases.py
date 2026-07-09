"""Phase checks for ports (spec 6, 6.1).

Intent: phases order graph < run < data. The one absolute rule enforceable
purely from a port declaration is that Object-bearing values must never have
`graph` phase (spec 6.1): Object identity cannot exist at graph-construction
time. Phase-*flow* rules (data must not flow back to run/graph) require the
resolved dataflow graph and are checked in the object/graph layer; this pass
covers the per-port prohibition.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.types import (
    TypeEnv,
    TypeParseError,
    is_object_bearing,
    parse_type,
    process_type_params,
)
from ofplang.validate.yamlnode import YMap, YScalar, YNode

_PHASES = {"graph", "run", "data"}


def _check_ports(
    diags: Diagnostics, ports: YNode | None, env: TypeEnv, tp: dict[str, str], base: str
) -> None:
    if not isinstance(ports, YMap):
        return
    for pname in ports.keys():
        port = ports.get(pname)
        if not isinstance(port, YMap):
            continue
        ppath = f"{base}.{pname}"

        phase_node = port.get("phase")
        # A null phase is owned by the null scan (spec 2.3); treat it as absent
        # here so we do not double-report it as an unknown phase word.
        phase = (
            phase_node.text
            if isinstance(phase_node, YScalar) and not phase_node.is_null
            else None
        )
        # An unrecognized phase word is a shape mistake; report it but keep going
        # so we can still evaluate the Object-bearing rule where applicable.
        if phase is not None and phase not in _PHASES:
            diags.add(errors.WRONG_VALUE_KIND, f"unknown phase {phase!r}", f"{ppath}.phase", at=phase_node)

        # Object-bearing + graph phase is the prohibited combination. We must
        # resolve the port type to know whether it is Object-bearing; malformed
        # or unknown types were reported by the type pass and resolve to
        # non-Object-bearing here, so no cascade.
        type_node = port.get("type")
        if not isinstance(type_node, YScalar) or not type_node.is_str:
            continue
        try:
            expr = parse_type(type_node.text)
        except TypeParseError:
            continue
        if phase == "graph" and is_object_bearing(expr, env, tp):
            diags.add(
                errors.OBJECT_GRAPH_PHASE,
                "Object-bearing values must not have graph phase",
                ppath,
                at=phase_node or port,
            )


def check_phases(doc: YMap, diags: Diagnostics, env: TypeEnv) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        base = f"processes.{pname}"
        tp = process_type_params(proc)
        _check_ports(diags, proc.get("inputs"), env, tp, f"{base}.inputs")
        _check_ports(diags, proc.get("outputs"), env, tp, f"{base}.outputs")
