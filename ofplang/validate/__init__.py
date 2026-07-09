"""ofplang v0 -- object-flow programming language tooling."""

from ofplang.validate.validator import (
    Diagnostic,
    ValidationResult,
    validate,
    STRICT,
    EXTENSION_TOLERANT,
    MODES,
)

__all__ = [
    "Diagnostic",
    "ValidationResult",
    "validate",
    "STRICT",
    "EXTENSION_TOLERANT",
    "MODES",
]
