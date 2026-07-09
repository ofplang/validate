"""Identifier grammar and reserved-name checking (spec 2.4).

Intent: v0 identifiers are deliberately restrictive (case-sensitive ASCII, no
dot, a fixed reserved-word list) so that future namespace/module syntax stays
open and so documents are portable. This pass validates *declaration* sites
(names the document introduces) rather than reference sites: a reference to a
badly named thing is better reported where the name is declared, which keeps the
error count from doubling and points the author at the root cause.
"""

from __future__ import annotations

import re

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.yamlnode import YMap, YNode

# The core identifier grammar (spec 2.4). Anchored so the whole name must match.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Reserved keywords (spec 2.4). These are structural keys and role words that
# must not be reused as user-defined names anywhere. Built-in type/trait names
# (Bool/Int/Float/String/Array/Numeric) are handled separately as
# `redeclare_builtin` in the type layer, so they are intentionally absent here.
RESERVED_NAMES = frozenset(
    {
        "inputs", "outputs", "self", "view", "objects", "features", "traits",
        "types", "processes", "entry", "body", "nodes", "returns", "state",
        "bind", "carry", "each", "args", "then", "else", "condition",
        "scheduling", "policies", "during", "object", "from", "to", "kind",
        "process", "phase", "type", "value", "script", "contracts", "requires",
        "ensures",
    }
)


def classify_name(name: str) -> str | None:
    """Return the error code for a declared ``name``, or ``None`` if it is legal.

    The order matters: a dot is called out specifically (it is the reserved
    path separator, spec 2.4) so the author gets a precise reason, before the
    generic grammar failure. Reserved-word collisions are only meaningful once
    the name is otherwise grammatical.
    """
    if not _IDENT_RE.match(name):
        # Distinguish the common, intentional-looking "used a dotted/qualified
        # name" mistake from arbitrary garbage.
        if "." in name:
            return errors.DOT_IN_IDENTIFIER
        return errors.INVALID_IDENTIFIER
    if name in RESERVED_NAMES:
        return errors.RESERVED_NAME
    return None


def _check(diags: Diagnostics, name: str, path: str, at=None) -> None:
    code = classify_name(name)
    if code is not None:
        diags.add(code, f"invalid identifier {name!r}", path, at=at)


def _check_map_keys(diags: Diagnostics, node: YNode | None, base: str) -> None:
    """Validate every key of a mapping as a declaration name."""
    if not isinstance(node, YMap):
        return
    for key in node.keys():
        # Anchor the diagnostic at the declaring key node.
        _check(diags, key, f"{base}.{key}", at=node.key_node(key))


def check_identifiers(doc: YMap, diags: Diagnostics) -> None:
    """Validate all declaration-site names in the document.

    We walk the fixed set of positions where v0 introduces names. This is
    intentionally explicit (rather than a blanket tree walk) so that keyword
    keys like ``kind``/``phase`` — which are structural, not user names — are
    never mistaken for reserved-name violations.
    """
    # Top-level nominal namespaces: process, type, and trait names.
    _check_map_keys(diags, doc.get("processes"), "processes")
    _check_map_keys(diags, doc.get("types"), "types")
    _check_map_keys(diags, doc.get("traits"), "traits")

    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return

    # Per-process declaration sites: type parameters, ports, and node ids.
    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        base = f"processes.{pname}"
        _check_map_keys(diags, proc.get("type_params"), f"{base}.type_params")
        _check_map_keys(diags, proc.get("inputs"), f"{base}.inputs")
        _check_map_keys(diags, proc.get("outputs"), f"{base}.outputs")

        # Node ids live in body.nodes[*].id for composite processes.
        body = proc.get("body")
        if isinstance(body, YMap):
            nodes = body.get("nodes")
            from ofplang.validate.yamlnode import YSeq  # local import avoids cycle noise

            if isinstance(nodes, YSeq):
                for i, item in enumerate(nodes.items):
                    if isinstance(item, YMap):
                        id_node = item.get("id")
                        from ofplang.validate.yamlnode import YScalar

                        if isinstance(id_node, YScalar):
                            _check(diags, id_node.text, f"{base}.body.nodes[{i}].id", at=id_node)
