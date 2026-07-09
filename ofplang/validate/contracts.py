"""Contract expression language (spec 9).

Intent: contracts are small, side-effect-free Boolean assertions over `.view`
projections. The expression language is tiny and its literal/associativity rules
are exact (no leading-zero ints, float needs digits both sides, comparisons are
non-associative), so we hand-write the lexer and a precedence-climbing parser to
control classification precisely — a grammar library would blur the line between
"malformed literal" and other parse errors.

Validation proceeds lex -> parse -> resolve+type-check, each producing at most
one precise diagnostic per expression:
  * comparison chaining (`a < b < c`) is rejected at parse time (spec 9.2);
  * reference scope (`requires` may not read `outputs`, spec 9.1) and unknown
    view fields (spec 7.4) are caught during resolution; and
  * the whole expression must type-check to Bool (spec 9.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ofplang.validate import errors
from ofplang.validate.diagnostics import Diagnostics
from ofplang.validate.types import (
    ArrayT,
    Atom,
    PRIMITIVE_TYPES,
    TypeEnv,
    TypeExpr,
    TypeParseError,
    parse_type,
)
from ofplang.validate.yamlnode import YMap, YScalar, YSeq, YNode


class ContractError(Exception):
    """A contract-expression failure carrying the specific diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- Lexer ----------------------------------------------------------------
# A reference is a dotted path (e.g. inputs.x.view.field); bare words `and`,
# `or`, `not`, `true`, `false` are keywords. Numbers follow the strict v0 forms.
_FLOAT_RE = re.compile(r"[0-9]+\.[0-9]+([eE][+-]?[0-9]+)?")
_INT_RE = re.compile(r"0|[1-9][0-9]*")
_PATH_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
_STRING_RE = re.compile(r'"(\\.|[^"\\])*"')
_OPS = ["==", "!=", "<=", ">=", "<", ">", "+", "-", "*", "/"]
_KEYWORDS = {"and", "or", "not", "true", "false"}


@dataclass
class Tok:
    kind: str  # 'num_int','num_float','str','ref','kw','op','lparen','rparen'
    text: str


def _lex(s: str) -> list[Tok]:
    toks: list[Tok] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in " \t":
            i += 1
            continue
        if c == "(":
            toks.append(Tok("lparen", "("))
            i += 1
            continue
        if c == ")":
            toks.append(Tok("rparen", ")"))
            i += 1
            continue
        if c == '"':
            m = _STRING_RE.match(s, i)
            if not m:
                raise ContractError(errors.CONTRACT_PARSE_ERROR, "unterminated string")
            toks.append(Tok("str", m.group()))
            i = m.end()
            continue
        # Operators (multi-char first so '<=' is not read as '<' then '=').
        matched_op = next((op for op in _OPS if s.startswith(op, i)), None)
        if matched_op:
            toks.append(Tok("op", matched_op))
            i += len(matched_op)
            continue
        # A float must be tried before an int (it starts with digits too).
        if c.isdigit():
            mf = _FLOAT_RE.match(s, i)
            if mf and mf.end() == _float_span_end(s, i, mf):
                toks.append(Tok("num_float", mf.group()))
                i = mf.end()
                continue
            mi = _INT_RE.match(s, i)
            if mi:
                # Reject a trailing '.' or stray digits that would make this an
                # invalid numeric literal (e.g. `1.`), rather than silently
                # splitting it into an int and junk.
                end = mi.end()
                if end < n and s[end] == ".":
                    raise ContractError(errors.CONTRACT_PARSE_ERROR, f"malformed number near {s[i:]!r}")
                toks.append(Tok("num_int", mi.group()))
                i = end
                continue
            raise ContractError(errors.CONTRACT_PARSE_ERROR, f"malformed number near {s[i:]!r}")
        # Identifier path or keyword.
        mp = _PATH_RE.match(s, i)
        if mp:
            text = mp.group()
            if text in _KEYWORDS:
                toks.append(Tok("kw", text))
            else:
                toks.append(Tok("ref", text))
            i = mp.end()
            continue
        raise ContractError(errors.CONTRACT_PARSE_ERROR, f"unexpected character {c!r}")
    return toks


def _float_span_end(s: str, i: int, m: re.Match) -> int:
    # Guard against `1.2.3`-style over-reads: the float regex already stops at
    # the second dot, so the match end is authoritative; this hook exists to
    # keep the numeric branch readable and future-proof.
    return m.end()


# --- AST ------------------------------------------------------------------
@dataclass
class Lit:
    type_name: str  # 'Bool'|'Int'|'Float'|'String'
    value: object = None  # concrete Python value, for constant folding


@dataclass
class Ref:
    path: list[str]


@dataclass
class Unary:
    op: str
    operand: object


@dataclass
class Binary:
    op: str
    left: object
    right: object


# --- Parser (precedence: or < and < comparison(non-assoc) < +- < */ < unary) --
_COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}


class _Parser:
    def __init__(self, toks: list[Tok]) -> None:
        self.toks = toks
        self.i = 0

    def _peek(self) -> Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self) -> Tok | None:
        t = self._peek()
        if t is not None:
            self.i += 1
        return t

    def parse(self):
        expr = self._parse_or()
        if self.i != len(self.toks):
            raise ContractError(errors.CONTRACT_PARSE_ERROR, "trailing tokens in expression")
        return expr

    def _parse_or(self):
        left = self._parse_and()
        while (t := self._peek()) and t.kind == "kw" and t.text == "or":
            self._next()
            left = Binary("or", left, self._parse_and())
        return left

    def _parse_and(self):
        left = self._parse_cmp()
        while (t := self._peek()) and t.kind == "kw" and t.text == "and":
            self._next()
            left = Binary("and", left, self._parse_cmp())
        return left

    def _parse_cmp(self):
        left = self._parse_add()
        t = self._peek()
        if t and t.kind == "op" and t.text in _COMPARISONS:
            self._next()
            right = self._parse_add()
            # Comparisons are non-associative (spec 9.2): a second comparison at
            # the same level (`a < b < c`) is a specific validation error.
            t2 = self._peek()
            if t2 and t2.kind == "op" and t2.text in _COMPARISONS:
                raise ContractError(errors.COMPARISON_CHAIN, "comparison operators are non-associative")
            return Binary(t.text, left, right)
        return left

    def _parse_add(self):
        left = self._parse_mul()
        while (t := self._peek()) and t.kind == "op" and t.text in ("+", "-"):
            self._next()
            left = Binary(t.text, left, self._parse_mul())
        return left

    def _parse_mul(self):
        left = self._parse_unary()
        while (t := self._peek()) and t.kind == "op" and t.text in ("*", "/"):
            self._next()
            left = Binary(t.text, left, self._parse_unary())
        return left

    def _parse_unary(self):
        t = self._peek()
        if t and ((t.kind == "kw" and t.text == "not") or (t.kind == "op" and t.text == "-")):
            self._next()
            return Unary(t.text, self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self):
        t = self._next()
        if t is None:
            raise ContractError(errors.CONTRACT_PARSE_ERROR, "unexpected end of expression")
        if t.kind == "lparen":
            expr = self._parse_or()
            close = self._next()
            if close is None or close.kind != "rparen":
                raise ContractError(errors.CONTRACT_PARSE_ERROR, "missing ')'")
            return expr
        # Literals retain their concrete value so a fully-constant contract can
        # be folded at graph time (spec 9.2).
        if t.kind == "num_int":
            return Lit("Int", int(t.text))
        if t.kind == "num_float":
            return Lit("Float", float(t.text))
        if t.kind == "str":
            return Lit("String", t.text[1:-1])  # strip quotes (escapes kept literally)
        if t.kind == "kw" and t.text in ("true", "false"):
            return Lit("Bool", t.text == "true")
        if t.kind == "ref":
            return Ref(t.text.split("."))
        raise ContractError(errors.CONTRACT_PARSE_ERROR, f"unexpected token {t.text!r}")


# --- Resolution context & type checking -----------------------------------
@dataclass
class ContractCtx:
    inputs: dict[str, TypeExpr]
    outputs: dict[str, TypeExpr]
    view_schemas: dict[str, dict[str, TypeExpr]]  # user type -> field -> type
    scope: str  # 'requires' | 'ensures'


def _resolve_ref(path: list[str], ctx: ContractCtx) -> str:
    """Resolve a `.view` reference to a primitive type name.

    Enforces reference scope and the explicit-`.view` requirement, then resolves
    the (optional) field against the port type's view schema. Returns the
    primitive type name the reference denotes.
    """
    if len(path) < 3 or path[0] not in ("inputs", "outputs") or path[2] != "view":
        # Contracts may only reference explicit `.view` projections (spec 9.2).
        raise ContractError(errors.CONTRACT_INVALID_REFERENCE, f"invalid reference {'.'.join(path)}")

    side, port = path[0], path[1]
    # `requires` may reference only inputs; `ensures` may reference both (9.1).
    if side == "outputs" and ctx.scope == "requires":
        raise ContractError(errors.CONTRACT_REFERENCE_SCOPE, "requires cannot reference outputs")

    ports = ctx.inputs if side == "inputs" else ctx.outputs
    if port not in ports:
        raise ContractError(errors.CONTRACT_INVALID_REFERENCE, f"unknown port {port!r}")
    port_type = ports[port]

    field = path[3] if len(path) >= 4 else None
    if len(path) > 4:
        # Nested field paths below a view field are not defined in v0 (spec 2.6.5).
        raise ContractError(errors.CONTRACT_INVALID_REFERENCE, "nested view field path")

    return _resolve_view_field(port_type, field, ctx)


def _resolve_view_field(port_type: TypeExpr, field: str | None, ctx: ContractCtx) -> str:
    """Type of `<port>.view[.field]` (spec 7.4).

    Primitive views are the scalar itself; `Array<T>.view.length` is Int; a
    user type's fields come from its declared view schema. Any other field is an
    unknown view field.
    """
    if field is None:
        # Bare `.view`: only meaningful (as a comparable scalar) for primitives.
        if isinstance(port_type, Atom) and port_type.name in PRIMITIVE_TYPES:
            return port_type.name
        raise ContractError(errors.CONTRACT_INVALID_REFERENCE, "non-scalar .view needs a field")

    if isinstance(port_type, ArrayT):
        if field == "length":
            return "Int"  # standard Array view field (spec 7.4)
        raise ContractError(errors.UNKNOWN_VIEW_FIELD, f"Array has no view field {field!r}")

    if isinstance(port_type, Atom):
        if port_type.name in PRIMITIVE_TYPES:
            # Primitives expose no named fields; their `.view` is the scalar.
            raise ContractError(errors.UNKNOWN_VIEW_FIELD, f"primitive has no field {field!r}")
        schema = ctx.view_schemas.get(port_type.name, {})
        if field not in schema:
            raise ContractError(errors.UNKNOWN_VIEW_FIELD, f"unknown view field {field!r}")
        ftype = schema[field]
        # View field types are primitive-or-Array-of-primitive; for comparison
        # purposes we surface the primitive name (Array fields aren't comparable
        # scalars and would be a type error if used directly).
        if isinstance(ftype, Atom) and ftype.name in PRIMITIVE_TYPES:
            return ftype.name
        return "Array"  # non-scalar; downstream operators will reject it

    raise ContractError(errors.CONTRACT_INVALID_REFERENCE, "unresolvable reference")


_NUMERIC = {"Int", "Float"}


def _numeric_result(a: str, b: str) -> str:
    return "Int" if a == "Int" and b == "Int" else "Float"


def _type_of(node, ctx: ContractCtx) -> str:
    """Compute a node's type, raising ContractError on any type violation."""
    if isinstance(node, Lit):
        return node.type_name
    if isinstance(node, Ref):
        return _resolve_ref(node.path, ctx)
    if isinstance(node, Unary):
        t = _type_of(node.operand, ctx)
        if node.op == "not":
            if t != "Bool":
                raise ContractError(errors.CONTRACT_TYPE_ERROR, "'not' needs Bool")
            return "Bool"
        # unary minus
        if t not in _NUMERIC:
            raise ContractError(errors.CONTRACT_TYPE_ERROR, "unary '-' needs a number")
        return t
    if isinstance(node, Binary):
        lt = _type_of(node.left, ctx)
        rt = _type_of(node.right, ctx)
        op = node.op
        if op in ("and", "or"):
            if lt != "Bool" or rt != "Bool":
                raise ContractError(errors.CONTRACT_TYPE_ERROR, f"'{op}' needs Bool operands")
            return "Bool"
        if op in ("==", "!="):
            same_primitive = lt == rt and lt in PRIMITIVE_TYPES
            numeric_pair = lt in _NUMERIC and rt in _NUMERIC
            if not (same_primitive or numeric_pair):
                raise ContractError(errors.CONTRACT_TYPE_ERROR, f"'{op}' operand mismatch")
            return "Bool"
        if op in ("<", "<=", ">", ">="):
            if not (lt in _NUMERIC and rt in _NUMERIC):
                raise ContractError(errors.CONTRACT_TYPE_ERROR, "ordering needs numeric operands")
            return "Bool"
        if op in ("+", "-", "*"):
            if not (lt in _NUMERIC and rt in _NUMERIC):
                raise ContractError(errors.CONTRACT_TYPE_ERROR, f"'{op}' needs numeric operands")
            return _numeric_result(lt, rt)
        if op == "/":
            if not (lt in _NUMERIC and rt in _NUMERIC):
                raise ContractError(errors.CONTRACT_TYPE_ERROR, "'/' needs numeric operands")
            return "Float"
    raise ContractError(errors.CONTRACT_TYPE_ERROR, "unrecognized expression")


def _has_ref(node) -> bool:
    """Whether the expression reads any runtime/instance view value."""
    if isinstance(node, Ref):
        return True
    if isinstance(node, Unary):
        return _has_ref(node.operand)
    if isinstance(node, Binary):
        return _has_ref(node.left) or _has_ref(node.right)
    return False


def _eval(node):
    """Evaluate a fully-constant (Ref-free) expression to a Python value."""
    if isinstance(node, Lit):
        return node.value
    if isinstance(node, Unary):
        v = _eval(node.operand)
        return (not v) if node.op == "not" else (-v)
    if isinstance(node, Binary):
        a, b = _eval(node.left), _eval(node.right)
        op = node.op
        if op == "and":
            return bool(a) and bool(b)
        if op == "or":
            return bool(a) or bool(b)
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b  # ZeroDivisionError caught by caller as a static error
    raise ContractError(errors.CONTRACT_TYPE_ERROR, "uncomputable constant")


def _check_expr(diags: Diagnostics, text: str, ctx: ContractCtx, path: str, at=None) -> None:
    try:
        ast = _Parser(_lex(text)).parse()
        result = _type_of(ast, ctx)
        # A contract must type-check to Bool (spec 9.2).
        if result != "Bool":
            raise ContractError(errors.CONTRACT_TYPE_ERROR, f"contract is {result}, not Bool")
        # Constant folding: a contract with no runtime references that is
        # statically false (or hits a static eval error like /0) is invalid at
        # graph time (spec 9.2). Reference-bearing contracts are runtime checks.
        if not _has_ref(ast):
            try:
                if _eval(ast) is False:
                    raise ContractError(errors.CONTRACT_STATIC_FALSE, "contract is statically false")
            except ZeroDivisionError:
                raise ContractError(errors.CONTRACT_STATIC_FALSE, "static division by zero")
    except ContractError as exc:
        # Position points at the contract expression scalar (the whole line);
        # sub-token offsets within the expression are not tracked in v1.
        diags.add(exc.code, str(exc), path, at=at)


# --- View schema + port type collection -----------------------------------
def _build_view_schemas(doc: YMap) -> dict[str, dict[str, TypeExpr]]:
    """Map each user type to its view field name -> parsed field type."""
    out: dict[str, dict[str, TypeExpr]] = {}
    types = doc.get("types")
    if not isinstance(types, YMap):
        return out
    for tname in types.keys():
        decl = types.get(tname)
        if not isinstance(decl, YMap):
            continue
        view = decl.get("view")
        fields: dict[str, TypeExpr] = {}
        if isinstance(view, YMap):
            for fname in view.keys():
                fdecl = view.get(fname)
                if isinstance(fdecl, YMap):
                    tnode = fdecl.get("type")
                    if isinstance(tnode, YScalar) and tnode.is_str:
                        try:
                            fields[fname] = parse_type(tnode.text)
                        except TypeParseError:
                            pass
        out[tname] = fields
    return out


def _port_types(ports: YNode | None) -> dict[str, TypeExpr]:
    out: dict[str, TypeExpr] = {}
    if not isinstance(ports, YMap):
        return out
    for pname in ports.keys():
        port = ports.get(pname)
        if isinstance(port, YMap):
            tnode = port.get("type")
            if isinstance(tnode, YScalar) and tnode.is_str:
                try:
                    out[pname] = parse_type(tnode.text)
                except TypeParseError:
                    pass
    return out


def check_contracts(doc: YMap, diags: Diagnostics, env: TypeEnv) -> None:
    processes = doc.get("processes")
    if not isinstance(processes, YMap):
        return
    view_schemas = _build_view_schemas(doc)

    for pname in processes.keys():
        proc = processes.get(pname)
        if not isinstance(proc, YMap):
            continue
        contracts = proc.get("contracts")
        if not isinstance(contracts, YMap):
            continue

        inputs = _port_types(proc.get("inputs"))
        outputs = _port_types(proc.get("outputs"))
        base = f"processes.{pname}.contracts"

        for scope in ("requires", "ensures"):
            section = contracts.get(scope)
            if not isinstance(section, YSeq):
                continue
            ctx = ContractCtx(inputs=inputs, outputs=outputs, view_schemas=view_schemas, scope=scope)
            for i, item in enumerate(section.items):
                if not isinstance(item, YMap):
                    continue
                expr_node = item.get("expr")
                if isinstance(expr_node, YScalar) and expr_node.is_str:
                    _check_expr(diags, expr_node.text, ctx, f"{base}.{scope}[{i}]", at=expr_node)
