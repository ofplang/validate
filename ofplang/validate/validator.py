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
    # The import-expanded document as plain Python (what ``yaml.safe_load`` would
    # yield for the fully-resolved tree), populated only when ``validate`` is
    # called with ``expand=True`` and load + ``$import`` resolution succeeded;
    # otherwise ``None``. It lets a front door validate and obtain the exact
    # document to execute in one pass, instead of re-reading the unexpanded file.
    document: dict | None = None

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
    expand: bool = False,
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
    expand:
        When ``True``, populate :attr:`ValidationResult.document` with the plain
        Python form of the import-expanded tree (see :func:`expand`) whenever
        load + ``$import`` resolution succeeds — even if later passes report
        errors. Lets a front door validate and obtain the exact document to
        execute in a single pass. Defaults to ``False`` (no extra work, and the
        result's ``document`` stays ``None``).

    The pipeline follows the spec's processing order (spec 2.2): load, then a
    sequence of passes each appending to a shared :class:`Diagnostics` sink.
    """
    # Imported lazily so this module has no import-time dependency on PyYAML or
    # the pass modules — keeps the public API cheap to import.
    from ofplang.validate import contracts as contracts_pass
    from ofplang.validate import duplicates as duplicates_pass
    from ofplang.validate import entry as entry_pass
    from ofplang.validate import features as features_pass
    from ofplang.validate import generics as generics_pass
    from ofplang.validate import identifiers as identifiers_pass
    from ofplang.validate import nodes as nodes_pass
    from ofplang.validate import objects as objects_pass
    from ofplang.validate import phases as phases_pass
    from ofplang.validate import references as references_pass
    from ofplang.validate import scheduling as scheduling_pass
    from ofplang.validate import script as script_pass
    from ofplang.validate import shape as shape_pass
    from ofplang.validate import traits as traits_pass
    from ofplang.validate import typecheck as typecheck_pass
    from ofplang.validate import views as views_pass
    from ofplang.validate.diagnostics import Diagnostics
    from ofplang.validate.imports import load_expanded
    from ofplang.validate.objects import build_signatures
    from ofplang.validate.types import build_env
    from ofplang.validate.yamlnode import YamlError, YMap, to_plain

    if mode not in MODES:
        raise ValueError(f"unknown validation mode: {mode!r}")

    diags = Diagnostics()

    # Step 1: load and import-expand the document (spec 2.2 step 1). Both YAML
    # load failures and structural import failures are fatal — nothing can be
    # validated without a fully expanded tree — so they surface as the sole
    # diagnostic and stop (with `document` left None, since there is no tree).
    try:
        root = load_expanded(source, base_dir)
    except YamlError as exc:
        # Surface the failure's own position (file/line) when it has one.
        diags.add(exc.code, exc.message, at=exc.pos)
        return diags.result()

    # The expanded document is available now that load succeeded; capture it up
    # front (when requested) so it is returned even if later passes find errors.
    document = to_plain(root) if expand else None

    # Step 2: structural shape, reserved-key, and metadata-format checks.
    shape_pass.check_shape(root, diags, mode)

    # Duplicate mapping keys (spec 2.3, 2.4): non-fatal, so they no longer
    # suppress other findings. Import-merge duplicates were already caught
    # fatally during expansion (spec 3.2).
    duplicates_pass.check_duplicates(root, diags)

    # Identifier grammar / reserved-name checks on declaration sites.
    if isinstance(root, YMap):
        identifiers_pass.check_identifiers(root, diags)

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
        references_pass.check_references(root, diags, sigs, env)
        contracts_pass.check_contracts(root, diags, env)
        scheduling_pass.check_scheduling(root, diags, mode, sigs)

    # Entry process resolution and process-dependency acyclicity.
    entry_pass.check_entry(root, diags)
    entry_pass.check_process_dependencies(root, diags)

    result = diags.result()
    result.document = document  # type: ignore[assignment]  # dict for a valid v0 root
    return result


def expand(source: str | Path, *, base_dir: str | Path | None = None) -> dict:
    """Return the fully ``$import``-expanded document rooted at ``source`` as
    plain Python (spec 2.2 step 1 / spec 3).

    This is the structural expansion step on its own — load, resolve every
    ``$import``, and convert to the plain value ``yaml.safe_load`` would produce
    for the resolved tree. It performs no v0 validation; a front door that also
    wants diagnostics should call :func:`validate` with ``expand=True`` instead,
    which does both in one pass.

    Raises :class:`~ofplang.validate.yamlnode.YamlError` for any structural
    import failure (unreadable target, cycle, multi-document target, wrong-shape
    merge, duplicate key after merge, …) — the same failures that make the whole
    document unusable for validation. ``base_dir`` overrides where the root's
    relative imports resolve (see :func:`~ofplang.validate.imports.load_expanded`).

    A syntactically valid but non-mapping root (a bare scalar or sequence) is
    returned as-is; callers that require a mapping (e.g. a scheduler or runner
    front door) make that check themselves.
    """
    from ofplang.validate.imports import load_expanded
    from ofplang.validate.yamlnode import to_plain

    return to_plain(load_expanded(source, base_dir))  # type: ignore[return-value]
