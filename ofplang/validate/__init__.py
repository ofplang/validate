"""ofplang.validate -- validator for Object-flow Programming Language v0."""

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
