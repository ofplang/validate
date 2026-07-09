"""Command-line interface for the ofplang v0 validator.

Intent: this is a thin presentation layer over :func:`ofplang.validate.validate` — it
does no validation itself, it only parses arguments, drives the library over one
or more files, and renders results. Keeping all logic in the library means the
CLI cannot drift from the conformance-tested behavior.

Usage:
    ofp-validate [--mode {strict,extension-tolerant}] [--format {text,json}]
                 [-q/--quiet] [--no-color] <file>...

Also invocable as `python -m ofplang.validate`, and (once installed) as the
`validate` subcommand of the umbrella `ofp` CLI.

Exit codes (linter convention):
    0  every file is valid
    1  at least one file has validation errors
    2  usage / input error (bad arguments, missing input file)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ofplang.validate import validate
from ofplang.validate.validator import STRICT, EXTENSION_TOLERANT, ValidationResult

# Exit codes are part of the CLI contract (scripts/CI depend on them).
EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2

# ANSI colors, applied only when writing to a TTY and not disabled.
_RED = "\033[31m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ofp-validate",
        description="Validate ofplang v0 documents.",
    )
    parser.add_argument("paths", nargs="+", metavar="FILE", help="document(s) to validate")
    parser.add_argument(
        "--mode",
        choices=[STRICT, EXTENSION_TOLERANT],
        default=STRICT,
        help="validation mode (default: strict)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="suppress per-diagnostic lines; show only the summary"
    )
    parser.add_argument("--no-color", action="store_true", help="disable ANSI color output")
    return parser


def _color_enabled(no_color: bool) -> bool:
    # Color only when explicitly allowed AND attached to a terminal, so piped or
    # redirected output stays clean and parseable.
    return not no_color and sys.stdout.isatty()


def _render_text(results: list[tuple[str, ValidationResult]], quiet: bool, color: bool) -> str:
    """Human-oriented report. One block per file, then a one-line summary."""
    def c(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if color else text

    lines: list[str] = []
    total_errors = 0
    invalid_files = 0
    multi = len(results) > 1

    for path, result in results:
        if result.ok:
            if not quiet:
                # Only announce OK files explicitly when validating several, so
                # single-file runs stay terse.
                if multi:
                    lines.append(f"{path}: {c('OK', _GREEN)}")
            continue
        invalid_files += 1
        total_errors += len(result.diagnostics)
        if multi:
            lines.append(f"{path}:")
        if not quiet:
            for d in result.diagnostics:
                # Prefer a concrete source position as the locator; fall back to
                # the logical path. When both exist, show the path as a dim
                # trailing detail (position primary, path for context).
                if d.location:
                    locator = d.location
                    detail = f"  {c(d.path, _DIM)}" if d.path else ""
                else:
                    locator = d.path or "<root>"
                    detail = ""
                msg = f"  {d.message}" if d.message else ""
                indent = "  " if multi else ""
                lines.append(f"{indent}{locator}: {c('error', _RED)} {d.code}{detail}{msg}")

    if total_errors == 0:
        lines.append(c(f"all valid ({len(results)} file{'s' if len(results) != 1 else ''})", _GREEN))
    else:
        lines.append(
            c(f"{total_errors} error{'s' if total_errors != 1 else ''} in "
              f"{invalid_files} of {len(results)} file{'s' if len(results) != 1 else ''}", _RED)
        )
    return "\n".join(lines)


def _render_json(results: list[tuple[str, ValidationResult]]) -> str:
    """Machine-readable report for CI/editor integration."""
    payload = {
        "ok": all(r.ok for _, r in results),
        "results": [
            {
                "file": path,
                "ok": result.ok,
                "diagnostics": [
                    {
                        "code": d.code,
                        "path": d.path,
                        "message": d.message,
                        "file": d.file,
                        "line": d.line,
                        "col": d.col,
                    }
                    for d in result.diagnostics
                ],
            }
            for path, result in results
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Pre-check inputs so a mistyped/missing path is a usage error (exit 2)
    # rather than being reported as an in-document diagnostic.
    missing = [p for p in args.paths if not Path(p).is_file()]
    if missing:
        for p in missing:
            print(f"ofp-validate: cannot open {p!r}: no such file", file=sys.stderr)
        return EXIT_USAGE

    # Validate each file via the library (the sole source of truth).
    results: list[tuple[str, ValidationResult]] = [
        (p, validate(p, mode=args.mode)) for p in args.paths
    ]

    if args.format == "json":
        print(_render_json(results))
    else:
        print(_render_text(results, args.quiet, _color_enabled(args.no_color)))

    # Exit code reflects the worst outcome across all inputs.
    return EXIT_OK if all(r.ok for _, r in results) else EXIT_INVALID


if __name__ == "__main__":  # pragma: no cover - module entry
    raise SystemExit(main())
