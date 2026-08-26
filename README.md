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

The first run creates `backend/data/schedul.db` and seeds an organisation with
nine schedule types (the eight from the v1 `schema.json`, plus Radiant Panel).

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
| `SCHEDUL_DATA` | Where the SQLite file lives. |
| `SCHEDUL_SOFFICE` | Path to LibreOffice, when it is not on `PATH`. |

---

## How it fits together

```
backend/schedul/
  core/       the domain. No web, no database, no UI — enforced by a test
    formula.py    the house formula language: one parser, two backends
    catalogue.py  schedule types and the three column kinds
    naming.py     scoped tokens -> document number
    numbering.py  allocation and the renumber operations
    revisions.py  which revision is current
    house.py      everything that varies between practices
  db/         SQLAlchemy models. Organisation is the tenant boundary
  services/   transactions and lookups; every rule delegates to core
  export/     the vendored sheet construction, and PDF via LibreOffice
  api/        FastAPI over the services
frontend/     no build step: ES modules served by the backend
vendor/       the v1 toolkit this replaces, kept for reference and as test fixtures
docs/         SPEC.md (the original brief) and DECISIONS.md (what changed, and why)
```

### The three column kinds

Every column on a schedule is one of three things, and the kind decides where the
value comes from:

| Kind | Where it comes from | Colour |
|---|---|---|
| `input` | the engineer types it, and it differs per unit | blue on yellow |
| `library` | looked up from the shared equipment library | green |
| `derived` | calculated by formula, read-only | black |

That is the same colour contract the printed schedule uses, so the screen and
the paper mean the same thing. It also maps onto COBie/IFC Component vs Type
data, which is what keeps an IFC or COBie export possible later.

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

250 tests. The ones worth knowing about:

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

Tests needing LibreOffice skip themselves when it is not installed.

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
