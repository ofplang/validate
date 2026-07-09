"""View schema and static view value checks (spec 7.4).

Intent: a `.view` is a value's contract-visible Pure Data projection. v0
restricts view field types to primitive Pure Data (or Arrays recursively of
primitives) so that views can never leak Object identity and so contract
checking has a simple, decidable value model. This pass validates each view
field's type against that restriction and checks any static `value` for
conformance to its declared type.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.types import (
    PRIMITIVE_TYPES,
    ArrayT,
    Atom,
    TypeEnv,
    TypeExpr,
    TypeParseError,
    is_object_bearing,
    parse_type,
    resolve_error,
)
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode


def _is_primitive_only(expr: TypeExpr) -> bool:
    """Whether a (resolved) type is a primitive or an Array recursively of
    primitives — the only shapes allowed as view field types (spec 7.4)."""
    if isinstance(expr, ArrayT):
        return _is_primitive_only(expr.elem)
    return isinstance(expr, Atom) and expr.name in PRIMITIVE_TYPES


def _static_value_conforms(expr: TypeExpr, value: YNode) -> bool:
    """Recursively check a static value against a primitive-only view type.

    Float intentionally accepts an integer scalar as well (spec 7.4: YAML
    integer/float/exponent numeric forms are all acceptable Float values).
    """
    if isinstance(expr, ArrayT):
        if not isinstance(value, YSeq):
            return False
        return all(_static_value_conforms(expr.elem, item) for item in value.items)
    if not isinstance(value, YScalar):
        return False
    name = expr.name
    if name == "Bool":
        return value.is_bool
    if name == "Int":
        return value.is_int
    if name == "Float":
        return value.is_float or value.is_int
    if name == "String":
        return value.is_str
    return False


def check_views(doc: YMap, diags: Diagnostics, env: TypeEnv) -> None:
    types = doc.get("types")
    if not isinstance(types, YMap):
        return

    for tname in types.keys():
        decl = types.get(tname)
        if not isinstance(decl, YMap):
            continue
        view = decl.get("view")
        if not isinstance(view, YMap):
            continue

        for fname in view.keys():
            field = view.get(fname)
            if not isinstance(field, YMap):
                continue
            base = f"types.{tname}.view.{fname}"

            # Field type must parse and resolve first; view fields have no type
            # parameters in scope (views hang off nominal types, not processes).
            type_node = field.get("type")
            if not isinstance(type_node, YScalar) or not type_node.is_str:
                diags.add(errors.TYPE_FIELD_NOT_STRING, "view field type must be a string", f"{base}.type", at=type_node or field)
                continue
            try:
                expr = parse_type(type_node.text)
            except TypeParseError as exc:
                diags.add(errors.MALFORMED_TYPE_EXPR, str(exc), f"{base}.type", at=type_node)
                continue
            if resolve_error(expr, env, {}) is not None:
                diags.add(errors.UNKNOWN_TYPE, f"unknown type {type_node.text!r}", f"{base}.type", at=type_node)
                continue

            # Restriction: Object-bearing view fields are forbidden outright;
            # non-primitive Pure Data (a user Data type) is a different, still
            # invalid, shape. Split into two codes so the reason is precise.
            if is_object_bearing(expr, env, {}):
                diags.add(errors.OBJECT_BEARING_VIEW_FIELD, "view field is Object-bearing", f"{base}.type", at=type_node)
                continue
            if not _is_primitive_only(expr):
                diags.add(errors.INVALID_VIEW_FIELD_TYPE, "view field must be primitive Pure Data", f"{base}.type", at=type_node)
                continue

            # Optional static value must conform to the (now known primitive)
            # field type. A null value is left to the null scan (spec 7.4:
            # null is not a valid static value), so we do not double-report it.
            value = field.get("value")
            if value is None:
                continue
            if isinstance(value, YScalar) and value.is_null:
                continue
            if not _static_value_conforms(expr, value):
                diags.add(
                    errors.STATIC_VALUE_TYPE_MISMATCH,
                    "static value does not conform to field type",
                    f"{base}.value",
                    at=value,
                )
