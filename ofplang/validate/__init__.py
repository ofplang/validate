"""ofplang.validate -- validator for Object-flow Programming Language v0."""

from ofplang.validate.validator import (
    EXTENSION_TOLERANT,
    MODES,
    STRICT,
    Diagnostic,
    ValidationResult,
    validate,
)

__all__ = [
    "Diagnostic",
    "ValidationResult",
    "validate",
    "STRICT",
    "EXTENSION_TOLERANT",
    "MODES",
]
