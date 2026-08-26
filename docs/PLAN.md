# Implementation plan for the remaining backlog

Phase 1 (P0.1–P0.5) is done. This is the plan for everything else, ordered by
dependency rather than by the brief's phase numbers, because several items touch
the same seam and doing them apart would mean two passes over the same code.

## Decisions taken where the brief left a question

I asked three questions that would otherwise block. Rather than stall, each is
implemented as a **configurable mechanism with a stated default**, so the code is
right and only the value needs your confirmation.

| Question | Decision | How to change it |
|---|---|---|
| Volume → discipline mapping | Implemented as a lookup, seeded `5.2`/`5.3` → **P**, `5.4`/`5.5`/`5.6`/`5.7` → **M**. `5.4` and `5.5` are added to the volume list. | Settings → Volumes |
| Default start number | Left at **10**. Your real files start at 9/10, so changing the shipped default would surprise; it is already per-organisation. | Settings → Document numbering |
| What counts as "issued" | An explicit **Issue** action, not "any revision row". It matches how a document leaves the office and gives the issued-document lock a precise moment to attach to. | — |

Per-volume numbering is a **setting** (`numbering_scope`), defaulting to the
current per-building behaviour. The brief says separate sequences "can exist",
so making it opt-in keeps existing numbering stable and lets a firm choose.

## Order, and why

### 1. Column model — the foundation
`P2.3` visibility, `P2.4` project-specific columns, `P1.5`/`P2.5` library
overrides.

All three change what "the columns of this schedule" means, and the grid, the
renderer and the formula validator each derive from that. Doing them together
means one `merged_columns()` function that everything reads, instead of three
places learning about extras separately.

- `Column.visibility` — `{editor, xlsx, pdf}`, absent means visible everywhere,
  so no migration.
- `Project.type_extras` — `{type_code: [column…]}`, appended to the base type.
  Additions only: a project cannot remove or reorder base columns, or the
  catalogue stops meaning anything.
- `ScheduleRow.overrides` — a separate column from `values`, so the guard that
  strips client-supplied computed values stays intact and "is this overridden"
  is a lookup rather than a guess.

### 2. Export polish
`P1.2` print theme, `P1.3` layout, `P1.4` branding, `P2.10` initials,
`P2.14` MAINPROJECTINFO review.

Depends on the column model, because visibility decides which columns reach the
`.xlsx` and the PDF.

- Export gains a `theme` — `working` keeps the editing colours, `issue` uses
  neutral fills. Issue is the default for PDF.
- Column widths come from the content, not just the declared width, so nothing
  arrives clipped.
- Branding lives on the house standard: logo, colours, fonts. Applied to the
  cover and revision page.

### 3. Revisions
`P2.6` snapshots, `P2.7` diff, `P2.9` bulk bump.

Snapshots must store the **computed** values, which means they must be taken
after the column model settles, or an issued document could not be reproduced.

### 4. Register and reporting
`P2.11` project-first, `P2.12` search, `P2.13` room summaries.

Room summaries need to know which column holds the room, which is a column-model
question — hence after 1.

### 5. Numbering
`P2.15`. Deliberately last and self-contained: it carries the only migration,
and it should not be tangled with anything else when it lands.

### 6. Designer and library
`P2.1` preview editing, `P2.2` type-to-library, `P1.7` library audit.

Polish on top of a settled model.

## What stays true throughout

- Business rules stay in `core/`, which imports nothing from the web or database
  layers — enforced by a test.
- The browser and the exported workbook must agree. Every change to computation
  is checked by handing a workbook to LibreOffice and reading the values back.
- Nothing is overwritten silently, and destructive actions preview first.
- Everything stays scoped by organisation.
