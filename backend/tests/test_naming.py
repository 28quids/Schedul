"""Scoped tokens and the document number -- SPEC.md 5.2.

The strongest test available: regenerate the eight shipped v1 filenames and
require an exact match.
"""

from __future__ import annotations

import pytest

from schedul.core.house import DEFAULT_NAMING
from schedul.core.naming import (
    NamingError,
    NamingScheme,
    ResolutionContext,
    filename_safe,
    slug,
    volume_context,
)


@pytest.fixture()
def scheme() -> NamingScheme:
    return NamingScheme.from_dict(DEFAULT_NAMING)


class TestScheme:
    def test_the_default_scheme_is_coherent(self, scheme):
        assert scheme.validate() == []

    def test_pattern_tokens_are_read_in_order(self, scheme):
        assert scheme.pattern_tokens[:3] == ["project_number", "originator", "volume"]

    def test_a_token_used_but_not_defined_is_reported(self):
        s = NamingScheme(pattern="{a}-{b}", tokens={"a": __import__(
            "schedul.core.naming", fromlist=["TokenSpec"]).TokenSpec(value="x")})
        assert any("no such token" in p for p in s.validate())

    def test_dict_round_trip(self, scheme):
        assert NamingScheme.from_dict(scheme.to_dict()).to_dict() == scheme.to_dict()


class TestReproducingV1:
    """Every shipped sample filename, rebuilt from the catalogue."""

    EXPECTED = {
        "MVHR": (10, "Mechanical Ventilation with Heat Recovery Unit Schedule"),
        "AHU": (11, "Air Handling Unit Schedule"),
        "FCU": (12, "Fan Coil Unit Schedule"),
        "PUMP": (13, "Pump Schedule"),
        "RAD": (14, "Radiator Schedule"),
        "EWH": (15, "Electric Water Heater Schedule"),
        "SUPGRILLE": (16, "Supply Air Terminal Schedule"),
        "EXTGRILLE": (17, "Extract Air Terminal Schedule"),
    }

    def test_all_eight_filenames_match_byte_for_byte(self, scheme, sample_schedules):
        on_disk = {p.name for p in sample_schedules.glob("*.xlsx")}
        assert len(on_disk) == 8

        for code, (number, title) in self.EXPECTED.items():
            ctx = ResolutionContext(
                project={"project_number": "Z9A6461Y19"},
                # v1 hardcoded 5_6 on the whole project; volume is per type now.
                type={"volume": "5_6"},
                schedule={"number": number},
            )
            assert scheme.filename(ctx, title) in on_disk, code

    def test_document_number_matches_the_stored_config_value(self, scheme):
        ctx = ResolutionContext(
            project={"project_number": "Z9A6461Y19"},
            type={"volume": "5_6"},
            schedule={"number": 10},
        )
        assert scheme.document_number(ctx) == (
            "Z9A6461Y19-BOV-5_6-PROJECTNUMBER-SC-M-00000010-G00300-XX-XX"
        )


class TestScopeResolution:
    def test_most_specific_scope_wins(self, scheme):
        ctx = ResolutionContext(
            company={"discipline": "C"},
            project={"discipline": "M", "project_number": "P1"},
            schedule={"discipline": "E", "number": 10},
        )
        assert scheme.resolve_token("discipline", ctx).value == "E"
        assert scheme.resolve_token("discipline", ctx).source == "schedule"

    def test_falls_through_to_the_token_default(self, scheme):
        ctx = ResolutionContext(project={"project_number": "P1"}, schedule={"number": 10})
        token = scheme.resolve_token("originator", ctx)
        assert (token.value, token.source) == ("BOV", "default")

    def test_blank_values_do_not_shadow_a_more_general_scope(self, scheme):
        ctx = ResolutionContext(
            project={"discipline": "M", "project_number": "P1"},
            building={"discipline": ""},
            schedule={"number": 10},
        )
        assert scheme.resolve_token("discipline", ctx).value == "M"

    def test_the_number_token_is_zero_filled_to_its_width(self, scheme):
        ctx = ResolutionContext(project={"project_number": "P1"}, schedule={"number": 10})
        assert scheme.resolve_token("number", ctx).value == "00000010"


class TestVolumeFollowsTheType:
    """SPEC.md acceptance step 15: AHU picks up 5_7 and the radiator 5_6,
    without either being set by hand."""

    @pytest.mark.parametrize(
        "code,volume,expected", [("AHU", "5.7", "5_7"), ("RAD", "5.6", "5_6"), ("EWH", "5.3", "5_3")]
    )
    def test_volume_is_filename_safe(self, scheme, code, volume, expected):
        ctx = ResolutionContext(
            project={"project_number": "CM4220"},
            building={"building": "HQ049"},
            type=volume_context(volume, scheme),
            schedule={"number": 10},
        )
        assert f"-{expected}-" in scheme.document_number(ctx)

    def test_volume_comes_from_the_type_scope(self, scheme, catalogue_types):
        ahu = next(t for t in catalogue_types if t.code == "AHU")
        rad = next(t for t in catalogue_types if t.code == "RAD")
        assert ahu.volume == "5.7"
        assert rad.volume == "5.6"


class TestBuildingScope:
    def test_building_differentiates_two_documents_sharing_a_number(self, scheme):
        """SPEC.md 5.3: numbering restarts per building and that is correct,
        because the building token already makes the numbers distinct."""
        def docnum(building: str) -> str:
            return scheme.document_number(
                ResolutionContext(
                    project={"project_number": "CM4220"},
                    building={"building": building},
                    type={"volume": "5_7"},
                    schedule={"number": 10},
                )
            )

        assert docnum("HQ049") != docnum("HQ014")
        assert "00000010" in docnum("HQ049") and "00000010" in docnum("HQ014")


class TestFailures:
    def test_a_missing_token_value_is_refused_not_silently_blanked(self, scheme):
        ctx = ResolutionContext(schedule={"number": 10})  # no project_number
        with pytest.raises(NamingError, match="no value"):
            scheme.document_number(ctx)

    def test_preview_reports_the_problem_instead_of_raising(self, scheme):
        result = scheme.preview(ResolutionContext(schedule={"number": 10}), "X Schedule")
        assert result["error"] and result["document_number"] == ""
        assert result["tokens"], "the preview still shows what did resolve"


class TestHelpers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Fan Coil Unit Schedule", "Fan_Coil_Unit_Schedule"),
            ("Mechanical Ventilation with Heat Recovery Unit Schedule",
             "Mechanical_Ventilation_with_Heat_Recovery_Unit_Schedule"),
            ("  odd   spacing  ", "odd_spacing"),
        ],
    )
    def test_slug(self, text, expected):
        assert slug(text) == expected

    def test_filename_safe_replaces_the_dot(self):
        assert filename_safe("5.7") == "5_7"
