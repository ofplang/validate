"""Closed-shape structural validation (spec 2.1, 2.3, 2.7, 6, 7, 8, 10).

Intent: portable v0 YAML is closed by default — at every defined mapping
position only spec-defined keys are allowed, and unknown keys are errors
(spec 2.3). This pass enforces that "closedness" plus the value-kind and
placement rules that do not require a resolved type model, so that later passes
can assume a structurally sane tree.

Value kind is part of that: at every position this pass inspects, a value of the
wrong kind is reported here (``wrong_value_kind``) rather than skipped. It used to
be left "to the pass that reads it", but for most of these positions no pass reads
it -- a `body` written as a string, or a port declaration written as a scalar, was
accepted outright -- and a document nothing rejects is one every consumer has to
survive on its own.

Two cross-cutting rules are handled here because they apply regardless of
position:
  * ``null`` is never a valid value in v0 (spec 2.3), enforced by a whole-tree
    scan; and
  * key prefixes ``$`` (only ``$import`` is legal) and ``x-`` (extension keys,
    accepted only in extension-tolerant mode) gate whether an otherwise unknown
    key is reported.
"""

from __future__ import annotations

import re

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.validator import EXTENSION_TOLERANT
from ofplang.validate.yamlnode import YMap, YNode, YScalar, YSeq

# Allowed top-level keys (spec 2, 2.3). Sections may be omitted; only
# `processes` is semantically required (checked below). `description` is
# optional metadata allowed at the document root (spec 2.7).
_TOP_LEVEL_KEYS = {
    "spec_version", "features", "traits", "types", "processes", "entry", "description",
}

# The two process kinds v0 defines (spec 10). An implementation-defined kind is
# not accepted, in either mode (spec 26), so this is not mode-dependent.
_PROCESS_KINDS = {"atomic", "composite"}

# Per-kind allowed process keys. The *misplaced* section variants — `objects`
# on a composite, `scheduling` on an atomic — get their own specific codes
# (spec 10.2, 23.3). To avoid also reporting them as generic unknown keys, both
# section names are included in *both* allowed sets: placement is judged by the
# dedicated checks below, not by the closed-key check.
# `description` is optional metadata allowed on a process definition (spec 2.7).
_ATOMIC_KEYS = {
    "kind", "inputs", "outputs", "objects", "scheduling", "script",
    "type_params", "where", "traits", "contracts", "description",
}
_COMPOSITE_KEYS = {
    "kind", "inputs", "outputs", "body", "objects", "scheduling",
    "type_params", "where", "traits", "contracts", "description",
}

# Closed key sets for the type/trait layer and process port/type-param
# declarations (spec 2.3, 7.2, 7.4, 6, 8). `description` is allowed only on the
# trait and type *definitions* (spec 2.7); it is deliberately absent from the
# view-field, port, and type-parameter sets so that a `description` there is an
# unknown key. Non-mapping declarations at these positions are left untouched:
# closedness applies to mappings only, and their malformedness surfaces (or is
# tolerated) in the type-resolution passes as today.
_TRAIT_KEYS = {"description"}
_TYPE_KEYS = {"domain", "implements", "view", "description"}
_VIEW_FIELD_KEYS = {"type", "value"}
_PORT_KEYS = {"type", "phase"}
_TYPE_PARAM_KEYS = {"domain"}

# Every binding / output-control section name a node may legally carry, across
# all kinds. A key outside this set on a node is an unknown key; a key inside it
# but not allowed for the node's kind is a section-not-valid-for-kind error
# (spec 2.3, 11, 21).
_NODE_SECTION_KEYS = {
    "id", "kind", "process", "state", "bind", "each", "carry",
    "condition", "max_iterations", "args", "then", "else", "outputs",
}

# Allowed sections per node kind. An ordinary (unkinded) node uses `state` for
# Object-bearing linear inputs and `bind` for Pure Data inputs (spec 11); it has
# no output-control section. `fold`, `do_while` and `branch` add their control
# sections and the `outputs` shaping section (spec 21). `bind` (Pure Data,
# unrestricted, spec 11) is allowed wherever a target process is invoked with
# per-port inputs.
#
# `map` deliberately has no `outputs`: its output shape is fixed -- every target
# output p: T is exposed as Array<T> -- so v0 defines no `map.outputs` to shape
# it with (spec 17, 21, summary rule 32). Writing one is a section-not-valid-for
# -kind error rather than a silently ignored section.
_NODE_ALLOWED = {
    "ordinary": {"id", "process", "state", "bind"},
    "map": {"id", "kind", "process", "each", "bind"},
    "fold": {"id", "kind", "process", "each", "carry", "bind", "outputs"},
    "do_while": {
        "id", "kind", "process", "carry", "bind", "condition", "max_iterations", "outputs"
    },
    "branch": {"id", "kind", "condition", "args", "then", "else", "outputs"},
}

# The node kinds v0 defines (spec 17-21). `ordinary` is this module's name for a
# node that writes no `kind` at all, not a value that may be written, so it is
# excluded -- `kind: ordinary` is as unknown as any other undefined value.
_NODE_KINDS = frozenset(_NODE_ALLOWED) - {"ordinary"}

# Closed mappings nested inside a process, all of them positions v0 defines
# (spec 2.3). Grouped here so "what is closed, and to what" is one table.
_OBJECTS_KEYS = {"map", "consume", "create", "transform"}          # spec 14.1-14.4
_TRANSFORM_ENTRY_KEYS = {"kind", "inputs", "outputs"}              # spec 14.4
_CONTRACTS_KEYS = {"requires", "ensures"}                          # spec 9.1
_CONTRACT_ENTRY_KEYS = {"expr"}                                    # spec 9
_SCRIPT_KEYS = {"language", "code"}                                # spec 22
_BODY_KEYS = {"nodes", "returns"}                                  # spec 10.2
_OUTPUTS_ENTRY_KEYS = {"mode"}                                     # spec 21
_ARM_KEYS = {"process"}                                            # spec 20
# A source entry carries exactly one of from/value (spec 2.6.6); the arity is the
# reference pass's concern, the key set is this one's. `body.returns` entries are
# closed to the same pair: v0 does not define their keys, and this is the set that
# rejects nothing already accepted.
_SOURCE_ENTRY_KEYS = {"from", "value"}
# `condition` differs by node kind: a branch reads a body dataflow reference, a
# do_while names one of the target's outputs (spec 2.6.7).
_CONDITION_KEYS = {"branch": {"from"}, "do_while": {"output"}}
# Node sections whose values are per-port source entries (spec 11).
_BINDING_SECTIONS = ("state", "bind", "carry", "each", "args")


def _check_node_sections(diags: Diagnostics, item: YMap, npath: str, mode: str) -> None:
    """Enforce the closed section set for a body node's kind (spec 2.3, 11, 21).

    A section defined for some node kind but not this one is reported with the
    dedicated `section_not_valid_for_kind` code; a key defined for no node kind
    is an ordinary unknown key. An unrecognized `kind` value is reported once,
    and section placement then defers to the union of every kind's sections: the
    author has one thing to fix, and guessing which sections they meant would
    bury it under errors derived from it.
    """
    kind_node = item.get("kind")
    nk = kind_node.text if isinstance(kind_node, YScalar) else None
    if nk is None:
        allowed = _NODE_ALLOWED["ordinary"]
    elif nk in _NODE_KINDS:
        allowed = _NODE_ALLOWED[nk]
    else:
        diags.add(
            errors.UNKNOWN_NODE_KIND,
            f"unknown node kind {nk!r}",
            f"{npath}.kind",
            at=kind_node,
        )
        allowed = set().union(*_NODE_ALLOWED.values())

    for key in item.keys():
        key_node = item.key_node(key)
        ext = _classify_extension_key(key, mode)
        if ext is not None:
            diags.add(ext, f"disallowed key {key!r}", f"{npath}.{key}", at=key_node)
            continue
        if key.startswith("$") or key.startswith("x-"):
            continue
        if key in allowed:
            continue
        if key in _NODE_SECTION_KEYS:
            diags.add(
                errors.SECTION_NOT_VALID_FOR_KIND,
                f"section {key!r} is not valid for node kind {nk or 'ordinary'!r}",
                f"{npath}.{key}",
                at=key_node,
            )
        else:
            diags.add(errors.UNKNOWN_KEY, f"unknown key {key!r}", f"{npath}.{key}", at=key_node)


def _check_node_interior(diags: Diagnostics, item: YMap, npath: str, mode: str) -> None:
    """Close the mappings *inside* a body node (spec 2.3, 11, 20, 21, 2.6.6, 2.6.7).

    Reached from the same walk as the node's own sections, so nothing here costs a
    second traversal. A section or entry written as the wrong value kind is reported
    here: this is the only pass that looks at these positions structurally.
    """
    kind_node = item.get("kind")
    nk = kind_node.text if isinstance(kind_node, YScalar) else None

    # Per-port source entries in every binding section.
    for section in _BINDING_SECTIONS:
        ports = _want_map(diags, item.get(section), f"{npath}.{section}", section)
        if ports is None:
            continue
        for portname in ports.keys():
            path = f"{npath}.{section}.{portname}"
            entry = _want_map(diags, ports.get(portname), path, "a binding source entry")
            if entry is not None:
                check_closed_map(diags, entry, _SOURCE_ENTRY_KEYS, path, mode)

    # Output-shaping entries: one `mode` per exposed port (spec 21).
    outs = _want_map(diags, item.get("outputs"), f"{npath}.outputs", "outputs")
    if outs is not None:
        for portname in outs.keys():
            path = f"{npath}.outputs.{portname}"
            entry = _want_map(diags, outs.get(portname), path, "an output-shaping entry")
            if entry is not None:
                check_closed_map(diags, entry, _OUTPUTS_ENTRY_KEYS, path, mode)

    # `condition` is shaped by the node kind, so an unknown kind has no shape to
    # check against and is left alone -- the kind itself is already reported.
    cond = _want_map(diags, item.get("condition"), f"{npath}.condition", "condition")
    if cond is not None and nk in _CONDITION_KEYS:
        check_closed_map(diags, cond, _CONDITION_KEYS[nk], f"{npath}.condition", mode)

    # Branch arms name a process and nothing else; arguments come from `args`
    # (spec 20).
    for arm in ("then", "else"):
        path = f"{npath}.{arm}"
        arm_node = _want_map(diags, item.get(arm), path, f"a branch {arm} arm")
        if arm_node is not None:
            check_closed_map(diags, arm_node, _ARM_KEYS, path, mode)


def _classify_extension_key(key: str, mode: str) -> str | None:
    """Decide how a non-standard key is treated, independent of position.

    Returns an error code to emit, or ``None`` if the key is acceptable here
    (an ``x-`` key in extension-tolerant mode). Standard keys return ``None``
    too and are validated against the position's allowed set by the caller.
    """
    if key.startswith("$"):
        # `$import` is the only legal $-key and is removed during import
        # resolution; anything else (or a leftover) is reserved (spec 2.4).
        if key == "$import":
            return None
        return errors.RESERVED_DOLLAR_KEY
    if key.startswith("x-"):
        # Extension keys are portable-v0 errors unless explicitly tolerated.
        return None if mode == EXTENSION_TOLERANT else errors.UNKNOWN_KEY
    return None


def check_closed_map(
    diags: Diagnostics,
    node: YMap,
    allowed: set[str] | frozenset[str],
    base: str,
    mode: str,
) -> None:
    """Report keys not in ``allowed`` at a closed mapping position.

    Public because closedness is enforced wherever a position is already being
    walked: the scheduling pass closes the policy mappings it visits rather than
    making this module walk them a second time.
    """
    for key in node.keys():
        # Point the diagnostic at the offending key node itself.
        key_node = node.key_node(key)
        ext = _classify_extension_key(key, mode)
        if ext is not None:
            diags.add(ext, f"disallowed key {key!r}", f"{base}.{key}", at=key_node)
            continue
        if key.startswith("$") or key.startswith("x-"):
            # Accepted extension/import key: skip the allowed-set check.
            continue
        if key not in allowed:
            diags.add(errors.UNKNOWN_KEY, f"unknown key {key!r}", f"{base}.{key}", at=key_node)


def _scan_nulls(diags: Diagnostics, node: YNode, base: str) -> None:
    """Whole-tree null scan (spec 2.3): explicit null is invalid everywhere.

    Done structurally rather than per-field because the rule is universal in v0
    core; future nullable features would carve out exceptions here.
    """
    if isinstance(node, YScalar):
        if node.is_null:
            # A null in a view field's static `value` position gets the specific
            # code (spec 7.4); every other null is the generic error (spec 2.3).
            # Path-based classification keeps this universal scan single-pass
            # without threading view context through the recursion.
            if ".view." in base and base.endswith(".value"):
                diags.add(
                    errors.NULL_STATIC_VALUE,
                    "null is not a valid static view value",
                    base,
                    at=node,
                )
            else:
                diags.add(errors.NULL_VALUE, "null is not a valid v0 value", base, at=node)
        return
    if isinstance(node, YSeq):
        for i, item in enumerate(node.items):
            _scan_nulls(diags, item, f"{base}[{i}]")
        return
    if isinstance(node, YMap):
        for k, v in node.entries:
            _scan_nulls(diags, v, f"{base}.{k.text}")


def _want_map(diags: Diagnostics, node: YNode | None, path: str, what: str) -> YMap | None:
    """The mapping at `path`, or None -- reporting the kind when something else is there.

    Absent is not a kind error (whether a position is required is a different rule,
    owned by the check that requires it), so None passes through silently.
    """
    if node is None or isinstance(node, YMap):
        return node
    diags.add(errors.WRONG_VALUE_KIND, f"{what} must be a mapping", path, at=node)
    return None


def _want_seq(diags: Diagnostics, node: YNode | None, path: str, what: str) -> YSeq | None:
    """The sequence at `path`, or None -- reporting the kind when something else is there."""
    if node is None or isinstance(node, YSeq):
        return node
    diags.add(errors.WRONG_VALUE_KIND, f"{what} must be a sequence", path, at=node)
    return None


def _check_spec_version(diags: Diagnostics, doc: YMap) -> None:
    """Validate reserved `spec_version` metadata format (spec 2.1).

    Must be a string scalar of the form MAJOR.MINOR. A null was already caught
    by the null scan, so we stay silent on null to avoid double-reporting.
    """
    node = doc.get("spec_version")
    if node is None:
        return  # omission is allowed in v0
    if not isinstance(node, YScalar) or node.is_null:
        return  # a null was already reported by the null scan
    # Must be a *string* scalar of the form MAJOR.MINOR (spec 2.1). A non-string
    # scalar (e.g. the YAML float `0.0`, tagged float not str) is malformed, as is
    # any non-two-number shape. Digits are ASCII only (`\d` under re.ASCII).
    if not node.is_str or re.fullmatch(r"\d+\.\d+", node.text, re.ASCII) is None:
        diags.add(
            errors.MALFORMED_SPEC_VERSION,
            f"spec_version must be a MAJOR.MINOR string, got {node.text!r}",
            "spec_version",
            at=node,
        )


def _check_description(diags: Diagnostics, mapping: YMap, path: str) -> None:
    """Validate one optional `description` metadata value (spec 2.7).

    `description` must be a YAML string scalar. A null is left to the null scan
    (spec 2.3), so we stay silent on null to avoid double-reporting, mirroring
    `_check_spec_version`. Any other non-string value is malformed.
    """
    node = mapping.get("description")
    if node is None:
        return  # optional at every position that allows it
    if isinstance(node, YScalar) and node.is_null:
        return  # a null was already reported by the null scan
    if not (isinstance(node, YScalar) and node.is_str):
        diags.add(
            errors.MALFORMED_DESCRIPTION,
            "description must be a string scalar",
            path,
            at=node,
        )


def _check_descriptions(diags: Diagnostics, doc: YMap) -> None:
    """Validate `description` metadata at the four positions v0 defines it
    (spec 2.7): the document root, and trait, type, and process definitions.

    Only the value kind is checked here. Whether `description` is an *allowed*
    key at a closed position (the document root and processes) is enforced by
    the closed-key checks; at trait and type definitions `description` rides
    along as metadata and is validated for kind only.
    """
    _check_description(diags, doc, "description")
    for section in ("traits", "types", "processes"):
        block = doc.get(section)
        if not isinstance(block, YMap):
            continue
        for name in block.keys():
            decl = block.get(name)
            if isinstance(decl, YMap):
                _check_description(diags, decl, f"{section}.{name}.description")


def _check_types_and_traits(diags: Diagnostics, doc: YMap, mode: str) -> None:
    """Close the type/trait layer's declaration mappings (spec 2.3, 7.2, 7.4).

    Each trait definition, type definition, and view-field declaration is a
    closed mapping, and one written as another kind is reported here. A type
    declaration that is not a mapping also fails to resolve later, so such a
    document collects the consequence as well as the cause; the cause is the one
    that says what to change.
    """
    traits = _want_map(diags, doc.get("traits"), "traits", "traits")
    if traits is not None:
        for name in traits.keys():
            decl = _want_map(diags, traits.get(name), f"traits.{name}", "a trait definition")
            if decl is not None:
                check_closed_map(diags, decl, _TRAIT_KEYS, f"traits.{name}", mode)

    types = _want_map(diags, doc.get("types"), "types", "types")
    if types is not None:
        for name in types.keys():
            decl = _want_map(diags, types.get(name), f"types.{name}", "a type definition")
            if decl is None:
                continue
            check_closed_map(diags, decl, _TYPE_KEYS, f"types.{name}", mode)
            view = _want_map(diags, decl.get("view"), f"types.{name}.view", "view")
            if view is not None:
                for fname in view.keys():
                    path = f"types.{name}.view.{fname}"
                    field = _want_map(diags, view.get(fname), path, "a view field declaration")
                    if field is not None:
                        check_closed_map(diags, field, _VIEW_FIELD_KEYS, path, mode)


def _check_process(diags: Diagnostics, pname: str, proc: YNode | None, mode: str) -> None:
    """Validate one process mapping's shape and section placement."""
    base = f"processes.{pname}"
    if not isinstance(proc, YMap):
        diags.add(errors.WRONG_VALUE_KIND, "process must be a mapping", base, at=proc)
        return

    # `kind` is required and selects which sections are legal (spec 10). A value
    # v0 does not define is reported once; section placement then defers to the
    # union of both kinds' keys, so the one thing to fix is not buried under
    # errors derived from it.
    kind_node = proc.get("kind")
    kind = kind_node.text if isinstance(kind_node, YScalar) else None
    if kind is None:
        diags.add(errors.MISSING_REQUIRED_KEY, "process requires 'kind'", f"{base}.kind", at=proc)
    elif kind not in _PROCESS_KINDS:
        diags.add(
            errors.UNKNOWN_PROCESS_KIND,
            f"unknown process kind {kind!r}",
            f"{base}.kind",
            at=kind_node,
        )

    # Placement rules with dedicated codes: objects only on atomic (spec 14),
    # scheduling only on composite (spec 23.3). Emit the specific code and rely
    # on the allowed-set check to skip re-reporting these keys as unknown.
    if kind == "composite" and proc.get("objects") is not None:
        diags.add(
            errors.OBJECTS_ON_COMPOSITE,
            "objects is atomic-only",
            f"{base}.objects",
            at=proc.get("objects"),
        )
    if kind == "atomic" and proc.get("scheduling") is not None:
        diags.add(
            errors.SCHEDULING_ON_ATOMIC,
            "scheduling is composite-only",
            f"{base}.scheduling",
            at=proc.get("scheduling"),
        )

    # Closed-key check against the kind's allowed set. Unknown `kind` values are
    # left to a later pass; here we default to the union so we do not spuriously
    # flag keys when the kind is missing/unrecognized.
    if kind == "atomic":
        allowed = _ATOMIC_KEYS
    elif kind == "composite":
        allowed = _COMPOSITE_KEYS
    else:
        allowed = _ATOMIC_KEYS | _COMPOSITE_KEYS
    check_closed_map(diags, proc, allowed, base, mode)

    # Close the process's type-parameter and port declaration mappings
    # (spec 6, 8). `type`/`phase` *presence* on a port is a required-key concern
    # owned by the type pass; here we only reject unknown keys, so a port
    # carrying e.g. `description` (spec 2.7 does not define it there) is flagged.
    type_params = _want_map(
        diags, proc.get("type_params"), f"{base}.type_params", "type_params"
    )
    if type_params is not None:
        for tpname in type_params.keys():
            path = f"{base}.type_params.{tpname}"
            decl = _want_map(diags, type_params.get(tpname), path, "a type parameter")
            if decl is not None:
                check_closed_map(diags, decl, _TYPE_PARAM_KEYS, path, mode)
    for section in ("inputs", "outputs"):
        ports = _want_map(diags, proc.get(section), f"{base}.{section}", section)
        if ports is None:
            continue
        for portname in ports.keys():
            path = f"{base}.{section}.{portname}"
            port = _want_map(diags, ports.get(portname), path, "a port declaration")
            if port is not None:
                check_closed_map(diags, port, _PORT_KEYS, path, mode)

    # The remaining closed mappings a process may carry. Placement (which kind may
    # carry `objects` / `scheduling` / `body`) is judged above; this only asks what
    # keys the section itself may hold, which does not depend on the kind.
    objects = _want_map(diags, proc.get("objects"), f"{base}.objects", "objects")
    if objects is not None:
        check_closed_map(diags, objects, _OBJECTS_KEYS, f"{base}.objects", mode)
        transform = _want_seq(
            diags, objects.get("transform"), f"{base}.objects.transform", "transform"
        )
        if transform is not None:
            for i, item in enumerate(transform.items):
                path = f"{base}.objects.transform[{i}]"
                entry = _want_map(diags, item, path, "a transform entry")
                if entry is not None:
                    check_closed_map(diags, entry, _TRANSFORM_ENTRY_KEYS, path, mode)

    contracts = _want_map(diags, proc.get("contracts"), f"{base}.contracts", "contracts")
    if contracts is not None:
        check_closed_map(diags, contracts, _CONTRACTS_KEYS, f"{base}.contracts", mode)
        for scope in ("requires", "ensures"):
            section_node = _want_seq(
                diags, contracts.get(scope), f"{base}.contracts.{scope}", scope
            )
            if section_node is None:
                continue
            for i, item in enumerate(section_node.items):
                path = f"{base}.contracts.{scope}[{i}]"
                entry = _want_map(diags, item, path, "a contract entry")
                if entry is not None:
                    check_closed_map(diags, entry, _CONTRACT_ENTRY_KEYS, path, mode)

    script = _want_map(diags, proc.get("script"), f"{base}.script", "script")
    if script is not None:
        check_closed_map(diags, script, _SCRIPT_KEYS, f"{base}.script", mode)

    # Composite body: each node must carry an `id` and a `process` target
    # (spec 11). Deeper per-kind node shape is validated in the node layer.
    if kind == "composite":
        body = _want_map(diags, proc.get("body"), f"{base}.body", "body")
        if body is not None:
            check_closed_map(diags, body, _BODY_KEYS, f"{base}.body", mode)
            # A return connects an internal output to the boundary (spec 12.3).
            # v0 does not enumerate the entry's keys; closing it to the source-entry
            # pair rejects nothing that was accepted before.
            returns = _want_map(
                diags, body.get("returns"), f"{base}.body.returns", "returns"
            )
            if returns is not None:
                for rname in returns.keys():
                    path = f"{base}.body.returns.{rname}"
                    ret = _want_map(diags, returns.get(rname), path, "a return entry")
                    if ret is not None:
                        check_closed_map(diags, ret, _SOURCE_ENTRY_KEYS, path, mode)
            nodes = _want_seq(diags, body.get("nodes"), f"{base}.body.nodes", "nodes")
            if nodes is not None:
                for i, item in enumerate(nodes.items):
                    npath = f"{base}.body.nodes[{i}]"
                    if not isinstance(item, YMap):
                        diags.add(errors.WRONG_VALUE_KIND, "node must be a mapping", npath, at=item)
                        continue
                    # Closed section set for the node's kind (spec 2.3, 11, 21),
                    # then the mappings inside those sections.
                    _check_node_sections(diags, item, npath, mode)
                    _check_node_interior(diags, item, npath, mode)
                    if item.get("id") is None:
                        diags.add(
                            errors.MISSING_REQUIRED_KEY,
                            "node requires 'id'",
                            f"{npath}.id",
                            at=item,
                        )
                    # Most node kinds target a single `process`; `branch` is the
                    # exception — it selects between `then`/`else` arms and so
                    # requires `then` instead of a top-level `process` (spec 20).
                    node_kind = item.get("kind")
                    is_branch = isinstance(node_kind, YScalar) and node_kind.text == "branch"
                    if is_branch:
                        if item.get("then") is None:
                            diags.add(
                                errors.MISSING_REQUIRED_KEY,
                                "branch requires 'then'",
                                f"{npath}.then",
                                at=item,
                            )
                    elif item.get("process") is None:
                        diags.add(
                            errors.MISSING_REQUIRED_KEY,
                            "node requires 'process'",
                            f"{npath}.process",
                            at=item,
                        )


def check_shape(doc: YNode, diags: Diagnostics, mode: str) -> None:
    """Top-level shape entry point.

    Order: null scan first (universal), then the top-level closed-key check,
    then metadata format, then per-process shape. `processes` is the one
    required section (spec 2.3).
    """
    if not isinstance(doc, YMap):
        diags.add(errors.WRONG_VALUE_KIND, "document root must be a mapping", "<root>", at=doc)
        return

    _scan_nulls(diags, doc, "<root>")
    check_closed_map(diags, doc, _TOP_LEVEL_KEYS, "<root>", mode)
    _check_spec_version(diags, doc)
    _check_descriptions(diags, doc)
    _check_types_and_traits(diags, doc, mode)

    processes = doc.get("processes")
    if processes is None:
        diags.add(errors.MISSING_REQUIRED_KEY, "processes section is required", "processes", at=doc)
    elif not isinstance(processes, YMap):
        diags.add(errors.WRONG_VALUE_KIND, "processes must be a mapping", "processes", at=processes)
    else:
        for pname in processes.keys():
            _check_process(diags, pname, processes.get(pname), mode)
