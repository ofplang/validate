"""Structured node validation: map / fold / do_while / branch (spec 16-21).

Intent: structured nodes wrap a target process with loop/branch control, and
each kind imposes extra well-formedness rules on top of the target's own Object
tracking completeness. This pass checks the kind-specific structural rules:

  * `fold` / `do_while` carry bindings need a matching same-name output on the
    target (structured carry compatibility, spec 16);
  * `do_while` requires an explicit `max_iterations` bound (spec 19); and
  * `branch` forbids one-sided Object-bearing outputs — an Object output must be
    common to both arms so its identity does not depend on the chosen arm
    (spec 20, 20.1).

Composite linearity intentionally skips structured nodes (their output shaping
differs — e.g. `map` wraps outputs in Array), so their Object flow is governed
by these node-local rules plus the target processes' completeness.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.objects import ProcSig
from ofplang.validate.types import Atom
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode


def _mode_of(entry: YNode | None) -> str | None:
    if isinstance(entry, YMap):
        m = entry.get("mode")
        if isinstance(m, YScalar):
            return m.text
    return None


def _map_sources(proc_def: YMap) -> dict[str, str]:
    """For an arm process, map output port -> input port it identity-maps from.

    Used for branch identity-equivalence: an output produced by `create`/
    `transform` (or absent) is simply not in this dict, which the caller reads
    as "not a same-argument identity map".
    """
    res: dict[str, str] = {}
    objects = proc_def.get("objects")
    if isinstance(objects, YMap):
        mp = objects.get("map")
        if isinstance(mp, YMap):
            for out_path in mp.keys():
                src = mp.get(out_path)
                op = out_path.split(".")
                if len(op) == 2 and op[0] == "outputs" and isinstance(src, YScalar):
                    ip = src.text.split(".")
                    if len(ip) == 2 and ip[0] == "inputs":
                        res[op[1]] = ip[1]
    return res


def _each_literal_lengths(node: YMap) -> tuple[list[int], bool]:
    """Lengths of `each` sources given as sequence literals, and whether *every*
    each source is such a literal (so the traversal length is graph-known)."""
    each = node.get("each")
    if not isinstance(each, YMap):
        return [], False
    lengths: list[int] = []
    total = 0
    for name in each.keys():
        total += 1
        entry = each.get(name)
        if isinstance(entry, YMap):
            val = entry.get("value")
            if isinstance(val, YSeq):
                lengths.append(len(val.items))
    return lengths, (len(lengths) == total and total > 0)


def _carry_names(node: YMap) -> list[str]:
    carry = node.get("carry")
    return carry.keys() if isinstance(carry, YMap) else []


def _check_carry_compat(
    diags: Diagnostics, node: YMap, nid: str, target: ProcSig, base: str
) -> None:
    """Every carry binding needs a same-name output on the target (spec 16).

    (Same-type/same-phase refinement layers on later; existence is the rule the
    current cases exercise, and a missing output is the primary failure mode.)
    """
    for cname in _carry_names(node):
        if cname not in target.outputs:
            diags.add(
                errors.CARRY_OUTPUT_MISSING,
                f"carry {cname!r} has no matching output on target process",
                f"{base}.nodes.{nid}.carry.{cname}",
                at=node,
            )


def _object_output_names(sig: ProcSig) -> set[str]:
    return {n for n, s in sig.outputs.items() if s.object_bearing}


def _check_zip(diags: Diagnostics, node: YMap, nid: str, base: str) -> None:
    """Zip-equal length mismatch known at graph phase (spec 17).

    Only literal `each` sources have a graph-known length; if two of them differ,
    the zip-equal traversal is provably ill-formed before runtime.
    """
    lengths, _ = _each_literal_lengths(node)
    if len(set(lengths)) > 1:
        diags.add(errors.ZIP_MISMATCH, "each sources have unequal literal lengths", f"{base}.nodes.{nid}", at=node)


def _check_fold_outputs(
    diags: Diagnostics, node: YMap, nid: str, target: ProcSig, base: str
) -> None:
    """fold output-mode rules for Object outputs (spec 18.1, 18.3)."""
    carry = set(_carry_names(node))
    obj_outputs = {n for n, s in target.outputs.items() if s.object_bearing}
    noncarry_obj = obj_outputs - carry

    outputs = node.get("outputs")
    if isinstance(outputs, YMap):
        # An Object-bearing output must not be dropped or reduced to `last`;
        # it may only be carried or collected (spec 18.1 rule 7).
        for oname in outputs.keys():
            if oname in obj_outputs and _mode_of(outputs.get(oname)) in ("last", "drop"):
                diags.add(
                    errors.OBJECT_OUTPUT_BAD_MODE,
                    f"Object output {oname!r} cannot use last/drop",
                    f"{base}.nodes.{nid}.{oname}",
                    at=node,
                )
    else:
        # With outputs omitted, a non-carry Object output has no way to be
        # exposed and must be listed explicitly with collect (spec 18.3).
        for oname in sorted(noncarry_obj):
            diags.add(
                errors.NONCARRY_OBJECT_OUTPUT_UNLISTED,
                f"non-carry Object output {oname!r} needs an explicit collect",
                f"{base}.nodes.{nid}.{oname}",
                at=node,
            )

    # Empty-traversal + mode:last is invalid when emptiness is graph-known (18.2).
    lengths, all_literal = _each_literal_lengths(node)
    empty_known = all_literal and lengths and all(x == 0 for x in lengths)
    if empty_known and isinstance(outputs, YMap):
        if any(_mode_of(outputs.get(o)) == "last" for o in outputs.keys()):
            diags.add(errors.LAST_ON_EMPTY_FOLD, "mode: last on a graph-empty traversal", f"{base}.nodes.{nid}", at=node)


def _check_do_while_outputs(
    diags: Diagnostics, node: YMap, nid: str, target: ProcSig, base: str
) -> None:
    """do_while Object-output prohibition and condition typing (spec 19)."""
    carry = set(_carry_names(node))
    noncarry_obj = {n for n, s in target.outputs.items() if s.object_bearing} - carry
    for oname in sorted(noncarry_obj):
        diags.add(
            errors.NONCARRY_OBJECT_OUTPUT_IN_DO_WHILE,
            f"do_while forbids non-carry Object output {oname!r}",
            f"{base}.nodes.{nid}.{oname}",
            at=node,
        )

    # condition.output must name a Boolean Data output of the target (spec 19).
    cond = node.get("condition")
    if isinstance(cond, YMap):
        out_node = cond.get("output")
        if isinstance(out_node, YScalar):
            cname = out_node.text
            osig = target.outputs.get(cname)
            is_bool = osig is not None and isinstance(osig.type_expr, Atom) and osig.type_expr.name == "Bool"
            if not is_bool:
                diags.add(
                    errors.BAD_CONDITION_OUTPUT,
                    f"condition.output {cname!r} is not a Boolean output",
                    f"{base}.nodes.{nid}.condition",
                    at=out_node,
                )


def _check_branch(
    diags: Diagnostics, node: YMap, nid: str, sigs: dict[str, ProcSig], processes: YMap, base: str
) -> None:
    """Reject Object-bearing outputs that are not common to both arms.

    We compare the Object-bearing output name sets of the two arm processes; any
    name present in one arm but not the other is a one-sided Object output. If
    `else` is omitted it acts as an implicit identity arm over the Object-bearing
    branch arguments (spec 20), so the "else side" is taken from `args`.
    """
    then_arm = node.get("then")
    else_arm = node.get("else")

    then_proc = then_arm.get("process") if isinstance(then_arm, YMap) else None
    then_obj: set[str] = set()
    if isinstance(then_proc, YScalar) and then_proc.text in sigs:
        then_obj = _object_output_names(sigs[then_proc.text])

    if isinstance(else_arm, YMap):
        else_proc = else_arm.get("process")
        else_obj: set[str] = set()
        if isinstance(else_proc, YScalar) and else_proc.text in sigs:
            else_obj = _object_output_names(sigs[else_proc.text])
    else:
        # Implicit identity else arm: it re-exposes each Object-bearing branch
        # argument as a same-name Object output (spec 20).
        else_obj = set()
        args = node.get("args")
        if isinstance(args, YMap):
            # An arg is Object-bearing if the then-arm's same-name *input* is;
            # arms share argument names/types, so the then signature is a proxy.
            then_inputs = sigs.get(then_proc.text) if isinstance(then_proc, YScalar) else None
            if then_inputs is not None:
                for aname in args.keys():
                    port = then_inputs.inputs.get(aname)
                    if port is not None and port.object_bearing:
                        else_obj.add(aname)

    for name in sorted(then_obj ^ else_obj):
        diags.add(
            errors.ONE_SIDED_OBJECT_OUTPUT,
            f"Object-bearing output {name!r} is not common to both arms",
            f"{base}.nodes.{nid}.{name}",
            at=node,
        )

    # Identity-equivalence for outputs common to both arms (spec 20.2): each arm
    # must derive the output from the *same* branch argument via an identity map.
    # An arm that creates/replaces the output (no map source), or maps it from a
    # different argument, makes the resulting identity arm-dependent.
    then_def = processes.get(then_proc.text) if isinstance(then_proc, YScalar) else None
    else_def = (
        processes.get(else_arm.get("process").text)
        if isinstance(else_arm, YMap) and isinstance(else_arm.get("process"), YScalar)
        else None
    )
    if isinstance(then_def, YMap) and isinstance(else_def, YMap):
        then_src = _map_sources(then_def)
        else_src = _map_sources(else_def)
        for name in sorted(then_obj & else_obj):
            ts, es = then_src.get(name), else_src.get(name)
            if ts is None or es is None or ts != es:
                diags.add(
                    errors.BRANCH_NOT_IDENTITY_EQUIVALENT,
                    f"common Object output {name!r} is not identity-equivalent across arms",
                    f"{base}.nodes.{nid}.{name}",
                    at=node,
                )


def check_nodes(doc: YMap, diags: Diagnostics, sigs: dict[str, ProcSig]) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return

    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        body = proc.get("body")
        if not isinstance(body, YMap):
            continue
        nodes = body.get("nodes")
        if not isinstance(nodes, YSeq):
            continue
        base = f"processes.{pname}.body"
        processes = doc.get("processes")

        for item in nodes.items:
            if not isinstance(item, YMap):
                continue
            kind_node = item.get("kind")
            kind = kind_node.text if isinstance(kind_node, YScalar) else None
            if kind is None:
                continue  # ordinary node: handled by linearity, not here
            id_node = item.get("id")
            nid = id_node.text if isinstance(id_node, YScalar) else "?"

            proc_ref = item.get("process")
            target = sigs.get(proc_ref.text) if isinstance(proc_ref, YScalar) else None

            if kind == "fold":
                if target is not None:
                    _check_carry_compat(diags, item, nid, target, base)
                    _check_fold_outputs(diags, item, nid, target, base)
                _check_zip(diags, item, nid, base)
            elif kind == "do_while":
                # max_iterations is required (spec 19, requirement 5).
                if item.get("max_iterations") is None:
                    diags.add(
                        errors.MISSING_MAX_ITERATIONS,
                        "do_while requires max_iterations",
                        f"{base}.nodes.{nid}",
                        at=item,
                    )
                if target is not None:
                    _check_carry_compat(diags, item, nid, target, base)
                    _check_do_while_outputs(diags, item, nid, target, base)
            elif kind == "branch":
                _check_branch(diags, item, nid, sigs, processes, base)
            elif kind == "map":
                # map uses zip-equal over its each sources (spec 17).
                _check_zip(diags, item, nid, base)
            # `map` has no carry/condition; its feature requirement is derived in
            # the feature pass and its Object flow by the target's completeness.
