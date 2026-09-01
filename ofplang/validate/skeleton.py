"""The Object skeleton of a process (spec 12.4).

Intent: five parts of the specification were each describing one aspect of the
same structure -- where a process moves Object identity to. Completeness (13),
the `object_identity_map` marker (15), a transform's correspondence (14.4.1), a
branch's two arms agreeing (20.2), and what a scheduling policy follows (24.1)
are all questions about it. This module is that structure, so those questions
are answered by computing one thing rather than by five rules that must each be
revisited whenever a node kind or a transform kind is added.

A skeleton is a triple (12.4.1): a partial injection `phi` from the process's
input Object slots to its output Object slots, the input slots it consumes, and
the output slots it creates. Injectivity is what says no Object is duplicated;
partiality is what allows one to be consumed.

Granularity: v0 relates the slot families of two collections by a
*correspondence kind* rather than by position (12.4.2), so nothing here
enumerates a collection or names an index, and a port stands for its whole slot
family. That is exact for every relation v0 can express -- since 0.1 removed
`array_uncons` and `array_cons`, no construct splits one port's slots across two
ports or merges two into one -- which is what keeps equality a comparison of
finite data (12.4.3) and the whole computation linear (12.4.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ofplang.validate.yamlnode import YMap, YScalar, YSeq

# Correspondence kinds (spec 12.4.2). `identity` is a special case of
# `order_preserving`, but they are distinct labels for equality: a process that
# regroups a collection does not have the skeleton of one that leaves it alone.
IDENTITY = "identity"
ORDER_PRESERVING = "order_preserving"

# Where a composite's boundary sits in the body's reference graph. `inputs` is
# what a body reference names a composite input port by; `returns` is not a node
# id and cannot collide with one, since a node id may not be a reserved name.
_INPUTS = "inputs"
_RETURNS = "returns"

# Every section in which a node binding may name a body value (spec 11).
_BINDING_SECTIONS = ("state", "bind", "carry", "args", "each")

# How following one Object through a body can end, other than at the boundary.
CONSUMED = "consumed"
UNRESOLVED = "unresolved"


def compose_kind(outer: str, inner: str) -> str:
    """The kind of one correspondence followed by another. Identity composes
    away; a chain through any regrouping is order-preserving at best."""
    return IDENTITY if outer == IDENTITY and inner == IDENTITY else ORDER_PRESERVING


@dataclass(frozen=True)
class Skeleton:
    """One process's or node's skeleton.

    ``phi`` maps an input port to the output port it corresponds to and the kind
    of that correspondence. ``consumed`` is a set of input ports; ``created``
    maps an output port to its creation-point identifier, which is ``None`` for
    a skeleton computed at a process *definition*, where the node that will
    invoke it is not yet known (12.4.3).
    """

    phi: dict[str, tuple[str, str]] = field(default_factory=dict)
    consumed: frozenset[str] = frozenset()
    created: dict[str, str | None] = field(default_factory=dict)

    def normal_form(self) -> tuple:
        """The normal form of 12.4.3: the input slots in dictionary order with
        their row values, then the created output slots in dictionary order with
        their creation points."""
        inputs = sorted(set(self.phi) | set(self.consumed))
        part1 = tuple((name, self.phi.get(name, CONSUMED)) for name in inputs)
        part2 = tuple((name, self.created[name]) for name in sorted(self.created))
        return (part1, part2)

    def placed_at(self, node: str) -> Skeleton:
        """This skeleton as it stands at a node: a creation point undetermined at
        the definition becomes this node (12.4.3)."""
        return Skeleton(
            phi=dict(self.phi),
            consumed=self.consumed,
            created={name: point or node for name, point in self.created.items()},
        )


def equal(a: Skeleton, b: Skeleton) -> bool:
    """Skeleton equality (spec 12.4.3), as comparison of normal forms."""
    return a.normal_form() == b.normal_form()


def restrict(inner: Skeleton, names: set[str]) -> Skeleton:
    """The part of a skeleton concerning the given port names -- what a `fold` or
    `do_while` carry slot sees of its target (spec 12.4.5, 16)."""
    return Skeleton(
        phi={i: o for i, o in inner.phi.items() if i in names},
        consumed=frozenset(n for n in inner.consumed if n in names),
        created={n: p for n, p in inner.created.items() if n in names},
    )


# --- Derivation (spec 12.4.4) ----------------------------------------------
# The `objects` section is read once, by the pass that validates it: that walk
# already resolves every path against the process's ports and counts how many
# declarations give each slot a fate or a provenance, and a second reader here
# would have to resolve the same paths again and could disagree with it about a
# half-resolved declaration. So the walk builds the skeleton as it goes and
# hands it over; this module owns what a skeleton *is*, not how a section is
# read.


def identity_map(ports: list[str]) -> Skeleton:
    """The skeleton the `object_identity_map` marker infers (spec 15): every
    top-level Object-bearing port corresponds to the same-name output."""
    return Skeleton(phi={name: (name, IDENTITY) for name in ports})


# --- Composition along a composite body (spec 12.4.5) -----------------------
def _consumers(body: YMap) -> dict[tuple[str, str], tuple[str, str]]:
    """Where each body value goes: (owner, name) -> (node or `returns`, port).

    Linearity gives each Object-bearing value exactly one use (12.2), so one
    entry per source is the whole picture. A source referred to twice is
    reported by the linearity pass; here the later entry simply wins, and the
    walk that reads this map stops rather than reporting the same mistake again.
    """
    out: dict[tuple[str, str], tuple[str, str]] = {}

    def record(entry: object, dest: tuple[str, str]) -> None:
        if not isinstance(entry, YMap):
            return
        frm = entry.get("from")
        if isinstance(frm, YScalar):
            parts = frm.text.split(".")
            if len(parts) == 2:
                out[(parts[0], parts[1])] = dest

    nodes = body.get("nodes")
    if isinstance(nodes, YSeq):
        for item in nodes.items:
            if not isinstance(item, YMap):
                continue
            nid = item.get("id")
            if not isinstance(nid, YScalar):
                continue
            for section in _BINDING_SECTIONS:
                bound = item.get(section)
                if isinstance(bound, YMap):
                    for port in bound.keys():
                        record(bound.get(port), (nid.text, port))

    returns = body.get("returns")
    if isinstance(returns, YMap):
        for name in returns.keys():
            record(returns.get(name), (_RETURNS, name))

    return out


def compose_body(
    body: YMap,
    object_inputs: list[str],
    node_skeletons: dict[str, Skeleton],
) -> Skeleton:
    """A composite's skeleton, composed along its body (spec 12.4.5).

    Each of the composite's Object-bearing input port slots is followed through
    the body: a node's `phi` carries it to that node's output, which is bound by
    something else, until it reaches a `returns` entry (a correspondence) or a
    node consumes it. Each Object a node creates is followed the same way, and
    the ones that reach `returns` are what the composite creates.

    A slot whose walk ends nowhere -- an unbound port, an unconsumed value --
    lands in no part of the result, which is what makes the skeleton incomplete
    (12.4.6) and is reported as such by the caller.
    """
    consumers = _consumers(body)
    phi: dict[str, tuple[str, str]] = {}
    consumed: set[str] = set()
    created: dict[str, str | None] = {}

    def walk(start: tuple[str, str]) -> tuple[str, str] | str:
        """Follow one Object from a body source to wherever it ends.

        Returns the `returns` name it reaches with the accumulated
        correspondence kind, `CONSUMED` where a node consumes it, or UNRESOLVED
        where the chain stops without either -- an unbound value, or one whose
        node the caller could not resolve. Those three endings are what decide
        whether the slot joins `phi`, `consumed`, or nothing at all.
        """
        cur, seen, kind = start, set(), IDENTITY
        while cur not in seen:
            seen.add(cur)
            dest = consumers.get(cur)
            if dest is None:
                return UNRESOLVED  # nothing uses it: an unused Object value
            owner, port = dest
            if owner == _RETURNS:
                return (port, kind)
            target = node_skeletons.get(owner)
            if target is None:
                return UNRESOLVED
            if port in target.consumed:
                return CONSUMED
            step = target.phi.get(port)
            if step is None:
                return UNRESOLVED
            out_port, step_kind = step
            kind = compose_kind(kind, step_kind)
            cur = (owner, out_port)
        return UNRESOLVED  # a cycle: not reachable in a well-formed body

    for name in object_inputs:
        ending = walk((_INPUTS, name))
        if ending == CONSUMED:
            consumed.add(name)
        elif isinstance(ending, tuple):
            phi[name] = ending

    for nid, sk in node_skeletons.items():
        for out_port, point in sk.created.items():
            ending = walk((nid, out_port))
            if isinstance(ending, tuple):
                created[ending[0]] = point or nid

    return Skeleton(phi=phi, consumed=frozenset(consumed), created=created)
