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

## Files SPEC.md references that were not supplied

Flagged rather than worked around:

- `mep_core.py` and `mep_manager.py` — SPEC.md section 2 says these are "already
  written for you ... in the repo". They are not in the upload. Not a blocker
  under the new architecture, since the layer they implemented is being replaced.
- **The hand-made Radiant Panel original** and its `MAINPROJECTINFO.xlsx` —
  SPEC.md 1a calls these "the ground truth for house format", and acceptance
  steps 19 and 20 depend on them. Without them, `scan_original` has no fixture
  and the house-format claims in 1a cannot be re-verified. The Radiant Panel
  **column list** is recoverable from 1a and is seeded into the catalogue; the
  branding, the external links and the `CELL("filename")` formula are not.
- `cover_template.xlsx` — expected on the shared path, supplied by the firm.

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
