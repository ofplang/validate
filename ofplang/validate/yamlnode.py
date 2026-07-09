"""YAML loading at the node level.

Intent: portable v0 imposes rules that a plain ``yaml.safe_load`` silently
violates, so we must load YAML *as a node tree* and inspect it ourselves:

  * Duplicate mapping keys are validation errors (spec 2.4, 3.2), but safe_load
    keeps the last occurrence and throws the duplicate away. We therefore keep
    every (key, value) pair in insertion order and detect collisions later.
  * ``null`` values are invalid in v0 YAML (spec 2.3). safe_load would hand us a
    Python ``None`` indistinguishable from an intentional value, so we keep the
    resolved YAML *tag* and expose an explicit null test.
  * Contracts and static view values require distinguishing ``Int`` from
    ``Float`` from ``Bool`` from ``String`` (spec 7.4, 9.2). The YAML resolver
    already assigns an implicit tag (``...:int`` / ``:float`` / ``:bool`` /
    ``:str``); we preserve that tag rather than collapsing to a Python value.
  * Diagnostics want line/column positions, which live on each node's marks.
  * Import targets must be single YAML documents (spec 3.4); ``compose_all``
    lets us count documents and reject multi-document streams.

The neutral node classes below (:class:`YScalar`, :class:`YSeq`, :class:`YMap`)
decouple the rest of the validator from PyYAML's internal node API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# --- Resolved YAML 1.1 core tags we care about ----------------------------
# We compare against these instead of constructing Python values so that the
# original scalar kind (and thus v0's Data-type distinctions) is never lost.
TAG_NULL = "tag:yaml.org,2002:null"
TAG_BOOL = "tag:yaml.org,2002:bool"
TAG_INT = "tag:yaml.org,2002:int"
TAG_FLOAT = "tag:yaml.org,2002:float"
TAG_STR = "tag:yaml.org,2002:str"


@dataclass(frozen=True)
class Pos:
    """1-based source position, for human-facing diagnostics.

    ``file`` records which source the position belongs to. This matters after
    ``$import`` expansion: an imported node's line refers to *its own* fragment
    file, not the root document, so a diagnostic can only be unambiguous when it
    also names the file.
    """

    line: int
    col: int
    file: str | None = None


@dataclass
class YNode:
    """Base class carrying the resolved tag and source position."""

    tag: str
    pos: Pos


@dataclass
class YScalar(YNode):
    """A scalar leaf. ``text`` is the raw source text; ``tag`` says how the
    YAML resolver classified it (int/float/bool/str/null)."""

    text: str

    @property
    def is_null(self) -> bool:
        return self.tag == TAG_NULL

    @property
    def is_bool(self) -> bool:
        return self.tag == TAG_BOOL

    @property
    def is_int(self) -> bool:
        return self.tag == TAG_INT

    @property
    def is_float(self) -> bool:
        return self.tag == TAG_FLOAT

    @property
    def is_str(self) -> bool:
        return self.tag == TAG_STR


@dataclass
class YSeq(YNode):
    items: list[YNode] = field(default_factory=list)


@dataclass
class YMap(YNode):
    """A mapping preserving *every* (key, value) pair in order.

    Duplicate keys are intentionally retained here (unlike safe_load) so that
    context-specific passes can flag them with the right code — e.g.
    ``duplicate_key_after_import`` (spec 3.2) or ``duplicate_port_name``
    (spec 2.4). ``get``/``keys`` use last-wins only for convenient lookup after
    duplicates have been checked.
    """

    entries: list[tuple[YScalar, YNode]] = field(default_factory=list)

    def keys(self) -> list[str]:
        """Distinct string keys in first-seen order."""
        out: list[str] = []
        seen: set[str] = set()
        for k, _ in self.entries:
            if k.text not in seen:
                seen.add(k.text)
                out.append(k.text)
        return out

    def duplicate_keys(self) -> list[str]:
        """Keys that appear more than once (for duplicate-key diagnostics)."""
        counts: dict[str, int] = {}
        for k, _ in self.entries:
            counts[k.text] = counts.get(k.text, 0) + 1
        return [k for k, n in counts.items() if n > 1]

    def get(self, key: str) -> YNode | None:
        """Last-wins lookup by key text; ``None`` if absent."""
        found: YNode | None = None
        for k, v in self.entries:
            if k.text == key:
                found = v
        return found

    def key_node(self, key: str) -> YScalar | None:
        """The (last) key node for ``key``, for positioning diagnostics."""
        found: YScalar | None = None
        for k, _ in self.entries:
            if k.text == key:
                found = k
        return found


class YamlError(Exception):
    """A YAML-level load failure (unparsable, multi-document, non-string key).

    Raised rather than collected because nothing downstream can run without a
    node tree; the validator turns this into a single fatal diagnostic.
    """

    def __init__(self, code: str, message: str, pos: Pos | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.pos = pos


def _pos(mark, file: str | None) -> Pos:
    # PyYAML marks are 0-based; present them 1-based to match editor gutters.
    if mark is None:
        return Pos(0, 0, file)
    return Pos(mark.line + 1, mark.column + 1, file)


def _convert(node, file: str | None) -> YNode:
    """Recursively convert a PyYAML node into our neutral tree.

    We deliberately do not use the loader's object constructor: constructing
    would resolve scalars to Python values and merge duplicate keys, discarding
    exactly the information this module exists to preserve. ``file`` is stamped
    into every position so diagnostics can name the originating source.
    """
    if isinstance(node, yaml.ScalarNode):
        return YScalar(tag=node.tag, pos=_pos(node.start_mark, file), text=node.value)

    if isinstance(node, yaml.SequenceNode):
        return YSeq(
            tag=node.tag,
            pos=_pos(node.start_mark, file),
            items=[_convert(item, file) for item in node.value],
        )

    if isinstance(node, yaml.MappingNode):
        entries: list[tuple[YScalar, YNode]] = []
        for key_node, value_node in node.value:
            # v0 keys are always ASCII string identifiers/keywords. A non-scalar
            # key (e.g. a mapping used as a key) has no meaning in v0 and cannot
            # be represented as a string key, so it is a hard load error.
            if not isinstance(key_node, yaml.ScalarNode):
                raise YamlError(
                    "wrong_value_kind",
                    "mapping keys must be scalars in v0",
                    _pos(key_node.start_mark, file),
                )
            key = YScalar(tag=key_node.tag, pos=_pos(key_node.start_mark, file), text=key_node.value)
            entries.append((key, _convert(value_node, file)))
        return YMap(tag=node.tag, pos=_pos(node.start_mark, file), entries=entries)

    # PyYAML only produces the three node kinds above.
    raise YamlError("wrong_value_kind", f"unsupported YAML node: {type(node).__name__}")


def compose_document(text: str, file: str | None = None) -> YNode:
    """Parse one YAML text into a single node tree.

    Enforces the "single document" rule (spec 3.4) that applies to both the
    root document and every import target: zero documents (empty file) and
    multi-document streams are both rejected. ``file`` labels positions.
    """
    try:
        docs = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise YamlError("wrong_value_kind", f"unparsable YAML: {exc}", _pos(mark, file)) from exc

    if len(docs) == 0:
        raise YamlError("wrong_value_kind", "empty YAML document")
    if len(docs) > 1:
        raise YamlError("multidoc_import", "YAML stream contains more than one document")

    root = docs[0]
    if root is None:
        raise YamlError("wrong_value_kind", "empty YAML document")
    return _convert(root, file)


def load_document(path: str | Path) -> YNode:
    """Read a file from disk and compose it into a node tree.

    Missing/unreadable files are reported with the import-oriented code because
    this loader is used both for the root document and for import targets; the
    caller decides how to surface it. The file path is stamped into positions.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise YamlError("unreadable_import", f"cannot read {p}: {exc}") from exc
    return compose_document(text, str(p))
