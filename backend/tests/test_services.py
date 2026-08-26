"""The service layer against a real database.

Walks SPEC.md section 12's acceptance steps that survive the architecture
change, since they were always the right tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from schedul.core.revisions import sort_key
from schedul.db.models import Base, RevisionRow, Schedule, ScheduleRow
from schedul.db.session import make_engine
from schedul.services import library, projects
from schedul.services.converters import type_from_row, design_constants_for
from schedul.services.grid import build_grid, editable_payload
from schedul.services.projects import ServiceError
from schedul.services.seed import seed_organisation


@pytest.fixture()
def session(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s


@pytest.fixture()
def org(session, repo_root):
    return seed_organisation(
        session, "Test Practice", "test", schema_path=repo_root / "vendor" / "schema.json"
    )


@pytest.fixture()
def project(session, org):
    from schedul.db.models import Project

    p = Project(
        organisation_id=org.id,
        name="Test Job",
        number="CM4220",
        client="A Client",
    )
    session.add(p)
    session.flush()
    return p


def type_row(session, org, code):
    from sqlalchemy import select
    from schedul.db.models import ScheduleTypeRow

    return session.scalar(
        select(ScheduleTypeRow).where(
            ScheduleTypeRow.organisation_id == org.id, ScheduleTypeRow.code == code
        )
    )


class TestSeed:
    def test_a_new_organisation_gets_a_full_catalogue(self, session, org):
        codes = {t.code for t in org.schedule_types}
        assert {"MVHR", "AHU", "FCU", "PUMP", "RAD", "EWH"} <= codes
        assert "RADPANEL" in codes, "the ninth type from SPEC.md 1a"

    def test_the_radiant_panel_type_carries_its_own_notes(self, session, org):
        rp = type_from_row(type_row(session, org, "RADPANEL"))
        assert len(rp.notes) == 4
        assert any("Merriott" in n for n in rp.notes)

    def test_two_organisations_do_not_share_a_catalogue(self, session, repo_root):
        a = seed_organisation(session, "A", "a", schema_path=repo_root / "vendor" / "schema.json")
        b = seed_organisation(session, "B", "b", schema_path=repo_root / "vendor" / "schema.json")
        assert {t.id for t in a.schedule_types}.isdisjoint({t.id for t in b.schedule_types})


class TestBuildingsAndNumbering:
    def test_acceptance_step_4_three_schedules_numbered_from_ten(self, session, project):
        hq049 = projects.add_building(session, project, "HQ049", "Main Building")
        for code in ("MVHR", "FCU", "AHU"):
            projects.add_schedule(session, hq049, code)
        assert [s.number for s in hq049.live_schedules] == [10, 11, 12]

    def test_acceptance_step_6_numbering_restarts_in_a_second_building(
        self, session, project
    ):
        hq049 = projects.add_building(session, project, "HQ049")
        for code in ("MVHR", "FCU", "AHU"):
            projects.add_schedule(session, hq049, code)

        hq014 = projects.add_building(session, project, "HQ014")
        for code in ("MVHR", "AHU", "PUMP"):
            projects.add_schedule(session, hq014, code)

        assert [s.number for s in hq014.live_schedules] == [10, 11, 12]
        assert [s.number for s in hq049.live_schedules] == [10, 11, 12]

    def test_document_numbers_stay_distinct_across_buildings(self, session, project):
        hq049 = projects.add_building(session, project, "HQ049")
        hq014 = projects.add_building(session, project, "HQ014")
        a = projects.add_schedule(session, hq049, "MVHR")
        b = projects.add_schedule(session, hq014, "MVHR")
        assert a.number == b.number == 10
        assert a.docnum != b.docnum
        assert "HQ049" in a.docnum and "HQ014" in b.docnum

    def test_acceptance_step_8_a_retired_number_is_not_reused(self, session, project):
        hq049 = projects.add_building(session, project, "HQ049")
        for code in ("MVHR", "FCU", "AHU"):
            projects.add_schedule(session, hq049, code)

        fcu = next(s for s in hq049.live_schedules if s.code == "FCU")
        projects.archive_schedule(session, fcu)
        assert 11 in hq049.retired_numbers

        ewh = projects.add_schedule(session, hq049, "EWH")
        assert ewh.number == 13, "must not reuse 11"

    def test_archiving_keeps_the_data(self, session, project):
        hq049 = projects.add_building(session, project, "HQ049")
        mvhr = projects.add_schedule(session, hq049, "MVHR")
        session.add(ScheduleRow(schedule_id=mvhr.id, position=0, values={"Unit Reference": "M-1"}))
        session.flush()

        projects.archive_schedule(session, mvhr)
        session.refresh(mvhr)
        assert mvhr.archived
        assert mvhr not in hq049.live_schedules
        assert len(mvhr.rows) == 1, "remove means remove from the record, not delete data"

    def test_the_same_type_can_be_added_again_after_archiving(self, session, project):
        hq049 = projects.add_building(session, project, "HQ049")
        first = projects.add_schedule(session, hq049, "MVHR")
        projects.archive_schedule(session, first)
        second = projects.add_schedule(session, hq049, "MVHR")
        assert second.id != first.id
        assert second.number == 11

    def test_one_schedule_of_each_type_per_building(self, session, project):
        hq049 = projects.add_building(session, project, "HQ049")
        projects.add_schedule(session, hq049, "MVHR")
        with pytest.raises(ServiceError, match="already has"):
            projects.add_schedule(session, hq049, "MVHR")

    def test_acceptance_step_15_volume_follows_the_type(self, session, project):
        hq049 = projects.add_building(session, project, "HQ049")
        ahu = projects.add_schedule(session, hq049, "AHU")
        rad = projects.add_schedule(session, hq049, "RAD")
        assert "-5_7-" in ahu.docnum, "AHU is ventilation"
        assert "-5_6-" in rad.docnum, "radiators are heating and cooling"


class TestCloneBuilding:
    def test_acceptance_step_7_clone_copies_the_selection_not_the_data(
        self, session, project
    ):
        hq014 = projects.add_building(session, project, "HQ014")
        for code in ("MVHR", "AHU", "PUMP"):
            projects.add_schedule(session, hq014, code)

        mvhr = next(s for s in hq014.live_schedules if s.code == "MVHR")
        session.add(
            ScheduleRow(schedule_id=mvhr.id, position=0, values={"Unit Reference": "SECRET"})
        )
        session.flush()

        nb17 = projects.clone_building(
            session, project, hq014, "NB17", "New Block", ["MVHR", "AHU", "EWH"]
        )
        assert [s.code for s in nb17.live_schedules] == ["MVHR", "AHU", "EWH"]
        assert [s.number for s in nb17.live_schedules] == [10, 11, 12]

        cloned_mvhr = next(s for s in nb17.live_schedules if s.code == "MVHR")
        assert cloned_mvhr.rows == [], "clone never copies filled-in data"

    def test_cloning_an_absent_type_is_refused(self, session, project):
        src = projects.add_building(session, project, "HQ014")
        projects.add_schedule(session, src, "MVHR")
        with pytest.raises(ServiceError, match="no schedule"):
            projects.clone_building(session, project, src, "NB17", "", ["MVHR", "CHIL"])


class TestRenumbering:
    @pytest.fixture()
    def building(self, session, project):
        b = projects.add_building(session, project, "HQ049")
        for code in ("MVHR", "FCU", "AHU"):
            projects.add_schedule(session, b, code)
        return b

    def test_swap_produces_a_plan_then_applies(self, session, building):
        plan = projects.plan_operation(session, building, "swap", code="MVHR", other_code="AHU")
        assert plan.can_apply
        assert projects.apply_plan(session, building, plan) == 2

        numbers = {s.code: s.number for s in building.live_schedules}
        assert numbers["MVHR"] == 12 and numbers["AHU"] == 10

    def test_applying_a_swap_refreshes_document_numbers(self, session, building):
        before = {s.code: s.docnum for s in building.live_schedules}
        plan = projects.plan_operation(session, building, "swap", code="MVHR", other_code="AHU")
        projects.apply_plan(session, building, plan)
        after = {s.code: s.docnum for s in building.live_schedules}
        assert after["MVHR"] != before["MVHR"]
        assert "00000012" in after["MVHR"]

    def test_insert_shifts_the_others_up(self, session, building):
        projects.add_schedule(session, building, "EWH")  # 13
        plan = projects.plan_operation(session, building, "insert", code="EWH", number=11)
        projects.apply_plan(session, building, plan)
        assert {s.code: s.number for s in building.live_schedules} == {
            "MVHR": 10, "EWH": 11, "FCU": 12, "AHU": 13
        }

    def test_compact_closes_a_gap(self, session, building):
        fcu = next(s for s in building.live_schedules if s.code == "FCU")
        projects.archive_schedule(session, fcu)
        plan = projects.plan_operation(session, building, "compact")
        projects.apply_plan(session, building, plan)
        assert [s.number for s in building.live_schedules] == [10, 11]

    def test_acceptance_step_16_an_issued_schedule_refuses_to_renumber(
        self, session, building
    ):
        mvhr = next(s for s in building.live_schedules if s.code == "MVHR")
        for i, code in enumerate(("P01", "P02")):
            session.add(
                RevisionRow(
                    schedule_id=mvhr.id, position=i, code=code,
                    status="S2 - Suitable for Information", sort_key=sort_key(code),
                )
            )
        session.flush()
        session.refresh(mvhr)

        plan = projects.plan_operation(session, building, "set", code="MVHR", number=20)
        assert not plan.can_apply
        assert "issued" in plan.blocked[0].blocked

        with pytest.raises(ServiceError, match="blocked"):
            projects.apply_plan(session, building, plan)

    def test_the_lock_can_be_overridden_explicitly(self, session, building):
        mvhr = next(s for s in building.live_schedules if s.code == "MVHR")
        for i, code in enumerate(("P01", "P02")):
            session.add(
                RevisionRow(schedule_id=mvhr.id, position=i, code=code, sort_key=sort_key(code))
            )
        session.flush()
        session.refresh(mvhr)

        plan = projects.plan_operation(
            session, building, "set", code="MVHR", number=20, allow_locked=["MVHR"]
        )
        assert plan.can_apply
        assert projects.apply_plan(session, building, plan) == 1


class TestBuildingRename:
    def test_acceptance_step_13_rename_touches_only_that_building(self, session, project):
        hq049 = projects.add_building(session, project, "HQ049")
        hq014 = projects.add_building(session, project, "HQ014")
        for code in ("MVHR", "AHU"):
            projects.add_schedule(session, hq049, code)
            projects.add_schedule(session, hq014, code)

        untouched = {s.code: s.docnum for s in hq049.live_schedules}

        plan = projects.rename_building_plan(session, hq014, "HQ015")
        assert len(plan.changes) == 2
        assert all("HQ015" in c.new_docnum for c in plan.changes)

        projects.apply_building_rename(session, hq014, "HQ015")
        assert hq014.ref == "HQ015"
        assert all("HQ015" in s.docnum for s in hq014.live_schedules)
        assert {s.code: s.docnum for s in hq049.live_schedules} == untouched

    def test_renaming_onto_an_existing_ref_is_refused(self, session, project):
        projects.add_building(session, project, "HQ049")
        hq014 = projects.add_building(session, project, "HQ014")
        with pytest.raises(ServiceError, match="already has"):
            projects.apply_building_rename(session, hq014, "HQ049")

    def test_renaming_a_building_with_issued_schedules_is_refused(self, session, project):
        b = projects.add_building(session, project, "HQ014")
        s = projects.add_schedule(session, b, "MVHR")
        session.add(RevisionRow(schedule_id=s.id, position=0, code="C01", sort_key=sort_key("C01")))
        session.flush()
        session.refresh(s)
        with pytest.raises(ServiceError, match="issued"):
            projects.apply_building_rename(session, b, "HQ015")


class TestAudit:
    def test_acceptance_step_18_a_clean_building_audits_clean(self, session, project):
        b = projects.add_building(session, project, "HQ049")
        for code in ("MVHR", "AHU"):
            projects.add_schedule(session, b, code)
        assert projects.run_audit(session, b) == []

    def test_a_stored_number_that_drifts_from_the_tokens_is_reported(
        self, session, project
    ):
        b = projects.add_building(session, project, "HQ049")
        s = projects.add_schedule(session, b, "MVHR")
        s.docnum = "SOMETHING-ELSE"
        session.flush()
        issues = projects.run_audit(session, b)
        assert any(i.kind == "docnum-drift" for i in issues)

    def test_a_stale_type_version_is_reported(self, session, project, org):
        b = projects.add_building(session, project, "HQ049")
        projects.add_schedule(session, b, "MVHR")
        row = type_row(session, org, "MVHR")
        row.version = 5
        session.flush()
        assert any(i.kind == "stale-type" for i in projects.run_audit(session, b))


class TestGrid:
    @pytest.fixture()
    def schedule(self, session, project):
        b = projects.add_building(session, project, "HQ049")
        return projects.add_schedule(session, b, "MVHR")

    def test_derived_columns_are_computed_from_typed_values(
        self, session, project, org, schedule
    ):
        st = type_from_row(schedule.schedule_type)
        session.add(
            ScheduleRow(
                schedule_id=schedule.id,
                position=0,
                values={
                    "Unit Reference": "MVHR-01",
                    "Supply Airflow (l/s)": 450,
                    "Extract Airflow (l/s)": 450,
                    "Total Power Input (W)": 396,
                },
            )
        )
        session.flush()
        session.refresh(schedule)

        house = projects.house_standard_for(session, org.id)
        constants = design_constants_for(project, house)
        from schedul.services.converters import constant_aliases

        grid = build_grid(session, schedule, st, org.id, constant_aliases(constants))
        row = grid.rows[0]
        assert row.value("Total Airflow (l/s)") == 900
        assert row.value("Specific Fan Power (W/(l/s))") == pytest.approx(0.88)

    def test_library_columns_come_from_the_equipment_library(
        self, session, project, org, schedule
    ):
        st = type_from_row(schedule.schedule_type)
        library.save_equipment(
            session, org.id, st, "MVHR-EX-01",
            {"Manufacturer": "Systemair", "Length (mm)": 1200},
        )
        session.add(
            ScheduleRow(
                schedule_id=schedule.id, position=0,
                values={"Unit Reference": "M-1", "Model Reference": "MVHR-EX-01"},
            )
        )
        session.flush()
        session.refresh(schedule)

        grid = build_grid(session, schedule, st, org.id, {})
        assert grid.rows[0].value("Manufacturer") == "Systemair"
        assert grid.rows[0].value("Length (mm)") == 1200

    def test_an_unknown_model_reference_is_reported_not_silently_blank(
        self, session, project, org, schedule
    ):
        st = type_from_row(schedule.schedule_type)
        session.add(
            ScheduleRow(
                schedule_id=schedule.id, position=0,
                values={"Unit Reference": "M-1", "Model Reference": "NOPE"},
            )
        )
        session.flush()
        session.refresh(schedule)

        grid = build_grid(session, schedule, st, org.id, {})
        cell = grid.rows[0].cells["Manufacturer"]
        assert cell.problem and "not in the equipment library" in cell.problem

    def test_an_empty_row_renders_blank_not_zero(self, session, project, org, schedule):
        st = type_from_row(schedule.schedule_type)
        session.add(ScheduleRow(schedule_id=schedule.id, position=0, values={}))
        session.flush()
        session.refresh(schedule)

        grid = build_grid(session, schedule, st, org.id, {})
        assert grid.rows[0].value("Total Airflow (l/s)") is None

    def test_computed_columns_cannot_be_written_by_the_client(self, session, schedule):
        st = type_from_row(schedule.schedule_type)
        cleaned = editable_payload(
            {
                "Unit Reference": "M-1",
                "Manufacturer": "FORGED",
                "Total Airflow (l/s)": 99999,
            },
            st,
        )
        assert cleaned == {"Unit Reference": "M-1"}


class TestLibraryReview:
    @pytest.fixture()
    def mvhr(self, session, org):
        return type_from_row(type_row(session, org, "MVHR"))

    def test_a_new_entry_is_live_immediately(self, session, org, mvhr):
        entry, findings = library.save_equipment(
            session, org.id, mvhr, "M-1", {"Manufacturer": "Systemair"}
        )
        assert entry.review_state == "live"
        assert any(f.kind == "NEW" for f in findings)

    def test_spelling_drift_is_detected(self, session, org, mvhr):
        library.save_equipment(session, org.id, mvhr, "M-1", {"Manufacturer": "Grundfos"})
        _, findings = library.save_equipment(
            session, org.id, mvhr, "M-2", {"Manufacturer": "GRUNDFOS"}
        )
        assert any(f.kind == "DRIFT" for f in findings)

    def test_a_conflicting_value_for_the_same_reference_is_flagged(self, session, org, mvhr):
        library.save_equipment(session, org.id, mvhr, "M-1", {"Manufacturer": "Systemair"})
        _, findings = library.save_equipment(
            session, org.id, mvhr, "M-1", {"Manufacturer": "Vent-Axia"}
        )
        assert any(f.kind == "CONFLICT" for f in findings)

    def test_an_identical_resubmission_is_not_worth_a_reviewers_time(
        self, session, org, mvhr
    ):
        library.save_equipment(session, org.id, mvhr, "M-1", {"Manufacturer": "Systemair"})
        entry, findings = library.save_equipment(
            session, org.id, mvhr, "M-1", {"Manufacturer": "Systemair"}
        )
        assert any(f.kind == "DUPLICATE" for f in findings)
        assert not [f for f in entry.flags if f.kind == "DUPLICATE"]

    def test_the_queue_ranks_conflicts_above_incomplete_entries(self, session, org, mvhr):
        library.save_equipment(session, org.id, mvhr, "M-1", {"Manufacturer": "Systemair"})
        library.save_equipment(session, org.id, mvhr, "M-1", {"Manufacturer": "Other"})
        library.save_equipment(session, org.id, mvhr, "M-2", {"Manufacturer": "Vent-Axia"})

        queue = library.review_queue(session, org.id)
        assert queue[0]["model_reference"] == "M-1"
        assert queue[0]["flags"][0]["kind"] in ("CONFLICT", "DRIFT")

    def test_only_library_columns_are_stored(self, session, org, mvhr):
        entry, _ = library.save_equipment(
            session, org.id, mvhr, "M-1",
            {"Manufacturer": "Systemair", "Unit Reference": "MVHR-01", "Total Airflow (l/s)": 900},
        )
        assert set(entry.values) == {"Manufacturer"}

    def test_rejecting_hides_an_entry_from_lookups(self, session, org, mvhr):
        entry, _ = library.save_equipment(
            session, org.id, mvhr, "M-1", {"Manufacturer": "Systemair"}
        )
        library.set_review_state(session, entry.id, "rejected")

        from schedul.services.grid import library_index

        assert "M-1" not in library_index(session, org.id, "MVHR")
