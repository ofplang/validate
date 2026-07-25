"""Non-fatal duplicate mapping-key detection (spec 2.3, 2.4).

Intent: v0 mappings have a defined shape at every position, so a repeated key is
a validation error (spec 2.3). Two positions get a more specific code:

  * duplicate input/output *port* names -> ``duplicate_port_name`` (spec 2.4,
    which states duplicate names within ``inputs`` / ``outputs`` are errors); and
  * every other duplicate mapping key -> ``duplicate_key``.

This runs on the fully import-expanded tree and *collects* findings rather than
raising, so a duplicate no longer suppresses the rest of the diagnostics. A
duplicate produced specifically by an ``$import`` merge is handled earlier, and
fatally, in :mod:`ofplang.validate.imports` (``duplicate_key_after_import``,
spec 3.2); this pass only sees plain source-document duplicates.

A small context walk distinguishes port maps from every other mapping: only the
``inputs`` / ``outputs`` maps *of a process* are port maps. Look-alike sections
elsewhere (e.g. a transform entry's ``inputs`` / ``outputs`` role maps) stay
generic and report ``duplicate_key``.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.yamlnode import YMap, YSeq, YNode


def _child_ctx(ctx: str, key: str) -> str:
    """The context of the value under ``key`` given the current map's context.

    The chain that reaches a port map is root -> ``processes`` value
    (process map) -> each process -> its ``inputs`` / ``outputs`` value.
    """
    if ctx == "root" and key == "processes":
        return "process_map"  # value maps process name -> process
    if ctx == "process_map":
        return "process"  # each value is a single process
    if ctx == "process" and key in ("inputs", "outputs"):
        return "ports"  # value maps port name -> port declaration
    return "generic"


def _walk(diags: Diagnostics, node: YNode, base: str, ctx: str) -> None:
    if isinstance(node, YMap):
        code = errors.DUPLICATE_PORT_NAME if ctx == "ports" else errors.DUPLICATE_KEY
        for dup in node.duplicate_keys():
            key_node = node.key_node(dup)
            diags.add(code, f"duplicate key {dup!r}", f"{base}.{dup}", at=key_node)
        # Recurse over distinct keys (last-wins value); the duplicate itself is
        # already reported, so walking one representative subtree suffices.
        for key in node.keys():
            _walk(diags, node.get(key), f"{base}.{key}", _child_ctx(ctx, key))
    elif isinstance(node, YSeq):
        for i, item in enumerate(node.items):
            _walk(diags, item, f"{base}[{i}]", "generic")


def check_duplicates(doc: YNode, diags: Diagnostics) -> None:
    """Report every duplicate mapping key in the expanded document."""
    _walk(diags, doc, "<root>", "root")
