"""Canonical ofplang v0 validation error codes.

This module is the single source of truth for the error-code vocabulary used by
conformance test fixtures. Every ``code`` listed in a ``*.expected.yaml`` file
must be a member of :data:`ERROR_CODES`; the conformance runner rejects unknown
codes so the fixtures cannot drift from this catalog.

Codes are grouped by the specification area they come from (see SPECIFICATION.md
section references in the comments). The string values are stable identifiers
and MUST NOT be renamed casually -- fixtures reference them by value.
"""

from __future__ import annotations

# --- Document shape / YAML strictness (spec 2.3, 2.4) ---------------------
UNKNOWN_KEY = "unknown_key"
NULL_VALUE = "null_value"
RESERVED_DOLLAR_KEY = "reserved_dollar_key"
MISSING_REQUIRED_KEY = "missing_required_key"
WRONG_VALUE_KIND = "wrong_value_kind"
SECTION_NOT_VALID_FOR_KIND = "section_not_valid_for_kind"
OBJECTS_ON_COMPOSITE = "objects_on_composite"

# --- Reserved metadata (spec 2.1) -----------------------------------------
MALFORMED_SPEC_VERSION = "malformed_spec_version"

# --- Identifiers and reserved names (spec 2.4) ----------------------------
INVALID_IDENTIFIER = "invalid_identifier"
DOT_IN_IDENTIFIER = "dot_in_identifier"
RESERVED_NAME = "reserved_name"
DUPLICATE_PORT_NAME = "duplicate_port_name"

# --- Type expressions (spec 2.5, 7.1) -------------------------------------
UNKNOWN_TYPE = "unknown_type"
MALFORMED_TYPE_EXPR = "malformed_type_expr"
ARRAY_ARITY = "array_arity"
REDECLARE_BUILTIN = "redeclare_builtin"
TYPE_PARAM_SHADOW = "type_param_shadow"
TYPE_FIELD_NOT_STRING = "type_field_not_string"

# --- Structural imports (spec 3) ------------------------------------------
IMPORT_CYCLE = "import_cycle"
DUPLICATE_KEY_AFTER_IMPORT = "duplicate_key_after_import"
INVALID_IMPORT_SHAPE = "invalid_import_shape"
UNREADABLE_IMPORT = "unreadable_import"
MULTIDOC_IMPORT = "multidoc_import"
URI_FRAGMENT_IMPORT = "uri_fragment_import"
EMPTY_IMPORT = "empty_import"
NON_STRING_IMPORT_PATH = "non_string_import_path"

# --- Feature model (spec 4) -----------------------------------------------
UNKNOWN_FEATURE = "unknown_feature"
MISSING_REQUIRED_FEATURE = "missing_required_feature"

# --- Phases (spec 6) ------------------------------------------------------
OBJECT_GRAPH_PHASE = "object_graph_phase"
INVALID_PHASE_FLOW = "invalid_phase_flow"

# --- Traits (spec 7.3) ----------------------------------------------------
UNKNOWN_TRAIT = "unknown_trait"
IMPLEMENTS_NUMERIC = "implements_numeric"
REDECLARE_NUMERIC = "redeclare_numeric"

# --- Views (spec 7.4) -----------------------------------------------------
OBJECT_BEARING_VIEW_FIELD = "object_bearing_view_field"
INVALID_VIEW_FIELD_TYPE = "invalid_view_field_type"
STATIC_VALUE_TYPE_MISMATCH = "static_value_type_mismatch"
NULL_STATIC_VALUE = "null_static_value"

# --- Contracts (spec 9) ---------------------------------------------------
CONTRACT_PARSE_ERROR = "contract_parse_error"
CONTRACT_TYPE_ERROR = "contract_type_error"
UNKNOWN_VIEW_FIELD = "unknown_view_field"
CONTRACT_STATIC_FALSE = "contract_static_false"
CONTRACT_INVALID_REFERENCE = "contract_invalid_reference"
CONTRACT_REFERENCE_SCOPE = "contract_reference_scope"
COMPARISON_CHAIN = "comparison_chain"

# --- Generic constraints and instantiation (spec 8) -----------------------
TYPE_PARAM_NOT_IN_INPUT = "type_param_not_in_input"
UNINFERABLE_TYPE_PARAM = "uninferable_type_param"
CONFLICTING_INFERENCE = "conflicting_inference"
MALFORMED_CONSTRAINT = "malformed_constraint"
CONSTRAINT_ON_CONCRETE = "constraint_on_concrete"
CONSTRAINT_NOT_SATISFIED = "constraint_not_satisfied"
MISSING_TYPE_PARAM_DOMAIN = "missing_type_param_domain"
BAD_TYPE_PARAM_DOMAIN = "bad_type_param_domain"

# --- Object tracking / linearity (spec 12, 13) ----------------------------
OBJECT_OUTPUT_UNUSED = "object_output_unused"
OBJECT_FANOUT = "object_fanout"
OBJECT_INPUT_NO_SOURCE = "object_input_no_source"
OBJECT_INPUT_MULTI_SOURCE = "object_input_multi_source"
INCOMPLETE_OBJECTS = "incomplete_objects"
MULTIPLE_FATES = "multiple_fates"
MULTIPLE_PROVENANCES = "multiple_provenances"
IMPLICIT_CREATE = "implicit_create"
IMPLICIT_DISCARD = "implicit_discard"
OBJECTS_PATH_NOT_FOUND = "objects_path_not_found"
DATA_INDEGREE = "data_indegree"
OBJECT_VIA_BIND = "object_via_bind"

# --- Transforms (spec 14.4) -----------------------------------------------
UNKNOWN_TRANSFORM_KIND = "unknown_transform_kind"
INVALID_TRANSFORM_ROLES = "invalid_transform_roles"
TRANSFORM_ROLE_TYPE_MISMATCH = "transform_role_type_mismatch"
PURE_DATA_IN_TRANSFORM = "pure_data_in_transform"

# --- Structured nodes (spec 16-21) ----------------------------------------
CARRY_OUTPUT_MISSING = "carry_output_missing"
CARRY_OUTPUT_NOT_CARRY_MODE = "carry_output_not_carry_mode"
ONE_SIDED_OBJECT_OUTPUT = "one_sided_object_output"
BRANCH_NOT_IDENTITY_EQUIVALENT = "branch_not_identity_equivalent"
INVALID_OUTPUT_MODE = "invalid_output_mode"
OBJECT_OUTPUT_BAD_MODE = "object_output_bad_mode"
NONCARRY_OBJECT_OUTPUT_UNLISTED = "noncarry_object_output_unlisted"
LAST_ON_EMPTY_FOLD = "last_on_empty_fold"
ZIP_MISMATCH = "zip_mismatch"
ARRAY_UNCONS_EMPTY = "array_uncons_empty"
MISSING_MAX_ITERATIONS = "missing_max_iterations"
BAD_CONDITION_OUTPUT = "bad_condition_output"
NONCARRY_OBJECT_OUTPUT_IN_DO_WHILE = "noncarry_object_output_in_do_while"

# --- Script processes (spec 22) -------------------------------------------
SCRIPT_OBJECT_PORT = "script_object_port"
SCRIPT_HAS_OBJECTS = "script_has_objects"
UNSUPPORTED_SCRIPT_LANGUAGE = "unsupported_script_language"

# --- Scheduling policies (spec 23, 24) ------------------------------------
SCHEDULING_ON_ATOMIC = "scheduling_on_atomic"
GAP_WITH_OBJECT = "gap_with_object"
TEMPERATURE_WITHOUT_OBJECT = "temperature_without_object"
UNKNOWN_PREFER_KIND = "unknown_prefer_kind"
BAD_TEMPORAL_REF = "bad_temporal_ref"
NON_OBJECT_BEARING_TARGET = "non_object_bearing_target"
MALFORMED_PREFER_PAYLOAD = "malformed_prefer_payload"

# --- Entry process (spec 10.3) --------------------------------------------
NO_ENTRY_PROCESS = "no_entry_process"
UNKNOWN_ENTRY_PROCESS = "unknown_entry_process"
RECURSIVE_PROCESS_DEPENDENCY = "recursive_process_dependency"

# --- References (spec 2.6) ------------------------------------------------
MALFORMED_REFERENCE = "malformed_reference"
UNKNOWN_REFERENCE = "unknown_reference"
INVALID_REFERENCE_SCOPE = "invalid_reference_scope"
BINDING_SOURCE_ARITY = "binding_source_arity"


def _collect_codes() -> "frozenset[str]":
    codes = set()
    for name, value in globals().items():
        if name.isupper() and isinstance(value, str) and not name.startswith("_"):
            codes.add(value)
    return frozenset(codes)


#: Every error code known to the specification vocabulary. Conformance
#: fixtures may only reference codes in this set.
ERROR_CODES: "frozenset[str]" = _collect_codes()
