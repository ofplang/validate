"""The resource-bound condition of spec 1.1.

Intent: spec 1.1 guarantees that the upper bound on the physical resources a
workflow needs is fixed before the run, but only under a condition it states
rather than validates -- every `Array` output port of every atomic process must
have a length that is either *derivable* from the process's own declarations or
*bounded* by something v0 cannot see. This pass reports the ports that are
neither, which is the condition failing.

Why it matters: an `Array` length nothing relates to an input is the one place a
document can introduce a number of Objects that no other length reaches. Two
shapes of that, and they are the same shape:

    outputs: { cups: { type: "Array<Cup>" } }   with  objects: { create: [outputs.cups] }
    outputs: { hits: { type: "Array<Int>"  } }   fed to a map whose target creates

The first creates directly, the second decides how many times a `map` creates.
Everywhere else a length comes from something reachable at run start -- a
literal, an argument, a collected output, a transform, a branch's arms, or a
loop bounded by `max_iterations` -- so these ports are the whole condition.

Why it is a warning and not an error: v0 cannot see into an atomic process
(spec 14.1). Whether such a port is bounded in fact is a statement about the
process's behavior, which the specification trusts rather than proves. A
document that draws this warning is still portable v0; what it loses is the
guarantee of 1.1, not validity.

A Pure Data `Array` output always draws it. The `objects` section describes
Object behavior, and a Pure Data port has no Object slots for it to relate, so
no derivation is available for one. Bounding it is the second clause of the
condition -- today a matter for the implementation, since v0 does not read a
length relation out of `contracts`.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics

# `_has_behavior` and `_same_port` are what the `object_identity_map` inference
# is built from (spec 15), which pairs ports on name, type and phase. Reusing
# them keeps this pass from drifting from the inference it has to agree with.
from ofplang.validate.objects import (
    OBJECT_IDENTITY_MAP,
    ProcSig,
    _has_behavior,
    _same_port,
)
from ofplang.validate.types import ArrayT, TypeEnv
from ofplang.validate.yamlnode import YMap, YScalar, YSeq


def check_array_output_bounds(
    doc: YMap, diags: Diagnostics, sigs: dict[str, ProcSig], env: TypeEnv
) -> None:
    """Warn for each atomic `Array` output port whose length is not derivable."""
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    for pname in processes.keys():
        proc = processes.get(pname)
        sig = sigs.get(pname)
        if not isinstance(proc, YMap) or sig is None or sig.kind != "atomic":
            continue
        derivable = _derivable_outputs(proc, sig, env)
        outputs = proc.get("outputs")
        for port, psig in sig.outputs.items():
            if not isinstance(psig.type_expr, ArrayT) or port in derivable:
                continue
            at = outputs.get(port) if isinstance(outputs, YMap) else proc
            diags.warning(
                errors.UNBOUNDED_ARRAY_OUTPUT,
                f"Array output {port!r} has no derivable length; "
                "the resource bound of 1.1 holds only if it is bounded by other means",
                f"processes.{pname}.outputs.{port}",
                at=at,
            )


def _derivable_outputs(proc: YMap, sig: ProcSig, env: TypeEnv) -> set[str]:
    """Output port names whose length follows from the process's declarations.

    The three sources are the ones spec 1.1 names, and each relates the output's
    slot family to an input's, so the length travels with the relation:
    `objects.map` (14.1) preserves it, `objects.transform` (14.4) fixes it by the
    kind's rule, and the `object_identity_map` inference (15) is a map written
    for the process.
    """
    objects = proc.get("objects")

    # `objects` omitted plus the marker: the inference pairs each Object-bearing
    # input with the same-name, same-type, same-phase output (spec 15). A Pure
    # Data port is not paired -- the marker speaks about Object identity -- so it
    # stays underivable, which is what the module docstring says.
    if objects is None:
        if not _has_behavior(proc, OBJECT_IDENTITY_MAP):
            return set()
        return {
            name
            for name, psig in sig.outputs.items()
            if psig.object_bearing
            and name in sig.inputs
            and _same_port(sig.inputs[name], psig, proc, env)
        }

    if not isinstance(objects, YMap):
        return set()

    found: set[str] = set()

    # `objects.map` keys are the target paths: `outputs.<port>: inputs.<port>`.
    map_node = objects.get("map")
    if isinstance(map_node, YMap):
        for out_path in map_node.keys():
            port = _output_port(out_path)
            if port is not None:
                found.add(port)

    # A transform entry's `outputs` mapping is role -> `outputs.<port>`.
    transform = objects.get("transform")
    if isinstance(transform, YSeq):
        for entry in transform.items:
            if not isinstance(entry, YMap):
                continue
            outs = entry.get("outputs")
            if not isinstance(outs, YMap):
                continue
            for role in outs.keys():
                value = outs.get(role)
                if isinstance(value, YScalar):
                    port = _output_port(value.text)
                    if port is not None:
                        found.add(port)

    # `objects.create` is deliberately absent from this list. It says a new
    # identity appears at every slot of the port and fixes no size (spec 14.3),
    # which is the case 1.1 is about.
    return found


def _output_port(path: str) -> str | None:
    """The port named by an `outputs.<port>` Object path (spec 2.6.2), else None."""
    head, sep, port = path.partition(".")
    return port if sep and head == "outputs" and port else None
