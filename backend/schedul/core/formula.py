"""The house derived-column formula language: one parser, two backends.

A derived column carries a formula written against other columns by name::

    ={Total Power Input (W)}/{Supply Airflow (l/s)}
    ={Heating Coil Duty (kW)}/(SETUP_CP*(SETUP_LPHWF-SETUP_LPHWR))
    =IF({Nominal Capacity (l)}>={Required Storage Volume (l)},"OK","UNDERSIZED")

That one source has to serve two masters. The web grid evaluates it as the user
types, and the exported workbook needs it as a real Excel formula so the file
still calculates when an engineer opens it and changes a duty. Writing those
twice guarantees they drift, so this module parses the source once into an AST
and gives it two walkers: :func:`to_excel` and :func:`evaluate`.

Parsing is also the validator. An unresolvable ``{Field Name}``, a banned
spilling function, or a syntax error fails here, in one place, rather than being
re-checked by the designer and the renderer separately.

The house rule is static formulas only -- no dynamic arrays, nothing post-2019.
openpyxl cannot reliably author dynamic-array formulas (they acquire ``_xlfn.``
and ``_xlpm.`` prefixes when written by anything other than Excel), and a static
formula is easier for an engineer to debug. See SPEC.md 6.1.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Iterator, Sequence

__all__ = [
    "FormulaError",
    "FormulaSyntaxError",
    "FormulaEvalError",
    "Node",
    "Num",
    "Str",
    "Bool",
    "FieldRef",
    "Const",
    "Unary",
    "Binary",
    "Func",
    "parse",
    "to_excel",
    "evaluate",
    "field_names",
    "constant_names",
    "CONSTANTS",
    "ALLOWED_FUNCTIONS",
    "BANNED_FUNCTIONS",
    "BLANK",
]


# --------------------------------------------------------------- contract ---

# The design constants a formula may reference. Each is a defined name in the
# exported workbook pointing at its row on the hidden Config sheet, and a value
# on the project record when evaluated in the app.
CONSTANTS: dict[str, str] = {
    "SETUP_LPHWF": "LPHW Flow Temperature (degC)",
    "SETUP_LPHWR": "LPHW Return Temperature (degC)",
    "SETUP_CHWF": "CHW Flow Temperature (degC)",
    "SETUP_CHWR": "CHW Return Temperature (degC)",
    "SETUP_CP": "Specific Heat Capacity of Water (kJ/kgK)",
    "SETUP_N": "EN 442 Radiator Exponent (n)",
    "SETUP_AMBIENT": "Design Ambient Temperature (degC)",
}

# Spilling or post-2019 functions. Rejected by name so the designer's error
# message can be specific rather than "unknown function".
BANNED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "XLOOKUP", "XMATCH", "FILTER", "UNIQUE", "SORT", "SORTBY", "SEQUENCE",
        "RANDARRAY", "LET", "LAMBDA", "TEXTBEFORE", "TEXTAFTER", "TEXTSPLIT",
        "TOCOL", "TOROW", "VSTACK", "HSTACK", "CHOOSECOLS", "CHOOSEROWS",
        "TAKE", "DROP", "EXPAND", "WRAPROWS", "WRAPCOLS", "BYROW", "BYCOL",
        "MAP", "REDUCE", "SCAN", "MAKEARRAY", "ISOMITTED", "ARRAYTOTEXT",
        "GROUPBY", "PIVOTBY", "PERCENTOF",
    }
)


class FormulaError(Exception):
    """Base class for anything wrong with a formula."""


class FormulaSyntaxError(FormulaError):
    """The formula could not be parsed, or references something unresolvable."""


class FormulaEvalError(FormulaError):
    """The formula parsed but could not produce a value for this row.

    Corresponds to an Excel error value. The caller renders it blank, matching
    the ``IFERROR(..., "")`` wrapper the exported workbook puts around every
    derived cell.
    """


class _Blank:
    """Excel's empty cell.

    Distinct from ``None`` so that "no value supplied for this field" and "this
    field is genuinely empty" stay separable. Coerces to 0 in arithmetic and to
    ``""`` in text, as Excel does.
    """

    _instance: "_Blank | None" = None

    def __new__(cls) -> "_Blank":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "BLANK"

    def __bool__(self) -> bool:
        return False


BLANK = _Blank()

Value = float | int | str | bool | _Blank


# ------------------------------------------------------------------- AST ---


class Node:
    """Base class for formula AST nodes."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Num(Node):
    value: float


@dataclass(frozen=True, slots=True)
class Str(Node):
    value: str


@dataclass(frozen=True, slots=True)
class Bool(Node):
    value: bool


@dataclass(frozen=True, slots=True)
class FieldRef(Node):
    """A reference to another column in the same schedule type, by full name."""

    name: str


@dataclass(frozen=True, slots=True)
class Const(Node):
    """A design constant, e.g. ``SETUP_CP``."""

    name: str


@dataclass(frozen=True, slots=True)
class Unary(Node):
    op: str
    operand: Node


@dataclass(frozen=True, slots=True)
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class Func(Node):
    name: str
    args: tuple[Node, ...]


# --------------------------------------------------------------- lexer ---

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<field>\{[^{}]*\})
    | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)
    | (?P<string>"(?:[^"]|"")*")
    | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
    | (?P<op><>|>=|<=|[+\-*/^&=<>(),])
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str
    pos: int


def _tokenise(src: str) -> Iterator[_Token]:
    pos = 0
    n = len(src)
    while pos < n:
        m = _TOKEN_RE.match(src, pos)
        if m is None:
            raise FormulaSyntaxError(
                f"unexpected character {src[pos]!r} at position {pos}"
            )
        kind = m.lastgroup
        assert kind is not None
        if kind != "ws":
            yield _Token(kind, m.group(), m.start())
        pos = m.end()
    yield _Token("end", "", n)


# -------------------------------------------------------------- parser ---

# Excel's precedence, loosest first. '^' is handled separately: it binds tighter
# than unary minus on its left but is right-associative.
_COMPARISON_OPS = {"=", "<>", ">", "<", ">=", "<="}


class _Parser:
    def __init__(self, tokens: Sequence[_Token], source: str) -> None:
        self._tokens = tokens
        self._src = source
        self._i = 0

    # -- token helpers ----------------------------------------------------
    @property
    def _cur(self) -> _Token:
        return self._tokens[self._i]

    def _advance(self) -> _Token:
        tok = self._tokens[self._i]
        self._i += 1
        return tok

    def _accept(self, kind: str, text: str | None = None) -> _Token | None:
        tok = self._cur
        if tok.kind == kind and (text is None or tok.text.upper() == text):
            return self._advance()
        return None

    def _expect(self, kind: str, text: str | None = None) -> _Token:
        tok = self._accept(kind, text)
        if tok is None:
            want = text or kind
            got = self._cur.text or "end of formula"
            raise FormulaSyntaxError(
                f"expected {want!r} but found {got!r} at position {self._cur.pos}"
            )
        return tok

    # -- grammar ----------------------------------------------------------
    def parse(self) -> Node:
        node = self._comparison()
        if self._cur.kind != "end":
            raise FormulaSyntaxError(
                f"unexpected {self._cur.text!r} at position {self._cur.pos}"
            )
        return node

    def _comparison(self) -> Node:
        left = self._concat()
        tok = self._cur
        if tok.kind == "op" and tok.text in _COMPARISON_OPS:
            self._advance()
            return Binary(tok.text, left, self._concat())
        return left

    def _concat(self) -> Node:
        node = self._additive()
        while self._cur.kind == "op" and self._cur.text == "&":
            self._advance()
            node = Binary("&", node, self._additive())
        return node

    def _additive(self) -> Node:
        node = self._multiplicative()
        while self._cur.kind == "op" and self._cur.text in ("+", "-"):
            op = self._advance().text
            node = Binary(op, node, self._multiplicative())
        return node

    def _multiplicative(self) -> Node:
        node = self._unary()
        while self._cur.kind == "op" and self._cur.text in ("*", "/"):
            op = self._advance().text
            node = Binary(op, node, self._unary())
        return node

    def _unary(self) -> Node:
        """Exponentiation, whose operands may carry a sign.

        Excel binds unary minus *tighter* than ``^``, so ``-2^2`` is ``(-2)^2``
        = 4, not ``-(2^2)`` = -4. That differs from Python and from most
        languages, and getting it wrong would make the grid and the exported
        workbook disagree on the same formula.
        """
        base = self._signed()
        if self._cur.kind == "op" and self._cur.text == "^":
            self._advance()
            return Binary("^", base, self._unary())  # right-associative
        return base

    def _signed(self) -> Node:
        if self._cur.kind == "op" and self._cur.text in ("+", "-"):
            op = self._advance().text
            operand = self._signed()
            return operand if op == "+" else Unary("-", operand)
        return self._primary()

    def _primary(self) -> Node:
        tok = self._cur

        if tok.kind == "number":
            self._advance()
            return Num(float(tok.text))

        if tok.kind == "string":
            self._advance()
            return Str(tok.text[1:-1].replace('""', '"'))

        if tok.kind == "field":
            self._advance()
            name = tok.text[1:-1].strip()
            if not name:
                raise FormulaSyntaxError(
                    f"empty field reference {{}} at position {tok.pos}"
                )
            return FieldRef(name)

        if tok.kind == "op" and tok.text == "(":
            self._advance()
            node = self._comparison()
            self._expect("op", ")")
            return node

        if tok.kind == "ident":
            self._advance()
            upper = tok.text.upper()
            if self._cur.kind == "op" and self._cur.text == "(":
                return self._funcall(upper, tok.pos)
            if upper == "TRUE":
                return Bool(True)
            if upper == "FALSE":
                return Bool(False)
            if tok.text in CONSTANTS:
                return Const(tok.text)
            if upper in CONSTANTS:
                return Const(upper)
            raise FormulaSyntaxError(
                f"unknown name {tok.text!r} at position {tok.pos}. "
                f"Reference other columns as {{Field Name}}; the only bare names "
                f"allowed are the design constants: {', '.join(sorted(CONSTANTS))}"
            )

        raise FormulaSyntaxError(
            f"unexpected {tok.text or 'end of formula'!r} at position {tok.pos}"
        )

    def _funcall(self, name: str, pos: int) -> Node:
        if name in BANNED_FUNCTIONS:
            raise FormulaSyntaxError(
                f"{name} is not allowed at position {pos}. It spills or is "
                f"post-2019; the house rule is static formulas only, and openpyxl "
                f"cannot author dynamic arrays reliably."
            )
        if name not in ALLOWED_FUNCTIONS:
            raise FormulaSyntaxError(
                f"unknown function {name} at position {pos}. "
                f"Allowed: {', '.join(sorted(ALLOWED_FUNCTIONS))}"
            )
        self._expect("op", "(")
        args: list[Node] = []
        if not (self._cur.kind == "op" and self._cur.text == ")"):
            args.append(self._comparison())
            while self._accept("op", ","):
                args.append(self._comparison())
        self._expect("op", ")")

        spec = ALLOWED_FUNCTIONS[name]
        if not spec.accepts(len(args)):
            raise FormulaSyntaxError(
                f"{name} takes {spec.arity_text()} but was given {len(args)} "
                f"at position {pos}"
            )
        return Func(name, tuple(args))


def parse(source: str, *, known_fields: Sequence[str] | None = None) -> Node:
    """Parse a house formula into an AST.

    A leading ``=`` is optional and stripped. When ``known_fields`` is given,
    every ``{Field Name}`` must resolve to one of them; forward references are
    fine, since a schedule type's columns are all known before any is rendered.

    Raises :class:`FormulaSyntaxError` on anything malformed, unresolvable or
    banned.
    """
    src = source.strip()
    if src.startswith("="):
        src = src[1:]
    if not src.strip():
        raise FormulaSyntaxError("formula is empty")

    node = _Parser(list(_tokenise(src)), src).parse()

    if known_fields is not None:
        allowed = set(known_fields)
        unknown = sorted(n for n in field_names(node) if n not in allowed)
        if unknown:
            raise FormulaSyntaxError(
                "formula references "
                + ", ".join(f"{{{n}}}" for n in unknown)
                + ", which is not a column in this schedule type"
            )
    return node


# ------------------------------------------------------------ inspection ---


def _walk(node: Node) -> Iterator[Node]:
    yield node
    if isinstance(node, Unary):
        yield from _walk(node.operand)
    elif isinstance(node, Binary):
        yield from _walk(node.left)
        yield from _walk(node.right)
    elif isinstance(node, Func):
        for arg in node.args:
            yield from _walk(arg)


def field_names(node: Node) -> list[str]:
    """Every ``{Field Name}`` the formula references, in first-seen order."""
    seen: dict[str, None] = {}
    for n in _walk(node):
        if isinstance(n, FieldRef):
            seen.setdefault(n.name, None)
    return list(seen)


def constant_names(node: Node) -> list[str]:
    """Every design constant the formula references, in first-seen order."""
    seen: dict[str, None] = {}
    for n in _walk(node):
        if isinstance(n, Const):
            seen.setdefault(n.name, None)
    return list(seen)


# ------------------------------------------------------- Excel emitter ---

# Precedence for parenthesising the emitted Excel. Tighter binding is a higher
# number; we only add brackets where the tree demands them, so the emitted
# formula stays close to what the author wrote.
_PRECEDENCE = {
    "=": 1, "<>": 1, ">": 1, "<": 1, ">=": 1, "<=": 1,
    "&": 2,
    "+": 3, "-": 3,
    "*": 4, "/": 4,
    # Unary minus binds tighter than '^' in Excel; see _Parser._unary.
    "^": 6,
    "unary": 7,
}


def _excel_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def to_excel(node: Node, resolve_field: Callable[[str], str]) -> str:
    """Emit the formula as an Excel expression, without a leading ``=``.

    ``resolve_field`` maps a column name to a cell reference for the row being
    written, e.g. ``'Supply Airflow (l/s)'`` -> ``'$D6'``. Design constants emit
    as their defined names, which the exported workbook points at the hidden
    Config sheet.
    """
    text, _ = _to_excel(node, resolve_field)
    return text


def _to_excel(node: Node, resolve: Callable[[str], str]) -> tuple[str, int]:
    if isinstance(node, Num):
        return _excel_number(node.value), 99
    if isinstance(node, Str):
        escaped = node.value.replace('"', '""')
        return f'"{escaped}"', 99
    if isinstance(node, Bool):
        return ("TRUE" if node.value else "FALSE"), 99
    if isinstance(node, FieldRef):
        return resolve(node.name), 99
    if isinstance(node, Const):
        return node.name, 99
    if isinstance(node, Func):
        args = ",".join(_to_excel(a, resolve)[0] for a in node.args)
        return f"{node.name}({args})", 99
    if isinstance(node, Unary):
        inner, prec = _to_excel(node.operand, resolve)
        if prec < _PRECEDENCE["unary"]:
            inner = f"({inner})"
        return f"-{inner}", _PRECEDENCE["unary"]
    if isinstance(node, Binary):
        prec = _PRECEDENCE[node.op]
        left, lprec = _to_excel(node.left, resolve)
        right, rprec = _to_excel(node.right, resolve)
        if lprec < prec or (lprec == prec and node.op == "^"):
            left = f"({left})"
        # Right operand needs brackets at equal precedence for the
        # non-associative operators, or the meaning changes: a-(b-c) != a-b-c.
        if rprec < prec or (rprec == prec and node.op in ("-", "/")):
            right = f"({right})"
        return f"{left}{node.op}{right}", prec
    raise TypeError(f"cannot emit {type(node).__name__}")


# ---------------------------------------------------- Python evaluator ---


def _num(value: Value, *, op: str) -> float:
    """Coerce an operand to a number the way Excel does in arithmetic."""
    if isinstance(value, _Blank) or value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise FormulaEvalError(
            f"cannot use text {value!r} as a number in {op!r}"
        ) from exc


def _text(value: Value) -> str:
    if isinstance(value, _Blank) or value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _compare(op: str, left: Value, right: Value) -> bool:
    """Excel comparison: numbers numerically, text case-insensitively.

    Excel orders any text above any number; that only shows up in ``<``/``>``
    against mixed types, which house formulas do not do, but matching it costs
    nothing and avoids a surprise.
    """
    lb = isinstance(left, _Blank) or left is None
    rb = isinstance(right, _Blank) or right is None
    l_num = isinstance(left, (int, float)) and not isinstance(left, bool)
    r_num = isinstance(right, (int, float)) and not isinstance(right, bool)

    if lb and rb:
        a: Value = 0.0
        b: Value = 0.0
    elif lb:
        a, b = (0.0, right) if r_num else ("", right)
    elif rb:
        a, b = (left, 0.0) if l_num else (left, "")
    else:
        a, b = left, right

    a_num = isinstance(a, (int, float)) and not isinstance(a, bool)
    b_num = isinstance(b, (int, float)) and not isinstance(b, bool)

    if a_num and b_num:
        x, y = float(a), float(b)  # type: ignore[arg-type]
    elif a_num != b_num:
        # Mixed: text sorts above number.
        if op == "=":
            return False
        if op == "<>":
            return True
        text_is_left = b_num
        return (op in (">", ">=")) if text_is_left else (op in ("<", "<="))
    else:
        x, y = _text(a).upper(), _text(b).upper()  # type: ignore[assignment]

    if op == "=":
        return x == y
    if op == "<>":
        return x != y
    if op == ">":
        return x > y
    if op == "<":
        return x < y
    if op == ">=":
        return x >= y
    return x <= y


def evaluate(
    node: Node,
    values: dict[str, Value],
    constants: dict[str, float] | None = None,
) -> Value:
    """Evaluate the formula for one row.

    ``values`` maps column name to that row's value; a missing key and an empty
    cell are both treated as blank, which is 0 in arithmetic, matching Excel.
    ``constants`` supplies the ``SETUP_*`` design constants.

    Raises :class:`FormulaEvalError` where Excel would produce an error value --
    division by zero, text where a number is needed, a missing constant. The
    caller renders that blank, matching the ``IFERROR(..., "")`` wrapper the
    exported workbook puts around every derived cell.
    """
    consts = constants or {}

    if isinstance(node, Num):
        return node.value
    if isinstance(node, Str):
        return node.value
    if isinstance(node, Bool):
        return node.value
    if isinstance(node, FieldRef):
        got = values.get(node.name, BLANK)
        return BLANK if got is None or got == "" else got
    if isinstance(node, Const):
        if node.name not in consts:
            raise FormulaEvalError(
                f"design constant {node.name} "
                f"({CONSTANTS.get(node.name, 'unknown')}) has no value on this project"
            )
        return consts[node.name]

    if isinstance(node, Unary):
        return -_num(evaluate(node.operand, values, consts), op="-")

    if isinstance(node, Binary):
        op = node.op
        left = evaluate(node.left, values, consts)
        right = evaluate(node.right, values, consts)

        if op in _COMPARISON_OPS:
            return _compare(op, left, right)
        if op == "&":
            return _text(left) + _text(right)

        a, b = _num(left, op=op), _num(right, op=op)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise FormulaEvalError("division by zero")
            return a / b
        if op == "^":
            try:
                result = a**b
            except (OverflowError, ValueError) as exc:
                raise FormulaEvalError(f"cannot raise {a} to the power {b}") from exc
            if isinstance(result, complex):
                raise FormulaEvalError(f"cannot raise {a} to the power {b}")
            return float(result)
        raise TypeError(f"unhandled operator {op!r}")

    if isinstance(node, Func):
        return _call(node, values, consts)

    raise TypeError(f"cannot evaluate {type(node).__name__}")


def _truthy(value: Value) -> bool:
    if isinstance(value, _Blank) or value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().upper()
    if text in ("TRUE", "FALSE"):
        return text == "TRUE"
    if not text:
        return False
    raise FormulaEvalError(f"cannot use text {value!r} as a condition")


def _call(node: Func, values: dict[str, Value], consts: dict[str, float]) -> Value:
    name = node.name

    # Short-circuiting functions evaluate their own arguments: IFERROR must
    # catch errors raised by the guarded branch, and IF must not evaluate the
    # branch it does not take.
    if name == "IF":
        cond = _truthy(evaluate(node.args[0], values, consts))
        if cond:
            return evaluate(node.args[1], values, consts)
        if len(node.args) > 2:
            return evaluate(node.args[2], values, consts)
        return False

    if name in ("IFERROR", "IFNA"):
        try:
            return evaluate(node.args[0], values, consts)
        except FormulaEvalError:
            return evaluate(node.args[1], values, consts)

    if name == "AND":
        return all(_truthy(evaluate(a, values, consts)) for a in node.args)
    if name == "OR":
        return any(_truthy(evaluate(a, values, consts)) for a in node.args)

    args = [evaluate(a, values, consts) for a in node.args]
    return ALLOWED_FUNCTIONS[name].impl(args)


# ------------------------------------------------------ function table ---


@dataclass(frozen=True, slots=True)
class _FuncSpec:
    min_args: int
    max_args: int | None  # None means variadic
    impl: Callable[[list[Value]], Value]

    def accepts(self, count: int) -> bool:
        if count < self.min_args:
            return False
        return self.max_args is None or count <= self.max_args

    def arity_text(self) -> str:
        if self.max_args is None:
            return f"at least {self.min_args} argument(s)"
        if self.min_args == self.max_args:
            return f"exactly {self.min_args} argument(s)"
        return f"{self.min_args} to {self.max_args} arguments"


def _n(v: Value) -> float:
    return _num(v, op="function argument")


def _guard(fn: Callable[..., float]) -> Callable[..., float]:
    """Turn a Python domain error into the Excel error value it corresponds to."""

    def wrapped(*args: float) -> float:
        try:
            return fn(*args)
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            raise FormulaEvalError(str(exc)) from exc

    return wrapped


def _excel_round(value: float, digits: float) -> float:
    """Excel rounds half away from zero; Python rounds half to even."""
    factor = 10 ** int(digits)
    scaled = value * factor
    rounded = math.floor(abs(scaled) + 0.5) * (1 if scaled >= 0 else -1)
    return rounded / factor


def _sequence(args: list[Value]) -> list[float]:
    return [_n(a) for a in args]


ALLOWED_FUNCTIONS: dict[str, _FuncSpec] = {
    # Short-circuiting: impl is never called, _call handles them.
    "IF": _FuncSpec(2, 3, lambda a: BLANK),
    "IFERROR": _FuncSpec(2, 2, lambda a: BLANK),
    "IFNA": _FuncSpec(2, 2, lambda a: BLANK),
    "AND": _FuncSpec(1, None, lambda a: BLANK),
    "OR": _FuncSpec(1, None, lambda a: BLANK),
    "NOT": _FuncSpec(1, 1, lambda a: not _truthy(a[0])),
    # Arithmetic
    "ABS": _FuncSpec(1, 1, lambda a: abs(_n(a[0]))),
    "SQRT": _FuncSpec(1, 1, lambda a: _guard(math.sqrt)(_n(a[0]))),
    "POWER": _FuncSpec(2, 2, lambda a: _guard(lambda x, y: float(x**y))(_n(a[0]), _n(a[1]))),
    "EXP": _FuncSpec(1, 1, lambda a: _guard(math.exp)(_n(a[0]))),
    "LN": _FuncSpec(1, 1, lambda a: _guard(math.log)(_n(a[0]))),
    "LOG10": _FuncSpec(1, 1, lambda a: _guard(math.log10)(_n(a[0]))),
    "LOG": _FuncSpec(
        1, 2,
        lambda a: _guard(math.log)(_n(a[0]), _n(a[1])) if len(a) > 1
        else _guard(math.log10)(_n(a[0])),
    ),
    "MOD": _FuncSpec(2, 2, lambda a: _guard(math.fmod)(_n(a[0]), _n(a[1]))),
    "PI": _FuncSpec(0, 0, lambda a: math.pi),
    # Rounding
    "ROUND": _FuncSpec(2, 2, lambda a: _excel_round(_n(a[0]), _n(a[1]))),
    "ROUNDUP": _FuncSpec(
        2, 2,
        lambda a: math.copysign(
            math.ceil(abs(_n(a[0])) * 10 ** int(_n(a[1]))) / 10 ** int(_n(a[1])),
            _n(a[0]),
        ),
    ),
    "ROUNDDOWN": _FuncSpec(
        2, 2,
        lambda a: math.copysign(
            math.floor(abs(_n(a[0])) * 10 ** int(_n(a[1]))) / 10 ** int(_n(a[1])),
            _n(a[0]),
        ),
    ),
    "INT": _FuncSpec(1, 1, lambda a: float(math.floor(_n(a[0])))),
    "TRUNC": _FuncSpec(1, 2, lambda a: float(math.trunc(_n(a[0])))),
    "CEILING": _FuncSpec(
        2, 2,
        lambda a: _guard(lambda x, s: math.ceil(x / s) * s)(_n(a[0]), _n(a[1])),
    ),
    "FLOOR": _FuncSpec(
        2, 2,
        lambda a: _guard(lambda x, s: math.floor(x / s) * s)(_n(a[0]), _n(a[1])),
    ),
    # Aggregates. Blanks are skipped, as Excel does over a range.
    "MIN": _FuncSpec(1, None, lambda a: min(_sequence(a), default=0.0)),
    "MAX": _FuncSpec(1, None, lambda a: max(_sequence(a), default=0.0)),
    "SUM": _FuncSpec(1, None, lambda a: sum(_sequence(a))),
    "AVERAGE": _FuncSpec(
        1, None,
        lambda a: (
            sum(v for v in _sequence(a)) / len(a)
            if a else _raise_div_zero()
        ),
    ),
    # Predicates
    "ISBLANK": _FuncSpec(1, 1, lambda a: isinstance(a[0], _Blank)),
    "ISNUMBER": _FuncSpec(
        1, 1,
        lambda a: isinstance(a[0], (int, float)) and not isinstance(a[0], bool),
    ),
    "ISTEXT": _FuncSpec(1, 1, lambda a: isinstance(a[0], str) and a[0] != ""),
    # Text
    "TEXT": _FuncSpec(2, 2, lambda a: _text(a[0])),
    "UPPER": _FuncSpec(1, 1, lambda a: _text(a[0]).upper()),
    "LOWER": _FuncSpec(1, 1, lambda a: _text(a[0]).lower()),
    "TRIM": _FuncSpec(1, 1, lambda a: " ".join(_text(a[0]).split())),
    "LEN": _FuncSpec(1, 1, lambda a: float(len(_text(a[0])))),
    "CONCATENATE": _FuncSpec(1, None, lambda a: "".join(_text(v) for v in a)),
}


def _raise_div_zero() -> float:
    raise FormulaEvalError("division by zero")
