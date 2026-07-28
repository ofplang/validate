"""ofplang.validate -- validator for Object-flow Programming Language v0."""

from ofplang.validate.validator import (
    EXTENSION_TOLERANT,
    MODES,
    STRICT,
    Diagnostic,
    ValidationResult,
    expand,
    validate,
)

__all__ = [
    "Diagnostic",
    "ValidationResult",
    "validate",
    "expand",
    "STRICT",
    "EXTENSION_TOLERANT",
    "MODES",
]
