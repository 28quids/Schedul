# MEP Schedule Manager — build specification

Internal tool. Windows, everything under `C:\` while it is a proof of concept.
Single user now, small team later, eventual web app with logins. Build it so the
last step is a UI swap, not a rewrite.

Read this whole document before writing code. Then read every file in the repo.
Then confirm the five facts in section 1 back to me before starting phase 1.

---

## 0. How to work

- **All logic in `core/`. The GUI is a shell.** No business rule, no path
  calculation, no file naming, no validation may live in a tkinter callback.
  Test: every feature must be drivable from a Python REPL with tkinter never
  imported. This is what makes the web version a new front end rather than a
  new product.
- **Every destructive operation dry-runs first**, returns a plan object
  describing what it would do, and only acts on a second explicit call.
- **Nothing is overwritten silently.** Ever. See section 9.
- Type-hint the core. Docstrings on public functions. No comments explaining
  what the line does, only why it does it.
- Commit per phase, with the phase's checkpoint test passing.

---

## 1. Five facts about the existing code

Three of these are traps. Confirm you have understood them before starting.

1. **Numbering is positional.** `build_project.py` computes
   `number = number_start + index_of_type_in_schema` and builds *every* type in
   `schema.json`. This is the single thing that blocks per-project schedule
   selection and it must be replaced (section 5).
2. **It deletes its output folder.** `main()` calls `shutil.rmtree(out)`. It is
   a scratch generator, not an incremental one.
3. **Paths are frozen into each workbook at build time.** Every generated
   `.xlsx` has a hidden `Config` sheet holding `path_project_info`,
   `path_equipment_library`, `path_submissions_folder` plus every project field.
   The macros read paths from there. `RefreshProjectData` refreshes project
   fields only. **Nothing updates the paths.** Move the library and every file
   across every project silently breaks.
4. **`schema.json`'s `"number"` field is dead.** The builder ignores it. Types
   claim 0001-0008; real doc numbers are 00000010-00000017. Delete it.
5. **openpyxl round-trips these files losslessly.** I verified: load and save a
   generated schedule and no zip parts, data validations, defined names, print
   settings or hidden-sheet states are lost. This is what makes fact 3 fixable
   in place, without rebuilding.

   **This does not hold for the hand-made originals.** Those carry branding as
   drawing objects and openpyxl warns `Shapes and drawings will be lost` on
   load. Never round-trip a hand-made file through Python. Migration reads them
   and generates fresh ones; it does not edit them in place.

---

## 1a. What the real house file tells us

The repo contains a hand-made original (`..._-_Radiant_Panel_Schedule.xlsx`) and
its `MAINPROJECTINFO.xlsx`. The generator was reverse-engineered from these.
Read them before phase 1; they are the ground truth for house format, and they
expose several things the generated files get differently.

**Layout confirmed.** Schedule sheet: title in A1, general notes in A2, field
names row 4, units row 5, data from row 6, print titles `$1:$5`, A4 landscape.
Front Cover and Revision page as generated. This all matches, keep it.

**Radiant Panel columns**, for reference and as a ninth catalogue type:
Ref, Level, Room Number, Location, Room Setpoint (°C Dry Resultant), Minimum
Heat Required (W), Radiant Panel Type, Quantity, Height (mm), Length (mm),
Depth (mm), Panel output (W), Flow Rate Per Panel (Kg/s), Pressure drop in each
panel (kPa), Notes.

**Six differences that matter:**

1. **General notes are per equipment type, not per project.** The real file's A2
   is radiant-panel-specific ("radiant panels are to be sized with a 55°C flow
   and 45°C return", "to be Merriott or equal and approved"). The generator puts
   one project-level note block on every schedule. Fix in 4.2: catalogue entries
   get their own `notes` array, and the rendered block is project notes followed
   by type notes.

2. **No Building field anywhere.** The schedule has Level, Room Number and
   Location but nothing identifying the building. See 4.6.

3. **The document number is derived from the filename**, via
   `TEXTBEFORE(TEXTBEFORE(TEXTAFTER(CELL("filename",A1),"["),"]"),"-",-1)`.
   Self-updating on rename, but volatile, blank until first save, and
   unauditable. The generated files store it in `Config!$B$4` instead. Keep the
   stored value: an explicit rename plan you can inspect beats a formula that
   silently changes meaning. Migration must replace this formula with the value.

4. **External links to a personal OneDrive.** The real file points at
   `rambollgbr-my.sharepoint.com/personal/...` for MAINPROJECTINFO and at a UNC
   path for a risk assessment. The generator writing project data as values is
   the fix. Migration must report every external link it finds and strip them.

5. **The revision logic uses `LET` / `XLOOKUP` / `XMATCH` / `TEXTBEFORE` and a
   structured table `RevisionTable[Revision]`.** The generator deliberately
   downgrades to `INDEX`/`COUNTA`. See 6.1 for why, and for the one place the
   downgrade is wrong.

6. **Two live bugs in the real file.** `Metadata!ScheduleName` points at
   `'Revision page'!B19`, which is the document number, not the name. And
   `Revision page` B10 hardcodes the project name while B11 pulls it from Setup.
   Do not carry either into the generator, and flag both when migrating.

---

## 2. What to keep, what to rewrite

Everything is on the table, but two things are worth keeping and I want to be
specific about why, because rewriting them costs weeks and buys nothing.

### Keep, vendored as-is

**The sheet-construction code inside `build_project.py`** (roughly lines
249-600: `build_schedule_file` and its helpers). This is ~350 lines of Excel
minutiae that is correct and verified: A4 portrait covers and landscape
schedules, Verdana 30pt title block, two-row header with units split onto row 5,
print titles `$1:$5`, `fitToWidth=1 / fitToHeight=0`, the `INDEX(...MAX(1,COUNTA(...)))`
revision-page derivation that avoids array formulas, the INDEX/MATCH product
lookups against the hidden Library sheet, the blue/green/black colour contract.
About 4,900 formulas across a set, hand-checked against manual workings.
Re-deriving this is pure risk with no upside.

Refactor its *interface* (section 6). Do not rewrite its *body*.

**`MEPSchedules.bas`.** The macro set works and its design is right: paths come
from the active workbook's Config sheet so nothing is hardcoded, and it ships as
an `.xlam` add-in so schedules stay plain `.xlsx`. Extend it (section 8), do not
replace it.

**`merge_submissions.py`.** The submissions-inbox pattern is the reason the
shared library does not get write-conflicted. Keep the NEW/DUPLICATE/CONFLICT/
CANNOT reporting and the spelling-drift detection.

### Rewrite

Everything else. `schema.json` becomes a catalogue (section 4.2). `project.json`
becomes generated output, not hand-edited input. `main()` in the builder becomes
a thin wrapper. `README.md` is superseded by this document.

### Already written for you

`mep_core.py` and `mep_manager.py` are in the repo and tested against the real
toolkit. They are a **starting point, not a constraint** — refactor them into the
module layout in section 3. What passes today:

- two projects built against one shared library, library written once, second
  build leaves it untouched
- rebuild over a folder containing a filled-in schedule: 0 installed, 8 skipped,
  typed data intact
- library moved and client renamed, then `sync_schedules`: Config updated across
  8 files, `Front Cover` formula `=Config!$B$5` still intact, `.bak` written
- appended a Fire Damper type: exactly 1 new file built
- inserted a type mid-schema: build aborted with a doc-number shift report

I could not run the tkinter GUI (no display in my environment), only syntax-check
it and verify every `core.*` reference resolves. Expect layout bugs on first run.

---

## 3. Repo layout

```
mep_manager/
  core/
    __init__.py
    paths.py          path normalisation, resolution, the C:\ conventions
    registry.py       load/save registry, project CRUD
    catalogue.py      schedule-type definitions: load, save, version, validate
    numbering.py      document number allocation and the ledger
    render.py         vendored sheet construction, refactored (section 6)
    build.py          orchestration: allocate -> render -> install
    sync.py           in-place Config repair across existing workbooks
    library.py        shared equipment library: add sheet, read, never clobber
    register.py       scrape Metadata/Config into register rows
    validate.py       all validation, returns structured issues
    migrate.py        v1 -> v2 adoption
  gui/
    app.py            main window, wiring only
    projects.py       project list + details tabs
    schedules.py      per-project schedule selection
    designer.py       schedule type designer
    settings.py       shared paths
    numbering.py      token editor + renumber/rename plans
    widgets.py        Field, PathField, LogPane, EditableTable, PlanTable
  vendor/
    MEPSchedules.bas
    merge_submissions.py
    Register.pq
  templates/
    cover_template.xlsx      branded cover, see section 8.3
  tests/
  SPEC.md
```

`core/` must not import anything from `gui/`. Enforce it with a test that walks
the AST of every file in `core/`.

---

## 4. Data model

Three stores. All JSON except the workbooks. All human-readable, because this is
a proof of concept and you will be debugging it by opening files.

### 4.1 Registry — `registry.json`

One file, on the shared path. Holds shared settings and every project.

```json
{
  "version": 2,
  "shared": {
    "catalogue_dir":       "C:\\MEP\\library\\schedule_types",
    "equipment_library":   "C:\\MEP\\library\\EQUIPMENT_LIBRARY_MASTER.xlsx",
    "submissions_folder":  "C:\\MEP\\library\\submissions",
    "cover_template":      "C:\\MEP\\library\\cover_template.xlsx"
  },
  "defaults": {
    "house_style":      { "...": "as project.json today" },
    "general_notes":    ["..."],
    "design_constants": { "...": "as project.json today" },
    "document_number":  { "pattern": "...", "originator": "BOV", "...": "..." }
  },
  "projects": [ { "see 4.3": true } ]
}
```

The local machine stores only a pointer to where `registry.json` lives, in
`%USERPROFILE%\.mep_manager\bootstrap.json`. Nothing else is machine-local.

### 4.2 Catalogue — one file per schedule type

Replaces the single `schema.json`. `schedule_types/MVHR.json`:

```json
{
  "code": "MVHR",
  "title": "Mechanical Ventilation with Heat Recovery Unit Schedule",
  "short": "MVHR Units",
  "version": 3,
  "created": "2026-08-25", "updated": "2026-09-14",
  "columns": [
    { "kind": "input",   "name": "Unit Reference", "unit": "",     "width": 14, "example": "MVHR-01" },
    { "kind": "library", "name": "Manufacturer",   "unit": "",     "width": 18, "example": "Systemair" },
    { "kind": "library", "name": "Length",         "unit": "mm",   "width": 12, "example": 1200 },
    { "kind": "derived", "name": "SFP",            "unit": "W/l/s", "width": 12,
      "formula": "={Total Power Input (W)}/{Supply Airflow (l/s)}",
      "note": "Specific fan power at the scheduled duty" }
  ],
  "notes": [
    "Where radiant panels are fed by the LTHW system they are to be sized with a 55degC flow and 45degC return temperature.",
    "LTHW radiant panels to be Merriott or equal and approved."
  ],
  "history": [ { "version": 2, "date": "2026-09-01", "change": "added Filter Grade" } ]
}
```

Plus `schedule_types/_catalogue.json` as an index: code, title, current version,
updated date. Rebuildable from the type files; it exists so the GUI can list the
catalogue without opening thirty files.

**Three column kinds, not two.** This is the correction to the mental model and
it matters:

| kind | old schema key | behaviour | colour |
|---|---|---|---|
| `input` | `instance_fields` | user types it, differs per unit | blue on yellow |
| `library` | `type_fields` | INDEX/MATCH on Model Reference | green |
| `derived` | `derived_fields` | formula, read-only | black |

This maps onto COBie/IFC (ISO 16739-1) Component vs Type data, with derived
being neither. Hold that alignment: it is what makes an IFC or COBie export
possible later, so the designer must not let a field sit in the wrong kind for
convenience.

`Model Reference` is inserted automatically between the input and library
columns. The user never defines it. Reject it if they try.

**Versioning.** Any change to `columns` bumps `version` and appends to `history`.
A project pins the version it built against, so editing FCU does not silently
invalidate every issued FCU schedule. Upgrading a project's schedule to a newer
type version is an explicit action that rebuilds that one file, and it must
refuse if the file has data in it (offer export-data / rebuild / re-import as a
later feature, not now).

### 4.3 Project and building records

**A building owns its schedules, and buildings differ.** A real project looks
like CM4220 with HQ049, HQ014 and NB17 under it, where HQ049 has gas boilers,
HQ014 has ASHPs, and NB17 has heat pumps and chillers. Overlapping sets, not
identical ones. Do not model a building as a copy of a template.

Building refs are independent codes from the client or asset register (`HQ049`,
`NB17`), not derived from the project number. The `building` token takes the ref
verbatim. This is what the `-PROJECTNUMBER-` placeholder in the sample files
actually is.

The project carries what is common (client, project number, address, people,
design constants). The building carries everything that produces files. Treat a
building as a near-independent container so adding a block to a live job cannot
disturb the others.

```json
{
  "id": "a1b2c3d4",
  "project": {
    "Client": "", "Project Name": "", "Project Number": "", "Site Address": "",
    "Architect": "", "Main Contractor": "", "RIBA Stage": "Stage 4",
    "Prepared By": "", "Checked By": "", "Approved By": ""
  },
  "naming": { "see 5.2": "project-scoped token values only" },
  "design_constants": { "...": "overrides house standard" },
  "folders": {
    "schedules_root": "...\\CM4220\\Documents\\Schedules",
    "admin":          "...\\CM4220\\Documents\\_schedule admin"
  },
  "submissions_mode": "shared",
  "buildings": [
    {
      "id": "b1",
      "ref": "HQ049",
      "name": "HQ049 Main Building",
      "folders": { "schedules": "...\\Schedules\\HQ049" },
      "naming_overrides": {},
      "schedules": {
        "MVHR": { "number": 10, "docnum": "...", "type_version": 3,
                  "file": "...xlsx", "added": "2026-08-25", "state": "built" },
        "AHU":  { "number": 11, "...": "..." }
      },
      "retired_numbers": []
    }
  ],
  "created": "...", "updated": "..."
}
```

Notes on the shape:

- `schedules` is keyed on type code **within a building**, so one AHU schedule
  per building is the rule and the key stays simple. If a building ever needs two
  of the same type, that is a later change to a composite key; do not
  pre-emptively build it.
- Each building's `folders.schedules` follows 4.3.1: the schedules root itself
  for a single-building project, or `schedules_root\<ref>` when there are
  several. Overridable, but the default should be right almost always.
- `state` is `allocated`, `built` or `missing`, as before.
- A project with exactly one building is the common case. The GUI must hide the
  building layer entirely when `len(buildings) == 1`, or the tool becomes
  annoying for small jobs. The data model still has the building; only the UI
  collapses.

**`clone_building(project, source_id, new_ref, new_name, codes)`** exists as a
head start, not a default. Buildings on the same job frequently share some types
and differ on others, so cloning must open a checklist of the source building's
types pre-ticked, let the user untick and add, and only then render. It copies
the *selection*, never filled-in data, and allocates fresh numbers in the new
building.

Adding a building from scratch with an empty selection must be equally easy. Do
not make clone the only path.

### 4.3.1 Folder convention

The real structure, and the tool must match it rather than impose its own:

```
Company\Projects\CM4220\Documents\Schedules\        <- one building: files here
Company\Projects\CM4220\Documents\Schedules\HQ049\  <- several: one folder each
                                        ...\HQ014\
                                        ...\NB17\
```

The path between the project number and `Schedules` varies by job (`Documents`
here, something else elsewhere). Do not hardcode any of it. The user points at
the `Schedules` folder; everything is relative to that.

`folders.schedules_root` on the project is that folder. A building's
`folders.schedules` is `schedules_root` for a single-building project, or
`schedules_root\<ref>` when there are several. The admin folder defaults to a
sibling of `schedules_root` and is overridable.

**The trap: promoting single to multi.** A job starts with one building, files
sitting directly in `Schedules\`. A second building is added. The first
building's files now need to move down into `Schedules\<ref>\`.

Handle it explicitly with `promote_to_multi_building(project)`, invoked
automatically when a second building is added:

1. create `schedules_root\<ref_of_building_1>\`
2. move building 1's files into it
3. update `folders.schedules` on building 1
4. update MAINPROJECTINFO

This is a **pure move**. Filenames do not change, so `Config!$B$4` is untouched
and no workbook is opened. That makes it safe, but it must still dry-run, report
locked files, and be reversible via `demote_to_single_building` for the case
where the second building was added by mistake.

Never leave building 1's files at the root as a special case. One inconsistent
project will cost more debugging than the move operation costs to write.

### 4.4 Generated `project.json`

Still written into each project's admin folder, in exactly the shape the
renderer consumes. It is **output now, not input**. Regenerate it on every save.
Add a header comment saying so, because someone will try to hand-edit it.

Fix the path escaping while you are there: the shipped sample has
`"C:\\\\SharePoint\\\\..."`, which decodes to a literal doubled separator.
Normalise every path on the way in and out (`core/paths.py`).

---

### 4.5 House standard — `house_standard.json`

Everything that varies between firms lives here and nowhere else. This is the
single most important separation in the codebase for the eventual product, so
treat any company-specific value found outside this file as a bug.

```json
{
  "name": "Default house standard",
  "naming":        { "see 5.2": true },
  "volume_lookup": { "5.2": "Above ground drainage", "5.3": "Domestic services",
                     "5.6": "Heating and cooling", "5.7": "Ventilation" },
  "status_codes":  [["S0","Work in Progress"], ["S1","Suitable for Coordination"], "..."],
  "revision_codes":{ "preliminary": "P{nn}", "published": "C{nn}", "max": 20 },
  "house_style":   { "cover_font": "Verdana", "schedule_font": "Arial",
                     "title_grey": "FF4D4D4D", "title_blue": "FF009DF0", "...": "..." },
  "colours":       { "input": "FFFFCC", "library": "008000", "derived": "000000" },
  "cover_template":"cover_template.xlsx",
  "general_notes": ["..."]
}
```

The registry references one house standard. A second firm is a second profile,
not a fork.

Worth being clear about the ambition here: ISO 19650 already defines the field
structure, which is why the naming looks the way it does. What differs between
firms is the token values, the pattern order and the branding. The product is a
configurable implementation of an existing standard, not a new standard. Design
accordingly.

---

### 4.6 Where the building appears in the schedule

The building is a property of the **document**, not of a row. One schedule
covers one building, so it does not become a column. It appears in three places
and they all derive from one Config value:

- `Config!Building` and `Config!BuildingRef`, written at build time
- **Revision page**, a new row between Project no. and Recipient: `Building`,
  showing `ref - name` (`HQ049 - Main Building`)
- **Front Cover**, under the project name, same value

`Config` is the only stored copy; the other two are `=Config!$B$n`, so a
building rename is still one write per file plus the filename.

Adding a row to the Revision page shifts the summary block, which currently runs
rows 10 to 22 with the derived cells at fixed positions. Do not hardcode the new
row number anywhere: build the summary from an ordered list of
`(label, source)` pairs and let the row numbers fall out, so the next field
someone wants costs one list entry.

**Open question for the user, answer before phase 4b.** If a single schedule
ever has to cover more than one building, this is wrong and Building becomes an
`input` column instead, and buildings stop being containers. The evidence says
one set per building (HQ049, HQ014, NB17 as separate folders), so this spec
assumes document-level. Confirm before building it.

### 4.7 Notes

Two sources, rendered as one block in Schedule A2:

- project-level notes from the house standard, the generic compliance wording
- type-level notes from the catalogue entry, the equipment-specific wording

Numbered `[1]`, `[2]`, ... continuously across both, project notes first. The
designer edits type notes; Settings edits the house standard notes.

---

## 5. Numbering and naming

This is the most-used part of the tool and the part most likely to be got wrong.
It lives in `core/naming.py` and `core/numbering.py`, never in the GUI.

### 5.1 The document number is one cell

Verified against the generated files: the document number appears in exactly one
place, `Config!$B$4`. `Revision page` B19 reads `=Config!$B$4`, and
`Metadata` B2 reads `='Revision page'!B19`. Nothing else hardcodes it.

So renaming a schedule is **two writes**: that cell, and the filename on disk.
Everything else follows by formula.

This is why there is no separate renamer program. A tool that renames the file
without updating `Config!$B$4` leaves the workbook's contents disagreeing with
its own name, and the register, MAINPROJECTINFO and every downstream scrape go
stale silently. Renaming is a record change that cascades to disk, so it needs
the project record. One application, a Numbering tab.

### 5.2 Tokens are scoped

The current flat `document_number` dict is wrong. Tokens vary at different
levels and the model must say so:

| scope | tokens | why |
|---|---|---|
| `company` | `originator` | constant for the firm |
| `project` | `project_number`, `doc_type`, `discipline`, `classification` | one value per job |
| `building` | `building` (currently mislabelled `client_ref`) | a project has several blocks |
| `type` | `volume` | 5.7 ventilation, 5.6 heating and cooling, 5.3 domestics, 5.2 above ground drainage. An AHU is always ventilation. This follows the equipment type, not the project |
| `schedule` | `number` | per document |

Resolution order, most specific wins: **schedule override → building → type
default → project → company default.**

`level` and `location` are project-scoped and effectively always `XX`: ISO 19650
uses them for drawings, not schedules. They stay in the pattern and stay
editable, but they do not belong in the per-schedule UI.

```json
"naming": {
  "pattern": "{project_number}-{originator}-{volume}-{building}-{doc_type}-{discipline}-{number}-{classification}-{level}-{location}",
  "separator": "-",
  "suffix": "_-_{title_slug}",
  "tokens": {
    "project_number": { "scope": "project",  "value": "Z9A6461Y19" },
    "originator":     { "scope": "company",  "value": "BOV" },
    "volume":         { "scope": "type",     "value": "5.6", "filename_value": "5_6" },
    "building":       { "scope": "building", "value": "PROJECTNUMBER" },
    "doc_type":       { "scope": "project",  "value": "SC" },
    "discipline":     { "scope": "project",  "value": "M" },
    "number":         { "scope": "schedule", "width": 8, "start": 10 },
    "classification": { "scope": "project",  "value": "G00300" },
    "level":          { "scope": "project",  "value": "XX" },
    "location":       { "scope": "project",  "value": "XX" }
  }
}
```

`filename_value` exists because the user writes the volume as `5.7` but the
filename carries `5_7`. Do not make anyone remember to type underscores.
Where absent, `value` is used for both, with a filename-safe transform applied.

The house standard (4.5) carries a `volume_lookup` table so a new schedule type
picks its volume from a list rather than free text.

### 5.3 Buildings and where numbering restarts

Buildings are defined in 4.3. Two consequences for numbering:

**Numbering restarts per building.** HQ049's schedules are 10, 11, 12 and
HQ014's are also 10, 11, 12. This is correct and deliberate: the `building`
token already differentiates the document numbers, so
`CM4220-BOV-5_7-HQ049-SC-M-00000010-...` and `...-HQ014-SC-M-00000010-...` are
distinct documents. Restarting per building also means adding NB17 later does
not depend on what the others did, and that two buildings with different
equipment do not produce baffling gaps in each other's sequences.

Allocation, retirement and the issued-document lock all operate **within a
building**, never across the project.

**Changing a building's `ref` is a mass rename** across every schedule in that
building and nothing else. This is the "swap the `-PROJECTNUMBER-` placeholder
for the real building code" flow, and scoping it to one building is what makes it
safe on a live multi-block job. If the building's folder is named after the ref,
the folder is renamed too, in the same plan.

`level` and `location` stay project-scoped with a permanent `XX` default. ISO
19650 uses them for drawings; schedules do not. Keep them in the pattern and
editable, but out of the per-schedule UI.

### 5.4 Allocation

1. Adding a schedule allocates `max(all numbers ever used by this building) + 1`,
   or the `number` token's `start` if the building has none. "Ever used" includes
   `retired_numbers`.
2. Removing a schedule retires its number within that building. Retired numbers
   are not reused by automatic allocation, though the explicit operations in 5.5
   may reclaim them.
3. The number is recorded with `state: "allocated"` **before** the file is
   rendered and flipped to `"built"` after, so a crashed build leaves a reserved
   number rather than an orphan.
4. Two projects, or two buildings within a project, sharing numbers is fine and
   expected: `project_number` and `building` are earlier tokens.
5. Warn when a building is within 10 of exhausting `width`.

### 5.5 Renumber operations

Free-text number editing produces collisions immediately. Offer four operations
and no raw editing:

- `set_number(code, n)` — explicit, rejected on collision with a live schedule
- `swap(code_a, code_b)` — exchange two numbers
- `insert_at(code, n)` — take number `n`, shift everything at or above it up one
- `compact()` — close gaps, preserving current order

Plus `rebase(start)` to change the starting number and renumber all in order.

Every one of these returns a plan (5.6) before touching anything.

**Issued-document lock.** Refuse to renumber any schedule whose revision log has
entries beyond the initial P01 row, or whose suitability status is not S0. ISO
19650's premise is that an issued reference is stable. Override requires typing
the filename. The sample set is entirely S0, so this will never fire during
testing, which is precisely why it belongs in code rather than in someone's head.

### 5.6 The rename cascade

```python
def plan_rename(project, changes) -> RenamePlan
def apply_rename(plan) -> RenameResult
```

`changes` may be a single schedule's token overrides, or a project- or
building-level token change that fans out to many schedules. One plan either way.

The plan lists, per affected schedule: old and new document number, old and new
filename, and any collision or lock that blocks it. The GUI shows it as a table
with a blocked-row count before Apply is enabled.

Apply, per file, in this order:

1. `.bak` the file
2. write the new value to `Config!$B$4` and save
3. `os.replace` to the new filename
4. update the project record
5. rewrite MAINPROJECTINFO's `ScheduleList`

If step 2 or 3 fails, restore from `.bak` and abort the whole batch. Report
locked files (open in Excel) and skip them cleanly rather than half-applying.

### 5.7 Audit

`numbering.audit(project, catalogue)` reports every inconsistency between the
record, the catalogue and the files on disk: filename not matching
`Config!$B$4`, record pointing at a missing file, a file in the folder with no
record, duplicate numbers, a schedule on a stale type version. Surface it as a
per-project health check, and run it automatically before any build or rename.

---

## 6. Renderer refactor

### 6.1 Current revision, and why the formulas were downgraded

"Show the most recent revision" is the functionality the user asked for, and
both existing implementations get it partly wrong.

**The real file** takes the maximum of `RevisionTable[Revision]` after stripping
`P`. That handles out-of-order rows correctly, which is good. But
`SUBSTITUTE(rev,"P","")` leaves `C01` as `C01`, `--"C01"` errors, `IFERROR`
turns it into `0`, and a published C-revision therefore sorts **below every
preliminary revision**. The moment a schedule goes to C01, the front cover
reverts to showing the last P revision. That is a live bug.

**The generator** takes the last non-empty row via
`INDEX(range, MAX(1, COUNTA(range)))`. Handles C-revisions fine, but breaks if
rows are entered out of order or a row is left blank in the middle.

**Correct behaviour:** rank by series then number, with the published series
always above the preliminary series. `P01 < P02 < ... < C01 < C02`. Implement it
as a hidden helper column on the Revision page holding a sort key
(`1000 + n` for `Pnn`, `2000 + n` for `Cnn`), and drive Revision, Date, Prepared
/ Checked / Approved by, Status and Status Description off `INDEX` /
`MATCH(MAX(...))` against that key. No `LET`, no `XLOOKUP`, no spilling.

The downgrade from `LET`/`XLOOKUP` is deliberate and should stay: openpyxl
cannot reliably author dynamic-array formulas, they carry `_xlfn.` /
`_xlpm.` prefixes when written by anything other than Excel, and a helper
column is easier for an engineer to debug than a nested `LET`. The house rule is
static formulas only. That is a generator constraint, not a claim about which
Excel version the team runs.

Also add a `RevisionTable`-equivalent: keep the revision log as a real Excel
table so the block grows when someone adds a row, rather than being a fixed
20-row range. Name it `RevisionTable` to match the existing file.

### 6.2 Interface

`core/render.py` is the vendored sheet code. Change its interface only.

```python
def render_schedule(
    type_def: ScheduleType,      # one catalogue entry, 4.2 shape
    project_cfg: dict,           # the 4.4 shape
    docnum: str,                 # allocated, passed in, never computed here
    out_path: Path,
) -> Path
```

Everything currently in `build_schedule_file` moves here with two changes: the
document number is a parameter rather than derived from a loop index, and
`columns` is read in the new shape rather than three parallel lists. Write a
`type_def_to_legacy_fields(type_def)` adapter so the body keeps working on
`instance_fields / type_fields / derived_fields` internally. That keeps the diff
small and reviewable.

Also extract:

```python
def render_project_info(project, schedule_records, out_path) -> Path
```

Keep `MAINPROJECTINFO.xlsx` broadly as it is: a `Setup` sheet (key/value project
fields) and a `ScheduleList` sheet. `RefreshProjectData` reads `Setup` by
key/value lookup, so that layout is a contract and must not change.

`ScheduleList` gains a leading `Building` column, so one file covers the whole
project across all blocks: Building, DocumentNumber, ScheduleName, Revision,
IssueDate, Status. Update `Register.pq` to match. One MAINPROJECTINFO per
project, not per building.

**Do not port `build_library_master` as-is.** Regenerating the shared library is
the single most dangerous thing in the old code. Replace it with
`core/library.py` (section 8.2).

Keep a `build_project.py` CLI shim that reproduces the old behaviour, so the
existing sample files stay reproducible for testing.

---

## 7. GUI

tkinter/ttk. Plain and dense beats pretty. Every long-running action logs to a
pane rather than blocking behind a spinner.

### 7.1 Projects

Left: project list. Right: notebook.

- **Details** — the ten project fields.
- **Document number** — pattern and tokens, with a live preview of the next
  number to be allocated.
- **Folders** — schedules folder first, with a "derive admin folder from this"
  button (the schedules folder is the thing the user actually knows). Radio for
  shared vs per-project submissions. A resolved-paths readout showing exactly
  what will be written into Config.
- **Design constants** — with a warning if a constant referenced by any of this
  project's schedules is blank.
- **Schedules** — section 7.2.

Bottom bar: Save, Validate, Set up folders, Sync existing schedules, Health check.

### 7.2 Buildings and schedules

A building selector across the top, hidden entirely when the project has one
building. Buttons: Add building (empty selection), Clone building (checklist,
4.3), Edit ref and name.

Adding a second building to a single-building project triggers
`promote_to_multi_building` (4.3.1). Show the file-move plan and require
confirmation before it runs.

Below it, two panes for the selected building. Left, the catalogue with the types
this building does not have. Right, the schedules it does, showing code, number,
type version, state, and whether the type has a newer version available.

- **Add** → allocate a number within this building, render one file into the
  building's schedules folder, add the library sheet if missing, update
  MAINPROJECTINFO. Never touches an existing file. This is the "add a hot water
  heater schedule to project 0004 block B" flow and it must be two clicks.
- **Clone building** → opens a checklist of the source building's types,
  pre-ticked and editable, then renders the chosen set under fresh numbers in the
  new building. Never copies filled-in data.
- **Remove** → out of the record, number retired, **file left on disk**. Say so
  in the confirmation.
- **Rebuild** → only offered when `state` is `missing`, or the file exists but is
  empty. Refuse otherwise.
- **Upgrade type version** → refuse if the file has data.

### 7.3 Numbering

One tab, not a second application. Two halves.

**Top: token editor.** Every token in the pattern as a row, showing its scope, its
resolved value for the current selection, and where that value came from
(company / project / building / type / schedule). Editable at the level the token
is scoped to. A pattern field with a live preview of the resulting document
number and filename underneath it. Reordering the pattern is drag or text edit,
with validation that every `{token}` resolves.

**Bottom: the schedule table.** Every schedule in the selected building: code,
number, document number, filename, volume, status, and a lock icon if the
issued-document rule (5.5) applies. Multi-select. Buttons for set, swap,
insert-at, compact, rebase, and change-building.

Any action opens the plan table from 5.6: old value, new value, blocked reason
where applicable, with Apply disabled until zero unresolved collisions. Applying
shows progress per file and reports locked files at the end.

The "swap `-PROJECTNUMBER-` for a real block reference" flow must be: edit the
building ref, see the plan, apply. Three actions, and it touches that building
only.

### 7.4 Schedule type designer

The reusable-type editor. A table of column rows, each with: three-way radio
(Input / From library / Derived), name, unit, width, example, and for derived a
formula field and a note.

Reorder by drag or up/down buttons. Live preview pane rendering the header row,
unit row and one example row exactly as they will appear.

Validation, all of it, because the renderer enforces none:

- derived formulas reference other columns as `{Field Name}`; every reference
  must resolve to a column in this type, and forward references are fine
- allowed constants only: `SETUP_LPHWF`, `SETUP_LPHWR`, `SETUP_CHWF`,
  `SETUP_CHWR`, `SETUP_CP`, `SETUP_N`, `SETUP_AMBIENT`
- reject `XLOOKUP`, `FILTER`, `UNIQUE`, `SORT`, `SEQUENCE`, `LET`, `LAMBDA` and
  anything else that spills or is post-2019; the renderer writes static formulas
  and the house rule is Excel 2007 compatibility
- no circular references between derived columns
- `degC` / `m2` / `m3` render as °C / m² / m³; show the pretty form in the
  preview, store the plain form
- a unit that is not a unit (`BS EN 1886`, `Initials`, `n`) stays in the name;
  keep the existing `NOT_A_UNIT` set
- codes unique across the catalogue, uppercase, no spaces
- at least one `input` column and at least one `library` column, or the type
  makes no sense

A notes pane alongside the column table, editing the type-level notes from 4.7.
Show the project-level notes above them, greyed, so the author can see the full
rendered block and does not duplicate generic wording.

Saving a change to an in-use type bumps the version and warns which projects are
on older versions.

### 7.5 Settings

The four shared paths. A "check" button that reports existence and writability
for each. Registry location, with a warning that changing it needs a restart.

---

## 8. The Excel side

### 8.1 Config sheet contract

The hidden `Config` sheet is the interface between Python and VBA. Keys:

```
EquipmentCode, ScheduleName, DocumentNumber
Building, BuildingRef
<the ten project fields>
path_project_info, path_equipment_library, path_submissions_folder, path_cover_template
<the seven design constants>
```

`core/sync.py` maintains all of these in place across an existing folder, with a
`.bak` before each change and a dry run first. Adding `path_cover_template` to
this list is new; make sure sync handles a key that older files do not have by
inserting a row rather than skipping.

### 8.2 Shared equipment library

`EQUIPMENT_LIBRARY_MASTER.xlsx`: an `INDEX` sheet (code, type name, a COUNTA
entry count per sheet) plus one sheet per type. On each type sheet row 1 is
field names, row 2 is units, row 3 onward is data, column A is always
`Model Reference` and is the lookup key.

`core/library.py` exposes exactly two write operations, and no others:

```python
def add_type_sheet(lib_path: Path, type_def: ScheduleType) -> bool   # append one sheet + INDEX row
def seed(lib_path: Path, catalogue) -> Path                          # only if the file does not exist
```

`add_type_sheet` must be a no-op returning `False` if the sheet exists. It must
never touch another sheet. Take a `.bak` first. Everything else goes through
`merge_submissions.py`.

### 8.3 Branded front cover

openpyxl cannot read or write drawing objects, so the generated `Front Cover` is
structurally right and visually plain. The real house cover carries branding as
images and shapes. Python will never produce it.

Fix: keep a branded `cover_template.xlsx` on the shared path. Add a macro
`ApplyCoverTemplate` that copies the template sheet into the active schedule,
writes the cover values into named ranges on it, and deletes the plain cover.
Excel preserves drawings on a sheet copy where Python does not. Add
`ExportAllPDFs`-style batch handling so it can be run across a folder.

### 8.4 Macro changes

Additions to `MEPSchedules.bas`:

- `ApplyCoverTemplate` (8.3)
- `RefreshPaths` — re-read the four paths from a small `paths.json` the manager
  writes next to the schedules folder. This is a belt-and-braces alternative to
  Python-side sync, for cases where the file is open or locked.
- `HealthCheck` — verify Config keys are all present and the library path
  resolves, and report rather than fail on first use.

Everything else stays. Do not change `RefreshLibrary`'s contract: it reads
`path_equipment_library` and `EquipmentCode`, opens the master read-only, and
copies the matching sheet into the local hidden `Library` sheet.

---

## 9. Safety rules

Non-negotiable. Each one gets a test.

1. Never overwrite an existing `.xlsx` in a live schedules folder. A `force`
   flag exists for the one case where it is right, and it requires typed
   confirmation of the filename.
2. Never write to `EQUIPMENT_LIBRARY_MASTER.xlsx` except via
   `library.add_type_sheet`, `library.seed` on a non-existent file, or
   `merge_submissions.py`.
3. Never delete a user file. "Remove" means remove from the record.
4. `.bak` before every in-place workbook edit, and never overwrite an existing
   `.bak`.
5. Every destructive op returns a plan on `dry_run=True` and the GUI shows it
   before the real call.
6. A locked file (open in Excel) is reported and skipped, never retried into a
   corrupt state.
7. Registry writes are atomic: write `.tmp`, then `os.replace`.

---

## 10. Migration from v1

`core/migrate.py`:

- `import_schema(schema_json_path)` → split into per-type catalogue files at
  version 1, dropping the dead `number` field, converting the three parallel
  field lists into the unified `columns` array.
- `import_project(project_json_path, schedules_root)` → walk the root. Loose
  `.xlsx` files at the root mean a single-building project; subfolders mean one
  building per subfolder, with the folder name as the candidate `ref`. Confirm
  each ref against the `building` token position parsed out of the filenames, and
  report any disagreement rather than guessing. Its `schedules` are
  reconstructed by reading `EquipmentCode` and `DocumentNumber` out of each
  existing file's Config sheet, and `retired_numbers` inferred from gaps.
  Importing a multi-block job means running this once per block folder and
  merging, which is acceptable for a proof of concept.
- `scan_original(path)` → report on a hand-made file before anything else:
  every external link, any `CELL("filename")` document-number formula, any
  dynamic-array function, whether it carries drawings, and whether
  `Metadata!ScheduleName` points at the right cell. Run it across a folder and
  print a table. This is the "how bad is it" tool and it is read-only.
- Migration **generates fresh files** from the extracted content. It never edits
  a hand-made original in place: openpyxl loses shapes and drawings on save, so
  a round-trip would silently strip the branding.
- After import, `sync` repoints the generated files at the shared library.

The eight sample schedules in the repo are the test fixture for this.

---

## 11. Build order

Do not build it all then test. Each phase ends with its checkpoint passing.

**Phase 1 — core skeleton.** `paths`, `registry`, `catalogue`, `validate`.
Migrate `schema.json` into per-type files.
*Checkpoint:* round-trip all eight types to catalogue files and back to the
legacy field shape, byte-identical.

**Phase 2 — numbering and render.** `numbering`, `render` refactor, `build`,
the revision sort key (6.1), the building rows (4.6), the two-source notes block
(4.7).
*Checkpoint:* rebuild the eight sample schedules from the catalogue via the new
path and diff against the originals; only the intended additions differ. Then
section 12 steps 20 and 21.

**Phase 3 — library and sync.** `library`, `sync`, `register`.
*Checkpoint:* two projects, one shared library, add a ninth type to one of them,
library gains one sheet, the other project's files are untouched.

**Phase 4 — project GUI.** Projects, folders, settings, build, sync.
*Checkpoint:* create a project with one building end to end without touching a
JSON file, files landing directly in the schedules root, building layer invisible.

**Phase 4b — buildings.** Add, clone with checklist, promote and demote, per
building folders and numbering.
*Checkpoint:* section 12 steps 6 and 7, with the promotion move verified as
non-destructive.

**Phase 5 — schedules GUI.** Per-project selection, add/remove/health.
*Checkpoint:* add a schedule to an existing populated project, one new file,
nothing else changes.

**Phase 6 — numbering and naming.** Scoped tokens, buildings, clone building,
renumber ops, the rename cascade, audit.
*Checkpoint:* section 12 steps 13-18.

**Phase 7 — designer.** Type editor with full validation.
*Checkpoint:* create a new type from scratch, add it to a project, open the file
in Excel, confirm the derived column calculates and the dropdown pulls from the
library.

**Phase 8 — cover template and macro additions.**
*Checkpoint:* section 12 step 9.

---

## 12. Acceptance test

Run it manually, top to bottom, on a clean `C:\MEPTest\`.

1. Fresh registry. Shared paths under `C:\MEPTest\library\`.
2. Migrate the v1 `schema.json`. Eight types in the catalogue, all at version 1,
   no `number` field anywhere.
3. Designer: create three real types the catalogue lacks. `GASB` gas boiler,
   `ASHP` air source heat pump, `CHIL` chiller. Each needs at least 4 input, 5
   library and 2 derived columns, with one derived formula using `SETUP_CP` and
   one using `SETUP_LPHWF`/`SETUP_LPHWR`. Set volume `5.6` on all three from the
   lookup. Confirm the designer rejects an `XLOOKUP` formula, an unresolvable
   `{Field Name}`, and a duplicate code.
4. Create project `CM4220`, schedules root
   `...\CM4220\Documents\Schedules`, one building `HQ049`. Add MVHR, FCU and
   GASB. Three files **directly in `Schedules\`**, numbers 10, 11, 12. Confirm
   the GUI hides the building layer.
5. Type data into HQ049's MVHR schedule and save.
6. Add building `HQ014`. Promotion fires: HQ049's three files move into
   `Schedules\HQ049\`, filenames unchanged, typed data intact, the building
   selector appears. Give HQ014 MVHR, AHU and ASHP: a partly different set.
   Three files in `Schedules\HQ014\`, numbers restarting at 10, 11, 12.
7. Clone HQ014 as `NB17`, untick ASHP, tick CHIL and EWH. Four files in
   `Schedules\NB17\` at 10 to 13. HQ014's files are byte-identical. NB17's
   MVHR contains no data from anywhere.
8. Remove FCU from HQ049. Number 11 is retired, the file is still on disk.
   Add EWH to HQ049. It gets number 13, not 11.
9. Move `C:\MEPTest\library\` to `C:\MEPTest\shared\`. Update Settings. Sync both
   projects. All files repointed, typed data intact, `Front Cover` formulas
   intact, `.bak` files present.
10. Open project 0004's MVHR in Excel. `RefreshLibrary` pulls from the moved
    library. `ApplyCoverTemplate` produces a branded cover. Add a revision log
    row, `FreezeForIssue`, PDF the frozen copy.
11. Register view lists all schedules across both projects with correct
    revision, status and issue date.
12. Delete `registry.json`, re-import both projects from their `project.json`
    and schedules folders. Records reconstructed including retired number 11.
13. Change HQ014's ref to `HQ015`. The plan lists HQ014's three schedules and
    nothing from HQ049 or NB17. Apply. Filenames, the folder name and
    `Config!$B$4` all updated, Metadata and Revision page still resolve, the
    register is still correct, HQ049's files are byte-identical.
14. Delete HQ015 as a building. Its files stay on disk. Confirm the project does
    NOT demote back to single-building automatically with two buildings left.
15. Confirm the AHU schedule picked up volume `5_7` from its type and the
    radiator schedule `5_6`, without either being set by hand.
16. Swap two schedules' numbers within a building. Then add a revision log row to
    one, set it to S2, and attempt to renumber it. It must refuse until the
    filename is typed.
17. MAINPROJECTINFO for CM4220 lists every building's schedules with the
    Building column populated, and the register view groups by building.
18. Audit reports clean on all buildings. Then rename one file in Explorer by
    hand and re-run: audit must report the filename disagreeing with
    `Config!$B$4`.
19. `scan_original` on the supplied Radiant Panel file reports both external
    links, the `CELL("filename")` document number, the dynamic-array revision
    formulas, the drawings, and the `ScheduleName` mis-reference.
20. Build a Radiant Panel schedule from the catalogue. Its A2 notes block shows
    project notes then the four radiant-panel-specific notes, numbered
    continuously. Front Cover and Revision page both show the building.
21. In a built schedule add revision rows P01, P02, then C01. The cover shows
    C01 and its date. Delete C01, add P03 above P02 out of order: the cover
    shows P03. This is the case both existing implementations get wrong.

---

## 13. Out of scope

Web app, logins, database, browser-based editing, IFC/COBie export, multi-user
locking. Keep the data model clean enough to migrate — `core/register.py` already
produces the row shape a web viewer would serve, and the catalogue is a database
schema in JSON form — but build none of it now.

One thing to be aware of for later, so you do not design yourself into a corner:
**PDF export is the hard part of the web version, not the easy part.** Excel
currently gives pagination, repeating headers and A4 fitting for free. HTML
renderers do not. Nothing in this build should assume PDF generation is trivial.
