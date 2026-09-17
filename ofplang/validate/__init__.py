"""ofplang.validate -- validator for Object-flow Programming Language v0."""

from ofplang.validate.validator import (
    ERROR,
    EXTENSION_TOLERANT,
    MODES,
    STRICT,
    WARNING,
    Diagnostic,
    ValidationResult,
    expand,
    validate,
)
from ofplang.validate.version import SPEC_VERSION

__all__ = [
    "SPEC_VERSION",
    "Diagnostic",
    "ValidationResult",
    "validate",
    "expand",
    "STRICT",
    "EXTENSION_TOLERANT",
    "MODES",
    "ERROR",
    "WARNING",
]
