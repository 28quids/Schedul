"""Schedule types: the three column kinds, validation, and v1 interop."""

from __future__ import annotations

import pytest

from schedul.core.catalogue import (
    MODEL_REFERENCE,
    CatalogueError,
    Column,
    ScheduleType,
    from_legacy,
    to_legacy,
    validate_catalogue,
    validate_type,
)
from schedul.core.migrate import round_trip_matches
from schedul.core.units import split_unit


def minimal(**kw) -> ScheduleType:
    defaults = dict(
        code="TEST",
        title="Test Schedule",
        columns=[
            Column(kind="input", name="Unit Reference", example="T-01"),
            Column(kind="library", name="Manufacturer", example="Acme"),
        ],
    )
    defaults.update(kw)
    return ScheduleType(**defaults)


def errors(st: ScheduleType, **kw) -> list[str]:
    return [i.message for i in validate_type(st, **kw) if i.severity == "error"]


def test_a_lowercase_code_is_normalised_not_rejected():
    """Typing 'ahu' means AHU. Normalise rather than nag."""
    assert minimal(code="ahu").code == "AHU"
    assert errors(minimal(code="ahu")) == []


class TestPhase1Checkpoint:
    """SPEC.md phase 1: round-trip all eight types to catalogue and back to the
    legacy field shape."""

    def test_legacy_round_trip_is_identical(self, legacy_schema):
        ok, differences = round_trip_matches(legacy_schema)
        assert ok, "\n".join(differences)

    def test_all_eight_types_import(self, catalogue_types):
        assert len(catalogue_types) == 8
        assert [t.code for t in catalogue_types] == [
            "MVHR", "AHU", "FCU", "PUMP", "RAD", "EWH", "SUPGRILLE", "EXTGRILLE",
        ]

    def test_every_imported_type_is_valid(self, catalogue_types):
        issues = validate_catalogue(catalogue_types)
        for code, found in issues.items():
            assert not [i for i in found if i.severity == "error"], f"{code}: {found}"

    def test_the_dead_number_field_is_gone(self, catalogue_types):
        for st in catalogue_types:
            assert not hasattr(st, "number")
            assert "number" not in st.to_dict()


class TestLayout:
    def test_model_reference_sits_between_input_and_library(self, catalogue_types):
        mvhr = next(t for t in catalogue_types if t.code == "MVHR")
        names = [c.name for c in mvhr.layout()]
        assert names.index(MODEL_REFERENCE) == len(mvhr.inputs)
        assert names[len(mvhr.inputs) + 1] == mvhr.library[0].name

    def test_layout_matches_the_real_generated_file(self, catalogue_types):
        """Row 4 of the shipped MVHR sample, read straight out of the workbook."""
        mvhr = next(t for t in catalogue_types if t.code == "MVHR")
        assert [c.name for c in mvhr.layout()][:12] == [
            "Unit Reference", "Location", "Area Served", "Supply Airflow",
            "Extract Airflow", "Supply External Static Pressure",
            "Extract External Static Pressure", "Total Power Input",
            "Summer Bypass", "Duty / Standby", "Model Reference", "Manufacturer",
        ]

    def test_derived_columns_come_last(self, catalogue_types):
        for st in catalogue_types:
            names = [c.name for c in st.layout()]
            if st.derived:
                assert names[-len(st.derived) :] == [c.name for c in st.derived]


class TestUnits:
    @pytest.mark.parametrize(
        "field,expected",
        [
            ("Supply Airflow (l/s)", ("Supply Airflow", "l/s")),
            ("Specific Fan Power (W/(l/s))", ("Specific Fan Power", "W/(l/s)")),
            ("Room Setpoint (degC)", ("Room Setpoint", "°C")),
            ("Free Area (m2)", ("Free Area", "m²")),
            ("Sensible Heat Ratio", ("Sensible Heat Ratio", "")),
        ],
    )
    def test_split_unit(self, field, expected):
        assert split_unit(field) == expected

    @pytest.mark.parametrize(
        "field", ["Filter Grade (BS EN 1886)", "Prepared By (Initials)", "Exponent (n)"]
    )
    def test_a_unit_that_is_not_a_unit_stays_in_the_name(self, field):
        assert split_unit(field) == (field, "")

    def test_units_round_trip_through_storage(self, catalogue_types):
        for st in catalogue_types:
            for col in st.columns:
                # legacy_name must rebuild the original v1 string exactly.
                assert split_unit(col.legacy_name)[0] == col.name


class TestValidation:
    def test_model_reference_may_not_be_defined_by_hand(self):
        st = minimal()
        st.columns.append(Column(kind="library", name=MODEL_REFERENCE))
        assert any("inserted automatically" in e for e in errors(st))

    def test_a_type_needs_at_least_one_input_column(self):
        st = minimal(columns=[Column(kind="library", name="Manufacturer")])
        assert any("input column" in e for e in errors(st))

    def test_a_type_needs_at_least_one_library_column(self):
        st = minimal(columns=[Column(kind="input", name="Unit Reference")])
        assert any("library column" in e for e in errors(st))

    def test_duplicate_column_names_are_rejected(self):
        st = minimal()
        st.columns.append(Column(kind="library", name="Manufacturer"))
        assert any("duplicate" in e for e in errors(st))

    @pytest.mark.parametrize("code", ["HAS SPACE", "1LEADING", "WITH-DASH", "HAS.DOT"])
    def test_bad_codes_are_rejected(self, code):
        assert any("uppercase" in e for e in errors(minimal(code=code)))

    def test_duplicate_codes_across_the_catalogue_are_rejected(self):
        st = minimal(code="AHU")
        assert any("already used" in e for e in errors(st, other_codes=["AHU", "MVHR"]))

    def test_validate_catalogue_flags_a_genuine_duplicate(self):
        from schedul.core.catalogue import validate_catalogue as vc
        found = vc([minimal(code="AHU"), minimal(code="AHU")])
        assert any("already used" in i.message for i in found["AHU"])

    def test_an_unresolvable_field_reference_is_rejected(self):
        st = minimal()
        st.columns.append(
            Column(kind="derived", name="Bad", formula="={Nope}*2", note="n")
        )
        assert any("not a column" in e for e in errors(st))

    def test_a_spilling_function_is_rejected(self):
        st = minimal()
        st.columns.append(
            Column(kind="derived", name="Bad", formula="=XLOOKUP(1,2,3)", note="n")
        )
        assert any("not allowed" in e for e in errors(st))

    def test_a_derived_column_referring_to_itself_is_rejected(self):
        st = minimal()
        st.columns.append(Column(kind="derived", name="Loop", formula="={Loop}+1", note="n"))
        assert any("own column" in e for e in errors(st))

    def test_circular_references_between_derived_columns_are_rejected(self):
        st = minimal()
        st.columns += [
            Column(kind="derived", name="A", formula="={B}+1", note="n"),
            Column(kind="derived", name="B", formula="={A}+1", note="n"),
        ]
        assert any("circular" in e for e in errors(st))

    def test_a_derived_column_may_reference_another_derived_column(self):
        st = minimal()
        st.columns += [
            Column(kind="derived", name="A", formula="={Unit Reference}", note="n"),
            Column(kind="derived", name="B", formula="={A}", note="n"),
        ]
        assert errors(st) == []
        assert [c.name for c in st.evaluation_order()] == ["A", "B"]

    def test_a_valid_type_has_no_errors(self):
        assert errors(minimal()) == []


class TestVersioning:
    def test_bump_records_history(self):
        st = minimal(version=2, updated="2026-09-01")
        st.bump("added Filter Grade", today="2026-09-14")
        assert st.version == 3
        assert st.updated == "2026-09-14"
        assert st.history[-1] == {
            "version": 2, "date": "2026-09-01", "change": "added Filter Grade"
        }


class TestSerialisation:
    def test_dict_round_trip(self, catalogue_types):
        for st in catalogue_types:
            assert ScheduleType.from_dict(st.to_dict()).to_dict() == st.to_dict()

    def test_legacy_round_trip_preserves_field_lists(self, catalogue_types):
        for st in catalogue_types:
            assert from_legacy(to_legacy(st)).to_dict()["columns"] == st.to_dict()["columns"]
