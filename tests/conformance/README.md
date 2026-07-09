# ofplang v0 conformance test suite

This suite pins the behavioral contract of the ofplang v0 validator **before**
the validator is implemented (test-driven development). Each test case is a
spec-derived example document paired with its expected validation outcome.

The tests assert on **stable error codes**, never on message strings, so they
survive validator refactors and force invalid cases to fail *for the intended
reason* rather than by accident.

## Running

```
pip install -e ".[test]"
pytest
```

While the validator is a stub (`validate` raises `NotImplementedError`), every
case reports as `xfail` ("pending implementation"). Once implementation starts,
each category flips to real assertions as it lands. To hold a finished validator
to the full contract (so leftover `NotImplementedError`s fail):

```
OFPLANG_STRICT_TESTS=1 pytest
```

## Case layout

A *case* comes in one of two on-disk forms:

**Sidecar** — a single-file document plus its expectation:

```
cases/types/unknown_type.yaml
cases/types/unknown_type.expected.yaml
```

**Directory** — multi-file cases (e.g. `$import`); the root document is
`main.yaml`:

```
cases/imports/cycle/
  main.yaml
  a.yaml
  expected.yaml
```

The case id is the path under `cases/` with the expectation suffix stripped
(`types/unknown_type`, `imports/cycle`).

## Expected-outcome schema

```yaml
mode: strict            # optional: strict | extension-tolerant (default: strict)
outcome: invalid        # required: valid | invalid
match: exact            # optional: exact | superset (default: exact)
errors:                 # required iff outcome == invalid
  - code: unknown_type  # required; must exist in ofplang/validate/errors.py
    path: "processes.x" # optional location hint (not matched by default)
pending: "reason"       # optional: see below
notes: "why this is invalid, quoting the spec clause"   # optional
```

### Pending cases (tests ahead of implementation)

A case may carry `pending: "<reason>"` to document behavior the validator does
**not satisfy yet** — a spec area not implemented, or a known false positive.
Such a case is marked `xfail` (non-strict, so an unexpected pass shows as
`XPASS`) in **both** default and strict runs, keeping the suite green while the
test is committed ahead of its implementation. When the behavior lands, delete
the `pending` line and the case becomes a hard requirement.

This is how coverage is expanded spec-first: add the case with `pending`, then
remove `pending` in the change that implements it.

- **`match: exact`** (default): the set of produced error codes must equal the
  set of expected codes. Use for minimal single-violation cases — this is what
  guarantees "invalid for the right reason".
- **`match: superset`**: every expected code must appear; extra codes are
  tolerated. Use only when a single violation legitimately fans out into
  several diagnostics.

Codes are compared as **sets** (order- and duplicate-insensitive).

## Authoring conventions

1. **One violation per invalid case.** Break exactly one rule; keep the rest of
   the document valid so the intended code is the only one produced.
2. **Minimal documents.** Include only what the rule needs. A shared minimal
   valid baseline lives in `cases/_baseline.yaml` for reference.
3. **Cite the spec.** Put the governing clause number in `notes`.
4. **Graph-phase only.** The validator core covers graph-time validation.
   Run-start/preflight and runtime-data errors (spec 6.2) are out of scope,
   except where the fact is statically known at graph phase (e.g. an empty
   literal Array feeding `mode: last`). Mark such cases in `notes`.
5. **New error code?** Add it to `ofplang/validate/errors.py` first — the runner rejects
   any code not in `ERROR_CODES`.

## Category map (spec section → cases directory)

| Directory     | Spec sections | Covers |
|---------------|---------------|--------|
| `shape/`      | 2.3, 2.4      | unknown keys, `null`, `$`-keys, required keys, value kinds |
| `metadata/`   | 2.1           | `spec_version` format |
| `identifiers/`| 2.4           | identifier grammar, reserved names, dot, duplicate ports |
| `types/`      | 2.5, 7.1      | type expressions, `Array<T>`, unknown/builtin types |
| `imports/`    | 3             | merge, splice, cycles, boundary conditions |
| `features/`   | 4             | derivation, declared-set validation, unknown features |
| `phases/`     | 6             | phase flow, object-bearing phase restrictions |
| `traits/`     | 7.3           | trait declaration, `implements`, `Numeric` rules |
| `views/`      | 7.4           | view schema, field types, static value conformance |
| `contracts/`  | 9             | expression grammar, type-check, reference scope, static eval |
| `generics/`   | 8             | `type_params`, `where`, inference |
| `objects/`    | 13, 14        | fate/provenance, map/consume/create completeness |
| `transforms/` | 14.4          | transform kinds, roles, typing |
| `linearity/`  | 12            | port degree, fan-out, indegree |
| `nodes/`      | 16-21         | map/fold/do_while/branch output modes and defaults |
| `script/`     | 22            | python-only, pure-data restriction |
| `scheduling/` | 23, 24        | placement, temporal refs, object targets, `prefer` schema |
| `entry/`      | 10.3          | entry resolution, acyclic dependency |
| `extensions/` | 26            | `x-` keys/features in both modes |
