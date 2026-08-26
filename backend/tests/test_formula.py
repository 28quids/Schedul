"""The formula engine: one AST, two backends, and they must never disagree."""

from __future__ import annotations

import pytest

from schedul.core import formula as F
from schedul.core.formula import (
    FormulaEvalError,
    FormulaSyntaxError,
    evaluate,
    field_names,
    parse,
    to_excel,
)

CONSTANTS = {
    "SETUP_LPHWF": 70.0,
    "SETUP_LPHWR": 50.0,
    "SETUP_CHWF": 6.0,
    "SETUP_CHWR": 12.0,
    "SETUP_CP": 4.18,
    "SETUP_N": 1.3,
    "SETUP_AMBIENT": 21.0,
}


def ev(src: str, values: dict | None = None):
    return evaluate(parse(src), values or {}, CONSTANTS)


class TestExcelSemantics:
    """Where Excel differs from Python, the engine follows Excel."""

    @pytest.mark.parametrize(
        "src,expected",
        [
            # Excel binds unary minus tighter than '^', unlike Python.
            ("=-2^2", 4.0),
            ("=-(2^2)", -4.0),
            ("=2^-1", 0.5),
            ("=2^3^2", 512.0),  # right-associative
            ("=10-3-2", 5.0),  # left-associative
            ("=100/10/2", 5.0),
            ("=2*3+4", 10.0),
            ("=2+3*4", 14.0),
            ("=(2+3)*4", 20.0),
        ],
    )
    def test_precedence_and_associativity(self, src, expected):
        assert ev(src) == expected

    def test_concatenation(self):
        assert ev('="a"&"b"') == "ab"
        assert ev("=1&2") == "12"

    def test_round_halves_away_from_zero(self):
        # Excel rounds 0.5 up; Python's round() goes to even.
        assert ev("=ROUND(2.5,0)") == 3.0
        assert ev("=ROUND(-2.5,0)") == -3.0

    def test_blank_is_zero_in_arithmetic(self):
        assert ev("={a}+{b}", {"a": 5}) == 5.0

    def test_comparison_is_case_insensitive(self):
        assert ev('="OK"="ok"') is True


class TestEmitterMatchesEvaluator:
    """Emitted Excel must re-parse to the same value, or the grid and the
    exported workbook drift apart."""

    @pytest.mark.parametrize(
        "src",
        [
            "=-2^2",
            "=2^-1",
            "=2^3^2",
            "=10-3-2",
            "=100/10/2",
            "=1-(2-3)",
            "=8/(4/2)",
            "=(1+2)*(3+4)",
            "=-(1+2)",
            '=IF(1>2,"a","b")',
        ],
    )
    def test_reparsing_emitted_excel_is_stable(self, src):
        node = parse(src)
        emitted = to_excel(node, lambda n: n)
        assert evaluate(parse("=" + emitted), {}, CONSTANTS) == evaluate(
            node, {}, CONSTANTS
        )

    def test_field_references_become_cell_references(self):
        node = parse("={Total Power Input (W)}/{Supply Airflow (l/s)}")
        cols = {"Total Power Input (W)": "$H6", "Supply Airflow (l/s)": "$D6"}
        assert to_excel(node, lambda n: cols[n]) == "$H6/$D6"

    def test_constants_emit_as_defined_names(self):
        node = parse("=SETUP_CP*(SETUP_LPHWF-SETUP_LPHWR)")
        assert to_excel(node, lambda n: n) == "SETUP_CP*(SETUP_LPHWF-SETUP_LPHWR)"

    def test_redundant_brackets_are_dropped_but_meaning_kept(self):
        assert to_excel(parse("=((1+2))*3"), lambda n: n) == "(1+2)*3"


class TestRealHouseFormulas:
    """The formulas that actually ship, checked against hand calculations."""

    def test_specific_fan_power(self):
        got = ev(
            "={Total Power Input (W)}/{Supply Airflow (l/s)}",
            {"Total Power Input (W)": 396, "Supply Airflow (l/s)": 450},
        )
        assert got == pytest.approx(0.88)

    def test_lphw_flow_rate(self):
        got = ev(
            "={Heating Coil Duty (kW)}/(SETUP_CP*(SETUP_LPHWF-SETUP_LPHWR))",
            {"Heating Coil Duty (kW)": 50},
        )
        assert got == pytest.approx(50 / (4.18 * 20))

    def test_radiator_correction_factor(self):
        got = ev(
            "=(((SETUP_LPHWF+SETUP_LPHWR)/2-{Room Design Temperature (degC)})/50)^SETUP_N",
            {"Room Design Temperature (degC)": 21},
        )
        assert got == pytest.approx((((70 + 50) / 2 - 21) / 50) ** 1.3)

    @pytest.mark.parametrize(
        "required,expected", [(700, "OK"), (723, "OK"), (724, "UNDERSIZED"), (900, "UNDERSIZED")]
    )
    def test_radiator_output_check(self, required, expected):
        src = (
            '=IF({Output at dT50 (W)}*((((SETUP_LPHWF+SETUP_LPHWR)/2'
            '-{Room Design Temperature (degC)})/50)^SETUP_N)'
            '>={Required Heat Output (W)},"OK","UNDERSIZED")'
        )
        got = ev(
            src,
            {
                "Output at dT50 (W)": 1000,
                "Room Design Temperature (degC)": 21,
                "Required Heat Output (W)": required,
            },
        )
        assert got == expected

    def test_every_catalogue_formula_parses_and_emits(self, catalogue_types):
        for st in catalogue_types:
            for col in st.derived:
                node = st.parse_formula(col)
                cols = {n: f"$A{i + 6}" for i, n in enumerate(st.field_names)}
                assert to_excel(node, lambda n: cols[n])


class TestErrors:
    def test_division_by_zero_is_an_eval_error(self):
        with pytest.raises(FormulaEvalError):
            ev("={a}/{b}", {"a": 5})

    def test_iferror_catches_it(self):
        assert ev("=IFERROR({a}/{b},0)", {"a": 5}) == 0.0

    def test_if_does_not_evaluate_the_untaken_branch(self):
        # The false branch divides by zero; taking the true branch must not raise.
        assert ev("=IF(1=1,7,{a}/{b})", {"a": 5}) == 7.0

    @pytest.mark.parametrize(
        "src",
        ["=XLOOKUP(1,2,3)", "=LET(x,1,x)", "=FILTER(1,2)", "=TEXTBEFORE(1,2)", "=LAMBDA(x,x)"],
    )
    def test_spilling_and_post_2019_functions_are_rejected(self, src):
        with pytest.raises(FormulaSyntaxError, match="not allowed"):
            parse(src)

    def test_unresolvable_field_reference_is_rejected(self):
        with pytest.raises(FormulaSyntaxError, match="not a column"):
            parse("={Nope}", known_fields=["a", "b"])

    def test_forward_references_are_fine(self):
        # All of a type's columns are known before any is rendered.
        assert parse("={b}+{a}", known_fields=["a", "b"])

    @pytest.mark.parametrize("src", ["=1+", "=(1", "=*2", "={}", "=1 2"])
    def test_syntax_errors_are_rejected(self, src):
        with pytest.raises(FormulaSyntaxError):
            parse(src)

    def test_unknown_function_is_rejected(self):
        with pytest.raises(FormulaSyntaxError, match="unknown function"):
            parse("=FOO(1)")

    def test_wrong_arity_is_rejected(self):
        with pytest.raises(FormulaSyntaxError, match="arguments"):
            parse("=IF(1)")

    def test_bare_name_that_is_not_a_constant_is_rejected(self):
        with pytest.raises(FormulaSyntaxError, match="unknown name"):
            parse("=SETUP_NONSENSE*2")

    def test_missing_constant_is_an_eval_error(self):
        with pytest.raises(FormulaEvalError, match="no value"):
            evaluate(parse("=SETUP_CP*2"), {}, {})


class TestInspection:
    def test_field_names_are_reported_in_order(self):
        node = parse("={b}+{a}*{b}")
        assert field_names(node) == ["b", "a"]

    def test_constant_names_are_reported(self):
        node = parse("=SETUP_CP*SETUP_N")
        assert F.constant_names(node) == ["SETUP_CP", "SETUP_N"]
