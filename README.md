# ofplang validate

A validator for **Object-flow Programming Language v0** — a YAML-based dataflow
workflow IR with linear Object tracking. The language is defined in the
[ofplang/spec](https://github.com/ofplang/spec) repository.

The validator checks that a document is well-formed portable v0: structure and
types, the feature model, linear Object tracking, structured nodes, contracts,
and scheduling policies. It reports findings as stable **error codes** rather
than free text, so results are easy to consume in tests and tooling.

## Install

```sh
pip install -e ".[test]"
```

Requires Python 3.10+. The only runtime dependency is PyYAML.

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

This tool is also intended to be exposed as the `validate` subcommand of the
umbrella `ofp` CLI (a separate repository in the `ofplang` organization).

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
