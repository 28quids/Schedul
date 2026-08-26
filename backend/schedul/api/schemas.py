"""Request and response shapes for the HTTP API."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "ProjectIn", "ProjectOut", "ProjectSummary",
    "BuildingIn", "BuildingOut",
    "ScheduleIn", "ScheduleOut",
    "RowIn", "RowOut", "GridOut", "GridColumn",
    "PasteIn", "PastePreviewIn", "CellEdit", "CellsIn", "DeleteRowsIn", "FillIn",
    "RevisionIn", "RevisionOut",
    "EquipmentIn", "EquipmentOut",
    "TypeIn", "TypeOut", "TypeSummary", "ColumnIn",
    "RenumberIn", "PlanOut", "PlanChange",
    "RegisterRow", "AuditOut", "HouseStandardIn",
]


# ------------------------------------------------------------------ types ---


class ColumnIn(BaseModel):
    kind: Literal["input", "library", "derived"]
    name: str
    unit: str = ""
    width: int = 14
    example: Any = ""
    formula: str | None = None
    note: str | None = None
    #: Where this column appears: editor / xlsx / pdf. An absent key means
    #: visible, so a payload written before visibility existed still works.
    visibility: dict[str, bool] = Field(default_factory=dict)


class TypeIn(BaseModel):
    code: str
    title: str
    short: str = ""
    volume: str = ""
    columns: list[ColumnIn] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    change: str = ""


class TypeSummary(BaseModel):
    id: str
    code: str
    title: str
    short: str
    version: int
    volume: str
    volume_label: str = ""
    column_count: int
    updated_at: _dt.datetime | None = None


class TypeOut(TypeSummary):
    columns: list[dict[str, Any]]
    notes: list[str]
    history: list[dict[str, Any]]
    project_notes: list[str] = Field(default_factory=list)
    issues: list[dict[str, str]] = Field(default_factory=list)


# --------------------------------------------------------------- projects ---


class ProjectIn(BaseModel):
    name: str = ""
    number: str = ""
    client: str = ""
    site_address: str = ""
    architect: str = ""
    main_contractor: str = ""
    riba_stage: str = "Stage 4"
    prepared_by: str = ""
    checked_by: str = ""
    approved_by: str = ""
    naming_overrides: dict[str, Any] = Field(default_factory=dict)
    design_constants: dict[str, Any] = Field(default_factory=dict)


class ScheduleOut(BaseModel):
    id: str
    code: str
    title: str
    number: int
    docnum: str
    filename: str = ""
    state: str
    type_version: int
    latest_type_version: int
    volume: str = ""
    row_count: int = 0
    revision: str = ""
    issue_date: _dt.date | None = None
    status: str = ""
    status_description: str = ""
    locked: bool = False
    lock_reason: str = ""


class BuildingIn(BaseModel):
    ref: str
    name: str = ""


class BuildingOut(BaseModel):
    id: str
    ref: str
    name: str
    label: str
    position: int
    retired_numbers: list[int] = Field(default_factory=list)
    schedules: list[ScheduleOut] = Field(default_factory=list)


class ProjectSummary(BaseModel):
    id: str
    name: str
    number: str
    client: str
    building_count: int
    schedule_count: int
    updated_at: _dt.datetime | None = None


class ProjectOut(ProjectSummary):
    site_address: str = ""
    architect: str = ""
    main_contractor: str = ""
    riba_stage: str = ""
    prepared_by: str = ""
    checked_by: str = ""
    approved_by: str = ""
    naming_overrides: dict[str, Any] = Field(default_factory=dict)
    design_constants: dict[str, Any] = Field(default_factory=dict)
    effective_constants: dict[str, float] = Field(default_factory=dict)
    buildings: list[BuildingOut] = Field(default_factory=list)
    naming_preview: dict[str, Any] = Field(default_factory=dict)


class ScheduleIn(BaseModel):
    code: str


class CloneIn(BaseModel):
    ref: str
    name: str = ""
    codes: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------- grid ---


class GridColumn(BaseModel):
    name: str
    legacy_name: str
    kind: str
    unit: str
    unit_display: str
    width: int
    example: Any = ""
    formula: str | None = None
    note: str | None = None
    editable: bool
    visibility: dict[str, bool] = Field(default_factory=dict)
    project_extra: bool = False


class RowOut(BaseModel):
    id: str
    position: int
    values: dict[str, Any]
    computed: dict[str, Any]
    problems: dict[str, str] = Field(default_factory=dict)
    #: Library columns this row deliberately diverges from.
    overrides: dict[str, Any] = Field(default_factory=dict)


class GridOut(BaseModel):
    schedule: ScheduleOut
    columns: list[GridColumn]
    rows: list[RowOut]
    project_id: str
    project_name: str = ""
    building_id: str
    building_ref: str = ""
    building_count: int = 1
    notes: list[str] = Field(default_factory=list)
    #: The resolved note layers, each carrying where it came from.
    note_layers: list[dict[str, Any]] = Field(default_factory=list)
    #: True when this schedule has its own notes rather than inheriting.
    notes_customised: bool = False
    #: Whether undo and redo are available, and of what.
    history: dict[str, Any] = Field(default_factory=dict)
    #: Set when the type has moved on since this schedule was built.
    type_drift: dict[str, Any] = Field(default_factory=dict)


class RowIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    position: int | None = None
    #: Deliberate divergences from the equipment library, keyed by column name.
    #: Sending an empty value clears the override and restores the library value.
    overrides: dict[str, Any] | None = None


class PasteIn(BaseModel):
    """Rows pasted from a spreadsheet, and what to do with them.

    Paste used to be replace-only, which made it an all-or-nothing destructive
    action on a schedule someone had already filled in. It now defaults to
    appending, and the one mode that removes anything has to say so twice: once
    by naming itself, and once by confirming the preview.

    ``text`` is the raw clipboard block. Parsing it here rather than in the
    browser is what lets the preview and the apply agree exactly -- they run the
    same planner over the same text.
    """

    mode: Literal["replace", "append", "insert"] = "append"
    rows: list[RowIn] = Field(default_factory=list)
    #: Raw tab-separated text. Takes precedence over ``rows`` when given.
    text: str = ""
    #: True/False forces the header decision; None detects one.
    header: bool | None = None
    #: Where 'insert' puts them. Ignored by the other modes.
    position: int = 0
    #: Required before a replace may remove rows that carry typed values.
    confirm: bool = False


class PastePreviewIn(BaseModel):
    """A paste to plan but not perform."""

    mode: Literal["replace", "append", "insert"] = "append"
    text: str = ""
    header: bool | None = None
    position: int = 0


class CellEdit(BaseModel):
    """One row's worth of a multi-cell edit.

    Values are merged into the row rather than replacing it, so a rectangular
    paste or a range delete touches only the columns it names.
    """

    row_id: str
    values: dict[str, Any] = Field(default_factory=dict)
    overrides: dict[str, Any] | None = None


class CellsIn(BaseModel):
    """A block of cell edits applied as one undoable step."""

    edits: list[CellEdit] = Field(default_factory=list)
    #: What to call this in the undo stack.
    action: str = "cells"


class DeleteRowsIn(BaseModel):
    """Delete several rows at once, as one undoable step."""

    row_ids: list[str] = Field(default_factory=list)


class FillIn(BaseModel):
    """Fill one or more columns down from a starting row.

    ``columns`` fills a selected block; ``column`` remains for the single-column
    case and for callers written before ranges existed.
    """

    column: str = ""
    columns: list[str] = Field(default_factory=list)
    start_position: int
    #: How many rows below the start to fill. Omit to reach the end.
    count: int | None = None
    mode: Literal["series", "copy"] = "series"

    @property
    def target_columns(self) -> list[str]:
        return self.columns or ([self.column] if self.column else [])


# -------------------------------------------------------------- revisions ---


class RevisionIn(BaseModel):
    code: str = ""
    status: str = ""
    issue_date: _dt.date | None = None
    prepared_by: str = ""
    checked_by: str = ""
    approved_by: str = ""
    description: str = ""


class RevisionOut(RevisionIn):
    id: str
    position: int
    sort_key: int
    is_current: bool = False
    #: True once the revision has been issued and its state frozen.
    issued: bool = False
    issued_at: _dt.datetime | None = None


class BulkRevisionIn(BaseModel):
    """Append the same revision to many schedules at once.

    Each schedule continues its own series rather than being forced to a shared
    code, so a schedule already at P03 goes to P04 while one at P01 goes to P02.
    """

    schedule_ids: list[str] = Field(default_factory=list)
    status: str = ""
    issue_date: _dt.date | None = None
    prepared_by: str = ""
    checked_by: str = ""
    approved_by: str = ""
    description: str = ""
    published: bool = False
    issue: bool = False
    apply: bool = False


# -------------------------------------------------------------- equipment ---


class EquipmentIn(BaseModel):
    type_code: str
    model_reference: str
    values: dict[str, Any] = Field(default_factory=dict)
    created_by: str = ""


class EquipmentOut(BaseModel):
    id: str
    type_code: str
    model_reference: str
    values: dict[str, Any]
    review_state: str
    created_by: str = ""
    updated_at: _dt.datetime | None = None
    flags: list[dict[str, Any]] = Field(default_factory=list)


# -------------------------------------------------------------- numbering ---


class RenumberIn(BaseModel):
    operation: Literal["set", "swap", "insert", "compact", "rebase"]
    code: str | None = None
    other_code: str | None = None
    number: int | None = None
    allow_locked: list[str] = Field(default_factory=list)
    apply: bool = False


class PlanChange(BaseModel):
    code: str
    old_number: int
    new_number: int
    old_docnum: str = ""
    new_docnum: str = ""
    old_filename: str = ""
    new_filename: str = ""
    blocked: str | None = None
    changed: bool = False


class PlanOut(BaseModel):
    operation: str
    changes: list[PlanChange]
    warnings: list[str] = Field(default_factory=list)
    blocked_count: int = 0
    can_apply: bool = False
    applied: int = 0


class RenameBuildingIn(BaseModel):
    ref: str
    apply: bool = False
    force: bool = False


# --------------------------------------------------------------- register ---


class RegisterRow(BaseModel):
    project_id: str
    project_name: str
    project_number: str
    building_id: str
    building: str
    schedule_id: str
    code: str
    document_number: str
    schedule_name: str
    file_name: str
    revision: str = ""
    issue_date: _dt.date | None = None
    status: str = ""
    status_description: str = ""
    row_count: int = 0
    state: str = ""


class AuditOut(BaseModel):
    building_id: str
    building: str
    issues: list[dict[str, str]]


class ProjectColumnsIn(BaseModel):
    """The extra columns a project adds to one catalogue type."""

    type_code: str
    columns: list[ColumnIn] = Field(default_factory=list)


class HouseStandardIn(BaseModel):
    name: str | None = None
    naming: dict[str, Any] | None = None
    general_notes: list[str] | None = None
    design_constants: dict[str, float] | None = None
    house_style: dict[str, Any] | None = None
    volume_lookup: dict[str, str] | None = None
    status_codes: list[list[str]] | None = None
    #: Volume -> discipline. An empty dict clears it, leaving discipline
    #: project-scoped; omitting the field leaves it as it was.
    volume_discipline: dict[str, str] | None = None
    #: 'building' or 'building_volume'.
    numbering_scope: str | None = None
    branding: dict[str, Any] | None = None
