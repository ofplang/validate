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

A Pure Data `Array` output has no *derivation* available: the `objects` section
describes Object behavior, and a Pure Data port has no Object slots for it to
relate. Its only route is the second clause, which is why this pass also reads
`ensures` for a contract that bounds the port's length.

**What a clean run does and does not mean.** The check is neither a proof nor a
complete detector, and both gaps are deliberate:

  * It under-reports nothing but over-reports plenty. A port genuinely bounded
    in a shape this pass does not read -- a bound needing algebra to solve for
    the length, one stated through another output, one held by knowledge of the
    process and written nowhere -- still draws the warning. Recognising those
    would mean solving for a variable, which spec 12.4.8 rules out for exactly
    this kind of reasoning.
  * Suppression is not verification. A contract is checked at run time (spec
    9.3), so reading one here says the document *claims* a bound, not that one
    holds -- the same standing `objects.map` has under 14.1.

So no warnings means "every `Array` output is accounted for, by derivation or
by claim", not "proved bounded". A warning means "this validator cannot see a
bound", not "unbounded". The code name says `not_derivable` for that reason.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.contracts import Binary, Lit, Ref, Unary, parse_expression
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
        accounted = _derivable_outputs(proc, sig, env) | _contract_bounded_outputs(proc, sig)
        outputs = proc.get("outputs")
        for port, psig in sig.outputs.items():
            if not isinstance(psig.type_expr, ArrayT) or port in accounted:
                continue
            at = outputs.get(port) if isinstance(outputs, YMap) else proc
            diags.warning(
                errors.ARRAY_OUTPUT_LENGTH_NOT_DERIVABLE,
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


# --- The second clause: a contract that bounds the length -----------------
# Comparisons that put an upper bound on the left operand, and on the right.
# `>=` and `!=` appear in neither: a lower bound and a disequality leave the
# length free above, which is the direction that costs Objects.
_UPPER_ON_LEFT = frozenset({"==", "<=", "<"})
_UPPER_ON_RIGHT = frozenset({"==", ">=", ">"})


def _contract_bounded_outputs(proc: YMap, sig: ProcSig) -> set[str]:
    """Output port names an `ensures` clause bounds the length of.

    Only `ensures` is read: a bound is a statement about an output, and
    `requires` may not refer to one (spec 9.1).
    """
    contracts = proc.get("contracts")
    if not isinstance(contracts, YMap):
        return set()
    ensures = contracts.get("ensures")
    if not isinstance(ensures, YSeq):
        return set()

    found: set[str] = set()
    for entry in ensures.items:
        if not isinstance(entry, YMap):
            continue
        expr = entry.get("expr")
        if not isinstance(expr, YScalar):
            continue
        node = parse_expression(expr.text)
        if node is not None:
            _collect_bounds(node, sig, found)
    return found


def _collect_bounds(node: object, sig: ProcSig, found: set[str]) -> None:
    """Add every output port this expression puts an upper bound on.

    `and` is descended because both conjuncts hold, so a bound in either one is
    a bound. `or` and `not` are not: neither guarantees the operand holds.
    """
    if not isinstance(node, Binary):
        return
    if node.op == "and":
        _collect_bounds(node.left, sig, found)
        _collect_bounds(node.right, sig, found)
        return

    # The port reference must be one whole side of the comparison. A length
    # buried in an expression -- `outputs.xss.view.length * n == inputs.xs...`
    # -- would have to be solved for, and 12.4.8 keeps that kind of algebra out
    # of this reasoning. Such a port is derivable through `objects.transform`
    # in every case v0 can write anyway.
    for op_set, near, far in (
        (_UPPER_ON_LEFT, node.left, node.right),
        (_UPPER_ON_RIGHT, node.right, node.left),
    ):
        if node.op not in op_set:
            continue
        port = _length_ref(near)
        if port is not None and port in sig.outputs and _reachable(far, sig):
            found.add(port)


def _length_ref(node: object) -> str | None:
    """The port name of an `outputs.<port>.view.length` reference, else None."""
    if isinstance(node, Ref) and len(node.path) == 4:
        head, port, view, field = node.path
        if head == "outputs" and view == "view" and field == "length":
            return port
    return None


def _reachable(node: object, sig: ProcSig) -> bool:
    """Whether the bounding side is built only from quantities the bound
    computation of spec 1.1 can already reach.

    Two kinds of leaf qualify, and the difference between them is where the
    number comes from:

    `inputs.<p>.view.length` for an `Array` input port -- reached by the same
    recursion that is computing this bound, since the traversal follows the
    graph to whatever was bound to that port. Its phase does not matter: an
    Object-bearing value is normally `data` phase (spec 6.1), so requiring
    `run` here would reject the most natural contract there is.

    `inputs.<p>.view` for a Pure Data scalar port fixed no later than `run` --
    known once the arguments are given, which is when the bound is wanted. A
    `data` phase scalar is a number computed during the run and reaches nothing.

    An `outputs.*` reference does not qualify. It would make the bound a chain
    through another port, which this pass does not follow.
    """
    if isinstance(node, Lit):
        return node.type_name in ("Int", "Float")
    if isinstance(node, Unary):
        return node.op == "-" and _reachable(node.operand, sig)
    if isinstance(node, Binary):
        return node.op in ("+", "-", "*", "/") and (
            _reachable(node.left, sig) and _reachable(node.right, sig)
        )
    if not isinstance(node, Ref) or not node.path or node.path[0] != "inputs":
        return False

    port = sig.inputs.get(node.path[1]) if len(node.path) > 1 else None
    if port is None:
        return False
    if len(node.path) == 4 and node.path[2:] == ["view", "length"]:
        return isinstance(port.type_expr, ArrayT)
    if len(node.path) == 3 and node.path[2] == "view":
        return not isinstance(port.type_expr, ArrayT) and port.phase in ("graph", "run")
    return False
