# Schedul

MEP equipment schedule manager. Projects, buildings, ISO 19650 document
numbering, a shared equipment library, and Excel and PDF deliverables that match
your house format.

The database is the record. A schedule is filled in the browser, and the
workbook is an export of it — so there is no download, run a macro, upload it
back again.

---

## Running it

Needs Python 3.11+. LibreOffice is optional and only used for PDF export.

```bash
cd backend
pip install -e .
python -m uvicorn schedul.api.main:app --reload --port 8000
```

Then open <http://127.0.0.1:8000>.

The first run creates the database and seeds an organisation with nine schedule
types (the eight from the v1 `schema.json`, plus Radiant Panel).

### Where your data lives, and keeping it across updates

**The database is not inside this folder.** Everything — projects, the equipment
library, branding, every schedule — is one SQLite file kept in a per-user data
directory:

| Platform | Path |
|---|---|
| Windows | `%LOCALAPPDATA%\Schedul\schedul.db` |
| macOS | `~/Library/Application Support/Schedul/schedul.db` |
| Linux | `~/.local/share/schedul/schedul.db` (or `$XDG_DATA_HOME/schedul`) |

That is the answer to "why is everything gone when I download the new version".
It used to be `backend/data/schedul.db`, inside the checkout, so updating by
downloading a fresh zip into a new folder left the record behind in the old one.
It was never deleted, but there was nowhere obvious to go and find it. A database
in the old location is copied up to the new one automatically the first time this
version starts, and the original is left where it is as a spare.

So: **update however you like.** `git pull`, or download a new copy and delete
the old folder — the database is not in it.

Settings → Your data shows the exact path, and hands out a backup taken through
SQLite's own backup API (safe to take while the server is running). Keep one
before anything risky. To restore, stop the server and put the file back at that
path as `schedul.db`.

`SCHEDUL_DATA` overrides the directory. Pointing it at a synced folder —
OneDrive, Dropbox — is also how two machines share one record, though one at a
time: SQLite over a sync client does not take concurrent writers.

### PDF export

PDF conversion runs the real `.xlsx` through headless LibreOffice, so pagination,
repeating header rows and A4 fitting stay Excel's own rather than being
re-derived by an HTML renderer.

- **Windows:** install LibreOffice; it is found automatically in
  `C:\Program Files\LibreOffice`.
- **Linux:** `apt-get install libreoffice-calc`
- Anywhere else, point `SCHEDUL_SOFFICE` at the `soffice` binary.

Without it everything else works and the sidebar shows "Excel only". The exported
workbook is ordinary Excel and prints to PDF from Excel.

### Configuration

| Variable | Meaning |
|---|---|
| `SCHEDUL_DATABASE_URL` | Defaults to SQLite under `backend/data/`. Point at PostgreSQL to move to a server. |
| `SCHEDUL_DATA` | Where the SQLite file lives. Defaults to the per-user directory above, never inside the checkout. |
| `SCHEDUL_SOFFICE` | Path to LibreOffice, when it is not on `PATH`. |

---

## How it fits together

```
backend/schedul/
  core/       the domain. No web, no database, no UI — enforced by a test
    formula.py    the house formula language: one parser, two backends
    catalogue.py  schedule types, the three column kinds, and what a change to
                  them would break
    naming.py     scoped tokens -> document number
    numbering.py  allocation and the renumber operations
    revisions.py  which revision is current
    notes.py      organisation -> project -> type -> schedule, and where each
                  line came from
    tabular.py    reading a pasted block, and planning what pasting it would do
    branding.py   what a practice's documents look like and which fields they carry
    house.py      everything that varies between practices
  db/         SQLAlchemy models. Organisation is the tenant boundary
              upgrade.py adds columns an older database is missing, additively
  services/   transactions and lookups; every rule delegates to core
              history.py  undo and redo for the grid's risky operations
              importing.py a supplier's product list, planned before it is applied
              impact.py   why a schedule says something different from last week
  export/     the vendored sheet construction, and PDF via LibreOffice
              library.py  the equipment library as a workbook, out and back
  api/        FastAPI over the services
frontend/     no build step: ES modules served by the backend
  js/grid/    the selection model, keyboard rules and block-paste planner, with
              no DOM in them, so they can be tested
  tests/      those tests, run under Node from pytest
vendor/       the v1 toolkit this replaces, kept for reference and as test fixtures
docs/         SPEC.md (the original brief) and DECISIONS.md (what changed, and why)
```

### The grid

One active cell and a rectangle around it, as a spreadsheet has: drag or
Shift+Arrow to extend the selection, Ctrl+C and Ctrl+V for a block, Delete to
clear one, Ctrl+D to fill down, Ctrl+Z to undo. Undo covers the operations that
rewrite several rows at once — paste, delete, duplicate, fill — and is a restore
from a recorded state rather than an inverse operation worked out per action.

The selection carries the corner **fill handle**, and it behaves as Excel's
does: dragging it counts up from a reference ending in digits (`RAD-001`,
`RAD-002`), holding Ctrl copies instead, and dragging upwards counts down. A
chip afterwards offers the other one, so changing your mind is a click rather
than an undo and a different button. The increment rule stays in
`core/references.py` — the browser sends a direction and a count, never a list
of values — so a drag, the toolbar button and an importer all agree.

Library cells can be taken over and put back a **selection at a time**: a row
that diverges from the library usually diverges in company.

Enter and Tab walk a selected block — across the row, then down to the start of
the next — so filling in a chosen rectangle is typing and Enter, without
steering. Copying one cell and pasting into a range fills the range, as a
spreadsheet does.

### What the grid already knows

Three offers, all read out of the schedule rather than imposed on it:

- typing `Cu` in a column that already says Cupboard **completes it inline**,
  and only when exactly one value matches — two candidates would make it a
  guess;
- adding a row under `MVHR-005` **offers `MVHR-006`** as a ghost, taken with Tab
  or Enter. What "next" means comes from the column, so a practice that starts
  the first floor at `MVHR-101` gets `MVHR-102` offered next;
- filling in the airflow on one of five Cupboards **offers to set the other
  four** — empty cells only, two or more matching rows, and once.

A schedule also goes out as a spreadsheet and comes back (`Excel…` on the
toolbar): the typed columns with the headings on row 1, read back through the
same paste planner the browser uses. That is the working file; `export.xlsx` is
still the deliverable.

Pasting is planned before it happens. The preview says how many rows were found,
whether the first line was a header, and what would be appended, inserted or
removed; replacing every row is refused unless it is confirmed, and only when
there is something to lose.

### The three column kinds

Every column on a schedule is one of three things, and the kind decides where the
value comes from:

| Kind | Where it comes from | Colour |
|---|---|---|
| `input` | the engineer types it, and it differs per unit | blue on yellow |
| `library` | looked up from the shared equipment library | green |
| `derived` | calculated by formula, read-only | black |

That is the same colour contract the printed schedule uses while a schedule is
being filled in. It also maps onto COBie/IFC Component vs Type data, which is
what keeps an IFC or COBie export possible later.

An **export** drops it. A file that leaves the office is read, not filled in, so
`export.xlsx` and the PDF both default to a neutral print theme;
`?theme=editor` gives the working colours back. The two hold identical values.

### Hiding a column on one schedule

A column can be hidden on the screen, on the Excel export and on the PDF
independently, per schedule — which is how a `Price` column stays in the working
file and off the client's copy. It is stored on the schedule rather than the
type, because it is a decision about one document; the type and every other
schedule built from it are untouched, and the values are kept rather than
deleted.

The lookup key and any column a calculation reads are refused rather than
half-hidden: the API says which formula needs the column, and the renderer
leaves such a cell blank rather than emitting a reference to a column that is
not on the sheet.

### Notes

Notes come from four places, printed general to specific: the practice's
standing wording, what the project adds, what the equipment type says, and — only
when it has to — what one schedule says instead. A schedule that takes its notes
over replaces the inherited set rather than adding to it, and reverting drops
its own copy.

### Branding

Organisation-level: a logo, a font from a list every machine has, a palette, and
which fields the cover and revision page show and in what order. Configuration
rather than a document designer — the hand-made branded originals contain
drawing objects that cannot be round-tripped, so what is offered is what the
renderer can carry out honestly. A field the workbook reads by formula cannot be
hidden.

**A project can differ, about which fields show and nothing else.** A job with
no blocks does not want a Building row and a job with three does, and settling
that once for the whole practice was a lie the settings screen used to tell.
Each field has three states per project — follow the practice, always show,
always hide — so a job does not silently freeze its answer at whatever the
standard said the day it was created. Fonts, colours and the logo stay house
standard: the point of a house standard is that every document that leaves the
office looks like it came from the same place.

### The equipment library as a workbook

Products get in three ways and all three end at the same planner: typed on a
schedule, pasted as a block, or filled into the workbook the library hands out —
a blank template, one type's entries, or the whole library as one file with a
sheet per type. That last is the mass route: take everything out, correct it in
Excel where correcting a hundred rows is a drag of the fill handle, and bring
the same file back.

One renderer produces all three, so the file that comes back always matches the
file that went out. Reading one back goes through
`services/importing.py` — the planner a pasted block already uses — so the
duplicate handling, the "a blank cell means not stated" rule and the
plan-before-it-happens guarantee are the ones that already exist. A sheet whose
tab does not name a schedule type is reported rather than guessed at.

### One formula, two engines

A derived column such as `={Total Power Input (W)}/{Supply Airflow (l/s)}` has to
work twice: the grid evaluates it in Python as you type, and the exported
workbook needs it as a real Excel formula so the file still calculates when
somebody opens it and changes a duty.

Writing those separately guarantees they drift, so `core/formula.py` parses the
source once into an AST and gives it two emitters. The test suite hands exported
workbooks to LibreOffice, lets it recalculate, and asserts the two agree.

---

## Testing

```bash
cd backend
pip install -e ".[dev]"
pytest
```

591 tests. The ones worth knowing about:

- **`test_formula.py`** — Excel's semantics where they differ from Python's,
  including `-2^2 = 4`, and that emitted Excel re-parses to the same value.
- **`test_naming.py`** — regenerates all eight v1 sample filenames and requires
  an exact match.
- **`test_revisions.py`** — `P01 < P02 < C01`, and out-of-order rows.
- **`test_export.py`** — hands workbooks to LibreOffice and reads the computed
  values back.
- **`test_end_to_end.py`** — API to workbook: what the browser showed and what
  Excel computes must be the same number.
- **`test_architecture.py`** — walks the AST of every module in `core/` and fails
  if one imports the web, database or UI layer.
- **`test_history.py`** — undo restores exactly, including a row's own id.
- **`test_tabular.py`** — every count a paste preview reports, since each one is
  something a user is about to confirm on our word.
- **`test_branding.py`** — a field the workbook reads cannot be hidden, and the
  issue theme and the editor theme hold the same numbers.
- **`test_frontend.py`** — runs the grid's selection and keyboard rules, and
  `el()`'s property-versus-attribute rule, under Node. Skips itself when Node is
  not installed.
- **`test_columns.py`** — a schedule hiding a column on one target and not
  another, and the two things it is refused: the lookup key, and a column a
  formula reads.
- **`test_importing.py`** — the library workbook round trip: an export that
  comes back as no change at all, and a blank template that imports as nothing.
- **`test_storage.py`** — the database is never inside the checkout, an older
  one is adopted rather than overwritten, and a backup is a database that opens.
- **`test_references.py`** — which number in a value a fill counts, and what a
  column offers as the next reference down it.

Tests needing LibreOffice skip themselves when it is not installed, as do the
front-end ones when Node is not.

---

## Where this came from

`docs/SPEC.md` is the original build specification, for a Windows tkinter tool
where each workbook was the record. `docs/DECISIONS.md` records every departure
from it and the reason — most importantly the inversion to a database-backed web
app, which is what removes the macro round trip.

The v1 toolkit is preserved under `vendor/`. Its eight sample schedules are test
fixtures, and its sheet-construction code was kept rather than rewritten: it is
about 350 lines of verified Excel minutiae, and re-deriving it would be risk with
no upside.
