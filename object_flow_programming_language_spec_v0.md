# Object-flow Programming Language v0 Specification Current Draft

Status: current working draft  
Date: 2026-07-08  
Based on: baseline 2026-07-01, with design resolutions through 2026-07-08, including YAML shape, identifier, type-expression, reference-syntax, import-boundary, contract-expression, static-view-value, generic-instantiation, implementation-extension, and scheduling-policy-schema resolutions

This document is a self-contained current draft specification for a dataflow-oriented workflow IR with linear Object tracking. It focuses on successful workflow semantics, Object/data flow, structured control, scheduling policies, and type modeling. Runtime failures, exceptions, retries, cancellation, compensation, and recovery are intentionally outside the scope of v0.

v0 uses a **Core + Features** model. The canonical v0 form contains a `features` section listing all features required by the document body. If `features` is omitted, the required features are derived from the document body and the document is interpreted as if the derived feature set had been written explicitly.

---

## 1. Design Goals

The language is intended to describe workflows involving both ordinary data and physical or logical Objects that must be tracked linearly.

The key goals of v0 are:

1. Keep the core language small.
2. Support structural `$import` for splitting descriptions across files without introducing a module system.
3. Track Object-bearing values without implicit creation, loss, duplication, discard, or consumption.
4. Support structured dataflow patterns through explicit feature-gated node kinds.
5. Keep scheduling policies as best-effort preferences rather than hard semantic obligations.
6. Avoid deep subtyping, inheritance, refinement types, Optional, Result, union branch outputs, and dependent typing in v0.
7. Express common operational grouping through nominal type traits rather than subtyping or parameterized Object types.
8. Keep contract-visible views and trait membership explicit in YAML, except for v0 built-in primitive and Array views.
9. Keep feature derivation mostly syntactic and easy to validate from YAML structure.

---

## 2. Document Structure and Processing Order

A v0 document may contain:

```yaml
spec_version: "0.0"
features: []
traits: {}
types: {}
processes: {}
entry: main
```

### 2.1 Specification version metadata

A v0 document may declare reserved specification-version metadata using the top-level `spec_version` field.

```yaml
spec_version: "0.0"
```

In v0, `spec_version` is metadata only. It does not affect document interpretation, validation semantics, feature derivation, type checking, Object tracking, scheduling policy handling, or runtime behavior.

`spec_version` is not the workflow author's document version.

If present, the value of `spec_version` must be a string using a two-number version format:

```text
MAJOR.MINOR
```

For the current v0 draft, the conventional value is `"0.0"`.

A malformed `spec_version` value is a validation error. Omission of `spec_version` is allowed in v0.

### 2.2 Processing order

The processing order is:

1. Resolve `$import` structurally.
2. Validate document shape, reserved keys, and reserved metadata field formats.
3. Resolve types, type traits, view schemas, phases, and process references.
4. Derive required features from the expanded document body.
5. Validate the declared `features` section, if present.
6. Type-check node bindings and structured node outputs.
7. Check Object tracking completeness and linearity.
8. Check scheduling policy references and feature requirements.
9. Resolve the entry process.

`$import` has no runtime meaning after import resolution.


### 2.3 YAML shape strictness

v0 portable YAML is closed by default. At every defined mapping position, only keys explicitly defined by the v0 specification are allowed. Unknown keys are validation errors.

Implementation extension keys are allowed only when they use the reserved extension-key prefix `x-`. A document containing `x-` extension keys is not strict portable v0 unless the validator is explicitly run in an extension-tolerant mode.

The `scheduling.policies[*].prefer` payload has a v0-defined closed shape for v0-defined scheduling preference kinds. Unit strings inside scheduling preference payloads are implementation-defined strings in v0. Extension preference kinds and extension payload fields are allowed only in extension-tolerant mode when they use the reserved extension-key prefix `x-`.

For every mapping position, the specification defines a shape schema consisting of allowed keys, required keys, value kinds, and conditional requirements. A value-kind mismatch, missing required key, unexpected sequence item shape, or unexpected `null` value is a validation error unless explicitly allowed by the relevant schema rule.

`null` values are not valid in v0 portable YAML. Future feature extensions may define nullable values explicitly, but v0 core does not.

Omitted `traits`, `types`, `inputs`, and `outputs` sections are interpreted as empty mappings. The `features` section may be omitted and is then interpreted as the feature set derived from the expanded document body. The `processes` section is required.

A process that has no input ports may omit `inputs`. A process that has no output ports may omit `outputs`. Omitted `inputs` and `outputs` are equivalent to empty mappings.

Each node kind defines the binding and output-control sections that are valid for that kind. A section that is not defined for the node kind is a validation error.

### 2.4 Identifier syntax and reserved names

v0 portable YAML uses case-sensitive ASCII identifiers.

Unless a more specific rule is defined for a syntactic position, user-defined identifiers must match:

```text
[A-Za-z_][A-Za-z0-9_]*
```

The period character `.` is not allowed in v0 identifiers. It is reserved for possible future use by namespaces, modules, qualified names, or other path-like constructs. This restriction applies to process names as well as type names, trait names, type parameter names, port names, node ids, binding names, return names, and view field names.

The recommended style is:

```text
Type names: PascalCase or UpperCamelCase
Trait names: PascalCase or UpperCamelCase
Type parameter names: short uppercase names such as T, P, A, or B
Process names: lower_snake_case
Port names, node ids, binding names, return names, and view field names: snake_case or another ASCII identifier style chosen by the author
```

These style recommendations are not validation requirements unless an implementation chooses to enforce them as non-portability diagnostics.

Names are case-sensitive. For example, `Sample`, `sample`, and `SAMPLE` are distinct names.

Unicode characters are not allowed in identifiers in portable v0. UTF-8 text is allowed in YAML string values and comments, including Japanese text.

The built-in names `Bool`, `Int`, `Float`, `String`, `Array`, and `Numeric` are reserved and must not be redeclared as user-defined type names, trait names, or type parameter names.

Input port names and output port names are separate namespaces. Therefore, an input port and an output port of the same process may have the same name. Duplicate names within `inputs` are validation errors. Duplicate names within `outputs` are validation errors.

The key `$import` is the only `$`-prefixed reserved key defined by v0. Any other `$`-prefixed key is a validation error in portable v0.

The following names are reserved and must not be used as process names, port names, node ids, binding names, return names, view field names, type names, trait names, or type parameter names:

```text
inputs
outputs
self
view
objects
features
traits
types
processes
entry
body
nodes
returns
state
bind
carry
each
args
then
else
condition
scheduling
policies
during
object
from
to
kind
process
phase
type
value
script
contracts
requires
ensures
```

A validator may report a more specific error when a name fails the grammar for its syntactic position or conflicts with a reserved name.


### 2.5 Type expression syntax

In v0 portable YAML, every `type` field value must be a YAML string scalar containing a v0 type expression.

The v0 type expression grammar is:

```text
TypeExpr      ::= TypeAtom | ArrayType
ArrayType     ::= "Array" "<" S? TypeExpr S? ">"
TypeAtom      ::= Identifier
Identifier    ::= [A-Za-z_][A-Za-z0-9_]*
S             ::= one or more ASCII space or tab characters
```

Whitespace is allowed only immediately inside the angle brackets of `Array<T>`. Therefore the following type expressions are valid and have the same meaning:

```text
Array<T>
Array< T >
Array<   T   >
Array<Array< Sample >>
```

Whitespace between `Array` and `<` is not allowed. Therefore the following are validation errors:

```text
Array <T>
Array < T >
```

The recommended canonical style is to write type expressions without whitespace:

```text
Array<T>
Array<Array<Sample>>
```

The only built-in primitive Data types are:

```text
Bool
Int
Float
String
```

The only built-in type constructor is:

```text
Array<T>
```

`Array` requires exactly one type argument. Nested Arrays such as `Array<Array<Sample>>` are valid.

The names `Bool`, `Int`, `Float`, `String`, `Array`, and `Numeric` are reserved and must not be redeclared as user-defined type names, trait names, or type parameter names.

A type atom must resolve to exactly one of:

```text
a built-in primitive type
a top-level user-defined type
a type parameter declared by the current process
```

A type parameter must not shadow a top-level user-defined type name or a reserved built-in name.

Unknown type names are validation errors. Malformed type expressions are validation errors. A `type` field whose value is not a YAML string scalar is a shape validation error.

No other type constructors or type syntax are defined by v0. In particular, `Optional<T>`, `Result<T,E>`, union types, nullable suffixes such as `T?`, map types, tuple types, function types, and multiple type arguments are not valid v0 type expressions.

Names such as `Optional`, `Result`, and `Map` are not reserved by v0 merely because future versions may define additional type constructors.

Implementations may normalize parsed type expressions internally, but such normalization has no effect on document semantics and must not silently repair, case-normalize, or otherwise rewrite malformed type expressions.

### 2.6 Reference syntax

In v0 portable YAML, every `from` field value must be a YAML string scalar containing a v0 reference expression.

The following reference syntaxes are distinct and are valid only in their specified contexts.

#### 2.6.1 Body dataflow references

Node bindings, `branch.condition.from`, and `body.returns` use body dataflow references:

```text
BodyRef ::= "inputs" "." Identifier | Identifier "." Identifier
```

The first form refers to an input port of the current composite process. The second form refers to an output of a direct child node in the same composite body.

Body dataflow references must not include `.view`, temporal fields, nested node paths, process names, or `outputs.*`.

#### 2.6.2 Atomic Object paths

Atomic `objects` declarations use Object paths:

```text
ObjectInputPath  ::= "inputs" "." Identifier
ObjectOutputPath ::= "outputs" "." Identifier
```

`objects.map` maps Object output paths to Object input paths. `objects.consume` lists Object input paths. `objects.create` lists Object output paths. `objects.transform` role values use Object input paths under `inputs` and Object output paths under `outputs`.

v0 Object paths refer to process ports. They do not directly address Array elements, Object slots, `.view` fields, or implementation-specific internal fields. Object slots are derived from the resolved port type.

#### 2.6.3 Scheduling Object target references

`scheduling.policies[*].object.from` uses a body-visible Object-bearing value reference:

```text
PolicyObjectRef ::= "inputs" "." Identifier | Identifier "." Identifier
```

The referenced value must be Object-bearing in the declaring composite scope.

#### 2.6.4 Temporal references

`scheduling.policies[*].during.from` and `scheduling.policies[*].during.to` use temporal references:

```text
TemporalRef ::= "self" "." ("start" | "end")
              | Identifier "." ("start" | "end")
```

`self.start` and `self.end` refer to the current composite invocation. `Identifier.start` and `Identifier.end` refer to a direct child node in the current composite body.

#### 2.6.5 Contract view references

Contract expressions use contract view references.

In `requires`, references may use:

```text
inputs.<port>.view
inputs.<port>.view.<field>
```

In `ensures`, references may use:

```text
inputs.<port>.view
inputs.<port>.view.<field>
outputs.<port>.view
outputs.<port>.view.<field>
```

Contract expressions do not directly reference ports without `.view`. Although every valid contract reference is a view reference, v0 requires `.view` to be written explicitly.

In v0, contract view field references address one view field at a time. Nested field paths below a view field are not defined in v0.

#### 2.6.6 Binding source entries

A binding source entry must contain exactly one of:

```text
from
value
```

A source entry containing both `from` and `value`, or neither, is a validation error.

#### 2.6.7 Structured condition references

`branch.condition.from` uses a body dataflow reference.

`do_while.condition.output` is an output name of the target process, not a body dataflow reference. The named target process output must exist and must be a Boolean Data output. It is evaluated after each invocation of the target process. The `do_while` node repeats while this output value is `true` and exits when it is `false`, subject to `max_iterations`.

The condition output is an ordinary non-carry Data output for output-mode purposes. It may be exposed using `collect`, `last`, or `drop` when explicit `do_while.outputs` are present. If `do_while.outputs` is omitted, the condition output is dropped by default.

Reference parsing and reference resolution are separate validation steps. A malformed reference is a validation error. A syntactically valid reference whose target does not exist is an unknown reference validation error. A reference that is not valid in its syntactic context is an invalid reference scope validation error. A reference whose resolved type or phase does not satisfy the target requirement is a type or phase validation error.

---

## 3. Structural Imports

v0 supports structural imports using the reserved key `$import`.

```yaml
$import: <path>
```

The value of `$import` may also be a sequence of paths. In that case, the files are imported in list order.

```yaml
$import:
  - <path>
  - <path>
```

`$import` is resolved before validation, type checking, Object tracking completeness checks, feature derivation, scheduling policy checks, and entry process resolution.

`$import` is structural inclusion, not a module system. It does not introduce namespaces, aliases, selective imports, visibility rules, or re-export semantics.

Import cycles are validation errors.

`$import` is a reserved key in v0. Top-level `imports` and `$imports` are not part of v0.

### 3.1 Import paths

In v0, the value of `$import` is a path string, or a sequence of path strings.

Relative `$import` paths are resolved relative to the file containing the `$import`.

```yaml
$import: ./types.yaml
```

```yaml
$import:
  - ./base.yaml
  - ../shared/labware.yml
```

The v0 core language does not forbid `..` path segments.

Absolute paths without a URI scheme are allowed in v0, but they are discouraged because they reduce portability across machines, directory layouts, and packaging contexts. Authors should prefer relative paths for portable documents.

URI-scheme references such as `file:`, `http:`, and `https:` are implementation extensions in v0 and are not portable v0.

A `$import` target denotes a whole YAML document. URI fragments in `$import` values are not defined in v0 and are validation errors for portable v0 documents.

v0 does not require a specific file extension. Authors should prefer `.yaml` or `.yml` for ofplang documents and imported fragments.

### 3.2 Import insertion rules

When `$import` appears in a mapping, each imported document must be a mapping. Imported mappings are merged into the surrounding mapping.

Example:

```yaml
types:
  $import:
    - ./base_types.yaml
    - ./labware_types.yaml

  LocalType:
    domain: data
```

Import resolution is equivalent to replacing the surrounding mapping with the merge of:

1. imported mappings in list order, and
2. local keys in the surrounding mapping.

Duplicate keys are validation errors. Duplicate keys are checked at the same mapping level in the final expanded mapping. v0 uses shallow structural merge at the insertion location only; it does not define deep merge, override, or conflict-resolution semantics.

When `$import` appears as an item of a sequence, each imported document may be either a single item or a sequence.

If the imported document is a sequence, its items are spliced into the surrounding sequence at the import position.

If the imported document is not a sequence, it is inserted as one sequence item.

Example:

```yaml
processes:
  main:
    kind: composite
    body:
      nodes:
        - id: prep
          process: prep
        - $import: ./process_nodes.yaml
        - id: finish
          process: finish
```

If `process_nodes.yaml` contains a sequence of nodes, those nodes are spliced between `prep` and `finish`.

### 3.3 Imports and reserved metadata

`$import` is direct structural inclusion. Imported fragments normally do not contribute top-level reserved metadata such as `spec_version`.

If an imported fragment contributes a reserved metadata key into a mapping that already contains the same key, the result is a duplicate key after import resolution and is a validation error. v0 does not define override, version negotiation, or multi-version import semantics.

After import resolution, the expanded document is validated as ordinary ofplang.

### 3.4 Import resolution boundary conditions

The value of `$import` must be either a non-empty YAML string scalar or a non-empty sequence of non-empty YAML string scalars.

An empty `$import` sequence, an empty import path string, `null`, or any non-string import path value is a validation error.

Relative `$import` paths are resolved relative to the file containing the `$import`.

Import resolution is recursive. If an imported document itself contains `$import`, those imports are resolved before the importing document is validated as an expanded document.

`$import` is an import-resolution construct only. It has no runtime meaning and does not remain as a key in the expanded document.

An import target must be a single YAML document. YAML multi-document streams are not portable v0 import targets.

When `$import` appears in a mapping, each imported document must expand to a mapping. Imported mappings are merged into the surrounding mapping using v0 shallow structural merge rules.

When `$import` appears as an item of a sequence, each imported document may expand to either a sequence or a single item. If the imported document expands to a sequence, its items are spliced into the surrounding sequence at the import position. Otherwise, it is inserted as one sequence item.

Import cycles are validation errors. Implementations must detect cycles after resolving relative paths against the importing file. Implementations should use a stable canonical file identity when available.

The same import target may be imported more than once, but the expanded result must still satisfy all duplicate-key, namespace uniqueness, shape, and semantic validation rules.

If an import target cannot be found, cannot be read, cannot be parsed as YAML, contains more than one YAML document, has an invalid root shape for its insertion location, or otherwise cannot be expanded, import resolution fails and the document is invalid.

URI-scheme references such as `file:`, `http:`, and `https:` are implementation extensions in v0 and are not portable v0. URI fragments in `$import` values are not defined in v0 and are validation errors for portable v0 documents.

Extension keys imported from another document are treated like extension keys written locally. Duplicate-key detection applies to extension keys as well.

---

## 4. Feature Model

### 4.1 Canonical feature declaration

`features` declares the feature set required by the document.

The canonical v0 form contains a `features` section listing all features required by the document body.

If `features` is omitted, the required feature set is derived from the document body, and the document is interpreted as if that derived set had been written explicitly.

If `features` is present, it must include every feature required by the document body. Missing required features are validation errors.

A `features` section may include features that are not required by the document body. Such extra features are allowed and do not change the semantics of the document.

Every feature name listed in `features` must be a feature name defined by v0. Unknown feature names are validation errors.

If a document requires a feature that is defined by v0 but not supported by a particular implementation, the document is valid v0 but unsupported by that implementation.

### 4.2 v0 feature names

Defined v0 feature names are:

```text
node_map
node_fold
node_do_while
node_branch
python_script_processes
scheduling_policies
```

### 4.3 Feature derivation

Required features are derived primarily from explicit YAML syntax.

Structured node features are derived from node `kind` values:

```text
kind: map       -> node_map
kind: fold      -> node_fold
kind: do_while  -> node_do_while
kind: branch    -> node_branch
```

A `script` section with `script.language: python` requires:

```text
python_script_processes
```

A `scheduling` section requires:

```text
scheduling_policies
```

The `scheduling_policies` feature covers both scalar Object policy targets and Object-bearing collection policy targets. No separate `object_collection_policies` feature is defined in v0.

### 4.4 Unsupported features vs validation errors

A validation error means the IR does not satisfy v0 well-formedness rules.

An unsupported feature means the IR is valid v0 but requires a v0-defined feature not supported by a particular implementation.

Examples of validation errors:

```text
unknown feature name
required feature missing from an explicit features section
malformed spec_version metadata value
Object-bearing output is unused
Object-bearing output fans out
Object-bearing input has no source
Object-bearing input has multiple sources
objects path does not exist
objects section is incomplete
objects section appears on a composite process
import cycle
duplicate key after import resolution
invalid import shape for insertion location
unknown type name
unknown trait name
unknown implemented trait
malformed view schema
view field has Object-bearing type
view field static value does not conform to its declared type
contract references an unknown view field
contract contains a statically known basic type error
script process has Object-bearing input or output
script process contains an objects section
script process declares an unsupported script language
fold or do_while carry output is missing
branch arm has one-sided Object-bearing output
branch common Object-bearing output is not identity-equivalent across arms
Object-bearing value has graph phase
Object slot has multiple incompatible fates
Object slot has multiple incompatible provenances
unknown v0 transform kind
invalid transform role set
transform role type mismatch
Pure Data path appears in objects.transform
transform path participates in multiple incompatible Object fates or provenances
array_uncons on an Array known to be empty at graph phase
mode: last on a fold traversal known to be empty at graph phase
zip-equal traversal length mismatch known at graph phase
```

Examples of run-start or runtime data errors:

```text
mode: last on a fold traversal that is empty, when emptiness is first determined at run or data phase
array_uncons on an empty Array, when emptiness is first determined at run or data phase
zip-equal traversal length mismatch, when the mismatch is first determined at run or data phase
```

Examples of unsupported features:

```text
workflow uses kind: map but implementation lacks node_map
workflow uses kind: fold but implementation lacks node_fold
workflow uses kind: do_while but implementation lacks node_do_while
workflow uses kind: branch but implementation lacks node_branch
workflow uses scheduling policies but implementation lacks scheduling_policies
workflow uses script.language: python but implementation lacks python_script_processes
```

---

## 5. Data, Objects, and Object-Bearing Values

### 5.1 Data vs Object

A type has a domain:

```yaml
types:
  Image:
    domain: data

  Robot:
    domain: object
```

- `domain: data` values are unrestricted Pure Data.
- `domain: object` values are Object identities tracked linearly.

An Object is not merely a record of attributes. It represents a workflow token for something whose identity, use, and lifecycle must be tracked.

v0 tracks physical Object identity and explicit Object creation/consumption. It does not model automatic policy or identity transfer across physical replacement.

### 5.2 Object-bearing values

A value is **Object-bearing** if its type contains one or more Object slots.

v0 has only one built-in type constructor:

```text
Array<T>
```

Object slots are defined recursively:

```text
object_slots(Atomic Data)   = []
object_slots(Atomic Object) = [self]
object_slots(Array<T>)      = elements[*].object_slots(T)
```

Examples:

```text
Image                  => []
Cup                    => [self]
Array<Cup>             => [elements[*]]
Array<Array<Cup>>      => [elements[*].elements[*]]
Array<Image>           => []
```

If `object_slots(T)` is empty, values of type `T` are Pure Data. If not, they are Object-bearing and subject to linear Object tracking.

Object identity belongs to Object slots, not to an enclosing Object-bearing value merely because that value contains Object slots. For an Atomic Object type, the `self` slot carries the Object identity. For `Array<T>`, the Array container is not itself a separate Object identity; the contained Object identities are the identities of the Object slots in the elements.

Linearity for Object-bearing values is an accounting rule for all contained Object slots. It must not be interpreted as assigning a distinct physical Object identity to the container value itself.

### 5.3 Object-bearing containers

An Object-bearing value may be bound as an ordinary node `state` input, a loop `carry` value, a branch `args` value, or a node output. Object-bearing containers such as `Array<Cup>` are treated as Object-bearing values.

When an Object-bearing container is passed through a binding, the container value is treated as one linear value at the binding level. The binding does not by itself destructure or traverse the contained Object slots.

Structured `each` bindings are an explicit exception: an Object-bearing collection used as an `each` source is linearly used by the structured node, and its contained Object slots are distributed to per-element invocations according to the structured node semantics. The source collection value must not also be connected elsewhere in the same body or returned directly.

Object tracking completeness is still checked at Object slot level. A container-level `objects.map` or `objects.transform` must account for every contained Object slot according to its declared meaning.

---

## 6. Phases

Every process port has a `type` and a `phase`. Process input ports are declared under `inputs`, and process output ports are declared under `outputs`.

```yaml
inputs:
  x:
    type: T
    phase: data
```

Phase order:

```text
graph < run < data
```

Meaning:

- `graph`: available during graph construction, validation, and type or shape determination.
- `run`: fixed at workflow run start and fixed for one run.
- `data`: ordinary runtime dataflow values, including most Object-bearing values.

Allowed phase flow:

```text
graph -> graph/run/data
run   -> run/data
data  -> data only
```

Invalid phase flow:

```text
data -> run/graph
run  -> graph
```

A value may flow from an earlier phase to a later phase, but not from a later phase to an earlier phase.

### 6.1 Phase of Object-bearing values

Object-bearing values are normally `data` phase values.

A `run` phase Object-bearing value is allowed only for rare cases where the Object identity is fixed at workflow run start, such as an initial resource or externally supplied Object token.

Object-bearing values must not have `graph` phase in v0.

When a `run` phase Object-bearing value flows into a `data` phase port, it is treated as the same linear Object token entering ordinary runtime dataflow. This phase lowering does not create, duplicate, consume, or otherwise alter the Object identity.

Most Object-producing process outputs should be `data` phase.

### 6.2 Phase-dependent error classification

Some requirements depend on values such as Array lengths or traversal emptiness. These facts may become known at different phases. v0 classifies such errors by the earliest phase at which the violation is determined.

If a violation is determined at `graph` phase, it is a graph-time validation error. The IR is not a valid portable v0 document.

If a violation is not known at `graph` phase but is determined at `run` phase, it is a run-start validation error or preflight error for that run. The IR may still be structurally valid v0, but the run must not proceed under those run-phase values.

If a violation is determined only at `data` phase, it is a runtime data error. Runtime data errors are outside the core validation semantics of v0, but this specification may define standard execution behavior for such cases where useful.

This principle applies to phase-dependent conditions such as empty traversal with `mode: last`, `array_uncons` on an empty Array, zip-equal length mismatch, and similar checks whose truth may depend on graph, run, or data phase values.

---

## 7. Types, Traits, and View Metadata

### 7.1 Built-in primitive Data types

v0 defines the following built-in primitive Data types:

```text
Bool
Int
Float
String
```

These primitive types are Pure Data types. They have no Object slots.

v0 defines one built-in type constructor:

```text
Array<T>
```

`Array<T>` may be Pure Data or Object-bearing depending on `T`. If `object_slots(T)` is empty, `Array<T>` is Pure Data. Otherwise, `Array<T>` is Object-bearing.

No other primitive types or type constructors are defined by v0. Additional primitive-like types or constructors are implementation extensions unless represented as user-defined nominal Data types.

### 7.2 Nominal Data and Object types

User-defined atomic types are nominal and are declared in the top-level `types` section. Each user-defined type has a `domain`:

```yaml
types:
  Image:
    domain: data

  Robot:
    domain: object
```

Object types should represent meaningful workflow state classes.

Guideline:

```text
If two physical Objects are not safely interchangeable as workflow state values,
they should normally be represented as different Object types.
```

Examples of appropriate Object types:

```text
Robot
Camera
Cup
Plate96
Plate384
Tube15ml
Sample
```

User-defined Data types are also nominal. Their internal representation is not specified by v0 unless exposed through their view schema.

### 7.3 Type traits

v0 defines one built-in type trait:

```text
Numeric
```

The built-in primitive Data types that satisfy `Numeric` are:

```text
Int
Float
```

`Numeric` is a closed built-in trait in v0. It must not be redeclared in the document's top-level `traits` section, and user-defined types must not implement `Numeric`. `Numeric` may be used only as a generic type constraint over v0 primitive numeric types.

This does not affect process-level inference markers such as `elidable_iso`, which are not type traits.

All non-built-in type trait names used by user-defined types or generic constraints must be declared in the document's top-level `traits` section.

```yaml
traits:
  PlateLike: {}
  HasWells: {}
  Labware: {}
```

Document-defined traits are nominal membership markers only. A document-defined trait does not imply fields, operators, conversions, subtyping, inheritance, Object behavior, or view structure. The closed built-in primitive trait `Numeric` is satisfied only by `Int` and `Float`; it does not define user-extensible operators, implicit conversions, subtyping, or numeric behavior for user-defined types.

User-defined nominal Data and Object types may implement declared non-built-in traits using `implements`:

```yaml
traits:
  PlateLike: {}
  HasWells: {}
  Labware: {}

types:
  Plate96:
    domain: object
    implements:
      - PlateLike
      - HasWells
      - Labware
    view:
      manufacturer:
        type: String
      catalog_number:
        type: String
      well_count:
        type: Int

  Plate384:
    domain: object
    implements:
      - PlateLike
      - HasWells
      - Labware
    view:
      manufacturer:
        type: String
      catalog_number:
        type: String
      well_count:
        type: Int
```

An unknown type trait name in top-level `traits`, `implements`, or `where` is a validation error. A type must not implement a trait that is not declared in the document's top-level `traits` section, except that `Numeric` is built in and cannot be implemented by user-defined types. Redeclaring `Numeric` in top-level `traits` or listing `Numeric` in a user-defined type's `implements` section is a validation error.

Common process grouping should be represented by traits, not by subtyping or parameterized Object types in v0. However, because traits are nominal membership markers only, any concrete process, field, view structure, conversion, or Object behavior must still be declared explicitly elsewhere in the document.

A generic process can accept any type that implements a declared trait:

```yaml
traits:
  PlateLike: {}

processes:
  plate_seal:
    kind: atomic
    type_params:
      P:
        domain: object
    where:
      - PlateLike<P>
    inputs:
      plate:
        type: P
        phase: data
    outputs:
      plate:
        type: P
        phase: data
```

The concrete type is preserved through the process. The constraint `PlateLike<P>` means only that the concrete type substituted for `P` implements the nominal trait `PlateLike`.

### 7.4 View metadata

`.view` denotes the contract-visible projection of a value. Physical Object identity is not expressed through `.view`; it is tracked by Object flow.

Primitive Data types have specification-defined views. For `Bool`, `Int`, `Float`, and `String`, `.view` is the scalar value itself.

For `Array<T>`, `.view` exposes the standard field:

```text
Array<T>.view.length: Int
```

`Array<T>.view.length` is the number of top-level elements in the Array value. This applies uniformly to Pure Data Arrays and Object-bearing Arrays. For example, both `Array<Float>` and `Array<Sample>` expose `.view.length`.

User-defined nominal Data and Object types define their contract-visible view schema in the document's `types` section. A view schema is a mapping from field names to field declarations. Each field declaration has a `type` and may optionally have a statically known `value`.

```yaml
types:
  Image:
    domain: data
    view:
      width:
        type: Int
      height:
        type: Int
      channels:
        type: Int

  Plate96:
    domain: object
    implements:
      - PlateLike
    view:
      manufacturer:
        type: String
      catalog_number:
        type: String
      well_count:
        type: Int
        value: 96

  Plate384:
    domain: object
    implements:
      - PlateLike
    view:
      well_count:
        type: Int
        value: 384
```

View fields are required, read-only Pure Data projections. In v0, a user-defined view field type must be a v0 primitive Pure Data type or an Array whose element type recursively satisfies the same restriction.

Therefore, the following view field types are valid:

```text
Bool
Int
Float
String
Array<Bool>
Array<Int>
Array<Float>
Array<String>
Array<Array<Int>>
```

The following view field types are not valid in v0:

```text
user-defined nominal Data types
Object types
Array<user-defined nominal Data type>
Array<Object type>
```

Object-bearing view fields are validation errors. User-defined nominal Data types are not valid view field types in v0, even when they define their own view schemas. If structured contract-visible metadata is needed in v0, authors should represent it using separate primitive view fields or Arrays of primitive view fields.

If a view field declaration contains `value`, that value is a type-level static view value. It is the same for every value of that nominal type. The static value must conform to the declared view field type.

`null` is not a valid static view value in v0.

Static view values are checked against their declared types as follows:

```text
Bool:
  the YAML value must be a boolean scalar.

Int:
  the YAML value must be an integer scalar.

Float:
  the YAML value must be a finite numeric scalar.
  YAML integer, floating-point, and exponent numeric forms are accepted when
  the YAML processor represents them as numeric values.
  NaN and infinity values are not valid portable v0 static values.

String:
  the YAML value must be a string scalar.
  UTF-8 string contents are allowed.

Array<T>:
  the YAML value must be a sequence.
  Each element must recursively conform to T.
```

A static value must not rely on YAML custom tags or implementation-specific scalar types for portable v0 conformance.

If a view field declaration omits `value`, the field is an ordinary required runtime or instance-level view projection. Static and non-static view fields use the same contract reference syntax.

Examples:

```yaml
contracts:
  requires:
    - expr: "inputs.plate.view.well_count == 96"
```

When `inputs.plate` has type `Plate96`, `inputs.plate.view.well_count` is statically known to be `96` because the `Plate96` type declares that static view value.

A malformed static view value, or a static view value that does not conform to the declared field type, is a validation error.

A contract expression may reference only fields present in the resolved type's view schema, or specification-defined primitive and Array views. An unknown view field reference is a validation error. If a referenced view field has a static value, an implementation may use that value during graph-time contract checking and constant folding.

Object-bearing values may have `.view` metadata, but that metadata is associated with the Object token and is Pure Data only. `.view` must not expose Object identity or Object-bearing values. A static view value is type-level metadata and does not create, consume, duplicate, map, transform, or otherwise affect Object identity.

Static view values are graph-time type-level constants. They do not create workflow values, do not create Object identities, do not consume or map Objects, and do not affect Object tracking except by providing Pure Data metadata for validation and contract checking.

v0 view schemas do not support Optional, Result, union, nullable, dependent, computed, effectful, nested nominal record, or runtime-shape-dependent fields. Static view values are explicit constants, not computed fields. View fields are always present if the value exists.

Contract expressions may refer to Array length through the standard Array view field:

```yaml
contracts:
  requires:
    - expr: "inputs.samples.view.length > 0"
```

v0 contract expressions do not include a built-in `len` function. Array length is accessed through the standard Array view field instead.

### 7.5 When to split Object types

Use a separate Object type when at least one of the following is true:

1. The accepted process set is substantially different.
2. The Objects are not safely interchangeable as workflow state values.
3. The Objects imply different address spaces, data shapes, or operational geometry.

For example, `Plate96` and `Plate384` should be separate Object types because they have different well layouts and are not generally state-compatible.

Do not introduce `Plate<96WellPlate>` or other parameterized Object types in v0. Use nominal types plus traits.

---

## 8. Generic Constraints

Generic type parameters are written using `type_params`. Each type parameter declaration must specify a domain:

```yaml
type_params:
  T:
    domain: data
  O:
    domain: object
```

The valid type parameter domains are:

```text
data
object
```

A type parameter is instantiated with an atomic type whose domain matches the declared type parameter domain.

A `domain: data` type parameter may be instantiated with a built-in primitive Data type or a user-defined nominal Data type.

A `domain: object` type parameter may be instantiated with a user-defined atomic Object type.

A type parameter is not instantiated with an Array type. Object-bearing collection types are expressed by writing `Array<O>`, where `O` is an Object-domain type parameter. Data collection types are expressed by writing `Array<T>`, where `T` is a Data-domain type parameter.

Type-level constraints are written using `where`. In v0, constraints are trait membership constraints. Document-defined traits are nominal membership constraints. The built-in `Numeric` trait is a closed primitive membership constraint satisfied only by `Int` and `Float`.

```yaml
traits:
  Washable: {}

processes:
  wash_any:
    kind: atomic
    type_params:
      O:
        domain: object
    where:
      - Washable<O>
    inputs:
      item:
        type: O
        phase: data
    outputs:
      item:
        type: O
        phase: data
```

A constraint `TraitName<T>` is satisfied when the concrete type substituted for `T` implements the declared trait `TraitName`.

A constraint `Numeric<T>` is satisfied only when `T` is instantiated as `Int` or `Float`. This is not a union type and does not allow a single port or value to have type `Int | Float`. It restricts generic instantiation to one concrete primitive numeric type. Because `Numeric` is satisfied only by primitive Data types, it may be applied only to a `domain: data` type parameter.

Traits do not imply operators, fields, conversions, subtyping, inheritance, Object behavior, or view structure. The built-in `Numeric` trait does not make user-defined numeric types possible in v0.

Unknown trait names, malformed constraints, constraints applied to non-type-parameter references, attempts to redeclare `Numeric`, and attempts by user-defined types to implement `Numeric` are validation errors.

v0 has no implicit conversions in workflow dataflow, process input port binding, process output port typing, or generic instantiation. Limited numeric promotion exists only inside contract expressions.

### 8.1 Generic process instantiation

v0 does not define explicit type argument syntax at node invocation sites. A generic process invocation is instantiated by inferring type arguments from the values bound to the target process input ports.

Every type parameter declared by a process must appear in at least one input port type of that process. A type parameter that appears only in output port types, only in `where`, or not at all is a validation error.

Type inference uses structural matching between target process input port types and the resolved types of the bound source values:

```text
A built-in primitive type matches only the same built-in primitive type.
A user-defined nominal type matches only the same nominal type.
Array<X> matches Array<Y> by recursively matching X and Y.
An unbound type parameter matches a concrete atomic type whose domain matches the declared type parameter domain.
An already-bound type parameter matches only the same concrete atomic type.
All other matches fail.
```

No subtyping, implicit conversion, trait-based widening, union matching, common-supertype inference, or Array-to-type-parameter binding is performed.

If a type parameter cannot be inferred, or if matching constraints infer incompatible concrete types for the same type parameter, the invocation is a validation error.

Type argument inference is performed during graph validation. It does not depend on runtime values. Source value types are resolved from process signatures, node binding rules, structured node output rules, and previously validated graph structure.

After type arguments are inferred, all `where` constraints are checked during graph validation. A document-defined trait constraint `Trait<T>` is satisfied only if the inferred concrete type for `T` implements `Trait`. The built-in constraint `Numeric<T>` is satisfied only when `T` is `Int` or `Float`.

The `where` section, if present, must be a sequence of string scalar constraints. Each constraint must have the form:

```text
Constraint ::= TraitName "<" S? TypeParamName S? ">"
S          ::= one or more ASCII space or tab characters
```

Whitespace is allowed only immediately inside the angle brackets. Therefore `PlateLike<P>` and `PlateLike< P >` are valid and have the same meaning. `PlateLike <P>` is a validation error.

The trait name must be either a declared document-defined trait or the built-in trait `Numeric`. The type parameter name must name a type parameter declared by the same process. Constraints over concrete types, multiple type arguments, unknown traits, unknown type parameters, and malformed constraints are validation errors.

Generic atomic process Object tracking is validated after instantiation. The process definition is first checked for syntactic correctness of paths, roles, and declarations. After concrete types are inferred, Object-bearing slots are computed and `objects` completeness, `elidable_iso` inference, transform role typing, and Object linearity are checked on the instantiated process signature.

A type parameter has no phase. Phase is a property of process ports, not of types or type parameters.

---

## 9. Contracts

Contracts express value-level hard conditions over type-defined `.view` projections.

```yaml
contracts:
  requires:
    - expr: "inputs.x.view >= 0"
  ensures:
    - expr: "outputs.y.view >= 0"
```

### 9.1 Reference scope

Reference scope:

```text
requires: inputs.*.view
ensures:  inputs.*.view and outputs.*.view
```

`requires` expressions may reference only `inputs.*.view`.

`ensures` expressions may reference both `inputs.*.view` and `outputs.*.view`.

The structure and meaning of `.view` are defined by the referenced value's type. A contract expression may reference only fields present in the resolved type's view schema, or specification-defined primitive and Array views.

### 9.2 Contract expression language

Contracts use a small, side-effect-free expression language. In v0 portable YAML, every contract `expr` value must be a YAML string scalar containing a v0 contract expression.

A contract expression may contain only:

```text
Boolean literals: true, false
integer literals
floating-point literals
double-quoted string literals
contract view references
the operators: and, or, not, ==, !=, <, <=, >, >=, +, -, *, /
parentheses
ASCII whitespace
```

Boolean literals are lowercase `true` and `false`.

Integer literals use decimal notation:

```text
IntLiteral ::= 0 | [1-9][0-9]*
```

Floating-point literals use decimal notation with digits on both sides of the decimal point, optionally followed by an exponent:

```text
FloatLiteral ::= [0-9]+ "." [0-9]+ Exponent?
Exponent     ::= ("e" | "E") ("+" | "-")? [0-9]+
```

Negative numbers are parsed as unary `-` applied to a positive numeric literal. Therefore `-1.0e+023` is parsed as unary `-` applied to the floating-point literal `1.0e+023`.

Leading-zero integer literals, floating-point literals without digits on both sides of the decimal point, `NaN`, and infinity literals are not valid v0 contract literals. For example, `1e3`, `1.`, `.5`, `NaN`, and `Infinity` are invalid, while `1.0e3`, `1.0e+23`, and `1.0E-23` are valid floating-point literals.

String literals use double quotes and JSON-style escapes. UTF-8 text is allowed in string literal contents.

Contract references must explicitly include `.view`.

In `requires`, references may use:

```text
inputs.<port>.view
inputs.<port>.view.<field>
```

In `ensures`, references may use:

```text
inputs.<port>.view
inputs.<port>.view.<field>
outputs.<port>.view
outputs.<port>.view.<field>
```

v0 contract expressions do not support direct port references, omitted `.view`, function calls, method calls, indexing, slicing, quantifiers, assignment, mutation, I/O, host-language expressions, implicit conversions, or Object creation/consumption. Array length is accessed through `Array<T>.view.length`.

Operator precedence, from highest to lowest, is:

```text
parentheses
unary not and unary -
* and /
+ and -
==, !=, <, <=, >, >=
and
or
```

Arithmetic and Boolean binary operators are left-associative. Unary operators are right-associative. Comparison operators are non-associative, so expressions such as `1 < x < 10` are validation errors. Write such expressions explicitly with Boolean operators, such as `1 < x and x < 10`.

v0 performs no implicit type conversions in workflow dataflow, process input port binding, process output port typing, or generic instantiation. Contract expressions have a limited numeric promotion rule for primitive numeric operands only. This rule is local to contract expressions and does not imply implicit conversion anywhere else in v0.

The following operator category checks are defined by v0:

| Operator | Allowed operands | Result type | Notes |
|---|---|---|---|
| `and`, `or` | `Bool`, `Bool` | `Bool` | Boolean operands only. |
| `not` | `Bool` | `Bool` | Boolean operand only. |
| `==`, `!=` | Same primitive type, or any numeric pair | `Bool` | Primitive types are `Bool`, `Int`, `Float`, and `String`; numeric pairs are any combination of `Int` and `Float`. |
| `<`, `<=`, `>`, `>=` | Any numeric pair | `Bool` | String ordering is not supported in v0. |
| binary `+`, `-`, `*` | Any numeric pair | `Int` if both operands are `Int`; otherwise `Float` | Numeric pairs are any combination of `Int` and `Float`. |
| binary `/` | Any numeric pair | `Float` | `Int / Int` produces `Float` in contract expressions. |
| unary `-` | `Int` or `Float` | Same type as operand | |

String values support only `==` and `!=`. String ordering and string concatenation are not supported in v0 contract expressions. Mixed numeric operations and comparisons are allowed only by the local numeric promotion rule above.

A contract expression must type-check to `Bool`.

All subexpressions are parsed and type-checked. Validation must not depend on Boolean short-circuiting to ignore malformed, invalid, or statically erroneous subexpressions.

A statically determinable basic evaluation error, such as division by zero in a constant subexpression, is a validation error. Runtime contract evaluation errors that depend on runtime view values are runtime verification concerns unless they can be determined statically.

If a contract expression can be fully evaluated from static view values and literals at graph validation time, and it evaluates to `false`, the document is invalid. If the expression depends on runtime or instance-level view values, false evaluation is a runtime contract violation rather than an IR validation error.

Numeric precision, overflow, division behavior beyond statically determinable errors, and Boolean short-circuit behavior during runtime evaluation are implementation concerns unless otherwise specified by a future feature.

### 9.3 Validation and runtime verification

A malformed contract expression, an invalid reference, an unknown view field, or a statically known basic type error is a validation error.

If a contract expression evaluates to false during execution, the invocation violates the contract. Runtime contract violations and failures to obtain required view projections are runtime verification concerns, not IR validation errors unless they can be determined statically.

Contracts are hard conditions for implementations that support them. A contract violation is not a validation error in the IR; it is a runtime or verification concern.

---

## 10. Processes

Every executable unit is a `process`.

```yaml
processes:
  some_process:
    kind: atomic
    inputs: {}
    outputs: {}
```

The input and output endpoints of a process are collectively called **ports**.
Process input ports are declared under `inputs`, and process output ports are declared under `outputs`.
The YAML keys are `inputs` and `outputs`; `ports` is the generic term used when rules apply to both input and output endpoints.

Process kinds:

```text
atomic
composite
```

### 10.1 Atomic processes

Atomic processes declare their Object behavior using `objects`.

### 10.2 Composite processes

Composite processes define a body graph and returns.

```yaml
processes:
  main:
    kind: composite
    inputs: {}
    outputs: {}
    body:
      nodes:
        - id: step
          process: some_process
      returns:
        result:
          from: step.result
```

Composite processes do not have an `objects` section in v0. Their Object behavior is derived from the body graph and `returns`.

The process dependency graph must be acyclic. Recursive composite dependencies are not allowed in v0.

### 10.3 Entry process

`entry` names the entry process. If `entry` is omitted and a process named `main` exists, `main` may be used as the entry. If `entry` is omitted and no process named `main` exists, the document has no entry process and this is a validation error.

---

## 11. Node Invocation Bindings

Node bindings connect values to process input ports. Node-side inputs are classified by binding section, and the valid binding sections depend on the node kind.

```text
state: Object-bearing linear input for ordinary non-structured node invocations.
       `state` is not used for loop-carried structured values in v0.

carry: Loop-carried value for `fold` and `do_while`.
       A carry value may be Pure Data or Object-bearing.
       For Object-bearing carry, linearity and Object tracking rules apply.
       For Pure Data carry, ordinary Data rules apply, but the value is threaded
       through structured control by same-name, same-type, same-phase outputs.

args: Branch arm arguments.
       These values are made available only to the selected branch arm at runtime.
       An Object-bearing branch argument is not duplicated across arms.
       Branch arguments may be Pure Data or Object-bearing.

each:  Array input.
       Traversed element-wise by structured nodes such as `map` and `fold`.

bind:  Pure Data input.
       Ordinary data or literal; unrestricted and not threaded.
```

Ordinary non-structured node invocations use `state` to bind Object-bearing linear input ports and `bind` to bind Pure Data input ports.

Example ordinary node invocation:

```yaml
- id: move_once
  process: robot_move_to
  state:
    robot:
      from: inputs.robot
  bind:
    pose:
      from: inputs.pose
```

`bind` is Pure Data only. Object-bearing values must not be passed through `bind`.

Literal values are written under `value`:

```yaml
bind:
  intensity:
    value: 3
```

In `fold` and `do_while`, `carry` means a loop-carried value. The output of one iteration becomes the carry input of the next iteration. `carry` does not imply physical Object identity preservation; physical identity behavior is determined independently by Object tracking declarations.

In `branch`, `args` are supplied only to the selected arm. They are not fan-out, even when Object-bearing. Branch output ports are selected-arm output ports exposed as common outputs.

---

## 12. Port Degree and Linearity Rules

### 12.1 Data ports

Pure Data input port:

```text
indegree = 1
```

Pure Data output port:

```text
outdegree >= 0
fan-out allowed
unused output allowed
```

### 12.2 Object-bearing ports

Object-bearing input port:

```text
indegree = 1
```

Object-bearing output port:

```text
outdegree = 1
fan-out forbidden
implicit discard forbidden
```

An Object-bearing output port must be connected either to:

1. A downstream node input in the same body, or
2. `body.returns` as a composite output, or
3. A structured node output explicitly exposed by that structured node.

If an Object-bearing output port is not connected, it is a validation error.

### 12.3 Composite returns

`body.returns` is a connection from an internal graph output port to the composite boundary.

```yaml
body:
  nodes:
    - id: step
      process: cup_wash
      state:
        cup:
          from: inputs.cup
  returns:
    cup:
      from: step.cup
```

Here `step.cup` is connected to the composite output port `cup`; it is not unused.

---

## 13. Object Tracking Completeness

Object tracking completeness is the central well-formedness property for Object behavior.

```text
Object tracking completeness:
  Every Object-bearing input port slot and output port slot of a process must have a complete,
  explicit, derivable explanation in terms of map, consume, create, transform,
  body graph flow, or returns.
```

It forbids:

```text
implicit Object creation
implicit Object disappearance
implicit Object duplication
implicit Object discard
unknown Object output provenance
unknown Object input fate
```

All atomic and composite processes must satisfy Object tracking completeness.

For atomic processes, this is checked from the `objects` section or from the special elidable inference rule. For composite processes, this is derived from the body graph and `returns`.

Object tracking completeness describes successful invocation behavior. Runtime failures and exceptions are out of scope for v0.

### 13.1 Object slot fate and provenance

For every atomic process after generic instantiation:

1. Compute `object_slots` for every input port and output port.
2. Every input port Object slot must have exactly one declared fate.
3. Every output port Object slot must have exactly one declared provenance.
4. No Object slot may be duplicated across outputs.
5. No Object slot may disappear unless explicitly consumed.
6. No Object slot may be implicitly created.

Valid input fates include:

```text
map source
transform input
consume
```

Valid output provenances include:

```text
map target
transform output
create
```

No input port Object slot may have multiple incompatible fates, and no output port Object slot may have multiple incompatible provenances.

Examples of invalid Object behavior:

```yaml
objects:
  map:
    outputs.sample: inputs.sample
  consume:
    - inputs.sample
```

This is invalid because `inputs.sample` has two incompatible fates: mapped and consumed.

### 13.2 Object replacement

A process may consume an Object-bearing input and create a same-name, same-type Object-bearing output.

```yaml
objects:
  consume:
    - inputs.sample
  create:
    - outputs.sample
```

This is Object replacement. The created Object is a new physical Object identity. v0 does not treat the created Object as a continuation of the consumed Object for identity, policy, or metadata purposes.

Object replacement by `consume + create` does not imply `.view` metadata inheritance. The created output Object has its own Object identity and its own type-defined view metadata. Any relationship between the consumed input Object's view metadata and the created output Object's view metadata must be defined by the process's semantics or expressed by contracts.

Structured carry compatibility may allow such replacement when the output has the required same name, type, and phase.

Scheduling policies do not automatically transfer across Object replacement.

---

## 14. Atomic `objects` Section

The `objects` section is allowed only on atomic processes.

All paths in `objects` use explicit namespaces in v0:

```text
inputs.<path>
outputs.<path>
```

Short forms such as `cup: cup` are not canonical v0 syntax.

### 14.1 `map`

`map` preserves physical Object identity.

```yaml
objects:
  map:
    outputs.cup: inputs.cup
```

Meaning:

```text
outputs.cup is the same physical Object identity as inputs.cup.
```

`map` may be cross-wired. For example, physical switching can be expressed as:

```yaml
objects:
  map:
    outputs.a: inputs.b
    outputs.b: inputs.a
```

For atomic processes, `objects.map` is a declarative Object behavior claim made by the process definition.

For Object-bearing container values, a `map` from `inputs.xs` to `outputs.ys` is interpreted recursively over Object slots. The IR processor treats the source and target value structures as corresponding identity-preservingly. For `Array<T>`, this declared behavior preserves top-level length, nesting structure, element order, and contained Object identities at corresponding slots. Processes that declare Object-bearing container structure changes, such as changing order, length, grouping, or nesting, must use an explicit `objects.transform` rather than `map`.

This declaration is trusted by the IR processor. v0 validation checks that the declaration is well-formed and complete, but it does not prove that an implementation of the atomic process actually preserves the runtime container structure. If an implementation changes order, drops elements, duplicates elements, or otherwise violates the declared mapping, that is an implementation correctness error or runtime verification concern, not an IR validation error unless it can be determined statically.

### 14.2 `consume`

`consume` terminates an input Object identity in the current workflow.

```yaml
objects:
  consume:
    - inputs.sample
```

### 14.3 `create`

`create` introduces a new Object identity.

```yaml
objects:
  create:
    - outputs.sample
```

### 14.4 `transform`

`transform` describes standard structural transformations of Object slots for Arrays.

The canonical syntax is a list of transform entries:

```yaml
objects:
  transform:
    - kind: <transform-kind>
      inputs:
        <role>: <inputs.* path>
      outputs:
        <role>: <outputs.* path>
```

Valid transform kinds in v0 are:

```text
array_uncons
array_cons
array_reverse
```

All transform paths use explicit namespaces such as `inputs.*` and `outputs.*`.

Transforms preserve physical Object identities while changing container structure. A transform must account for all Object slots in its input and output paths exactly once. No Object slot may be duplicated, lost, implicitly created, or implicitly discarded by a transform.

#### 14.4.1 Transform validation

A transform entry is validated from its declared `kind`, its role names, and the resolved types of its input and output paths.

All paths in `objects.transform` must refer to Object-bearing values. A Pure Data path in `objects.transform` is a validation error.

Each transform kind defines an exact set of required input and output roles. Missing roles, extra roles, invalid namespaces, paths that do not exist, or role type mismatches are validation errors.

For v0 transform kinds, role typing is:

```text
array_uncons:
  inputs.xs:    Array<T>
  outputs.head: T
  outputs.tail: Array<T>

array_cons:
  inputs.head:  T
  inputs.tail:  Array<T>
  outputs.xs:   Array<T>

array_reverse:
  inputs.xs:    Array<T>
  outputs.ys:   Array<T>
```

In each case, the same `T` must be used consistently within that transform entry, and the referenced paths must be Object-bearing after type resolution.

Transform completeness is checked using slot-level correspondence rules defined by each transform kind. These rules use index variables and slice-like notation, such as `i`, `*`, `0`, and `1..`, to describe all runtime elements without enumerating them statically. For example, `inputs.xs[i] -> outputs.ys[n - 1 - i]` means that every contained Object slot at runtime index `i` in `inputs.xs` corresponds to the contained Object slot at runtime index `n - 1 - i` in `outputs.ys`.

These correspondence rules allow validation to check that each input Object slot is accounted for exactly once and that each output Object slot has exactly one provenance, even when Array lengths are not statically known.

Nested Arrays are handled by applying the transform kind's standard meaning recursively to contained Object slots. For example, `array_uncons` on `Array<Array<Cup>>` maps the Object slots in `xs[0][*]` to `head[*]` and the Object slots in `xs[1..][*]` to `tail[*][*]`.

Runtime Array lengths are not generally statically known in v0. When a transform has a length precondition, such as `array_uncons` requiring a non-empty input Array, violations are classified by the earliest phase at which they are determined. If the violation is known at graph phase, it is a validation error. If it is first known at run phase, it is a run-start validation or preflight error. If it is first known at data phase, it is a runtime data error.

Multiple transform entries may appear in one `objects.transform` list, but each entry declares a direct Object slot relation from `inputs.*` paths to `outputs.*` paths for the atomic process. v0 does not define transform chaining, intermediate transform values, or references from one transform entry to another.

An Object slot must not be accounted for by more than one Object behavior declaration. If a slot appears in `map`, `consume`, `create`, or `transform` in a way that gives it multiple fates or multiple provenances, it is a validation error.

#### 14.4.2 `array_uncons`

```text
Array<T> -> T + Array<T>
```

Canonical syntax:

```yaml
objects:
  transform:
    - kind: array_uncons
      inputs:
        xs: inputs.xs
      outputs:
        head: outputs.head
        tail: outputs.tail
```

Meaning for `T = Cup`:

```text
inputs.xs[0]   -> outputs.head
inputs.xs[1..] -> outputs.tail[*]
```

Meaning for `T = Array<Cup>`:

```text
inputs.xs[0][*]   -> outputs.head[*]
inputs.xs[1..][*] -> outputs.tail[*][*]
```

`array_uncons` requires the input Array to be non-empty. Empty input handling follows the phase-dependent error classification rule.

#### 14.4.3 `array_cons`

```text
T + Array<T> -> Array<T>
```

Canonical syntax:

```yaml
objects:
  transform:
    - kind: array_cons
      inputs:
        head: inputs.head
        tail: inputs.tail
      outputs:
        xs: outputs.xs
```

Meaning for `T = Cup`:

```text
inputs.head    -> outputs.xs[0]
inputs.tail[*] -> outputs.xs[1..]
```

#### 14.4.4 `array_reverse`

```text
Array<T> -> Array<T>
```

Canonical syntax:

```yaml
objects:
  transform:
    - kind: array_reverse
      inputs:
        xs: inputs.xs
      outputs:
        ys: outputs.ys
```

Meaning:

```text
inputs.xs[i] -> outputs.ys[n - 1 - i]
```

`array_reverse` preserves contained physical Object identities but changes Array order. Therefore it is not `elidable_iso`.

---

## 15. Elidable Iso

`elidable_iso` is a strong, convenient process-level trait and inference permission. It is not a type trait, and it is not the general condition for structured control.

```text
elidable_iso:
  A same-name, same-type, same value-structure, physical identity-preserving
  mapping from Object-bearing inputs to Object-bearing outputs.
```

For an Object-bearing input and output with the same name and the same type, `elidable_iso` means that the output is the same logical value structure as the input and that all contained Object identities are preserved at corresponding Object slots.

For Object-bearing Arrays, this implies that Array length, nesting structure, element order, and contained Object identities are preserved. A process that changes Array length, order, grouping, or nesting is not `elidable_iso`.

Implementations may approximate this check by verifying that corresponding `object_slots` are mapped identity-preservingly and that no Object-bearing structural transform is declared.

Example:

```yaml
processes:
  cup_inspect:
    kind: atomic
    traits:
      - elidable_iso
```

For atomic processes, if `objects` is completely omitted and the process has the process-level trait `elidable_iso`, v0 may infer same-name mappings for top-level Object-bearing inputs and outputs.

Canonical inferred form:

```yaml
objects:
  map:
    outputs.cup: inputs.cup
```

If an `objects` section is present, no implicit completion is performed. The written `objects` section must account for all Object-bearing input and output slots.

---

## 16. Structured Carry Compatibility

Structured carry compatibility is required for `fold` and `do_while` carry outputs.

```text
For each carry binding name `c`, the target process must provide an output
named `c` with the same type and phase.
```

A carry value may be Pure Data or Object-bearing.

Structured carry compatibility does not imply physical Object identity preservation. It guarantees that a same-name, same-type, same-phase value is threaded across loop iterations.

For Object-bearing carry, the Object behavior of that carry value is determined independently by ordinary Object tracking completeness. A carry transition may preserve physical identity through `map` or `transform`, or may replace the Object-bearing value by consuming the input carry value and creating a same-name, same-type output carry value.

For Pure Data carry, the target process's same-name output becomes the next iteration's carry value. Pure Data carry is not subject to Object tracking or linearity restrictions.

`branch` does not use `carry` in v0. It uses `args` and explicit or implicit `common` outputs instead.

---

## 17. Feature: `node_map`

`node_map` enables structured nodes with `kind: map`.

`kind: map` performs independent element-wise invocations.

```yaml
- id: create_cups
  kind: map
  process: cup_create_from_label
  each:
    label:
      from: inputs.labels
```

Output shape:

```text
process output p: T
map output p: Array<T>
```

If the target process output is already an Array, nested Arrays are produced:

```text
process output groups: Array<Cup>
map output groups: Array<Array<Cup>>
```

`map` requires only Object tracking completeness of the target process. It does not require structured carry compatibility.

In v0, `map` does not define an `outputs` section. A `map` node exposes all declared outputs of the target process, with each output collected into an Array according to the output shape rules below.

Multiple `each` inputs use zip-equal semantics:

```text
zip: equal
```

All `each` inputs of the same `map` must have equal top-level Array length. If a length mismatch is determined at `graph` phase, it is a graph-time validation error. If it is first determined at `run` phase, it is a run-start validation error or preflight error. If it is first determined at `data` phase, it is a runtime data error.

If the `each` length is zero, the map performs zero invocations and produces empty collected outputs.

---

## 18. Feature: `node_fold`

`node_fold` enables structured nodes with `kind: fold`.

`kind: fold` threads loop-carried values through element-wise invocations while traversing one or more Arrays.

A fold carry value may be Pure Data or Object-bearing.

Example:

```yaml
- id: process
  kind: fold
  process: sample_process_once
  carry:
    sample:
      from: inputs.sample
    score:
      from: inputs.initial_score
  each:
    reagent:
      from: inputs.reagents
  outputs:
    sample:
      mode: carry
    score:
      mode: carry
    measurement:
      mode: collect
```

Requirements:

1. Target process is Object tracking complete.
2. Each `carry` binding has a same-name, same-type, same-phase output on the target process.
3. Carry outputs may be Pure Data or Object-bearing and must be exposed with `mode: carry` when `outputs` is present.
4. Non-carry Pure Data outputs may be exposed only using explicit output modes.
5. Non-carry Object-bearing outputs are allowed only with explicit `mode: collect`.
6. `bind` inputs are Pure Data only.
7. Multiple `each` inputs use zip-equal semantics, with phase-dependent error classification for length mismatches.

Object-bearing `each` values are not carry values. They follow ordinary Object tracking rules for the target process.

An Object-bearing collection used as an `each` source is linearly used by the `fold` node. The collection value itself is not treated as having a separate Object identity; its contained Object slots are passed to per-element invocations in traversal order and must be fully accounted for by the target process behavior and the fold output modes.

`bind` inputs are re-used for each invocation and are not updated between iterations. `carry` values are updated by the corresponding same-name carry output after each invocation.

### 18.1 Fold output modes

For a `fold` node, valid output modes are:

```text
carry
collect
last
drop
```

Rules:

1. `mode: carry` may be used only for outputs corresponding to fold carry bindings. The carry value may be Pure Data or Object-bearing.
2. When `outputs` is present, every carry binding must be listed with `mode: carry`.
3. `mode: collect` may be used for non-carry outputs of the target process. The collected output may be Pure Data or Object-bearing.
4. For target process output `p: T`, a fold output `p` with `mode: collect` has type `Array<T>` and contains per-invocation output values in invocation order.
5. `mode: last` may be used only for non-carry Pure Data outputs of the target process.
6. `mode: drop` may be used only for non-carry Pure Data outputs.
7. Object-bearing outputs must not use `mode: last` or `mode: drop`.
8. Every Object-bearing output of the target process must be exposed either as `mode: carry` or as `mode: collect`.
9. In v0, all collected per-invocation outputs of the same `fold` have the same Array length.
10. If `outputs` is present, it is fully explicit: every target process output must be listed with `mode: carry`, `collect`, `last`, or `drop`, subject to the Object-bearing restrictions above.

### 18.2 Empty fold traversal

Empty `each` traversal is not an error by itself.

For `fold`, if all `each` inputs have length zero, the invocation performs zero element-wise calls. Outputs with `mode: collect` produce empty Arrays, including Object-bearing collect outputs whose result contains zero Object slots. Carry outputs with `mode: carry` expose the initial carry value unchanged.

However, an output with `mode: last` requires at least one element-wise invocation.

If it is known at `graph` phase that the traversal is empty, a `fold` node exposing any `mode: last` output is a graph-time validation error.

If emptiness is not known at `graph` phase but is known at `run` phase, the same condition is a run-start validation error or preflight error for that run.

If emptiness is known only at `data` phase, the condition is a runtime data error. Runtime data errors are outside the core validation semantics of v0, but the standard v0 execution behavior is that the invocation cannot produce a value for the affected `mode: last` output.

Therefore, `mode: last` on an empty fold traversal is invalid at the earliest phase where emptiness is determined.

### 18.3 Default fold outputs

If `outputs` is omitted:

```text
carry outputs are exposed as carry
non-carry Pure Data outputs are dropped
if the target process has any non-carry Object-bearing output, the fold node must declare an explicit `outputs` section
```

Thus, `fold` does not collect outputs by default. Use explicit `mode: collect` or `mode: last` when non-carry outputs are needed. If the target process has any non-carry Object-bearing output, the fold node must have an explicit `outputs` section listing each such output with `mode: collect`. If `outputs` is present, the default behavior is disabled and the `outputs` section is fully explicit.

---

## 19. Feature: `node_do_while`

`node_do_while` enables structured nodes with `kind: do_while`.

`kind: do_while` invokes a process at least once and repeats while a Boolean condition output is true.

A do-while carry value may be Pure Data or Object-bearing.

Example:

```yaml
- id: loop
  kind: do_while
  process: sample_passage_once
  carry:
    sample:
      from: inputs.sample
    score:
      from: inputs.initial_score
  bind:
    medium:
      from: inputs.medium
  condition:
    output: continue
  max_iterations:
    value: 100
  outputs:
    sample:
      mode: carry
    score:
      mode: carry
```

Requirements:

1. Target process is Object tracking complete.
2. Each `carry` binding has a same-name, same-type, same-phase output on the target process.
3. Object-bearing outputs of the target process must be carry outputs. Non-carry Object-bearing outputs are forbidden in `do_while`.
4. `condition.output` names a Boolean Data output of the target process.
5. `max_iterations` is required and must be a graph/run phase integer.
6. Non-carry Data outputs may be exposed only using explicit output modes.

`condition.output` is an output name, not a body-scope dataflow reference. The condition output is evaluated after each invocation of the target process. The `do_while` node repeats while this output value is `true` and exits when it is `false`, subject to `max_iterations`.

If `max_iterations` is reached while the condition remains true, the `do_while` node terminates by reaching its iteration limit. This is **bounded termination**. Bounded termination is not an IR validation error, and v0 does not define it as a runtime failure.

Under bounded termination, the standard v0 execution behavior is that the `do_while` node still produces outputs from the invocations that actually ran. Carry outputs expose the final executed invocation's carry outputs. Collected outputs contain all executed invocation outputs in invocation order. Last outputs expose the final executed invocation output. If the condition output is explicitly exposed, the final collected or last condition value is `true`.

An implementation may report bounded termination as a diagnostic, warning, runtime status, policy concern, or runtime concern.

### 19.1 Do-while output modes

For a `do_while` node, valid output modes are:

```text
carry
collect
last
drop
```

Rules:

1. `mode: carry` may be used only for outputs corresponding to do-while carry bindings. The carry value may be Pure Data or Object-bearing.
2. When `outputs` is present, every carry binding must be listed with `mode: carry`.
3. `mode: collect` may be used only for non-carry Data outputs of the target process.
4. `mode: last` may be used only for non-carry Data outputs of the target process.
5. `mode: drop` may be used only for non-carry Data outputs.
6. The condition output is an ordinary Boolean Data output of the target process and may be exposed using `collect`, `last`, or `drop`.
7. Non-carry Object-bearing outputs remain forbidden in `do_while`.
8. Collected Data outputs are ordered by invocation order.
9. The condition output includes the final `false` value when the loop exits normally because the condition became false, if the condition output is collected.
10. If `max_iterations` is reached while the condition remains true, the node terminates by reaching its iteration limit. This is bounded termination.
11. Under bounded termination, exposed outputs are produced from the invocations that actually ran.
12. Under bounded termination, `mode: carry` exposes the final executed invocation's carry output.
13. Under bounded termination, `mode: collect` contains all executed invocation outputs in invocation order.
14. Under bounded termination, `mode: last` exposes the final executed invocation output.
15. If the condition output is collected under bounded termination, the collected values include the final `true` value.
16. If the condition output is exposed with `mode: last` under bounded termination, that value is `true`.
17. In v0, all collected per-invocation Data outputs of the same `do_while` have the same Array length.
18. If the target process has any non-carry Object-bearing output, the `do_while` node is invalid. Otherwise, when `outputs` is present, it is fully explicit: every target process output must be listed with `mode: carry`, `collect`, `last`, or `drop`.

### 19.2 Default do-while outputs

If `outputs` is omitted:

```text
carry outputs are exposed as carry
non-carry Data outputs are dropped, including the condition output
non-carry Object-bearing outputs are forbidden
```

Thus, `do_while` does not collect Data outputs by default. Use explicit `mode: collect` or `mode: last` when those outputs are needed. If `outputs` is present, the default behavior is disabled and the `outputs` section is fully explicit.

---

## 20. Feature: `node_branch`

`node_branch` enables structured nodes with `kind: branch`.

`kind: branch` selects one of two arms based on a Boolean Data condition.

A branch has `args`, not `state`. Branch arguments may be Pure Data or Object-bearing. They are supplied only to the selected arm at runtime. An Object-bearing branch argument is not duplicated across arms.

Example:

```yaml
- id: handle
  kind: branch
  condition:
    from: check.is_dirty
  args:
    cup:
      from: check.cup
  then:
    process: cup_wash
  else:
    process: cup_polish
```

Requirements:

1. Each arm process is Object tracking complete.
2. Each branch argument must correspond to an input port of the same name, type, and phase in each explicit arm process.
3. An Object-bearing branch argument is made available only to the selected arm and is not fan-out.
4. Branch outputs are common outputs selected from the executed arm.
5. One-sided Object-bearing outputs are forbidden in v0.
6. A common Object-bearing branch output must be identity-equivalent across arms.
7. v0 does not provide Optional, Result, union-like branch outputs, conditional Object provenance, or policy transfer semantics.

If `else` is omitted, it acts as an implicit identity arm for branch arguments for the purpose of Object-bearing common outputs. The implicit else arm returns each Object-bearing branch argument as a same-name Object-bearing output with the same type, phase, and physical identity. It does not implicitly expose Data outputs. Therefore, Data outputs from the `then` arm cannot be exposed as common outputs unless an explicit `else` arm is provided and the corresponding outputs are valid as common Data outputs.

### 20.1 Branch output modes

For a `branch` node, valid output modes are:

```text
common
drop
```

Rules when `outputs` is present:

1. `outputs` is authoritative. Only listed outputs are exposed.
2. `mode: common` may be used for outputs present in both arms with the same name, type, and phase. The output may be Pure Data or Object-bearing.
3. If an output listed as `common` is missing from either arm, it is a validation error.
4. If an output listed as `common` has different type or phase across arms, it is a validation error.
5. If an Object-bearing output listed as `common` is not identity-equivalent across arms, it is a validation error.
6. `mode: drop` may be used only for Data outputs.
7. Any Object-bearing output produced by either arm must be listed with `mode: common`.
8. Unlisted Data outputs are dropped.
9. One-sided Object-bearing outputs are forbidden in v0.
10. One-sided Data outputs may be dropped, but cannot be exposed as `common`.

Example with explicit outputs:

```yaml
- id: choose_process
  kind: branch
  condition:
    from: inputs.use_a
  args:
    sample:
      from: inputs.sample
  then:
    process: sample_process_a
  else:
    process: sample_process_b
  outputs:
    sample:
      mode: common
```

For this branch to be valid, both `sample_process_a` and `sample_process_b` must expose `outputs.sample` as the same Object identity as the branch argument `args.sample`.

### 20.2 Branch Object identity equivalence

A common Object-bearing branch output must have the same Object identity behavior in every arm. v0 intentionally forbids branch outputs whose Object identity may depend on which arm was selected.

For branch validation, the Object behavior of each arm is compared at Object slot level. An Object-bearing output slot is identity-equivalent across arms only when each arm derives that output slot from the same branch argument Object slot through identity-preserving `map` declarations or the same standard identity-preserving structural transform relation.

The following are validation errors for a common Object-bearing branch output in v0:

```text
then arm maps the output slot from one branch argument slot, while else arm maps it from another branch argument slot
then arm maps the output slot from a branch argument slot, while else arm creates or replaces the output Object
both arms create same-name, same-type output Objects independently
arms use different Object-bearing structural transforms for the same common output slot
```

This restriction is primarily intended to keep Object policy tracking stable. A scheduling policy targeting a branch Object output must not apply to different physical Object identities depending on the selected arm.

Future versions may introduce an explicit feature for policy transfer semantics across Object replacement or conditional Object provenance. That feature is outside v0. v0 does not infer or perform policy transfer across `consume` / `create`, and branch does not provide conditional policy target semantics.

### 20.3 Default branch outputs

If `outputs` is omitted, default branch output derivation applies only to Object-bearing outputs.

All Object-bearing outputs of the `then` and `else` arm processes are treated as implicit `common` output candidates.

For each Object-bearing output produced by either arm, both arms must produce an output with the same name, type, and phase, and the corresponding Object slots must be identity-equivalent across arms. If these conditions are satisfied, the branch node exposes that output as a common Object-bearing output. If an Object-bearing output is present in only one arm, if the corresponding outputs have different types or phases, or if the corresponding Object slots are not identity-equivalent across arms, it is a validation error.

Data outputs are not exposed by default. If `outputs` is omitted, Data outputs produced by branch arms are dropped.

If `outputs` is present, the default rules in this section do not apply.

---

## 21. Structured Node Output Summary

Structured nodes may declare an `outputs` section to control which outputs are exposed by the structured node and how those outputs are shaped.

If `outputs` is omitted, the node uses the default output behavior defined for its structured node kind.

If `outputs` is present, only outputs explicitly listed in `outputs` are exposed by the structured node.

Common modes:

```text
carry   exposes the final loop-carried value for `fold` or `do_while`
collect  collects per-invocation outputs into an Array; Object-bearing collect is allowed only where explicitly defined
last     exposes only the final Data output value
common   exposes a branch output common to both arms
drop     explicitly suppresses a Data output
```

The valid modes depend on the structured node kind.

Summary:

```text
map:
  process output p: T -> map output p: Array<T>

fold:
  carry output: final carry value, same name as carry binding
  Pure Data or Object-bearing output with mode collect: Array of per-iteration values
  Data output with mode last: final per-iteration value
  Data output with mode drop: not exposed
  default: expose carry outputs only; drop non-carry Pure Data outputs; require explicit outputs for non-carry Object-bearing outputs
  Object-bearing outputs: carry or explicit collect only; never last or drop

do_while:
  carry output: final carry value, same name as carry binding
  Data output with mode collect: Array of per-iteration values
  Data output with mode last: final per-iteration value
  Data output with mode drop: not exposed
  condition output: ordinary Boolean Data output; may use collect, last, or drop
  default: expose carry outputs only; drop non-carry Data outputs including condition
  Object-bearing outputs: carry outputs only

branch:
  common output: selected arm output, same name/type/phase in both arms
  Data output with mode common: common Data output from both arms
  Object-bearing output with mode common: common Object-bearing output from both arms, identity-equivalent across arms
  Data output with mode drop: not exposed
  default: expose all same-name/same-type/same-phase identity-equivalent Object-bearing outputs as common; drop Data outputs
  one-sided Object-bearing outputs: invalid
  arm-dependent Object identity for common Object-bearing outputs: invalid
```

---

## 22. Feature: `python_script_processes`

`python_script_processes` supports inline script execution for Pure Data processes using Python.

A script process is an `atomic` process with a `script` section.

```yaml
processes:
  math_add:
    kind: atomic
    inputs:
      x:
        type: Float
        phase: data
      y:
        type: Float
        phase: data
    outputs:
      z:
        type: Float
        phase: data
    script:
      language: python
      code: |
        return {
          "z": x + y
        }
```

In portable v0, the only defined script language is:

```text
python
```

A script process that declares any other `script.language` is a validation error for portable v0. Implementations may provide additional script languages as extensions, but documents using those extensions are not portable v0 documents.

No `script.returns` field is defined in v0.

### 22.1 Pure Data restriction

All script process input ports and output ports must be Pure Data.

Object-bearing input ports and Object-bearing output ports are validation errors for script processes.

A script process must not contain an `objects` section.

Script processes do not create, consume, map, or transform Objects.

### 22.2 Execution model

The script `code` is evaluated as the body of an implementation-provided Python function.

Input port names are bound as local variables using the corresponding process input port names.

The function must return a mapping from declared output names to output values.

The returned mapping must contain exactly the declared output names.

Returned values must conform to the declared output types.

In v0, script process outputs must have `phase: data`.

If a script process's declared interface violates v0 script restrictions, it is a validation error. This includes Object-bearing input ports or output ports, an `objects` section on a script process, or an unsupported script language.

If the script executes but returns a mapping that does not exactly match the declared output names, or returns values that do not conform to declared output types, the invocation fails runtime verification. Such failures are not IR validation errors unless they can be determined statically.

### 22.3 Python imports and dependencies

Portable v0 Python scripts should rely only on Python built-ins.

Imports of Python standard library modules are implementation-defined. An implementation may allow selected standard library modules for convenience, but portable v0 does not require any standard library module to be available.

Imports of third-party packages, local project modules, network modules, or implementation-specific modules are not portable v0 and must be treated as implementation extensions.

An implementation may restrict all imports, including standard library imports, for sandboxing, security, determinism, or reproducibility.

If a script imports a module that is not allowed by the implementation, the IR may still be syntactically valid, but the workflow requires an unsupported script dependency for that implementation.

---

## 23. Feature: `scheduling_policies`

`scheduling_policies` enables scheduling policy attachment.

Scheduling policies are best-effort preferences, not hard semantic requirements.

They may be used by an implementation for planning, execution guidance, reporting, visualization, or ignored if unsupported.

### 23.1 Temporal references

Policy and contract references are scoped lexically.

Available references:

```text
self.start
self.end
node_id.start
node_id.end
```

Rules:

- `self.start` / `self.end` refer to the current composite invocation.
- `node_id.start` / `node_id.end` refer to direct child nodes in the same composite body scope.
- Process names are not temporal reference names.
- Outer nodes, nested composite internals, branch arm internals, and per-iteration internals are not directly referenced from outside.
- A structured node's `start/end` refers to the whole structured node invocation.

### 23.2 Intervals

Intervals use structured YAML:

```yaml
during:
  from: collect.end
  to: analyze.start
```

v0 scheduling intervals do not use inline duration-expression syntax. Gap preferences are represented by scheduling preference payloads such as `max_gap` and `min_gap`, using `value` and implementation-defined `unit` strings.

### 23.3 Policy attachment and preference schema

v0 defines a scheduling policy attachment model and a small portable scheduling preference payload schema.

A scheduling policy attaches a v0-defined best-effort preference payload to a temporal interval, and, for Object-targeted policy kinds, to one or more Object identities.

The `scheduling` section is allowed only on composite processes.

The v0 core specification defines:

1. where scheduling policies may appear,
2. how temporal references such as `self.start`, `self.end`, `node_id.start`, and `node_id.end` are resolved,
3. how `during` intervals are interpreted,
4. how `object.from` targets Object identities,
5. how Object lifetime determines the effective interval of an Object policy,
6. how Object-bearing collection policy targets are interpreted, and
7. the portable shape of v0-defined scheduling preference payloads.

A scheduling policy entry is a mapping with the following allowed keys:

```text
during    required
object    conditionally allowed or required by preference kind
prefer    required
priority  optional
```

Unknown keys in a scheduling policy entry are validation errors in strict portable v0. In extension-tolerant mode, extension keys using the reserved `x-` prefix are allowed.

The `during` field defines the temporal interval to which the policy is attached.

The `object` field targets one or more Object identities. It is forbidden for `max_gap` and `min_gap` policies. It is required for `temperature` policies.

The `prefer` field must be a closed mapping containing a required `kind` field and the fields required by that preference kind. For v0-defined preference kinds, unknown keys in `prefer` are validation errors.

The `priority` field, if present, must be a YAML integer scalar. If omitted, priority is `0`. Negative priority values are allowed. Larger values indicate stronger preferences. Priority affects only implementation scheduling choices and has no effect on validation, type checking, Object tracking, or workflow semantics. v0 does not define conflict-resolution semantics for priorities.

Policy misses and conflicting scheduling preferences are not validation errors. Conflict resolution is implementation-defined.

### 23.4 Scheduling preference payloads

v0 defines the following portable scheduling preference kinds:

```text
max_gap
min_gap
temperature
```

The `prefer.kind` field must be a YAML string scalar naming a v0-defined scheduling preference kind. Unknown non-extension preference kinds are validation errors in strict portable v0. In extension-tolerant mode, an implementation-defined preference kind may use a name with the reserved extension-key prefix `x-`.

For all v0-defined scheduling preference kinds, `unit` must be a YAML string scalar containing at least one non-whitespace character. v0 defines only the presence and scalar shape of `unit`. Allowed unit strings, unit conversion rules, dimensional compatibility, normalization, scale interpretation, and conflict handling are implementation-defined.

For all v0-defined scheduling preference kinds, numeric `value` fields must be finite YAML integer or floating-point numeric scalars. `NaN` and infinity values are not valid portable v0 scheduling preference values.

#### 23.4.1 `max_gap`

`max_gap` is a best-effort preference that the operation-to-operation interval identified by `during` should be no longer than the specified non-negative value.

Canonical shape:

```yaml
prefer:
  kind: max_gap
  value: 10
  unit: min
```

Schema:

```text
kind   required, exactly "max_gap"
value  required, finite non-negative Int or Float
unit   required, String containing at least one non-whitespace character
```

A scheduling policy whose `prefer.kind` is `max_gap` must not contain an `object` field. A `max_gap` policy with an `object` field is a validation error.

#### 23.4.2 `min_gap`

`min_gap` is a best-effort preference that the operation-to-operation interval identified by `during` should be at least the specified non-negative value.

Canonical shape:

```yaml
prefer:
  kind: min_gap
  value: 5
  unit: min
```

Schema:

```text
kind   required, exactly "min_gap"
value  required, finite non-negative Int or Float
unit   required, String containing at least one non-whitespace character
```

A scheduling policy whose `prefer.kind` is `min_gap` must not contain an `object` field. A `min_gap` policy with an `object` field is a validation error.

#### 23.4.3 `temperature`

`temperature` is a best-effort environmental preference for targeted Object identities during the effective policy interval.

Canonical shape:

```yaml
object:
  from: inputs.sample
during:
  from: self.start
  to: self.end
prefer:
  kind: temperature
  value: 4
  unit: C
```

Schema:

```text
object  required
kind    required, exactly "temperature"
value   required, finite Int or Float
unit    required, String containing at least one non-whitespace character
```

A scheduling policy whose `prefer.kind` is `temperature` and that does not contain an `object` field is a validation error.

If `temperature` targets an Object-bearing collection, the preference applies individually to each contained Object identity.

For `temperature`, v0 defines only the portable payload shape and Object attachment rule. Allowed unit strings, temperature scale interpretation, tolerance, measurement point, environmental control behavior, resource capability matching, and conflict handling are implementation-defined.

### 23.5 Scheduling policy examples

Operation-to-operation maximum gap preference:

```yaml
scheduling:
  policies:
    - during:
        from: collect.end
        to: analyze.start
      prefer:
        kind: max_gap
        value: 10
        unit: min
```

Operation-to-operation minimum gap preference:

```yaml
scheduling:
  policies:
    - during:
        from: wash.end
        to: incubate.start
      prefer:
        kind: min_gap
        value: 0
        unit: min
```

Object-targeted temperature preference:

```yaml
scheduling:
  policies:
    - object:
        from: inputs.sample
      during:
        from: self.start
        to: self.end
      prefer:
        kind: temperature
        value: -80
        unit: C
```

Invalid Object target on a gap preference:

```yaml
scheduling:
  policies:
    - object:
        from: inputs.sample
      during:
        from: collect.end
        to: analyze.start
      prefer:
        kind: max_gap
        value: 10
        unit: min
```

This is invalid because `max_gap` and `min_gap` forbid `object`.

Invalid temperature preference without an Object target:

```yaml
scheduling:
  policies:
    - during:
        from: freeze.start
        to: freeze.end
      prefer:
        kind: temperature
        value: -80
        unit: C
```

This is invalid because `temperature` requires `object`.

---

## 24. Object Policy Targets

### 24.1 Object policy targets

In `scheduling_policies`, `object.from` may refer to an Atomic Object scalar value or an Object-bearing collection value in the current policy scope.

Example:

```yaml
object:
  from: inputs.sample
```

If `object.from` refers to an Atomic Object scalar value, the policy target is the physical Object identity referred to by that value.

If `object.from` refers to an Object-bearing Array or other Object-bearing collection, the policy target is the set of physical Object identities contained in that value's Object slots. The policy applies to each contained Object identity individually. The policy target is not the Array container value itself.

For `Array<Sample>`, this means:

```text
object_slots(value) = elements[*]
```

### 24.2 Object lifetime and effective interval

For policy purposes, an Object is considered available to the workflow from the start of the process that creates or introduces it until the end of the process that consumes or exports it from the current scope.

An Object is introduced into a composite scope when it appears through an Object-bearing input port of that composite invocation, or when it appears through an Object-bearing output port of a direct child node in that scope.

An Object is exported from a composite scope when it is connected to `body.returns`.

An Object leaves policy tracking in the current scope when it is consumed or exported from that scope.

This is a policy interpretation model, not a hard physical existence guarantee.

Effective interval:

```text
effective_interval = policy.during ∩ object_lifetime
```

If the effective interval is empty, it is not a validation error; an implementation may report a diagnostic.

### 24.3 Policy tracking through Object flow

Object policies track physical Object identity through `map` and standard `objects.transform`.

For `array_uncons`:

```text
xs[0]   -> head
xs[1..] -> tail[*]
```

A policy targeting `xs` follows the contained Object identities into both `head` and `tail`.

For `array_reverse`:

```text
xs[i] -> ys[n - 1 - i]
```

The policy follows Object identity, not index position.

Policies do not automatically transfer across `consume` / `create` replacement. If policy transfer across physical replacement is needed, it requires explicit policy transfer semantics, which are outside v0.

Future versions may define policy transfer semantics as an explicit feature. Such a feature could describe when and how policies attached to one Object identity transfer to a replacement Object identity. v0 does not define this feature, and no policy transfer is inferred from name, type, view metadata, branch structure, or `consume` / `create` replacement.

For a `fold` node with Object-bearing `each` sources and Object-bearing `mode: collect` outputs, policies follow contained Object identities through the per-iteration target process behavior into collected output Array element slots when those identities are preserved by `map` or standard `objects.transform`. The policy follows Object slots, not the collection container value itself.

### 24.4 Policy scope, nested execution, and non-retroactivity

A scheduling policy applies only to Object identities that are reachable through the referenced `object.from` value while that value is available in the policy's declaring composite scope.

If `object.from` refers to an output of a structured node, the referenced value becomes available in the declaring scope only when the structured node output is produced. The policy does not retroactively apply to Object identities created inside the structured node before that output becomes available in the declaring scope.

This non-retroactivity rule does not prevent policy propagation into nested execution. Once a policy applies to an Object identity in its declaring scope, the policy follows that Object identity through identity-preserving Object flow into nested composite invocations and structured node invocations, for the portion of the policy interval that overlaps the nested execution.

This is forward propagation along already-known Object identity flow, not retroactive policy application. The policy follows Object identity, not variable names, output names, collection container values, or view metadata.

Examples:

- If an outer composite policy targets `inputs.sample`, and `inputs.sample` is passed into a nested composite process, the policy remains applicable to the same Object identity inside the nested composite while the outer policy interval overlaps the nested invocation.
- If an outer composite policy targets `inputs.samples: Array<Sample>`, and `inputs.samples` is used as a `map` or `fold` `each` source, the policy follows the contained Object identities into the per-element invocations.
- If an Object-bearing carry value with an active outer policy is passed through `fold` or `do_while`, the policy follows the carry Object identity through identity-preserving carry transitions.

Policies do not automatically transfer across `consume` / `create` replacement in nested execution. If a nested process consumes an Object identity and creates a replacement Object identity, a policy applying to the consumed Object does not apply to the created Object in v0.

A policy declared in an inner composite scope does not escape to the caller and is not exported with returned Object identities. If policy behavior is needed in an outer scope, the outer scope must declare its own policy.

To apply a policy during the internal execution of a structured node to Objects created inside that structured node, the policy must be declared inside the process or composite scope where those created Object identities are available. To apply a policy after structured execution, declare a policy in the enclosing scope targeting the structured node output.

---

## 25. Runtime Failures and Exceptions

Runtime failures, exceptions, retries, cancellation, compensation, cleanup handlers, and recovery are outside the scope of v0.

v0 defines well-formed workflow structure and successful Object/Data flow semantics.

Recoverable domain-level failures may be modeled explicitly as ordinary Data outputs using user-defined Data types, if needed.

Example:

```yaml
types:
  AnalyzeOutcome:
    domain: data
```

No `Result<T, E>`, `Optional<T>`, exception edge, or try/catch construct is included in v0.

Runtime data errors and runtime verification errors may occur when invocation-time data violates requirements that could not be determined statically, such as `mode: last` on an empty fold traversal or a zip-equal traversal length mismatch when the relevant lengths are only known at data phase.

For phase-dependent requirements, v0 classifies the error by the earliest phase at which the violation is determined: graph-time validation error, run-start validation or preflight error, or runtime data error. Runtime data errors are outside the core validation semantics of v0, but this specification may still define standard execution behavior for particular runtime data errors.

`do_while` bounded termination is not an IR validation error and is not defined by v0 as a runtime failure. It is a runtime execution condition in which the node terminates by reaching `max_iterations` while the condition output remains `true`. v0 defines the outputs for bounded termination from the invocations that actually ran; implementations may report the condition as a diagnostic, warning, runtime status, policy concern, or runtime concern.

---

## 26. Implementation Extensions and Portability

v0 distinguishes portable v0 validation from implementation extensions.

A strict portable v0 document uses only v0-defined syntax, keys, feature names, type syntax, process kinds, node kinds, script languages, and validation semantics.

A validator may provide an extension-tolerant mode. Extension-tolerant mode may accept implementation-defined extension keys and extension scheduling preference kinds or payload fields using the reserved `x-` prefix, but those documents are not strict portable v0 documents unless the extensions are removed.

Implementation extension keys must use the reserved prefix `x-`. Unknown keys that do not use the `x-` prefix are validation errors even in extension-tolerant mode.

The v0 core validator does not define the schema or semantics of `x-` extension values. YAML well-formedness, string-key restrictions, duplicate-key restrictions, and import expansion still apply to extension keys and their values.

The `scheduling.policies[*].prefer` payload has a v0-defined closed shape for v0-defined scheduling preference kinds. v0 core validation checks scheduling placement, temporal references, Object targets, Object lifetime interpretation, feature requirements, preference kind names, required preference payload fields, and the scalar shape of preference values and units. Unit strings and unit conversion semantics are implementation-defined. Extension preference kinds and extension payload fields are allowed only in extension-tolerant mode when they use the reserved `x-` prefix.

A feature name listed in `features` must be either a v0-defined feature name or, in extension-tolerant mode, an implementation-defined extension feature name using the form:

```text
x-[A-Za-z_][A-Za-z0-9_]*
```

Unknown non-extension feature names are validation errors. A v0-defined feature that is required by the document but not supported by a particular implementation is not a v0 validation error; it is an unsupported feature condition for that implementation.

In strict portable v0, the only portable script language is `python`. Other script languages are not portable v0. An implementation may support additional script languages only as extensions.

v0 core validation does not accept implementation-defined type constructors, process kinds, node kinds, Object transform kinds, binding sections, output modes, or alternate generic inference semantics. Extensions that change these areas define an extended dialect rather than strict portable v0.

Implementations may report validation, portability, unsupported-feature, and extension-use diagnostics separately.

---

## 27. Summary of v0 Core Rules

1. A process's input and output endpoints are collectively called ports; input ports are declared under `inputs`, and output ports are declared under `outputs`.
2. A v0 document may include optional reserved `spec_version` metadata. If present, it must use the two-number string format `MAJOR.MINOR`; omission is allowed in v0.
3. Types are nominal; built-in primitive Data types are `Bool`, `Int`, `Float`, and `String`, and the only built-in type constructor is `Array<T>`.
4. `$import` provides structural inclusion before validation; it is not a module system and introduces no namespace or aliasing. Import paths should be relative for portability; URI-scheme references are implementation extensions, and URI fragments are not defined in v0.
5. Imported fragments normally omit reserved metadata; duplicate keys at the same expanded mapping level after import resolution are validation errors.
6. v0 portable YAML is closed by default; unknown keys are validation errors. Scheduling preference payloads have a v0-defined closed shape for v0-defined preference kinds, while unit strings are implementation-defined.
7. Implementation extension keys must use the `x-` prefix and are not strict portable v0 unless explicitly accepted by an extension-tolerant validator mode.
8. `null` values are not valid in v0 portable YAML.
9. Omitted `traits`, `types`, `inputs`, and `outputs` are interpreted as empty mappings; `features` may be omitted and derived; `processes` is required.
10. v0 identifiers are case-sensitive ASCII identifiers matching `[A-Za-z_][A-Za-z0-9_]*`; `.` is not allowed in identifiers, including process names.
11. UTF-8 text is allowed in YAML string values and comments, but not in identifiers.
12. `features` is canonical when present and must include all required features; if omitted, required features are derived from the body.
13. Unknown non-extension feature names are validation errors.
14. Object-bearing values are determined by recursive `object_slots`; Object identity belongs to Object slots, not to the enclosing Object-bearing value as a whole.
15. Object-bearing values are linear: no fan-out, no implicit discard of contained Object slots.
16. Object-bearing values are normally `data` phase; rare `run` phase is allowed; `graph` phase is invalid.
17. Ordinary node invocations use `state` for Object-bearing linear inputs and `bind` for Pure Data inputs.
18. In `fold` and `do_while`, `carry` is loop-carried and may be Pure Data or Object-bearing.
19. `branch` uses `args`, not `state`; Object-bearing branch arguments are supplied only to the selected arm.
20. Atomic Object behavior is declared using explicit `inputs.*` / `outputs.*` paths.
21. Composite Object behavior is derived from body graph flow and `returns`.
22. All processes must satisfy Object tracking completeness.
23. Every Object slot must have exactly one fate or provenance.
24. `map` preserves physical identity.
25. `consume` ends an input Object identity.
26. `create` introduces a new Object identity.
27. `consume + create` is Object replacement; policy and `.view` metadata do not automatically transfer across replacement.
28. v0 standard transforms are `array_uncons`, `array_cons`, and `array_reverse`; they have no `params`, require strict role typing, apply only to Object-bearing paths, and are checked using slot-level correspondence rules over Array structure.
29. `elidable_iso` is a strong process-level convenience trait and inference permission, not a type trait or structured-control requirement.
30. For Object-bearing Arrays, `elidable_iso` preserves length, nesting, order, and contained Object identities.
31. Structured node features are `node_map`, `node_fold`, `node_do_while`, and `node_branch`.
32. `map` requires only Object tracking completeness and exposes all target process outputs as Array outputs; v0 does not define `map.outputs`.
33. `fold` and `do_while` require structured carry compatibility for carry outputs; `fold` also allows non-carry Object-bearing outputs only with explicit `mode: collect`.
34. Empty `fold` traversal is allowed unless `mode: last` is required; errors are classified by the earliest phase at which emptiness is determined.
35. Zip-equal length mismatches are classified by the earliest phase at which the mismatch is determined.
36. `fold` and `do_while` expose carry outputs by default and drop non-carry Pure Data outputs by default; in `fold`, non-carry Object-bearing outputs must be explicitly listed with `mode: collect`.
37. If `do_while` reaches `max_iterations` while its condition output remains `true`, the node terminates by bounded termination; this is not an IR validation error or a v0-defined runtime failure, and outputs are produced from the invocations that actually ran.
38. `branch` exposes Object-bearing outputs by default only when they are common to both arms with the same name, type, phase, and identity-equivalent Object slot provenance; Data outputs are dropped by default.
39. A branch common Object-bearing output whose Object identity may depend on the selected arm is invalid in v0.
40. If branch `outputs` is present, it is authoritative and the default branch output derivation does not apply.
41. `python_script_processes` supports inline `language: python` script processes for Pure Data only; other script languages are not portable v0.
42. Python script code returns an output-name mapping; `script.returns` is not defined in v0.
43. Script return mismatches are runtime verification errors unless statically determined.
44. `scheduling_policies` covers scheduling policy attachment, including scalar Object and Object-bearing collection targets.
45. Scheduling policies are preferences; policy misses and conflicting preferences are not validation errors. v0 defines the portable `prefer` payload shape for `max_gap`, `min_gap`, and `temperature`; unit strings and conversion semantics are implementation-defined. `max_gap` and `min_gap` forbid `object` and require finite non-negative numeric values. `temperature` requires a finite numeric value and requires an `object` target.
46. `scheduling` is allowed only on composite processes, and temporal references are lexically scoped to the current composite body.
47. Policies do not apply retroactively to Objects before they become reachable from the declaring scope, but once a policy applies to an Object identity, it follows that identity through identity-preserving flow into nested composite and structured node executions.
48. Policies declared in inner scopes are not exported to callers, and policies do not transfer across `consume` / `create` replacement in v0.
49. Runtime failure and exception handling are outside v0.
50. v0 defines one closed built-in type trait, `Numeric`, satisfied only by `Int` and `Float`; document-defined type traits are declared in top-level `traits`.
51. Document-defined type traits are nominal membership markers only and do not imply fields, operators, conversions, subtyping, inheritance, Object behavior, or view structure; user-defined types cannot implement `Numeric`.
52. User-defined types implement traits using `implements`.
53. User-defined type views are declared in `types.*.view`; view fields are required, read-only Pure Data projections.
54. In v0, user-defined view field types must be primitive Pure Data types or Arrays recursively containing only primitive Pure Data element types.
55. User-defined nominal Data types, Object types, Arrays of user-defined nominal Data types, and Arrays of Object types are not valid user-defined view field types.
56. A user-defined view field may declare an optional static `value`; if present, it is a graph-time type-level constant that must conform to the field's declared view field type.
57. Static view values must not be `null`; Float static values may use YAML integer, floating-point, or exponent numeric forms, but NaN and infinity are not valid portable v0 static values.
58. Static view values do not create workflow values, Object identities, Object mappings, or Object tracking effects.
59. Every `type` field value is a YAML string scalar containing a v0 type expression. `Array<T>` is the only built-in type constructor, requires exactly one type argument, and may be nested.
60. Whitespace is allowed only immediately inside `Array` angle brackets, such as `Array< T >`; whitespace between `Array` and `<` is invalid.
61. Type atoms must resolve to a built-in primitive type, a top-level user-defined type, or a type parameter declared by the current process. Type parameters must not shadow top-level user-defined type names or reserved built-in names.
62. `Optional<T>`, `Result<T,E>`, union syntax, nullable suffixes such as `T?`, map types, tuple types, function types, and multiple type arguments are not valid v0 type expressions.
63. Generic type parameters are declared with a required `domain` of either `data` or `object`.
64. A `domain: data` type parameter may be instantiated only with a built-in primitive Data type or user-defined nominal Data type; a `domain: object` type parameter may be instantiated only with a user-defined atomic Object type.
65. Type parameters are not instantiated with Array types; collection genericity is written as `Array<T>` or `Array<O>`.
66. Generic type argument inference and `where` constraint validation are performed during graph validation and do not depend on runtime values.
67. `where` constraints are string scalar trait constraints of the form `TraitName<T>` or `TraitName< T >`; whitespace before `<` is invalid.
68. Body dataflow references use only `inputs.<port>` or `<node_id>.<output>`. They do not use `.view`, `outputs.*`, temporal fields, nested node paths, or process names.
69. Atomic Object paths use only `inputs.<port>` and `outputs.<port>`. Object slots and Array elements are derived from the resolved port type, not directly addressed by path syntax.
70. Scheduling Object targets use body-visible Object-bearing value references. Temporal references use only `self.start`, `self.end`, `<node_id>.start`, and `<node_id>.end`.
71. Contract expressions may reference only explicit `.view` references. v0 does not omit `.view` even though contracts only refer to views.
72. A binding source entry must contain exactly one of `from` or `value`.
73. `branch.condition.from` is a body dataflow reference. `do_while.condition.output` is a target process output name, not a body dataflow reference.
74. Primitive Data type views and `Array<T>.view.length` are defined by v0.
75. Contract expressions are checked against resolved view schemas and defined operator categories; limited `Int` / `Float` numeric promotion is local to contract expressions and does not imply implicit conversion elsewhere in v0.
76. Object type boundaries should reflect carry compatibility, replacement compatibility, and operational geometry.
77. `$import` values must be non-empty string scalars or non-empty sequences of non-empty string scalars. Import resolution is recursive, import targets must be single YAML documents, and `$import` does not remain in the expanded document.
78. Import cycles, unreadable or unparsable import targets, invalid import root shapes, YAML multi-document import targets, and URI fragments in portable v0 imports are validation errors.
79. Contract `expr` values are YAML string scalars using the v0 contract expression language. Contract references must explicitly include `.view`; direct port references and omitted `.view` are not valid v0 contract references.
80. Contract expression Float literals may use decimal exponent notation such as `1.0e+23`; integer exponent notation such as `1e3` is not a v0 Float literal.
81. Contract expressions must type-check to `Bool`; all subexpressions are parsed and type-checked without relying on Boolean short-circuiting to ignore invalid subexpressions.
82. Strict portable v0 uses only v0-defined syntax, keys, feature names, type syntax, process kinds, node kinds, script languages, and validation semantics.
83. Extension-tolerant mode may accept `x-` extension keys and `x-` extension feature names, but unknown non-`x-` keys remain validation errors.
84. v0 core validation does not accept implementation-defined type constructors, process kinds, node kinds, Object transform kinds, binding sections, output modes, or alternate generic inference semantics; such changes define an extended dialect.
