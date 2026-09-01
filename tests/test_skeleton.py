"""Unit tests for the Object skeleton (spec 12.4).

The conformance suite exercises the skeleton only through the diagnostics that
read it, and in this revision that is completeness alone. These pin the parts a
later check compares -- the normal form, equality, creation points, and what
composing a body produces -- so that the composition rules are held to 12.4.5
rather than to whatever the first consumer happens to accept.
"""

from __future__ import annotations

import textwrap

from ofplang.validate import objects as objects_pass
from ofplang.validate import skeleton as sk
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.types import build_env
from ofplang.validate.yamlnode import YMap, compose_document


def _skeletons(text: str) -> dict[str, sk.Skeleton]:
    root = compose_document(textwrap.dedent(text))
    assert isinstance(root, YMap)
    diags = Diagnostics()
    env = build_env(root)
    sigs = objects_pass.build_signatures(root, env)
    result = objects_pass.check_objects(root, diags, env, sigs)
    assert not diags.codes, diags.codes
    return result


CUP = """
    spec_version: "0.0"
    types:
      Cup:
        domain: object
    processes:
"""


def test_normal_form_orders_and_labels():
    s = sk.Skeleton(
        phi={"b": ("y", sk.IDENTITY), "a": ("x", sk.ORDER_PRESERVING)},
        consumed=frozenset({"c"}),
        created={"z": "n1"},
    )
    part1, part2 = s.normal_form()
    assert part1 == (
        ("a", ("x", sk.ORDER_PRESERVING)),
        ("b", ("y", sk.IDENTITY)),
        ("c", "consumed"),
    )
    assert part2 == (("z", "n1"),)


def test_equality_ignores_declaration_order_but_not_kind():
    a = sk.Skeleton(phi={"x": ("y", sk.IDENTITY)})
    b = sk.Skeleton(phi={"x": ("y", sk.IDENTITY)})
    c = sk.Skeleton(phi={"x": ("y", sk.ORDER_PRESERVING)})
    assert sk.equal(a, b)
    assert not sk.equal(a, c)


def test_creation_point_is_the_node_not_the_definition():
    definition = sk.Skeleton(created={"cup": None})
    at_a = definition.placed_at("a")
    at_b = definition.placed_at("b")
    assert at_a.created == {"cup": "a"}
    assert not sk.equal(at_a, at_b)


def test_composing_kinds_degrades_to_order_preserving():
    assert sk.compose_kind(sk.IDENTITY, sk.IDENTITY) == sk.IDENTITY
    assert sk.compose_kind(sk.IDENTITY, sk.ORDER_PRESERVING) == sk.ORDER_PRESERVING
    assert sk.compose_kind(sk.ORDER_PRESERVING, sk.IDENTITY) == sk.ORDER_PRESERVING


def test_atomic_skeleton_from_objects_section():
    s = _skeletons(CUP + """
      replace:
        kind: atomic
        inputs:
          cup: { type: Cup, phase: data }
        outputs:
          cup: { type: Cup, phase: data }
        objects:
          consume: [inputs.cup]
          create: [outputs.cup]
      main:
        kind: atomic
        inputs: {}
        outputs: {}
    entry: main
    """)["replace"]
    assert s.phi == {}
    assert s.consumed == frozenset({"cup"})
    assert s.created == {"cup": None}


def test_atomic_skeleton_from_the_behavior_marker():
    s = _skeletons(CUP + """
      inspect:
        kind: atomic
        behavior:
          - object_identity_map
        inputs:
          cup: { type: Cup, phase: data }
        outputs:
          cup: { type: Cup, phase: data }
      main:
        kind: atomic
        inputs: {}
        outputs: {}
    entry: main
    """)["inspect"]
    assert s.phi == {"cup": ("cup", sk.IDENTITY)}


def test_transform_contributes_an_order_preserving_correspondence():
    s = _skeletons(CUP + """
      flatten:
        kind: atomic
        inputs:
          xss: { type: Array<Array<Cup>>, phase: data }
        outputs:
          xs: { type: Array<Cup>, phase: data }
        objects:
          transform:
            - kind: array_flatten
              inputs: { xss: inputs.xss }
              outputs: { xs: outputs.xs }
      main:
        kind: atomic
        inputs: {}
        outputs: {}
    entry: main
    """)["flatten"]
    assert s.phi == {"xss": ("xs", sk.ORDER_PRESERVING)}


_CHAIN = CUP + """
      wash:
        kind: atomic
        inputs:
          cup: { type: Cup, phase: data }
        outputs:
          cup: { type: Cup, phase: data }
        objects:
          map:
            outputs.cup: inputs.cup
      regroup:
        kind: atomic
        inputs:
          xs: { type: Array<Cup>, phase: data }
        outputs:
          xss: { type: Array<Array<Cup>>, phase: data }
        objects:
          transform:
            - kind: array_unflatten
              inputs: { xs: inputs.xs }
              outputs: { xss: outputs.xss }
      make:
        kind: atomic
        inputs: {}
        outputs:
          cup: { type: Cup, phase: data }
        objects:
          create: [outputs.cup]
      sink:
        kind: atomic
        inputs:
          cup: { type: Cup, phase: data }
        outputs: {}
        objects:
          consume: [inputs.cup]
"""


def test_composite_passthrough_composes_to_a_correspondence():
    s = _skeletons(_CHAIN + """
      main:
        kind: composite
        inputs:
          cup: { type: Cup, phase: data }
        outputs:
          cup: { type: Cup, phase: data }
        body:
          nodes:
            - id: w
              process: wash
              state:
                cup: { from: inputs.cup }
          returns:
            cup: { from: w.cup }
    entry: main
    """)["main"]
    assert s.phi == {"cup": ("cup", sk.IDENTITY)}
    assert s.consumed == frozenset() and s.created == {}


def test_a_chain_through_a_transform_degrades_the_kind():
    s = _skeletons(_CHAIN + """
      main:
        kind: composite
        inputs:
          xs: { type: Array<Cup>, phase: data }
        outputs:
          xss: { type: Array<Array<Cup>>, phase: data }
        body:
          nodes:
            - id: g
              process: regroup
              state:
                xs: { from: inputs.xs }
          returns:
            xss: { from: g.xss }
    entry: main
    """)["main"]
    assert s.phi == {"xs": ("xss", sk.ORDER_PRESERVING)}


def test_composite_consumption_and_creation_reach_the_boundary():
    s = _skeletons(_CHAIN + """
      main:
        kind: composite
        inputs:
          cup: { type: Cup, phase: data }
        outputs:
          fresh: { type: Cup, phase: data }
        body:
          nodes:
            - id: s
              process: sink
              state:
                cup: { from: inputs.cup }
            - id: m
              process: make
          returns:
            fresh: { from: m.cup }
    entry: main
    """)["main"]
    assert s.phi == {}
    assert s.consumed == frozenset({"cup"})
    # The creation point is the interior node, carried out through returns.
    assert s.created == {"fresh": "m"}


def test_an_object_created_and_consumed_inside_leaves_no_trace():
    s = _skeletons(_CHAIN + """
      main:
        kind: composite
        inputs: {}
        outputs: {}
        body:
          nodes:
            - id: m
              process: make
            - id: s
              process: sink
              state:
                cup: { from: m.cup }
          returns: {}
    entry: main
    """)["main"]
    assert s.phi == {} and s.consumed == frozenset() and s.created == {}


def test_a_nested_composite_composes_through_two_levels():
    s = _skeletons(_CHAIN + """
      inner:
        kind: composite
        inputs:
          cup: { type: Cup, phase: data }
        outputs:
          cup: { type: Cup, phase: data }
        body:
          nodes:
            - id: w
              process: wash
              state:
                cup: { from: inputs.cup }
          returns:
            cup: { from: w.cup }
      main:
        kind: composite
        inputs:
          cup: { type: Cup, phase: data }
        outputs:
          cup: { type: Cup, phase: data }
        body:
          nodes:
            - id: i
              process: inner
              state:
                cup: { from: inputs.cup }
          returns:
            cup: { from: i.cup }
    entry: main
    """)
    assert s["inner"].phi == {"cup": ("cup", sk.IDENTITY)}
    assert s["main"].phi == {"cup": ("cup", sk.IDENTITY)}
