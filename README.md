# ofplang validate

[![CI](https://github.com/ofplang/validate/actions/workflows/ci.yml/badge.svg)](https://github.com/ofplang/validate/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ofplang-validate.svg)](https://pypi.org/project/ofplang-validate/)

A validator for **Object-flow Programming Language v0** — a YAML-based dataflow
workflow IR with linear Object tracking. The language is defined in the
[ofplang/spec](https://github.com/ofplang/spec) repository.

The validator checks that a document is well-formed portable v0: structure and
types, the feature model, linear Object tracking, structured nodes, contracts,
and scheduling policies. It reports findings as stable **error codes** rather
than free text, so results are easy to consume in tests and tooling.

## Install

```sh
pip install ofplang-validate
```

Requires Python 3.10+. The only runtime dependency is PyYAML. For development,
install editable with the test extra from a clone:

```sh
pip install -e ".[test]"
```

## Command line

```sh
ofp-validate <file>...                 # or: python -m ofplang.validate <file>...
ofp-validate --mode extension-tolerant doc.yaml
ofp-validate --format json doc.yaml
```

Options: `--mode {strict,extension-tolerant}`, `--format {text,json}`,
`-q/--quiet`, `--no-color`.

Exit codes: `0` all valid, `1` validation errors found, `2` usage/input error.

```
$ ofp-validate workflow.yaml
workflow.yaml:7:15: error unknown_type  processes.main.inputs.x.type  unknown type in 'Foo'
1 error in 1 of 1 file
```

Diagnostics carry a `file:line:col` source position (an imported fragment's own
file when the problem is inside an `$import`); `--format json` includes
`file`/`line`/`col` fields.

This tool is also the `validate` subcommand of the umbrella `ofp` CLI
([`ofplang`](https://pypi.org/project/ofplang/)), which forwards to it in-process:
`ofp validate doc.yaml` is the command above, with the same options and the same
exit codes.

## Library

```python
from ofplang.validate import validate

result = validate("workflow.yaml", mode="strict")
if not result.ok:
    for d in result.diagnostics:
        print(d.code, d.path, d.message)
```

`validate(source, *, mode="strict")` returns a `ValidationResult` with `.ok` and
`.diagnostics` (each a `Diagnostic(code, message, path, file, line, col)`). The
validator collects all independent findings rather than stopping at the first;
only a YAML parse or `$import` resolution failure is terminal.

`source` is a path **or an already-loaded document** (a mapping), so a caller that
builds one in memory — a generator, a notebook, a tool that rewrote a document
before running it — can validate exactly what it holds:

```python
result = validate(document)          # the same checks, the same codes
```

An in-memory document must already be import-expanded (there is no base directory
to resolve a relative `$import` against — call `expand()` on the file first), and it
cannot contain a value v0 has no spelling for, such as a `datetime`; either raises
`ValueError`. Its diagnostics carry no `file:line:col`, only `path`, and no
`duplicate_key` can arise for it: a mapping holds one value per key.

### Expanded document

`$import` (spec §3) is *structural* inclusion resolved before any other checks,
so the document that gets validated is the fully expanded one. To obtain that
expanded document — e.g. to hand a downstream tool the exact form it should
schedule or run, instead of re-reading the unexpanded file — use:

```python
from ofplang.validate import validate, expand

# structural expansion only (no validation); raises YamlError on import failure
doc = expand("workflow.yaml")

# validate and get the expanded document in one pass
result = validate("workflow.yaml", mode="extension-tolerant", expand=True)
if result.ok:
    run_it(result.document)   # exactly what was validated
```

`expand(source, *, base_dir=None)` returns the plain-Python document
`yaml.safe_load` would yield for the resolved tree (fidelity is exact, including
non-string scalar tags). `base_dir` overrides where the root's relative imports
resolve. `validate(..., expand=True)` sets `ValidationResult.document` to that
same value whenever load + `$import` resolution succeeds (otherwise `None`).

The package lives under the `ofplang` PEP 420 namespace (`ofplang.validate`),
shared across the organization's tools.

## Scope

Covers graph-time validation of portable v0. Runtime failures, and run/data-phase
preflight checks, are out of scope (spec §6.2, §25). Two modes are supported:
`strict` (portable v0) and `extension-tolerant` (accepts `x-` extension keys).

## Tests

The behavior is pinned by a spec-derived conformance suite that matches on error
codes (see [tests/conformance/README.md](tests/conformance/README.md)).

```sh
pytest                         # run everything
OFPLANG_STRICT_TESTS=1 pytest  # full contract, no pending escapes
```
