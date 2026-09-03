"""Structured node validation: map / fold / do_while / branch (spec 16-21).

Intent: structured nodes wrap a target process with loop/branch control, and
each kind imposes extra well-formedness rules on top of the target's own Object
tracking completeness. This pass checks the kind-specific structural rules:

  * `fold` / `do_while` carry bindings need a matching same-name output on the
    target, and an Object-bearing one must be threaded through it -- preserved,
    or consumed and created (structured carry compatibility, spec 16);
  * `map` and `fold` need at least one `each` source, which is what indexes
    their shape (spec 1.1, 17, 18);
  * `do_while` requires an explicit `max_iterations` bound, and exposes a
    reserved `exhausted` output that its `outputs` section never lists
    (spec 19, 19.3); and
  * `branch` requires its two arms to have equal Object skeletons, so that where
    an output Object's identity comes from does not depend on the chosen arm
    (spec 20.2, 12.4.7); a one-sided Object output is reported before that, as
    the more specific mistake it is.

What a node's Object flow is, rather than whether it is well-formed, is the
skeleton the objects pass computes; this pass reads it where a rule is about two
skeletons agreeing.
"""

from __future__ import annotations

from ofplang.validate import errors, skeleton
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.objects import DO_WHILE_RESERVED_OUTPUT, ProcSig
from ofplang.validate.skeleton import Skeleton
from ofplang.validate.types import Atom
from ofplang.validate.yamlnode import YMap, YNode, YScalar, YSeq

# Valid output-control modes per structured node kind (spec 18.1, 19.1, 20.1).
_FOLD_MODES = {"carry", "collect", "drop"}
_DO_WHILE_MODES = {"carry", "collect", "drop"}
_BRANCH_MODES = {"common", "drop"}


def _mode_of(entry: YNode | None) -> str | None:
    if isinstance(entry, YMap):
        m = entry.get("mode")
        if isinstance(m, YScalar):
            return m.text
    return None


def _check_output_modes(
    diags: Diagnostics, outputs: YMap, allowed: set[str], nid: str, base: str
) -> None:
    """Every listed output mode must be one the kind allows (spec 18.1/19.1/20.1).

    A missing mode is left to the (deferred) fully-explicit-listing rule; only a
    present-but-unrecognized mode word is reported here.
    """
    for oname in outputs.keys():
        mode = _mode_of(outputs.get(oname))
        if mode is not None and mode not in allowed:
            diags.add(
                errors.INVALID_OUTPUT_MODE,
                f"invalid output mode {mode!r} for {oname!r}",
                f"{base}.nodes.{nid}.{oname}",
                at=outputs.get(oname),
            )


def _check_carry_listed_as_carry(
    diags: Diagnostics, node: YMap, outputs: YMap, nid: str, base: str
) -> None:
    """When `outputs` is present, every carry binding must be listed with
    `mode: carry` (fold rule 2, do_while rule 2)."""
    for cname in _carry_names(node):
        if _mode_of(outputs.get(cname)) != "carry":
            diags.add(
                errors.CARRY_OUTPUT_NOT_CARRY_MODE,
                f"carry {cname!r} must be listed with mode: carry",
                f"{base}.nodes.{nid}.{cname}",
                at=outputs.get(cname) or node,
            )


def _check_all_outputs_listed(
    diags: Diagnostics, outputs: YMap, target: ProcSig, nid: str, base: str
) -> None:
    """When `outputs` is present, it is fully explicit: every target process
    output must be listed (fold rule 10, do_while rule 18). This includes the
    do_while condition output, which is an ordinary Data output listed with
    collect/drop; only when `outputs` is omitted is it dropped by default
    (spec 19.2)."""
    listed = set(outputs.keys())
    for oname in sorted(target.outputs):
        if oname not in listed:
            diags.add(
                errors.OUTPUT_NOT_LISTED,
                f"target output {oname!r} must be listed when outputs is present",
                f"{base}.nodes.{nid}.{oname}",
                at=outputs,
            )


def _each_literal_lengths(node: YMap) -> list[int]:
    """Lengths of the `each` sources given as sequence literals, which are the
    only ones whose length is known at graph phase (spec 17)."""
    each = node.get("each")
    if not isinstance(each, YMap):
        return []
    lengths: list[int] = []
    for name in each.keys():
        entry = each.get(name)
        if isinstance(entry, YMap):
            val = entry.get("value")
            if isinstance(val, YSeq):
                lengths.append(len(val.items))
    return lengths


def _carry_names(node: YMap) -> list[str]:
    carry = node.get("carry")
    return carry.keys() if isinstance(carry, YMap) else []


def _check_max_iterations(diags: Diagnostics, node: YMap, nid: str, base: str) -> None:
    """`max_iterations` must be an integer of at least 1 (spec 19, requirement 5).

    A `do_while` invokes its target at least once, so a bound below one
    contradicts the node's own semantics. Only a `value` literal is decided
    here: a bound given by `from` is not known until run phase, and 6.2 leaves
    it to the earliest phase at which it is determined. That the source *may*
    be a run-phase value is a separate condition -- `max_iterations` is a
    constant slot with upper bound `run` (spec 11.2) -- and is checked with the
    binding phase-flow in `references.py`.
    """
    entry = node.get("max_iterations")
    if not isinstance(entry, YMap):
        return
    value = entry.get("value")
    if not isinstance(value, YScalar):
        return
    if not value.is_int:
        diags.add(
            errors.INVALID_MAX_ITERATIONS,
            f"max_iterations must be an integer, got {value.text!r}",
            f"{base}.nodes.{nid}.max_iterations",
            at=value,
        )
    elif int(value.text) < 1:
        diags.add(
            errors.INVALID_MAX_ITERATIONS,
            f"max_iterations must be at least 1, got {value.text}",
            f"{base}.nodes.{nid}.max_iterations",
            at=value,
        )


def _check_each_present(diags: Diagnostics, node: YMap, nid: str, base: str) -> None:
    """`map` and `fold` need a traversal length, and only `each` gives them one.

    Their shape is the body L times, indexed by the common length of the `each`
    sources (spec 1.1, 17, 18). With no source there is no L, so an absent
    section and an empty one are the same error.
    """
    each = node.get("each")
    if not isinstance(each, YMap) or not each.keys():
        diags.add(
            errors.MISSING_EACH_SOURCE,
            "a map or fold node needs at least one each source",
            f"{base}.nodes.{nid}",
            at=node,
        )


def _check_carry_threading(
    diags: Diagnostics,
    node: YMap,
    nid: str,
    target: ProcSig,
    target_sk: Skeleton | None,
    base: str,
) -> None:
    """An Object-bearing carry must be threaded through the target (spec 16).

    Either the carried input port's fate is the same-name output port, or that
    input is consumed and that output created. A third arrangement -- the carried
    Object leaving through a collected output while the carry output is created
    -- balances, and Object tracking completeness therefore accepts it, but the
    node's own Object correspondence would have to name a position within a
    collection, which v0 cannot express.

    Asked of the target's skeleton, so a composite target is judged the same way
    as an atomic one; a composite declares no `objects` section and could not be
    judged at all while this read declarations. The requirement is about Object
    identity, so a Pure Data carry is not subject to it.
    """
    if target_sk is None:
        return
    for cname in _carry_names(node):
        out_port = target.outputs.get(cname)
        if out_port is None or not out_port.object_bearing:
            # Data has no identity, so the same-name output is the next carry
            # value whatever it was computed from (spec 16).
            continue
        restricted = skeleton.restrict(target_sk, {cname})
        preserved = restricted.phi.get(cname, (None, None))[0] == cname
        replaced = cname in restricted.consumed and cname in target_sk.created
        if not preserved and not replaced:
            diags.add(
                errors.CARRY_NOT_THREADED,
                f"carry {cname!r} is neither preserved nor replaced by the target",
                f"{base}.nodes.{nid}.carry.{cname}",
                at=node,
            )


def _check_carry_compat(
    diags: Diagnostics, node: YMap, nid: str, target: ProcSig, base: str
) -> None:
    """A carry binding needs a same-name, same-type, same-phase output (spec 16).

    A carry name ``c`` is threaded across iterations as the target's input port
    ``c`` -> output port ``c`` -> next iteration's input ``c``. For that to be
    well-formed the target must provide an output named ``c`` (existence, the
    primary failure mode) whose type and phase match the carried input port
    (spec 16 requires the threaded value be same-name, same-type, same-phase).
    """
    for cname in _carry_names(node):
        out_port = target.outputs.get(cname)
        if out_port is None:
            diags.add(
                errors.CARRY_OUTPUT_MISSING,
                f"carry {cname!r} has no matching output on target process",
                f"{base}.nodes.{nid}.carry.{cname}",
                at=node,
            )
            continue
        # Compare against the carried input port. If the target has no such
        # input the carry binding itself is reported by the reference/linearity
        # layer (binding to an unknown port), so we leave the type/phase check
        # to the well-formed case to avoid a duplicate diagnostic.
        in_port = target.inputs.get(cname)
        if in_port is None:
            continue
        if (
            in_port.type_expr is not None
            and out_port.type_expr is not None
            and in_port.type_expr != out_port.type_expr
        ):
            diags.add(
                errors.CARRY_TYPE_MISMATCH,
                f"carry {cname!r} output type does not match the carried input",
                f"{base}.nodes.{nid}.carry.{cname}",
                at=node,
            )
        if in_port.phase != out_port.phase:
            diags.add(
                errors.CARRY_PHASE_MISMATCH,
                f"carry {cname!r} output phase does not match the carried input",
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
    lengths = _each_literal_lengths(node)
    if len(set(lengths)) > 1:
        diags.add(
            errors.ZIP_MISMATCH,
            "each sources have unequal literal lengths",
            f"{base}.nodes.{nid}",
            at=node,
        )


def _check_fold_outputs(
    diags: Diagnostics, node: YMap, nid: str, target: ProcSig, base: str
) -> None:
    """fold output-mode rules for Object outputs (spec 18.1, 18.3)."""
    carry = set(_carry_names(node))
    obj_outputs = {n for n, s in target.outputs.items() if s.object_bearing}
    noncarry_obj = obj_outputs - carry

    outputs = node.get("outputs")
    if isinstance(outputs, YMap):
        # Modes must be valid for fold, every carry binding must be listed with
        # mode: carry, and the section is fully explicit (spec 18.1 rules 1-2, 10).
        _check_output_modes(diags, outputs, _FOLD_MODES, nid, base)
        _check_carry_listed_as_carry(diags, node, outputs, nid, base)
        _check_all_outputs_listed(diags, outputs, target, nid, base)
        # An Object-bearing output must not be dropped; it may only be carried
        # or collected (spec 18.1 rule 6).
        for oname in outputs.keys():
            if oname in obj_outputs and _mode_of(outputs.get(oname)) == "drop":
                diags.add(
                    errors.OBJECT_OUTPUT_BAD_MODE,
                    f"Object output {oname!r} cannot use drop",
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

    # When `outputs` is present, modes must be valid for do_while and every carry
    # binding must be listed with mode: carry (spec 19.1 rules 1-2). The section
    # is fully explicit -- every target output, including the condition output --
    # but only "otherwise": a non-carry Object output already makes the node
    # invalid (rule 18), so we do not also demand it be listed.
    outputs = node.get("outputs")
    if isinstance(outputs, YMap):
        _check_output_modes(diags, outputs, _DO_WHILE_MODES, nid, base)
        _check_carry_listed_as_carry(diags, node, outputs, nid, base)
        if not noncarry_obj:
            _check_all_outputs_listed(diags, outputs, target, nid, base)
        if DO_WHILE_RESERVED_OUTPUT in outputs.keys():
            diags.add(
                errors.RESERVED_OUTPUT_LISTED,
                f"{DO_WHILE_RESERVED_OUTPUT!r} is the node's own output and is never listed",
                f"{base}.nodes.{nid}.outputs.{DO_WHILE_RESERVED_OUTPUT}",
                at=outputs.key_node(DO_WHILE_RESERVED_OUTPUT),
            )

    # condition.output must name a Boolean Data output of the target (spec 19).
    cond = node.get("condition")
    if isinstance(cond, YMap):
        out_node = cond.get("output")
        if isinstance(out_node, YScalar):
            cname = out_node.text
            osig = target.outputs.get(cname)
            is_bool = (
                osig is not None
                and isinstance(osig.type_expr, Atom)
                and osig.type_expr.name == "Bool"
            )
            if not is_bool:
                diags.add(
                    errors.BAD_CONDITION_OUTPUT,
                    f"condition.output {cname!r} is not a Boolean output",
                    f"{base}.nodes.{nid}.condition",
                    at=out_node,
                )


def _check_branch(
    diags: Diagnostics,
    node: YMap,
    nid: str,
    sigs: dict[str, ProcSig],
    skeletons: dict[str, Skeleton],
    base: str,
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

    else_proc = else_arm.get("process") if isinstance(else_arm, YMap) else None

    if isinstance(else_arm, YMap):
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

    # Object outputs common to both arms (identity equivalence is checked
    # separately below; here we govern how they are exposed and typed).
    common_obj = then_obj & else_obj

    # When `outputs` is present, modes must be valid for branch, and every Object
    # output common to both arms must be exposed with `mode: common` -- it may
    # never be dropped or omitted (spec 20.1 rules 6-7).
    outputs = node.get("outputs")
    if isinstance(outputs, YMap):
        _check_output_modes(diags, outputs, _BRANCH_MODES, nid, base)
        for oname in sorted(common_obj):
            if _mode_of(outputs.get(oname)) != "common":
                diags.add(
                    errors.OBJECT_OUTPUT_BAD_MODE,
                    f"Object output {oname!r} must be exposed with mode: common",
                    f"{base}.nodes.{nid}.{oname}",
                    at=outputs.get(oname) or node,
                )

    # A common output must have the same type and phase in both arms (spec 20.1
    # rule 4). Compare the two arm signatures for the names exposed as common:
    # the explicitly listed `common` outputs, or -- with `outputs` omitted -- the
    # default Object-bearing commons (spec 20.3). Needs both arms to be real
    # processes; an implicit identity else preserves types by construction.
    else_sig = None
    if isinstance(else_arm, YMap):
        else_ref = else_arm.get("process")
        if isinstance(else_ref, YScalar):
            else_sig = sigs.get(else_ref.text)
    then_sig = sigs.get(then_proc.text) if isinstance(then_proc, YScalar) else None
    if then_sig is not None and else_sig is not None:
        if isinstance(outputs, YMap):
            common_names = {o for o in outputs.keys() if _mode_of(outputs.get(o)) == "common"}
        else:
            common_names = set(common_obj)
        for name in sorted(common_names):
            ts = then_sig.outputs.get(name)
            es = else_sig.outputs.get(name)
            if ts is None or es is None:
                continue  # missing from an arm: reported as one-sided / not-common
            if ts.type_expr is None or es.type_expr is None:
                continue  # a malformed type is already reported by the type pass
            if ts.type_expr != es.type_expr or ts.phase != es.phase:
                diags.add(
                    errors.BRANCH_COMMON_TYPE_MISMATCH,
                    f"common output {name!r} has a different type or phase across arms",
                    f"{base}.nodes.{nid}.{name}",
                    at=node,
                )

    # Skeleton equality (spec 20.2). What the arms must agree on is where each
    # Object comes from, which is their skeletons, and both are placed at *this*
    # node so that two arms which both create agree: the creation point is the
    # node, so whichever arm runs, one new Object appears at this node's output.
    #
    # Skipped where an output is one-sided, which is reported just above: the
    # skeletons then differ because of that, and saying so twice describes one
    # mistake in two ways.
    if then_obj == else_obj:
        then_sk = skeletons.get(then_proc.text) if isinstance(then_proc, YScalar) else None
        if isinstance(else_arm, YMap):
            else_sk = skeletons.get(else_proc.text) if isinstance(else_proc, YScalar) else None
        else:
            # The omitted arm is the identity correspondence over the
            # Object-bearing arguments (spec 20.3, 12.4.5).
            else_sk = skeleton.identity_map(sorted(else_obj))
        mismatched = (
            then_sk is not None
            and else_sk is not None
            and not skeleton.equal(then_sk.placed_at(nid), else_sk.placed_at(nid))
        )
        if mismatched:
            diags.add(
                errors.BRANCH_SKELETON_MISMATCH,
                "the two arms have unequal Object skeletons",
                f"{base}.nodes.{nid}",
                at=node,
            )


def check_nodes(
    doc: YMap,
    diags: Diagnostics,
    sigs: dict[str, ProcSig],
    skeletons: dict[str, Skeleton] | None = None,
) -> None:
    skeletons = skeletons or {}
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

            target_sk = (
                skeletons.get(proc_ref.text) if isinstance(proc_ref, YScalar) else None
            )

            if kind == "fold":
                if target is not None:
                    _check_carry_compat(diags, item, nid, target, base)
                    _check_carry_threading(diags, item, nid, target, target_sk, base)
                    _check_fold_outputs(diags, item, nid, target, base)
                _check_zip(diags, item, nid, base)
                _check_each_present(diags, item, nid, base)
            elif kind == "do_while":
                # max_iterations is required (spec 19, requirement 5).
                if item.get("max_iterations") is None:
                    diags.add(
                        errors.MISSING_MAX_ITERATIONS,
                        "do_while requires max_iterations",
                        f"{base}.nodes.{nid}",
                        at=item,
                    )
                else:
                    _check_max_iterations(diags, item, nid, base)
                if target is not None:
                    _check_carry_compat(diags, item, nid, target, base)
                    _check_carry_threading(diags, item, nid, target, target_sk, base)
                    _check_do_while_outputs(diags, item, nid, target, base)
            elif kind == "branch":
                _check_branch(diags, item, nid, sigs, skeletons, base)
            elif kind == "map":
                # map uses zip-equal over its each sources (spec 17).
                _check_zip(diags, item, nid, base)
                _check_each_present(diags, item, nid, base)
            # `map` has no carry/condition; its feature requirement is derived in
            # the feature pass and its Object flow by the target's completeness.
