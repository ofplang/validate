"""Closed-shape structural validation (spec 2.1, 2.3, 2.7, 6, 7, 8, 10).

Intent: portable v0 YAML is closed by default — at every defined mapping
position only spec-defined keys are allowed, and unknown keys are errors
(spec 2.3). This pass enforces that "closedness" plus the value-kind and
placement rules that do not require a resolved type model, so that later passes
can assume a structurally sane tree.

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
# no output-control section. Structured nodes add their control sections and the
# `outputs` shaping section (spec 21). `bind` (Pure Data, unrestricted, spec 11)
# is allowed wherever a target process is invoked with per-port inputs.
_NODE_ALLOWED = {
    "ordinary": {"id", "process", "state", "bind"},
    "map": {"id", "kind", "process", "each", "bind", "outputs"},
    "fold": {"id", "kind", "process", "each", "carry", "bind", "outputs"},
    "do_while": {
        "id", "kind", "process", "carry", "bind", "condition", "max_iterations", "outputs"
    },
    "branch": {"id", "kind", "condition", "args", "then", "else", "outputs"},
}


def _check_node_sections(diags: Diagnostics, item: YMap, npath: str, mode: str) -> None:
    """Enforce the closed section set for a body node's kind (spec 2.3, 11, 21).

    A section defined for some node kind but not this one is reported with the
    dedicated `section_not_valid_for_kind` code; a key defined for no node kind
    is an ordinary unknown key. An unrecognized `kind` value defers section
    placement (allow the union) so we do not spuriously flag on top of the
    separate unknown-kind concern.
    """
    kind_node = item.get("kind")
    nk = kind_node.text if isinstance(kind_node, YScalar) else None
    if nk is None:
        allowed = _NODE_ALLOWED["ordinary"]
    elif nk in _NODE_ALLOWED:
        allowed = _NODE_ALLOWED[nk]
    else:
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


def _check_closed_map(
    diags: Diagnostics,
    node: YMap,
    allowed: set[str],
    base: str,
    mode: str,
) -> None:
    """Report keys not in ``allowed`` at a closed mapping position."""
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
    closed mapping. Only mappings are inspected; a non-mapping declaration is
    left to the type-resolution passes (it simply fails to resolve there), so
    this pass does not add a value-kind error for it.
    """
    traits = doc.get("traits")
    if isinstance(traits, YMap):
        for name in traits.keys():
            decl = traits.get(name)
            if isinstance(decl, YMap):
                _check_closed_map(diags, decl, _TRAIT_KEYS, f"traits.{name}", mode)

    types = doc.get("types")
    if isinstance(types, YMap):
        for name in types.keys():
            decl = types.get(name)
            if not isinstance(decl, YMap):
                continue
            _check_closed_map(diags, decl, _TYPE_KEYS, f"types.{name}", mode)
            view = decl.get("view")
            if isinstance(view, YMap):
                for fname in view.keys():
                    field = view.get(fname)
                    if isinstance(field, YMap):
                        _check_closed_map(
                            diags, field, _VIEW_FIELD_KEYS, f"types.{name}.view.{fname}", mode
                        )


def _check_process(diags: Diagnostics, pname: str, proc: YNode | None, mode: str) -> None:
    """Validate one process mapping's shape and section placement."""
    base = f"processes.{pname}"
    if not isinstance(proc, YMap):
        diags.add(errors.WRONG_VALUE_KIND, "process must be a mapping", base, at=proc)
        return

    # `kind` is required and selects which sections are legal (spec 10).
    kind_node = proc.get("kind")
    kind = kind_node.text if isinstance(kind_node, YScalar) else None
    if kind is None:
        diags.add(errors.MISSING_REQUIRED_KEY, "process requires 'kind'", f"{base}.kind", at=proc)

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
    _check_closed_map(diags, proc, allowed, base, mode)

    # Close the process's type-parameter and port declaration mappings
    # (spec 6, 8). `type`/`phase` *presence* on a port is a required-key concern
    # owned by the type pass; here we only reject unknown keys, so a port
    # carrying e.g. `description` (spec 2.7 does not define it there) is flagged.
    type_params = proc.get("type_params")
    if isinstance(type_params, YMap):
        for tpname in type_params.keys():
            decl = type_params.get(tpname)
            if isinstance(decl, YMap):
                _check_closed_map(
                    diags, decl, _TYPE_PARAM_KEYS, f"{base}.type_params.{tpname}", mode
                )
    for section in ("inputs", "outputs"):
        ports = proc.get(section)
        if isinstance(ports, YMap):
            for portname in ports.keys():
                port = ports.get(portname)
                if isinstance(port, YMap):
                    _check_closed_map(
                        diags, port, _PORT_KEYS, f"{base}.{section}.{portname}", mode
                    )

    # Composite body: each node must carry an `id` and a `process` target
    # (spec 11). Deeper per-kind node shape is validated in the node layer.
    if kind == "composite":
        body = proc.get("body")
        if isinstance(body, YMap):
            nodes = body.get("nodes")
            if isinstance(nodes, YSeq):
                for i, item in enumerate(nodes.items):
                    npath = f"{base}.body.nodes[{i}]"
                    if not isinstance(item, YMap):
                        diags.add(errors.WRONG_VALUE_KIND, "node must be a mapping", npath, at=item)
                        continue
                    # Closed section set for the node's kind (spec 2.3, 11, 21).
                    _check_node_sections(diags, item, npath, mode)
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
    _check_closed_map(diags, doc, _TOP_LEVEL_KEYS, "<root>", mode)
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
