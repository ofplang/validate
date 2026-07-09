"""Type-field resolution and built-in redeclaration checks (spec 2.4, 2.5).

Intent: this pass turns every `type:` field on a process port into a parsed,
resolved type, reporting malformed expressions and unknown atoms. It also
enforces that reserved built-in names are not redeclared as user types or type
parameters. View-field types are intentionally *not* handled here — the view
pass owns them, because view fields have an extra restriction (Pure-Data-only)
that would otherwise need duplicated logic.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.types import (
    RESERVED_TYPE_LIKE,
    TypeEnv,
    TypeParseError,
    parse_type,
    process_type_params,
    resolve_error,
)
from ofplang.validate.yamlnode import YMap, YScalar, YNode


def _check_type_field(
    diags: Diagnostics,
    node: YNode | None,
    env: TypeEnv,
    type_params: dict[str, str],
    path: str,
) -> None:
    """Parse and resolve one `type` field value.

    A `type` field must be a string scalar (spec 2.5). Parse failure and
    resolution failure are distinct, sequential steps: we only attempt
    resolution once the expression is syntactically well-formed.
    """
    if node is None:
        return
    if not isinstance(node, YScalar) or not node.is_str:
        # A non-string type field is a shape-level mistake; report with the
        # dedicated code so it is not mistaken for a malformed expression.
        diags.add(errors.TYPE_FIELD_NOT_STRING, "type must be a string scalar", path, at=node)
        return
    try:
        expr = parse_type(node.text)
    except TypeParseError as exc:
        diags.add(errors.MALFORMED_TYPE_EXPR, str(exc), path, at=node)
        return
    code = resolve_error(expr, env, type_params)
    if code is not None:
        diags.add(code, f"unknown type in {node.text!r}", path, at=node)


def _check_ports(
    diags: Diagnostics, ports: YNode | None, env: TypeEnv, tp: dict[str, str], base: str
) -> None:
    if not isinstance(ports, YMap):
        return
    for pname in ports.keys():
        port = ports.get(pname)
        if isinstance(port, YMap):
            _check_type_field(diags, port.get("type"), env, tp, f"{base}.{pname}.type")


def check_types(doc: YMap, diags: Diagnostics, env: TypeEnv) -> None:
    # Reserved-name redeclaration: user types and type parameters must not reuse
    # a built-in name (spec 2.4). Trait redeclarations are handled by the trait
    # pass so that `Numeric` gets its dedicated code there.
    types = doc.get("types")
    if isinstance(types, YMap):
        for name in types.keys():
            if name in RESERVED_TYPE_LIKE:
                diags.add(errors.REDECLARE_BUILTIN, f"{name!r} is reserved", f"types.{name}", at=types.key_node(name))

    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return

    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        base = f"processes.{pname}"
        tp = process_type_params(proc)

        # Type parameters must not shadow reserved built-in names (spec 2.5).
        tp_node = proc.get("type_params")
        if isinstance(tp_node, YMap):
            for name in tp_node.keys():
                if name in RESERVED_TYPE_LIKE:
                    diags.add(
                        errors.REDECLARE_BUILTIN,
                        f"type parameter {name!r} is reserved",
                        f"{base}.type_params.{name}",
                        at=tp_node.key_node(name),
                    )

        # Resolve the type on every input and output port.
        _check_ports(diags, proc.get("inputs"), env, tp, f"{base}.inputs")
        _check_ports(diags, proc.get("outputs"), env, tp, f"{base}.outputs")
