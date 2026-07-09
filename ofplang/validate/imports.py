"""Structural `$import` resolution (spec 3).

Intent: `$import` is *textual/structural* inclusion resolved before any other
validation (spec 2.2 step 1) — it is not a module system. This module expands
the node tree in place: mapping-position imports merge, sequence-position
imports splice, and the result is validated as ordinary ofplang.validate.

Because every import failure mode (cycle, unreadable target, multi-document
target, wrong root shape, empty/non-string path, URI fragment, duplicate key
after merge) makes the *whole* document unusable, they are raised as
:class:`YamlError` and surfaced by the validator as a single fatal diagnostic —
matching the "one precise reason" expectation of the conformance cases.
"""

from __future__ import annotations

from pathlib import Path

from ofplang.validate import errors
from ofplang.validate.yamlnode import (
    YMap,
    YScalar,
    YSeq,
    YNode,
    YamlError,
    load_document,
)


def _resolve_path(current_file: Path, rel: str) -> Path:
    """Resolve an import path relative to the importing file (spec 3.1)."""
    return (current_file.parent / rel).resolve()


def _import_targets(value: YNode) -> list[str]:
    """Validate and normalize a `$import` value into a list of path strings.

    The value must be a non-empty string scalar or a non-empty sequence of
    non-empty string scalars (spec 3.4). Every other shape is an error, chosen
    to be specific: empty vs. non-string.
    """
    # Sequence form: a list of paths spliced/merged in order.
    if isinstance(value, YSeq):
        if not value.items:
            raise YamlError(errors.EMPTY_IMPORT, "empty $import sequence", value.pos)
        paths: list[str] = []
        for item in value.items:
            if not isinstance(item, YScalar) or not item.is_str:
                raise YamlError(errors.NON_STRING_IMPORT_PATH, "import path must be a string", item.pos)
            if item.text == "":
                raise YamlError(errors.EMPTY_IMPORT, "empty import path", item.pos)
            paths.append(item.text)
        return paths

    # Scalar form: a single path.
    if isinstance(value, YScalar):
        if value.is_null or not value.is_str:
            raise YamlError(errors.NON_STRING_IMPORT_PATH, "import path must be a string", value.pos)
        if value.text == "":
            raise YamlError(errors.EMPTY_IMPORT, "empty import path", value.pos)
        return [value.text]

    raise YamlError(errors.NON_STRING_IMPORT_PATH, "invalid $import value", value.pos)


def _load_targets(value: YNode, current_file: Path, stack: list[Path]) -> list[YNode]:
    """Load and fully expand each import target referenced by ``value``.

    Cycle detection uses canonical (resolved) file identity on the active DFS
    stack (spec 3.4). Each loaded target is expanded recursively before being
    returned so that nested imports are resolved depth-first.
    """
    results: list[YNode] = []
    for rel in _import_targets(value):
        # Anchor import-failure diagnostics at the `$import` site that triggered
        # them (value.pos), since the failure is about *this* import directive —
        # the target file often has no single meaningful position (a cycle, an
        # unreadable path, or a multi-document stream).
        site = value.pos

        # URI fragments are not defined for portable v0 imports (spec 3.4).
        if "#" in rel:
            raise YamlError(errors.URI_FRAGMENT_IMPORT, f"URI fragment in import {rel!r}", site)

        target = _resolve_path(current_file, rel)
        # A target already on the resolution stack closes an import cycle.
        if target in stack:
            raise YamlError(errors.IMPORT_CYCLE, f"import cycle at {target}", site)

        try:
            loaded = load_document(target)  # may raise unreadable/multidoc YamlError
        except YamlError as exc:
            # The target's failure has no position of its own; point at the
            # importing `$import` so the author can find the offending line.
            if exc.pos is None:
                raise YamlError(exc.code, exc.message, site) from exc
            raise
        stack.append(target)
        try:
            expanded = _expand(loaded, target, stack)
        finally:
            stack.pop()
        results.append(expanded)
    return results


def _expand(node: YNode, current_file: Path, stack: list[Path]) -> YNode:
    """Recursively expand `$import` occurrences within ``node``."""
    if isinstance(node, YMap):
        # Mapping position: a `$import` key is replaced by the entries of each
        # imported mapping, merged at this level (spec 3.2). Duplicate keys that
        # result are detected afterward by the whole-tree scan.
        new_entries: list[tuple[YScalar, YNode]] = []
        for key, value in node.entries:
            if key.text == "$import":
                for imported in _load_targets(value, current_file, stack):
                    if not isinstance(imported, YMap):
                        raise YamlError(
                            errors.INVALID_IMPORT_SHAPE,
                            "mapping-position import must be a mapping",
                            key.pos,
                        )
                    new_entries.extend(imported.entries)
            else:
                new_entries.append((key, _expand(value, current_file, stack)))
        return YMap(tag=node.tag, pos=node.pos, entries=new_entries)

    if isinstance(node, YSeq):
        # Sequence position: an item that is solely a `$import` mapping is a
        # splice point — a sequence target's items are inlined, a non-sequence
        # target is inserted as one item (spec 3.2).
        new_items: list[YNode] = []
        for item in node.items:
            if (
                isinstance(item, YMap)
                and len(item.entries) == 1
                and item.entries[0][0].text == "$import"
            ):
                for imported in _load_targets(item.entries[0][1], current_file, stack):
                    if isinstance(imported, YSeq):
                        new_items.extend(imported.items)
                    else:
                        new_items.append(imported)
            else:
                new_items.append(_expand(item, current_file, stack))
        return YSeq(tag=node.tag, pos=node.pos, items=new_items)

    return node


def _scan_duplicate_keys(node: YNode) -> None:
    """After expansion, reject any mapping with duplicate keys (spec 3.2).

    Duplicate keys are only well-defined once imports are merged, so this runs
    on the expanded tree. The first collision raises, matching the single-cause
    reporting of import errors.
    """
    if isinstance(node, YMap):
        dups = node.duplicate_keys()
        if dups:
            # Point at the duplicated key node itself, not the enclosing map.
            key_node = node.key_node(dups[0])
            raise YamlError(
                errors.DUPLICATE_KEY_AFTER_IMPORT,
                f"duplicate key {dups[0]!r} after import resolution",
                key_node.pos if key_node else node.pos,
            )
        for _, value in node.entries:
            _scan_duplicate_keys(value)
    elif isinstance(node, YSeq):
        for item in node.items:
            _scan_duplicate_keys(item)


def load_expanded(source: str | Path) -> YNode:
    """Load the root document and return its fully import-expanded tree.

    This is the single entry point the validator uses in place of a bare load:
    it performs load -> recursive expand -> duplicate-key scan, raising
    :class:`YamlError` for any structural import failure.
    """
    # Resolve for cycle identity, but load via the path as given so the root's
    # diagnostics display the same (often relative) path the caller passed.
    root_path = Path(source).resolve()
    root = load_document(source)
    expanded = _expand(root, root_path, [root_path])
    _scan_duplicate_keys(expanded)
    return expanded
