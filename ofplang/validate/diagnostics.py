"""Diagnostic collection.

Intent: every validation pass shares one growing list of findings rather than
raising on the first problem, because the conformance suite matches on the *set*
of error codes a document produces (see tests/conformance/README.md). Collecting
instead of raising lets one document surface several independent violations and
lets each pass keep going after a non-fatal error.

This module is a thin sink around the public ``Diagnostic``/``ValidationResult``
types defined in :mod:`ofplang.validate.validator`, so the API surface the tests depend on
stays in one place.
"""

from __future__ import annotations

from ofplang.validate.validator import Diagnostic, ValidationResult


class Diagnostics:
    """Mutable collector of :class:`Diagnostic` findings for one validation run."""

    def __init__(self) -> None:
        # Order of insertion is preserved for readable output, but callers must
        # not rely on it for matching: comparison against fixtures is set-based.
        self._items: list[Diagnostic] = []

    def add(
        self, code: str, message: str = "", path: str | None = None, at=None
    ) -> Diagnostic:
        """Record a finding. Returns it so callers can reference/inspect it.

        ``at`` optionally supplies the source position: either a node (anything
        exposing a ``.pos`` with ``file``/``line``/``col``) or a ``Pos`` itself.
        It is duck-typed so this module needs no import of the YAML node layer.
        """
        file = line = col = None
        pos = getattr(at, "pos", at)  # a node carries .pos; a Pos is used as-is
        if pos is not None:
            file = getattr(pos, "file", None)
            line = getattr(pos, "line", None)
            col = getattr(pos, "col", None)
        diag = Diagnostic(code=code, message=message, path=path, file=file, line=line, col=col)
        self._items.append(diag)
        return diag

    def has(self, code: str) -> bool:
        """Whether any recorded finding carries ``code``.

        Used by passes that should stay silent once an earlier, more specific
        error about the same construct has already been reported (avoids
        double-reporting that would break exact-match fixtures).
        """
        return any(d.code == code for d in self._items)

    @property
    def items(self) -> list[Diagnostic]:
        return list(self._items)

    @property
    def codes(self) -> list[str]:
        return [d.code for d in self._items]

    def result(self) -> ValidationResult:
        """Freeze the collected findings into the public result object."""
        return ValidationResult(diagnostics=list(self._items))
