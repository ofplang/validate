"""Trait declaration and `implements` checks (spec 7.3).

Intent: document traits are nominal membership markers. The rules this pass
enforces keep the closed built-in trait `Numeric` inviolable (it cannot be
redeclared, and user types cannot implement it) and require every implemented
trait to be declared. `where`-clause trait usage is validated in the generics
pass, where type parameters are in scope.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.types import BUILTIN_TYPE_NAMES, TypeEnv
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode


def check_traits(doc: YMap, diags: Diagnostics, env: TypeEnv) -> None:
    # Top-level trait declarations: `Numeric` is closed/built-in and other
    # built-in names are reserved, so neither may appear as a declared trait.
    traits = doc.get("traits")
    if isinstance(traits, YMap):
        for name in traits.keys():
            if name == "Numeric":
                diags.add(errors.REDECLARE_NUMERIC, "Numeric is built-in", f"traits.{name}", at=traits.key_node(name))
            elif name in BUILTIN_TYPE_NAMES:
                diags.add(errors.REDECLARE_BUILTIN, f"{name!r} is reserved", f"traits.{name}", at=traits.key_node(name))

    # `implements` on each user type: every listed trait must be a declared
    # document trait; `Numeric` cannot be implemented by user types (spec 7.3).
    types = doc.get("types")
    if not isinstance(types, YMap):
        return
    for tname in types.keys():
        decl = types.get(tname)
        if not isinstance(decl, YMap):
            continue
        impls = decl.get("implements")
        if not isinstance(impls, YSeq):
            continue
        for i, item in enumerate(impls.items):
            if not isinstance(item, YScalar):
                continue
            trait = item.text
            path = f"types.{tname}.implements[{i}]"
            if trait == "Numeric":
                diags.add(errors.IMPLEMENTS_NUMERIC, "cannot implement Numeric", path, at=item)
            elif trait not in env.traits:
                diags.add(errors.UNKNOWN_TRAIT, f"undeclared trait {trait!r}", path, at=item)
