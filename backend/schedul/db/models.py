"""The database schema. The database is the record; a workbook is an export.

This is the inversion from SPEC.md, and it is what removes the download-edit-
upload round trip: once the data lives here, an engineer types into the grid and
the equipment library is read directly, with no macro and no file to shuttle.

**Organisation is the top-level tenant.** Every catalogue entry, project and
piece of equipment hangs off one. That is SPEC.md 4.5's ambition -- "a second
firm is a second profile, not a fork" -- enforced by a foreign key rather than by
discipline. Retro-fitting a tenant boundary is expensive; having it from the
start costs almost nothing, and it runs single-tenant on localhost today.

Per-row schedule data and per-type column definitions are JSON. The shape of a
schedule is defined by its type and versioned, so a column-per-field table would
mean a migration every time someone adds a field in the designer. SQLite takes
JSON natively and PostgreSQL will take it as JSONB.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = [
    "Base",
    "Organisation",
    "HouseStandardRow",
    "ScheduleTypeRow",
    "Project",
    "Building",
    "Schedule",
    "ScheduleRow",
    "ScheduleEdit",
    "RevisionRow",
    "Equipment",
    "EquipmentFlag",
    "EquipmentChange",
    "ChangeEvent",
]


def _uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ----------------------------------------------------------------- tenant ---


class Organisation(TimestampMixin, Base):
    """One firm. The tenant boundary for everything below."""

    __tablename__ = "organisation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)

    house_standard: Mapped["HouseStandardRow"] = relationship(
        back_populates="organisation", uselist=False, cascade="all, delete-orphan"
    )
    schedule_types: Mapped[list["ScheduleTypeRow"]] = relationship(
        back_populates="organisation", cascade="all, delete-orphan"
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="organisation", cascade="all, delete-orphan"
    )
    equipment: Mapped[list["Equipment"]] = relationship(
        back_populates="organisation", cascade="all, delete-orphan"
    )


class HouseStandardRow(TimestampMixin, Base):
    """One firm's conventions: naming pattern, colours, notes, constants.

    Everything that varies between firms lives here and nowhere else. A
    company-specific value found outside this table is a bug.
    """

    __tablename__ = "house_standard"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(200), default="Default house standard")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    organisation: Mapped[Organisation] = relationship(back_populates="house_standard")


# -------------------------------------------------------------- catalogue ---


class ScheduleTypeRow(TimestampMixin, Base):
    """One catalogue entry: the reusable definition of a kind of schedule.

    ``columns`` holds the ordered list of input / library / derived columns in
    the shape ``core.catalogue.Column`` serialises to. ``version`` is pinned by
    every schedule built from it, so editing a type never silently invalidates
    an issued schedule.
    """

    __tablename__ = "schedule_type"
    __table_args__ = (UniqueConstraint("organisation_id", "code", name="uq_type_code_per_org"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    short: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    volume: Mapped[str] = mapped_column(String(16), default="")
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organisation: Mapped[Organisation] = relationship(back_populates="schedule_types")
    schedules: Mapped[list["Schedule"]] = relationship(back_populates="schedule_type")


# ----------------------------------------------------------------- project ---


class Project(TimestampMixin, Base):
    """A job. Carries what is common across its buildings.

    The ten project fields are columns rather than JSON because they are fixed
    by the house format -- ``MAINPROJECTINFO``'s ``Setup`` sheet is a key/value
    contract the exported workbook depends on.
    """

    __tablename__ = "project"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(300), default="")
    number: Mapped[str] = mapped_column(String(80), default="")
    client: Mapped[str] = mapped_column(String(300), default="")
    site_address: Mapped[str] = mapped_column(Text, default="")
    architect: Mapped[str] = mapped_column(String(300), default="")
    main_contractor: Mapped[str] = mapped_column(String(300), default="")
    riba_stage: Mapped[str] = mapped_column(String(40), default="Stage 4")
    prepared_by: Mapped[str] = mapped_column(String(40), default="")
    checked_by: Mapped[str] = mapped_column(String(40), default="")
    approved_by: Mapped[str] = mapped_column(String(40), default="")

    #: Project-scope token values, overriding the house standard's defaults.
    naming_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: Overrides the house standard's design constants for this job.
    design_constants: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: Extra columns this project adds on top of a catalogue type, keyed by type
    #: code. Additions only -- a project cannot remove or reorder base columns,
    #: or two projects' schedules of the same type stop being comparable.
    type_extras: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: What this job's documents show, over the organisation's branding.
    #:
    #: Only the parts a job legitimately differs on: which cover and revision
    #: fields appear, in what order, and the cover's subtitle. Fonts, colours
    #: and the logo stay house standard -- the point of a house standard is that
    #: every document that leaves the office looks like it came from the same
    #: place, and a per-project font would quietly end that.
    branding_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    #: Notes this job adds under the organisation's own, on every schedule in it.
    #: The middle layer of core.notes' organisation -> project -> type -> schedule
    #: resolution. Empty means the project adds nothing, which is the usual case.
    notes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    organisation: Mapped[Organisation] = relationship(back_populates="projects")
    buildings: Mapped[list["Building"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="Building.position",
    )

    @property
    def project_fields(self) -> dict[str, str]:
        """The ten fields, in the key order the Setup sheet expects."""
        return {
            "Client": self.client,
            "Project Name": self.name,
            "Project Number": self.number,
            "Site Address": self.site_address,
            "Architect": self.architect,
            "Main Contractor": self.main_contractor,
            "RIBA Stage": self.riba_stage,
            "Prepared By": self.prepared_by,
            "Checked By": self.checked_by,
            "Approved By": self.approved_by,
        }


class Building(TimestampMixin, Base):
    """A block. Owns its schedules, and its numbering restarts from the start.

    A building's ``ref`` is an independent code from the client or asset register
    (``HQ049``, ``NB17``), not derived from the project number. It is what the
    ``-PROJECTNUMBER-`` placeholder in the v1 sample files actually is.

    Buildings on the same job overlap without matching: HQ049 has gas boilers,
    HQ014 has ASHPs. A building is not a copy of a template.
    """

    __tablename__ = "building"
    __table_args__ = (UniqueConstraint("project_id", "ref", name="uq_building_ref_per_project"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ref: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(300), default="")
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    naming_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: Numbers used and given up. Never reallocated automatically (SPEC.md 5.4).
    retired_numbers: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)

    project: Mapped[Project] = relationship(back_populates="buildings")
    schedules: Mapped[list["Schedule"]] = relationship(
        back_populates="building",
        cascade="all, delete-orphan",
        order_by="Schedule.number",
    )

    @property
    def live_schedules(self) -> list["Schedule"]:
        """Schedules still in the record. Archived ones keep their data."""
        return [s for s in self.schedules if not s.archived]

    @property
    def label(self) -> str:
        """``HQ049 - Main Building``, as the cover and revision page show it."""
        return f"{self.ref} - {self.name}" if self.name else self.ref


class Schedule(TimestampMixin, Base):
    """One schedule document: one type, in one building, at one number.

    Removal is a soft delete. SPEC.md safety rule 3 says "remove" means remove
    from the record and never destroys a user's data -- which was free when the
    data lived in a file left on disk, and has to be deliberate now that the
    database *is* the record. An archived schedule keeps its rows and can be
    restored; its number is still retired and is not reallocated.
    """

    __tablename__ = "schedule"
    __table_args__ = (
        # 'deleted_marker' is '' while live and the schedule's own id once
        # archived, so uniqueness binds live schedules only and an archived one
        # never blocks re-adding the same type.
        UniqueConstraint(
            "building_id", "code", "deleted_marker", name="uq_schedule_code_per_building"
        ),
        # Volume is part of the key so per-volume sequences can coexist:
        # 5.2-00001 and 5.3-00001 are different documents. With numbering scoped
        # to the building, every schedule shares one volume slot in practice
        # because allocation never hands out a duplicate.
        UniqueConstraint(
            "building_id", "volume", "number", "deleted_marker",
            name="uq_schedule_number_per_building",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    building_id: Mapped[str] = mapped_column(
        ForeignKey("building.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schedule_type_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_type.id"), nullable=False, index=True
    )

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    docnum: Mapped[str] = mapped_column(String(300), default="")
    #: The type version this schedule was built against, pinned at creation.
    type_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: allocated -> built. Recorded before rendering so a crashed build leaves a
    #: reserved number rather than an orphan.
    state: Mapped[str] = mapped_column(String(20), default="allocated", nullable=False)
    #: Schedule-scope token overrides. Rare, but the most specific scope.
    naming_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: The volume this schedule was allocated under, copied from the type at
    #: creation. Denormalised so numbering can scope to it and so changing a
    #: type's volume later cannot silently re-file an existing schedule.
    volume: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    #: Columns this one schedule hides, keyed by column name.
    #:
    #: ``{"Price (GBP)": {"pdf": False, "xlsx": False}}`` -- an absent target
    #: means shown, so a schedule saved before this existed needs no migration.
    #: Kept on the schedule rather than the type because "do not put the cost on
    #: the client's copy of this one" is a decision about one document, and
    #: pushing it up to the catalogue would change every schedule in the
    #: practice to answer a question about one of them.
    column_visibility: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    #: This schedule's own notes, or None to inherit the resolved layers above.
    #:
    #: Null rather than an empty list on purpose: "inherit" and "deliberately no
    #: notes at all" are different answers, and reverting to the project default
    #: has to be able to say the first one.
    notes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: '' while live; the schedule's own id once archived. See __table_args__.
    deleted_marker: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    archived_at: Mapped[_dt.datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def archived(self) -> bool:
        return self.deleted_marker != ""

    building: Mapped[Building] = relationship(back_populates="schedules")
    schedule_type: Mapped[ScheduleTypeRow] = relationship(back_populates="schedules")
    rows: Mapped[list["ScheduleRow"]] = relationship(
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="ScheduleRow.position",
    )
    revisions: Mapped[list["RevisionRow"]] = relationship(
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="RevisionRow.position",
    )


class ScheduleRow(TimestampMixin, Base):
    """One line of equipment on a schedule.

    ``values`` is keyed by column name and holds only what the user typed --
    input columns and the Model Reference. Library columns are looked up from
    the equipment library and derived columns are calculated, so storing either
    would be storing a stale copy of something already known.
    """

    __tablename__ = "schedule_row"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedule.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: Library values this row deliberately diverges from, keyed by column name.
    #:
    #: Kept separate from ``values`` on purpose. Anything a client sends for a
    #: library column is stripped, because accepting it would let a stale or
    #: forged computed value be stored and rendered as fact. A value here is
    #: unambiguously a deliberate override, which keeps that guard intact.
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    schedule: Mapped[Schedule] = relationship(back_populates="rows")


class ScheduleEdit(Base):
    """One undoable edit to a schedule's rows.

    The risky grid operations -- paste, delete, duplicate, fill, a bulk override
    change -- rewrite several rows at once, and until now the only way back was
    retyping. Each records the rows before and after it, so undo is a restore
    rather than an inverse operation that has to be derived per action.

    Storing the whole row set is deliberate. A schedule is tens of rows of small
    JSON, so a snapshot costs almost nothing, and an inverse-operation journal
    would have to be right for every action separately -- which is exactly the
    kind of thing that is subtly wrong for one action and loses somebody's work.
    """

    __tablename__ = "schedule_edit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedule.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Position in this schedule's undo stack. Recorded in Python because
    #: SQLite's clock ties when two edits land in the same second.
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    at: Mapped[_dt.datetime] = mapped_column(
        DateTime,
        default=lambda: _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
        nullable=False,
    )
    #: paste | delete_rows | duplicate_row | fill | cells | add_row
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    after: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    #: True once undone. A new edit discards these, as a spreadsheet does.
    undone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RevisionRow(TimestampMixin, Base):
    """One row of a schedule's revision log.

    ``sort_key`` is stored so the register can order by it in SQL. It is the
    same key ``core.revisions.sort_key`` computes and the exported workbook's
    hidden helper column carries: published above preliminary, so C01 outranks
    every Pnn.
    """

    __tablename__ = "revision"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedule.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    code: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(80), default="")
    issue_date: Mapped[_dt.date | None] = mapped_column(Date, nullable=True)
    prepared_by: Mapped[str] = mapped_column(String(40), default="")
    checked_by: Mapped[str] = mapped_column(String(40), default="")
    approved_by: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    sort_key: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)

    #: When this revision was issued, and what the schedule looked like then.
    #:
    #: A snapshot holds the computed values as well as the typed ones, which is
    #: what stops a later library correction or formula fix changing the meaning
    #: of a document that has already gone out. Null until the revision is
    #: issued; such revisions render live and are labelled as doing so.
    issued_at: Mapped[_dt.datetime | None] = mapped_column(DateTime, nullable=True)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    schedule: Mapped[Schedule] = relationship(back_populates="revisions")

    @property
    def is_issued(self) -> bool:
        return self.snapshot is not None


# ------------------------------------------------------- equipment library ---


class Equipment(TimestampMixin, Base):
    """One product in the organisation's shared equipment library.

    Keyed on ``model_reference`` within a type, exactly as the v1 library sheets
    were: column A is always Model Reference and is the lookup key.

    Entries go live immediately so nobody is blocked mid-schedule, and are
    flagged for review rather than gated behind one. v1's submissions inbox
    existed to stop concurrent writes corrupting a shared .xlsx; a database does
    not have that problem, so the queue no longer has to be a gate.
    """

    __tablename__ = "equipment"
    __table_args__ = (
        UniqueConstraint(
            "organisation_id", "type_code", "model_reference", name="uq_equipment_key"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The schedule type code this product belongs to, e.g. ``MVHR``.
    type_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    model_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Library column values, keyed by column name.
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    #: live -> approved | rejected. 'live' is usable; review is not a gate.
    review_state: Mapped[str] = mapped_column(String(20), default="live", nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="schedule")
    created_by: Mapped[str] = mapped_column(String(120), default="")

    organisation: Mapped[Organisation] = relationship(back_populates="equipment")
    flags: Mapped[list["EquipmentFlag"]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan"
    )
    change_log: Mapped[list["EquipmentChange"]] = relationship(
        back_populates="equipment",
        cascade="all, delete-orphan",
        order_by="EquipmentChange.at.desc()",
    )


class EquipmentChange(TimestampMixin, Base):
    """One recorded change to a library entry.

    Library values are read rather than copied, so correcting a product changes
    every schedule that uses it at once. That is the feature, and it is also why
    a practice needs to be able to see what changed and where it landed.
    """

    __tablename__ = "equipment_change"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Recorded in Python rather than by the database clock: SQLite's now() has
    #: one-second resolution, so several changes in the same second would tie
    #: and the log would come back in an arbitrary order.
    at: Mapped[_dt.datetime] = mapped_column(
        DateTime,
        default=lambda: _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
        nullable=False,
        index=True,
    )
    #: created | updated | approved | rejected | restored
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    #: {column: [before, after]} for an update.
    changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    actor: Mapped[str] = mapped_column(String(120), default="")

    equipment: Mapped["Equipment"] = relationship(back_populates="change_log")


class EquipmentFlag(TimestampMixin, Base):
    """Something the review list should rank an entry by.

    Carries forward ``merge_submissions.py``'s intelligence -- NEW, DUPLICATE,
    CONFLICT, CANNOT and spelling drift (``GRUNDFOS`` against an existing
    ``Grundfos``) -- as ranking rather than as a gate.
    """

    __tablename__ = "equipment_flag"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    #: The other entry this flag relates to, for duplicates and conflicts.
    related_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    equipment: Mapped[Equipment] = relationship(back_populates="flags")


# ----------------------------------------------------------- change log ---


class ChangeEvent(Base):
    """One organisation-level change worth telling everybody about.

    A schedule can change under somebody without them touching it: a column was
    added to its type, the house notes were reworded, the branding moved. Each
    of those is already recorded somewhere -- a type's own history, the library
    change log -- but only in the place that caused it, which is not where the
    person affected is looking. This is the shared spine the impact log reads,
    and the only home for the changes that had none.

    ``detail`` stays free-form JSON: an impact entry is something to show a
    person, not something another query joins against.
    """

    __tablename__ = "change_event"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Recorded in Python: SQLite's now() ties when two changes land in one second.
    at: Mapped[_dt.datetime] = mapped_column(
        DateTime,
        default=lambda: _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
        nullable=False,
        index=True,
    )
    #: type | notes | branding | export | library | numbering
    area: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: The thing that changed, e.g. a type code, so the log can be filtered.
    subject: Mapped[str] = mapped_column(String(120), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    #: 'info' for a presentational change, 'warn' when schedules may move.
    severity: Mapped[str] = mapped_column(String(10), default="info")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    actor: Mapped[str] = mapped_column(String(120), default="")
