"""Feature model: derivation and declared-set validation (spec 4).

Intent: `features` is canonical when written but derivable when omitted. Feature
derivation is deliberately syntactic (spec 4.3) — it reads node `kind` values, a
`script.language: python`, and the presence of a `scheduling` section — so it is
cheap and unambiguous to check. When `features` is present it must list every
derived (required) feature and may list only v0-defined names.
"""

from __future__ import annotations

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.validator import EXTENSION_TOLERANT
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode

# The closed set of v0 feature names (spec 4.2).
V0_FEATURES = frozenset(
    {
        "node_map",
        "node_fold",
        "node_do_while",
        "node_branch",
        "python_script_processes",
        "scheduling_policies",
    }
)

# Node kind -> the feature it requires (spec 4.3).
_KIND_FEATURE = {
    "map": "node_map",
    "fold": "node_fold",
    "do_while": "node_do_while",
    "branch": "node_branch",
}


def derive_required(doc: YMap) -> set[str]:
    """Collect the feature set required by the document body (spec 4.3)."""
    required: set[str] = set()
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

        # Python script processes: a script section written for python.
        script = proc.get("script")
        if isinstance(script, YMap):
            lang = script.get("language")
            if isinstance(lang, YScalar) and lang.text == "python":
                required.add("python_script_processes")

        # A scheduling section requires scheduling_policies (spec 4.3).
        if proc.get("scheduling") is not None:
            required.add("scheduling_policies")

    return required


def check_features(doc: YMap, diags: Diagnostics, mode: str) -> None:
    required = derive_required(doc)

    features_node = doc.get("features")
    # Omitted features: the required set is taken as written; nothing to check.
    if features_node is None:
        return
    if not isinstance(features_node, YSeq):
        diags.add(errors.WRONG_VALUE_KIND, "features must be a sequence", "features", at=features_node)
        return

    # Validate each declared name and collect the declared set.
    declared: set[str] = set()
    for i, item in enumerate(features_node.items):
        if not isinstance(item, YScalar):
            diags.add(errors.WRONG_VALUE_KIND, "feature must be a string", f"features[{i}]", at=item)
            continue
        name = item.text
        declared.add(name)
        # Extension feature names (x-...) are allowed only in tolerant mode.
        if name.startswith("x-"):
            if mode != EXTENSION_TOLERANT:
                diags.add(errors.UNKNOWN_FEATURE, f"extension feature {name!r}", f"features[{i}]", at=item)
            continue
        if name not in V0_FEATURES:
            diags.add(errors.UNKNOWN_FEATURE, f"unknown feature {name!r}", f"features[{i}]", at=item)

    # Every required feature must be present in an explicit features section.
    for feat in sorted(required - declared):
        diags.add(
            errors.MISSING_REQUIRED_FEATURE,
            f"required feature {feat!r} is missing from features",
            "features",
            at=features_node,
        )
