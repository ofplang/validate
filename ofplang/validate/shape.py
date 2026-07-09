"""Closed-shape structural validation (spec 2.1, 2.3, 10).

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

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.validator import EXTENSION_TOLERANT
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode

# Allowed top-level keys (spec 2, 2.3). Sections may be omitted; only
# `processes` is semantically required (checked below).
_TOP_LEVEL_KEYS = {"spec_version", "features", "traits", "types", "processes", "entry"}

# Per-kind allowed process keys. The *misplaced* section variants — `objects`
# on a composite, `scheduling` on an atomic — get their own specific codes
# (spec 10.2, 23.3). To avoid also reporting them as generic unknown keys, both
# section names are included in *both* allowed sets: placement is judged by the
# dedicated checks below, not by the closed-key check.
_ATOMIC_KEYS = {
    "kind", "inputs", "outputs", "objects", "scheduling", "script",
    "type_params", "where", "traits", "contracts",
}
_COMPOSITE_KEYS = {
    "kind", "inputs", "outputs", "body", "objects", "scheduling",
    "type_params", "where", "traits", "contracts",
}


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
                diags.add(errors.NULL_STATIC_VALUE, "null is not a valid static view value", base, at=node)
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
        return
    text = node.text
    parts = text.split(".")
    ok = len(parts) == 2 and all(p.isdigit() for p in parts)
    if not ok:
        diags.add(
            errors.MALFORMED_SPEC_VERSION,
            f"spec_version must be MAJOR.MINOR, got {text!r}",
            "spec_version",
            at=node,
        )


def _check_process(diags: Diagnostics, pname: str, proc: YNode, mode: str) -> None:
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
        diags.add(errors.OBJECTS_ON_COMPOSITE, "objects is atomic-only", f"{base}.objects", at=proc.get("objects"))
    if kind == "atomic" and proc.get("scheduling") is not None:
        diags.add(errors.SCHEDULING_ON_ATOMIC, "scheduling is composite-only", f"{base}.scheduling", at=proc.get("scheduling"))

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
                    if item.get("id") is None:
                        diags.add(errors.MISSING_REQUIRED_KEY, "node requires 'id'", f"{npath}.id", at=item)
                    # Most node kinds target a single `process`; `branch` is the
                    # exception — it selects between `then`/`else` arms and so
                    # requires `then` instead of a top-level `process` (spec 20).
                    node_kind = item.get("kind")
                    is_branch = isinstance(node_kind, YScalar) and node_kind.text == "branch"
                    if is_branch:
                        if item.get("then") is None:
                            diags.add(errors.MISSING_REQUIRED_KEY, "branch requires 'then'", f"{npath}.then", at=item)
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

    processes = doc.get("processes")
    if processes is None:
        diags.add(errors.MISSING_REQUIRED_KEY, "processes section is required", "processes", at=doc)
    elif not isinstance(processes, YMap):
        diags.add(errors.WRONG_VALUE_KIND, "processes must be a mapping", "processes", at=processes)
    else:
        for pname in processes.keys():
            _check_process(diags, pname, processes.get(pname), mode)
