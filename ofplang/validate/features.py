"""Feature model: derivation and declared-set validation (spec 4).

Intent: `features` is canonical when written but derivable when omitted. Feature
derivation is deliberately syntactic (spec 4.3) — it reads node `kind` values, a
`type_params` section, a `script.language: python`, the presence of a
`scheduling` section, and a unit suffix in the written text of a `type` — so it
is cheap and unambiguous to check. When `features`
is present it must list every derived (required) feature and may list only
v0-defined names.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.identifiers import classify_name
from ofplang.validate.validator import EXTENSION_TOLERANT
from ofplang.validate.yamlnode import YMap, YNode, YScalar, YSeq

# The closed set of v0 feature names (spec 4.2).
V0_FEATURES = frozenset(
    {
        "node_map",
        "node_fold",
        "node_do_while",
        "node_branch",
        "generic_processes",
        "python_script_processes",
        "scheduling_policies",
        # Experimental (spec 4.5): an ordinary feature here, with no migration
        # path promised across a revision.
        "units",
    }
)

# Node kind -> the feature it requires (spec 4.3).
_KIND_FEATURE = {
    "map": "node_map",
    "fold": "node_fold",
    "do_while": "node_do_while",
    "branch": "node_branch",
}


def _has_unit_suffix(ports: YNode | None) -> bool:
    """Whether any `type` in a port or view-field map carries a unit suffix.

    Read from the **written text**, not from a parsed type. `Float[1]` is the
    same type as `Float` (spec 28.5), so a parsed type cannot say whether a
    suffix was written -- and 4.3 requires the feature wherever the syntax is
    used, dimensionless or not. `[` cannot occur in a v0 type expression for any
    other reason (2.5), so its presence is the whole test.
    """
    if not isinstance(ports, YMap):
        return False
    for name in ports.keys():
        decl = ports.get(name)
        if not isinstance(decl, YMap):
            continue
        tnode = decl.get("type")
        if isinstance(tnode, YScalar) and "[" in tnode.text:
            return True
    return False


def derive_required(doc: YMap) -> set[str]:
    """Collect the feature set required by the document body (spec 4.3)."""
    required: set[str] = set()

    # A non-empty `units` section requires the feature on its own (spec 4.3):
    # a shared vocabulary file declares atoms and nothing else.
    units = doc.get("units")
    if isinstance(units, YMap) and units.entries:
        required.add("units")

    # A unit-annotated view field type requires it too (spec 28.9). Type-level
    # metadata is part of the document body, and a document may carry a view
    # schema with no process using it.
    types = doc.get("types")
    if isinstance(types, YMap):
        for tname in types.keys():
            decl = types.get(tname)
            if isinstance(decl, YMap) and _has_unit_suffix(decl.get("view")):
                required.add("units")

    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return required

    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue

        # Structured node kinds inside a composite body.
        body = proc.get("body")
        if isinstance(body, YMap):
            nodes = body.get("nodes")
            if isinstance(nodes, YSeq):
                for item in nodes.items:
                    if isinstance(item, YMap):
                        kind = item.get("kind")
                        if isinstance(kind, YScalar) and kind.text in _KIND_FEATURE:
                            required.add(_KIND_FEATURE[kind.text])

        # A generic process: one that declares a `type_params` section (spec 4.3).
        if proc.get("type_params") is not None:
            required.add("generic_processes")

        # Python script processes: a script section written for python.
        script = proc.get("script")
        if isinstance(script, YMap):
            lang = script.get("language")
            if isinstance(lang, YScalar) and lang.text == "python":
                required.add("python_script_processes")

        # A scheduling section requires scheduling_policies (spec 4.3).
        if proc.get("scheduling") is not None:
            required.add("scheduling_policies")

        # A unit suffix on any port type requires units (spec 4.3, 28.2).
        if _has_unit_suffix(proc.get("inputs")) or _has_unit_suffix(proc.get("outputs")):
            required.add("units")

    return required


def check_features(doc: YMap, diags: Diagnostics, mode: str) -> None:
    required = derive_required(doc)

    features_node = doc.get("features")
    # Omitted features: the required set is taken as written; nothing to check.
    if features_node is None:
        return
    if not isinstance(features_node, YSeq):
        diags.add(
            errors.WRONG_VALUE_KIND, "features must be a sequence", "features", at=features_node
        )
        return

    # Validate each declared name and collect the declared set.
    declared: set[str] = set()
    for i, item in enumerate(features_node.items):
        if not isinstance(item, YScalar):
            diags.add(
                errors.WRONG_VALUE_KIND, "feature must be a string", f"features[{i}]", at=item
            )
            continue
        name = item.text
        declared.add(name)
        # Extension feature names (x-...) are allowed only in tolerant mode, and only
        # in the form `x-` + an identifier (spec 26). A name that misses the form is
        # not an extension feature name, so it falls under the same "unknown
        # non-extension feature name" rule as `bogus_feature` -- the grammar comes
        # from `identifiers` so there is one source for it (reserved words do not
        # apply: spec 26 constrains the shape only).
        if name.startswith("x-"):
            if mode != EXTENSION_TOLERANT:
                diags.add(
                    errors.UNKNOWN_FEATURE,
                    f"extension feature {name!r}",
                    f"features[{i}]",
                    at=item,
                )
            elif classify_name(name[len("x-") :], reserved=False) is not None:
                diags.add(
                    errors.UNKNOWN_FEATURE,
                    f"malformed extension feature name {name!r}",
                    f"features[{i}]",
                    at=item,
                )
            continue
        if name not in V0_FEATURES:
            diags.add(
                errors.UNKNOWN_FEATURE, f"unknown feature {name!r}", f"features[{i}]", at=item
            )

    # Every required feature must be present in an explicit features section.
    for feat in sorted(required - declared):
        diags.add(
            errors.MISSING_REQUIRED_FEATURE,
            f"required feature {feat!r} is missing from features",
            "features",
            at=features_node,
        )
