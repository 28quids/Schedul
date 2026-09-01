# Architecture decisions

`docs/SPEC.md` is the original build specification. It describes a Windows
tkinter desktop tool where **the workbook is the record**. This document records
where we now depart from it, and why. Where the two disagree, this document
wins; everything SPEC.md says that is not contradicted here still stands.

---

## The five facts in SPEC.md section 1, confirmed

All five verified against the shipped v1 code in `vendor/`:

1. **Numbering is positional.** `build_project.py:625` —
   `num = str(dn["number_start"] + i).zfill(dn["number_width"])` over
   `enumerate(schema["equipment_types"])`. Every type in the schema is built,
   every time. Confirmed: this is what blocks per-project schedule selection.
2. **It deletes its output folder.** `build_project.py:618` — `shutil.rmtree(out)`.
   A scratch generator, not an incremental one.
3. **Paths are frozen into each workbook at build time.** Confirmed by reading
   the `Config` sheet of a generated file: rows 15-17 hold `path_project_info`,
   `path_equipment_library`, `path_submissions_folder`. `RefreshProjectData`
   (`MEPSchedules.bas:108`) writes `Client`, `Project Name`, `Project Number` and
   any key/value rows from Setup row 6 down — **it never touches the path rows**.
   Move the library and every file across every project breaks silently.
4. **`schema.json`'s `number` field is dead.** Types claim `0001`-`0008`; the
   generated files carry `00000010`-`00000017`. The builder never reads it.
5. **openpyxl round-trips generated files losslessly.** Verified: load and save
   the MVHR schedule, zero zip parts gained or lost, data validations, defined
   names, print titles (`'Schedule'!$1:$5`) and hidden-sheet states all intact,
   no warnings raised.

Reading of "three of these are traps": 1, 2 and 3 are the live hazards — each
one silently destroys or misaligns work. 4 is dead weight, and 5 is the enabling
fact that made in-place repair viable under the old architecture.

**Also confirmed, and fixed here:** the path escaping bug SPEC.md 4.4 flags is
real. `project.json` holds `"C:\\\\SharePoint\\\\..."`, which decodes to a
literal `C:\\SharePoint\\...` with doubled separators, and that string is written
verbatim into every workbook's Config sheet.

**One thing the spec gets wrong about its own generator:** SPEC.md 1a.6 says not
to carry the real file's `Metadata!ScheduleName` bug into the generator. The
generator never had it — generated files point `Metadata!B3` at
`'Revision page'!B22`, which is the schedule name, correctly.

---

## What the real hand-made file actually contains

`mep_core.py`, `mep_manager.py`, a hand-made original and a `MAINPROJECTINFO.xlsx`
were supplied later and are now in `vendor/`. The original is a **Radiators**
schedule (document `...-00000009-...`), not the Radiant Panel one SPEC.md 1a
describes. Examined directly, it contradicts several of 1a's claims. 1a was
evidently written about a different file, so **do not treat it as a description
of house originals in general.**

**Confirmed by 1a:**

- Title in A1, general notes in A2, field names row 4, units row 5, data from
  row 6, print titles `$1:$5`. The autofilter `Schedule!$A$4:$M$26` pins row 4
  as the header independently.
- Notes are per equipment type, not per project. This file's read
  "radiators are to be sized with a 55oC flow and 45oC return", "to be Stelrad
  K2 or equal and approved" — equipment-specific and firm-specific wording, next
  to generic compliance text. This is the strongest evidence for the two-source
  notes block in 4.7.
- No Building field anywhere.
- openpyxl does warn `Shapes and drawings will be lost`, so a hand-made original
  must never be round-tripped through Python.

**Contradicted by the file:**

| SPEC.md 1a says | The file has |
|---|---|
| Document number derived via `CELL("filename")` | **No formulas at all.** Zero `<f>` elements across all three sheets. |
| Revision logic using `LET` / `XLOOKUP` / `XMATCH` / `TEXTBEFORE` | No formulas, so none of this exists |
| A structured table `RevisionTable[Revision]` | No tables |
| `Metadata!ScheduleName` points at the wrong cell | **There is no Metadata sheet.** Three sheets only: Front Cover, Revision page, Schedule |
| Two external links, one to a personal OneDrive for MAINPROJECTINFO | **One** external link, a UNC path to a Design Risk Management Schedule, feeding a `dropdownPick` defined name |
| Branding carried as drawing objects | One freeform line shape. No images |

The revision-ranking bug in 6.1 is therefore **not** present in this original —
it cannot be, because the file computes nothing. The fix still matters: it is
real in the v1 generator, which is what actually produces schedules.

**Two things the file reveals that the spec does not mention:**

1. **It is a blank template.** No data rows, no prepared/checked/approved, no
   document number, no suitability status. Only a revision `P01` and a date.
2. **Its filename disagrees with its contents.** The filename carries project
   number `Z9A6461Y19`; `Revision page!B11` says `Z9A6432Y19`. This is exactly
   the drift `numbering.audit` exists to catch, found in the first real file we
   looked at.

**Consequence for importing existing spreadsheets.** Because a real house file
is values with no formulas, importing one is reading a rectangular block of
data, not reverse-engineering a calculation model. That is a far smaller problem
than it appeared. See the "Importing existing spreadsheets" section of `docs/GOING-ONLINE.md`.

Still not supplied: `cover_template.xlsx`, expected on the shared path from the
firm's own branding.

---

## Decision 1: the database is the record, not the workbook

**SPEC.md:** each `.xlsx` is the record. JSON files index them. Repair happens
in place, across folders, guarded by `.bak` files, dry runs and lock detection.

**Now:** the database is the single source of truth for project data, schedule
data, equipment and revisions. A `.xlsx` is a **rendered export** — a deliverable
you issue, not a store you edit and read back.

Driven by the requirement that a user must never "download the excel, click
macros, then upload it back onto the web platform". Once data lives in a
database, that round trip has nothing left to do.

What this deletes outright:

| SPEC.md machinery | Why it goes |
|---|---|
| `core/sync.py`, Config-path repair | Nothing reads paths out of a workbook any more |
| `.bak` / lock detection / dry-run plans for in-place edits | No in-place workbook edits exist |
| `RefreshProjectData`, `RefreshLibrary` macros | The editor already has current data |
| Submissions inbox as JSON files on a share | A table with a review flag |
| `MAINPROJECTINFO.xlsx` as the live central record | Becomes an export like any other |
| Power Query register | The register is a page |
| Safety rules 1, 2, 4, 6 (SPEC.md section 9) | They guard a file-as-record model |

Safety rules **3** (never delete a user's data — "remove" means remove from the
record), **5** (destructive ops preview before acting) and **7** (atomic writes)
survive, re-expressed against the database.

The exported workbook is **macro-free and self-contained**. That is strictly
better as a client deliverable than the v1 `.xlsx` + `.xlam` pairing.

## Decision 2: dry runs become previews, not a second call

SPEC.md section 0 mandates dry-run-then-apply for every destructive operation,
because those operations walked a folder of files that might be locked, missing
or half-written. Against a transactional database that shape is overkill.

Kept where the user needs to see consequences before consenting — renumbering,
renaming a building ref, deleting anything with data under it — as a computed
plan the UI shows and the user confirms. Dropped as blanket policy. A rename that
would have been "write cell, save, `os.replace`, restore from `.bak` on failure"
across N files is now one transaction.

## Decision 3: derived formulas get one AST and two backends

The one genuinely new engineering problem the web editor creates.

A derived column such as
`={Total Power Input (W)}/{Supply Airflow (l/s)}` must now both:

- **evaluate in the app**, so the grid shows a number as the user types, and
- **emit an Excel formula**, so the exported workbook still calculates when an
  engineer opens it and changes a value.

Two hand-written implementations would drift. So `core/formula.py` parses the
house formula syntax once into an AST, and two emitters walk it: `to_excel()`
and `evaluate()`. A test asserts both agree across every formula in the
catalogue. The parser is also the validator SPEC.md 7.4 asks for — an
unresolvable `{Field Name}` or a banned `XLOOKUP` fails at parse time, in one
place, rather than being re-checked by the designer.

This is what makes SPEC.md section 13's warning tractable rather than fatal.

## Decision 4: PDF via headless LibreOffice

SPEC.md section 13 warns that PDF is the hard part of a web version, because
Excel gives pagination, repeating headers and A4 fitting for free and HTML
renderers do not.

Sidestepped: we do not render HTML to PDF. We render the real `.xlsx` — with the
print titles, `fitToWidth`, A4 landscape and page setup the vendored code already
gets right — and convert it with headless LibreOffice, which honours all of it.
The A4 fitting stays Excel's, exactly as it is today.

Native "Microsoft Print to PDF" remains available: the exported workbook is
ordinary Excel and prints correctly.

## Decision 5: organisation is the top-level tenant

The user's constraint: *"build this as if it's going to be a commercialised
project and not just an internal tool ... whichever way works best for multiple
organisations."*

So `organisation` scopes everything from day one — house standard, catalogue,
equipment library, projects, users. This is SPEC.md 4.5's ambition ("a second
firm is a second profile, not a fork") enforced by a foreign key instead of by
discipline. Retro-fitting a tenant boundary is expensive; adding it now is nearly
free.

Consequences already taken:

- No company-specific value sits outside the house standard. SPEC.md 4.5 calls
  any such value a bug; it is now also a schema violation.
- The equipment library is per-organisation. Firm A's approved products are not
  Firm B's.
- Deployment runs single-tenant on localhost today with one seeded organisation,
  and the same code serves many later.

## Decision 6: one schedule covers one building

The open question SPEC.md 4.6 requires answering before buildings are built.
Answered: **document-level**. Building is a property of the schedule, not a
column on a row. Buildings own their schedules, numbering restarts per building.

The user notes the rare cross-building case exists but is "just filing". If it
ever needs modelling, the cheap route is to let one schedule belong to a set of
buildings for labelling purposes only, without making Building a row-level input
or dissolving buildings as containers.

## Decision 7: equipment auto-saves, flagged for review

SPEC.md's submissions-inbox pattern existed to stop concurrent writes corrupting
a shared `.xlsx`. A database does not have that problem, so the queue no longer
has to gate use.

New equipment entered on a schedule is saved to the organisation's library
immediately and is usable at once. It lands in a review list where a nominated
engineer merges duplicates, fixes spelling drift, or rejects. The
`merge_submissions.py` intelligence worth keeping is its **detection** —
NEW / DUPLICATE / CONFLICT / CANNOT and spelling drift (`GRUNDFOS` against an
existing `Grundfos`) — which becomes the review list's ranking, not a gate.

## Decision 8: no tkinter

SPEC.md section 3 specifies a `gui/` tkinter package and section 7 details its
screens. Skipped entirely. SPEC.md section 0's own logic is the argument: if the
GUI is a shell over `core/`, then building the throwaway shell first buys
nothing when the target is known to be a web app.

The rule that survives, and matters more than ever: **`core/` imports nothing
from the web or database layers**, enforced by an AST test over every file in
`core/`. That is what keeps the domain logic portable.

---

## What is kept from v1, deliberately

- **The sheet-construction code.** ~350 lines of verified Excel minutiae —
  A4 portrait covers and landscape schedules, the Verdana 30pt title block, the
  two-row header with units split onto row 5, print titles `$1:$5`,
  `fitToWidth=1 / fitToHeight=0`, the blue/green/black colour contract. Refactored
  at its interface, not its body, exactly as SPEC.md section 6.2 instructs.
- **The three column kinds** (`input` / `library` / `derived`) and their mapping
  onto COBie/IFC Component vs Type data. This is what makes an IFC or COBie
  export possible later, and it is now a database schema rather than a JSON one.
- **Scoped naming tokens** (SPEC.md 5.2) and the ISO 19650 alignment. The product
  is a configurable implementation of an existing standard, not a new one.
- **The revision ranking fix** (SPEC.md 6.1). Both v1 implementations get "show
  the most recent revision" wrong; the real file sorts `C01` *below* every
  preliminary revision. Ranked by series then number, in the app and in the
  exported helper column.
- **The house standard separation** (SPEC.md 4.5), now the tenant boundary.
- **`merge_submissions.py`'s detection logic**, as review-list ranking.

## Status of the SPEC.md acceptance test

Section 12's 21 steps stay the functional target. Steps that assert file-level
mechanics of the old architecture (9's `.bak` files and re-pointing, 10's
`RefreshLibrary`, 12's rebuild-from-folders) are re-expressed against the
database. The substantive ones — per-building numbering restarts, promotion to
multi-building, clone-with-checklist, retired numbers not being reused, volume
following the type, the issued-document lock, and **steps 20 and 21** (the notes
block and the revision ordering both v1 implementations get wrong) — are all
still exactly the right tests.

---

## Consequences worth recording

**SPEC.md 4.3.1's promotion trap evaporates.** The spec devotes a section to
what happens when a single-building job gains a second building: building 1's
files must move from `Schedules\` down into `Schedules\<ref>\`, which needs
`promote_to_multi_building`, a dry run, lock detection and a
`demote_to_single_building` for the mistaken case. None of that is needed now.
Files are generated on demand into an export folder, so adding a building is one
row. The folder convention still governs **export layout** — a single-building
project exports flat, a multi-building one exports into per-building folders —
but that is decided at export time from current data, not migrated.

**Renaming a building ref stops being a cascade.** SPEC.md 5.3 makes it a mass
rename across every schedule in that building: write `Config!$B$4`, `os.replace`
the file, restore from `.bak` on failure, rewrite MAINPROJECTINFO. Now the
document number is derived from the tokens whenever it is needed, so changing a
ref is one field. The plan preview is kept anyway, because the user still wants
to see which documents change identity before consenting — that was always the
valuable half.

**The issued-document lock matters more, not less.** With renaming this cheap,
the only thing stopping someone silently changing the identity of a document
already issued to a client is the lock in SPEC.md 5.5. It is enforced in
`core/numbering.py`, not in the UI.

---

# V1.2

Five things were unsatisfying about V1.1 in use, and each has a decision behind
how it was fixed rather than a pile of features.

## The grid is a spreadsheet, and the rules that say so live outside the DOM

The grid had one editable cell at a time. Everything an engineer does to a
schedule — copy a block, clear a range, fill a column — was either impossible or
a per-cell chore, so people exported to Excel, worked there, and pasted back.

It now has an active cell and a rectangle around it. What matters is where the
rules live: `frontend/js/grid/` holds the selection model, the keyboard rules
and the block-paste planner as plain modules with no DOM in them, tested under
Node and run from `pytest`. "Shift+Down twice then Left once" has an exact
answer, and a rule that is only ever exercised by hand is a rule that quietly
stops working.

There is still no build step. Node is a test dependency, not a runtime one, and
those tests skip themselves when it is absent, exactly as the ones needing
LibreOffice do.

## A destructive operation is planned, shown, and only then performed

`core/tabular.py` plans a paste: how many rows, whether the first line is a
header, what would be appended, inserted or removed, and how much of what would
go carries typed values. The same planner produces the preview and performs the
paste, so the numbers somebody confirms are the numbers that happen. Replacing
every row is refused unless confirmed, and only when there is something to lose.

The library importer works the same way, because the same thing is true of it: a
careless column mapping can overwrite a hundred correct values in one click.

## Undo is a restore, not an inverse

`services/history.py` records the rows before and after each risky edit. The
alternative — an inverse operation per action — has to be right separately for
paste, delete, duplicate and fill, and the one that is subtly wrong is the one
that loses somebody's work. A schedule is tens of rows of small JSON, so a
whole-schedule snapshot is cheaper than being clever. Twenty deep, per schedule,
and a new edit discards the redo stack as every spreadsheet does.

Keystroke saves are deliberately not on the stack: the browser's own undo
reverses typing, and filling the stack with it would bury the paste somebody
actually wants back.

## Notes layer, and a schedule says whether it has diverged

Four layers, general to specific: the practice's standing wording, what the job
adds, what the equipment type says, and what one document says instead.
`core/notes.py` resolves them and labels each line with where it came from,
because "why does this schedule say that" is the question and an unlabelled list
cannot answer it.

A schedule that overrides **replaces** the resolved set rather than appending to
it. The reason to override is usually that one inherited note is wrong for this
document, and a model that can only add cannot express that. Reverting therefore
always means the same thing: drop this schedule's own copy.

## Branding is configuration, not a canvas

SPEC.md's warning stands: the hand-made branded originals carry drawing objects
that cannot be round-tripped through openpyxl. A freeform document designer would
either lose them or lie about what it produced.

So `core/branding.py` offers the decisions the renderer can carry out honestly —
a logo, a font from a list every machine has, a palette, and which cover and
revision-page fields appear and in what order. Hiding Building Number is a
checkbox; moving it two inches left is not on offer, and saying so plainly is
better than half-doing it.

The important guarantee: a field the workbook reads by formula cannot be hidden.
The settings screen shows it as fixed and the resolver refuses to drop it, so a
configured document can never be produced with a broken reference in it.

## An export is a document, not a working file

The colour contract — blue on yellow you type, green from the library, black
calculated — is an editing aid. It carries meaning while a schedule is being
filled in and makes an issued PDF look like somebody's screen. Exports now
default to a neutral print theme, xlsx as well as PDF, and `?theme=editor`
returns the working copy. The two hold identical values; only the styling
differs, and there is a test that says so.

Three export defects were found by rendering real PDFs and looking at them,
which is the only way any of them would have been found:

- the Metadata sheet carries no print area, so LibreOffice printed it — two
  pages of key/value pairs in front of every cover. Hidden on an issued
  document, still readable by anything that consumes it.
- a 30pt title ran off the page and over the line below it on any schedule with
  a long name. It wraps to two lines now, and shrinks only if that will not fit.
- an issued schedule carried the house standard's forty ruled empty rows under
  six units. A working file still does, because somebody is about to fill them
  in.

## Sharing is the feature, so the change log is not optional

A library value is read rather than copied. A type's columns are the type's. The
house notes print on every document. That is what makes the tool worth using and
it is also why somebody opens a schedule they have not touched and finds it
different.

Every one of those changes was already recorded where it happened — a type's own
history, the library's change log — and nowhere a person would look.
`services/impact.py` assembles them into one page, adds the standing condition
that is not an event at all (schedules built before a change), and the designer
dry-runs a column change against the schedules already using it before saving.

`core.compare_columns` separates a presentational change from one that can
orphan typed values: widths and order propagate and are meant to, while a rename
leaves rows keyed under a name nothing reads any more, and that is worth saying
out loud rather than discovering later.

## Additive schema upgrades

`Base.metadata.create_all` never alters an existing table, so a column added
after somebody has been using the tool would simply not be there. `db/upgrade.py`
compares the models against the database and adds what is missing. Deliberately
one-directional: nothing there drops a column, renames one, or rewrites a value.
An additive change is the only kind that cannot destroy somebody's work, and
anything beyond it deserves a real migration tool and a backup.

## The cover was two pages, and nobody could see why

The front cover began on row 11, and its print area was a fixed `A1:G50`. Fifty
rows of fifteen points plus two title rows of forty and eighty-seven is about
850 points; an A4 page with the default margins holds about 734. Every cover the
tool produced spilled onto a second sheet, and the ten blank rows at the top
were doing nothing but making it worse.

Both are gone. The fields start on row 1, the print area follows the content,
and the title block is placed in the middle of the space left below the fields
rather than at a fixed row 41 — pushed down only when a long field list leaves
no slack, because overflowing a page is worse than not being centred.
`fitToHeight = 1` on the cover is the guarantee behind the arithmetic: a cover is
one page by definition, so it says so rather than hoping.

The revision page had the mirror problem in the other direction — the log sat at
a hardcoded row 44 with a note in the gap explaining that the summary rows derive
from it. The note was true and belonged in the code, not on a document somebody
issues, so the log now follows the summary block by two rows and the gap closed
with it.

## Block capitals, and the building in the title

A house title block is set in capitals whatever was typed into the project form,
and on a job of several blocks the block is half of what identifies the
document. `MOTE ROAD - BLOCK A`, stored once on Config and read by both title
blocks, so a rename is still one write.

Only a building's *name* is appended. A single-building job carries a reference
the manager allocated rather than a block anybody named, and `HEAD OFFICE - CM1`
says nothing the document number does not.

## Hiding a column is a decision about one document

Column visibility already existed on the catalogue type: where a column belongs
in general. What was missing was the answer to "keep the cost off the copy that
goes to this client", which is about one schedule, and pushing it up to the type
would change every schedule in the practice to settle a question about one.

So a schedule carries its own show/hide map, per target — screen, Excel, PDF —
folded in before the target filter, so `columns_for(schedule, target="pdf")` is
the honest answer wherever it is asked. Two columns are refused rather than
half-hidden: the lookup key, and any column a visible formula reads. The
renderer also guards the emit itself, leaving such a cell blank rather than
writing a reference to a column that is not on the sheet — a workbook with
`#REF!` in it is worse than one with a blank column in it.

## Branding is house standard, except the part that legitimately is not

The branding screen offered "hide what a job does not need — Building on a
single-building project, for instance", of a setting that reached every job in
the practice. Either the note was wrong or the setting was in the wrong place.

The setting was in the wrong place, but only partly: which fields a document
carries is a decision about a document and two jobs differ, while fonts, colours
and the logo are exactly what a house standard is for. So `core.branding`
names the keys a project may answer — `PROJECT_KEYS` — and merges the show/hide
maps key by key rather than replacing them, so a project hiding Building does
not silently un-hide everything the practice had hidden. A project is validated
as a whole branding payload, so it can no more hide a row the workbook reads
than the organisation can.

Three states per field, not two. A project that stored a plain boolean would
freeze its answer at whatever the house standard said the day somebody opened
the screen, and stop tracking the practice without being asked to.

## The database left the source folder

`SCHEDUL_DATA` defaulted to `./data`, relative to wherever the server was
started — which is inside the checkout. Updating by pulling was fine. Updating
the way most people update a tool they downloaded — fetch the new zip, unpack it
somewhere else, run it — left the equipment library, the projects and the
branding in the old folder and came up looking like a first run. Nothing was
deleted and there was nowhere to go and look, which from where the user is
standing is the same thing.

The default is now the per-user data directory every other desktop application
uses, and a database in the old location is copied up to it once on first start.
Copied rather than moved: a migration that goes wrong should leave the original
where it was. Settings shows the path and hands out a backup taken through
SQLite's own backup API — a plain file copy of a database being written to is one
that may not open, and a backup nobody can restore is worse than none for what
it is believed to be.

## A spreadsheet's fill handle, and a header row you can copy

Two things people already know how to do that the tool made them learn again.

Dragging a reference down is the single most common act on a schedule, and it
needed finding a Fill series button in a toolbar. The selection now carries the
corner handle: series by default where the value ends in digits, Ctrl to copy,
upwards to count down, and a chip afterwards offering the other one. The rule
itself stays in `core/references.py`; the browser sends a direction and a count.

Getting a supplier's range into the library meant pasting a block into a
textarea and mapping columns you had not seen yet. The library now hands out the
spreadsheet with the headings already on it, and reads the filled-in file back
through the planner the paste importer already uses. One renderer for the blank
template, one type's export and the whole library, because two would eventually
disagree about the headings and the file that came back would stop matching the
file that went out.
