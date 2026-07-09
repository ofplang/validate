"""ofplang v0 validator -- public API.

The stable API surface the tests and CLI depend on is:

    validate(source, *, mode="strict", base_dir=None) -> ValidationResult

where ``source`` is a path to the root document (a ``.yaml`` file). The
returned :class:`ValidationResult` exposes ``ok`` and ``diagnostics``. Each
:class:`Diagnostic` carries a ``code`` drawn from :mod:`ofplang.validate.errors`
plus an optional source position (``file``/``line``/``col``).

``validate`` runs the passes in the spec's processing order (spec 2.2),
collecting all independent findings; only a YAML parse or ``$import`` resolution
failure is terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Validation modes.
STRICT = "strict"
EXTENSION_TOLERANT = "extension-tolerant"
MODES = frozenset({STRICT, EXTENSION_TOLERANT})


@dataclass(frozen=True)
class Diagnostic:
    """A single validation finding.

    ``code`` is a stable identifier from :mod:`ofplang.validate.errors`. ``path`` is an
    optional human-oriented logical location (e.g. ``processes.main.inputs.x``).
    ``file``/``line``/``col`` are the source position when known (1-based); they
    are optional so passes that cannot supply a node position still work.
    """

    code: str
    message: str = ""
    path: str | None = None
    file: str | None = None
    line: int | None = None
    col: int | None = None

    @property
    def location(self) -> str | None:
        """A ``file:line:col`` (or ``line:col``) string when a position is
        known, else ``None`` — the primary locator for human output."""
        if self.line is None:
            return None
        head = f"{self.file}:" if self.file else ""
        return f"{head}{self.line}:{self.col}"


@dataclass
class ValidationResult:
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    @property
    def codes(self) -> list[str]:
        return [d.code for d in self.diagnostics]


def validate(
    source: str | Path,
    *,
    mode: str = STRICT,
    base_dir: str | Path | None = None,
) -> ValidationResult:
    """Validate an ofplang v0 document rooted at ``source``.

    Parameters
    ----------
    source:
        Path to the root YAML document.
    mode:
        ``"strict"`` (portable v0) or ``"extension-tolerant"`` (accepts ``x-``
        extension keys/features/preference kinds).
    base_dir:
        Optional base directory for resolving relative ``$import`` paths.
        Defaults to the directory containing ``source``.

    The pipeline follows the spec's processing order (spec 2.2): load, then a
    sequence of passes each appending to a shared :class:`Diagnostics` sink.
    """
    # Imported lazily so this module has no import-time dependency on PyYAML or
    # the pass modules — keeps the public API cheap to import.
    from ofplang.validate.diagnostics import Diagnostics
    from ofplang.validate.yamlnode import YamlError, YMap
    from ofplang.validate.imports import load_expanded
    from ofplang.validate import shape as shape_pass
    from ofplang.validate import identifiers as identifiers_pass
    from ofplang.validate import entry as entry_pass
    from ofplang.validate import typecheck as typecheck_pass
    from ofplang.validate import traits as traits_pass
    from ofplang.validate import views as views_pass
    from ofplang.validate import phases as phases_pass
    from ofplang.validate import features as features_pass
    from ofplang.validate import objects as objects_pass
    from ofplang.validate import generics as generics_pass
    from ofplang.validate import script as script_pass
    from ofplang.validate import nodes as nodes_pass
    from ofplang.validate import contracts as contracts_pass
    from ofplang.validate import scheduling as scheduling_pass
    from ofplang.validate import references as references_pass
    from ofplang.validate.objects import build_signatures
    from ofplang.validate.types import build_env

    if mode not in MODES:
        raise ValueError(f"unknown validation mode: {mode!r}")

    diags = Diagnostics()

    # Step 1: load and import-expand the document (spec 2.2 step 1). Both YAML
    # load failures and structural import failures are fatal — nothing can be
    # validated without a fully expanded tree — so they surface as the sole
    # diagnostic and stop.
    try:
        root = load_expanded(source)
    except YamlError as exc:
        # Surface the failure's own position (file/line) when it has one.
        diags.add(exc.code, exc.message, at=exc.pos)
        return diags.result()

    # Step 2: structural shape, reserved-key, and metadata-format checks.
    shape_pass.check_shape(root, diags, mode)

    # Identifier grammar / reserved-name checks on declaration sites.
    identifiers_pass.check_identifiers(root, diags) if _is_map(root) else None

    # Type layer (spec 2.5, 4, 6, 7). These passes assume a mapping root and a
    # resolved type environment; a bad root was already reported by shape, so we
    # skip them rather than risk cascading noise.
    if isinstance(root, YMap):
        env = build_env(root)
        # Signatures are built once and shared by the graph-level passes.
        sigs = build_signatures(root, env)
        typecheck_pass.check_types(root, diags, env)
        traits_pass.check_traits(root, diags, env)
        views_pass.check_views(root, diags, env)
        phases_pass.check_phases(root, diags, env)
        features_pass.check_features(root, diags, mode)
        objects_pass.check_objects(root, diags, env)
        generics_pass.check_generics(root, diags, env, sigs)
        script_pass.check_scripts(root, diags, env)
        nodes_pass.check_nodes(root, diags, sigs)
        references_pass.check_references(root, diags, sigs)
        contracts_pass.check_contracts(root, diags, env)
        scheduling_pass.check_scheduling(root, diags, mode, sigs)

    # Entry process resolution and process-dependency acyclicity.
    entry_pass.check_entry(root, diags)
    entry_pass.check_process_dependencies(root, diags)

    return diags.result()


def _is_map(node) -> bool:
    # Small guard so identifier checking (which assumes a mapping root) is only
    # invoked on a well-formed root; a bad root was already reported by shape.
    from ofplang.validate.yamlnode import YMap

    return isinstance(node, YMap)
