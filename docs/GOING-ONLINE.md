# Going online, and what that means commercially

## It is already online

Worth being blunt about this, because it changes the size of the question: what
exists is a web application. FastAPI, a browser front end, a relational
database. There is no desktop app, no tkinter, no macros, no add-in. Nothing
about the architecture is local.

It runs on `localhost` today because that is where it is *pointed*, not what it
*is*. "Going online" is not a rewrite — it is deployment plus the account layer.

The expensive part of multi-tenancy is already done. `organisation` is the top
level of the data model, and every query in the service layer is already scoped
by it. There is exactly one function in the codebase that decides which tenant a
request belongs to:

```python
def current_org(session = Depends(get_db)) -> Organisation:
    """A local install runs single-tenant, so this resolves to the one seeded
    organisation. When logins arrive it reads the session instead, and nothing
    below it changes."""
    return ensure_default_organisation(session)
```

Every route already depends on it. Replacing its body with "the organisation on
the logged-in user's session" is the whole tenancy change.

---

## What is actually left

Roughly in the order you would do it.

### 1. Accounts and login

The only genuinely new subsystem. Users, an invite flow, password reset,
sessions, and a `user` table joined to `organisation`. Roles are worth having
from the start but can be coarse: **admin** (edits the house standard and the
catalogue, approves library entries) and **engineer** (everything else). The
review queue already assumes somebody is nominated to work it.

Use a hosted identity provider rather than writing this. It is a solved problem
where the cost of being clever is a breach.

### 2. PostgreSQL instead of SQLite

Change one environment variable. The models are plain SQLAlchemy with no SQLite
specifics; the JSON columns become `JSONB`, which is better. What this *does*
need is a migration tool (Alembic) before the first paying customer, because
from that point you can no longer drop the database when the schema changes.

### 3. A container with LibreOffice in it

PDF export shells out to LibreOffice. That means the container image has it
installed and enough memory to run it, and conversions want a worker queue
rather than blocking a request — a 40-schedule project export is minutes, not
milliseconds. Today it runs inline, which is fine for one user and not fine for
fifty.

This is the least glamorous item and the one most likely to bite. Budget real
time for it.

### 4. Concurrency

Two engineers on the same schedule is currently last-write-wins per row. Because
the grid saves one row at a time, the blast radius is small, but it is still a
silent overwrite. The honest fix is a version column per row and a "this row
changed under you" prompt. Live collaborative editing is a much bigger project
and almost certainly not what this market needs.

### 5. Backups, and getting data out

Non-negotiable for anything a firm runs a live project on. Automated backups, and
a "export everything this organisation owns" button. The second one is also a
sales asset: nobody wants to be locked in, and being able to say "your data
leaves whenever you like, as the Excel files you already use" removes an
objection.

**None of this is architectural.** It is the ordinary cost of running software
for other people.

---

## The commercial case

### What is genuinely strong

**It removes a real, specific, daily annoyance.** Not a vague "productivity"
claim. The engineer types a duty; the schedule calculates, numbers itself
correctly, and produces an issue-ready PDF. Today that is a macro-laden workbook
copied between folders.

**The equipment library compounds.** Every product entered makes the next
schedule faster, and the value accrues to the *practice*, not the individual.
That is a genuine switching cost built by ordinary use rather than by lock-in —
the good kind of retention. It is also the thing a spreadsheet fundamentally
cannot do, because a spreadsheet has no shared memory.

**The register is the thing directors actually want.** Every schedule across
every project, with its current revision, issue date and suitability status,
always correct. In the v1 toolkit this was a Power Query scrape somebody had to
remember to refresh. Ask any practice how confident they are about which
revision of which schedule went out last month.

**ISO 19650 numbering is a compliance argument, not a features argument.** That
sells to the person signing, who cares about auditability. And because the
naming pattern and its token scopes are configurable, another firm is a
configuration, not a fork.

**Excel stays the deliverable.** You are not asking anyone to abandon Excel —
you are removing the tedious half. The exported workbook is macro-free, has no
external links and opens anywhere. That is a much easier sell than "trust our
web viewer".

### What is genuinely hard

**A vertical this narrow has a small market.** UK/Ireland MEP consultancies
doing ISO 19650 work is maybe hundreds of firms, not thousands. That constrains
pricing strategy: this is a per-seat tool at a real price sold to a few hundred
firms, not a cheap tool sold to a hundred thousand. Plan for high-touch sales and
long sales cycles, and expect to know most of your customers by name.

**Every firm believes its format is special.** Mostly they are right about the
branding and the wording, and wrong about the structure — the ISO 19650 field
layout underneath is common. The house standard is built for exactly this, but
onboarding a firm will still mean sitting with them and configuring it. That is
a services cost per customer, which caps how fast you can grow and should be
priced in rather than wished away.

**The cover page is the one thing this genuinely cannot produce.** openpyxl
cannot write drawing objects, and the real file confirms house covers carry
shapes. The plan of record — keep a branded `cover_template.xlsx` per firm and
copy that sheet in — is sound but is per-customer setup work. Do not demo
against a plain cover; it is the first thing they will notice.

**Trust, on documents that go to clients.** A wrong duty on an issued schedule is
somebody's professional liability. That argues for keeping the audit and health
check prominent rather than hiding them, and for never silently changing a
number on an issued document. The issued-document lock already refuses.

**A competent Excel user can approximate a lot of this.** Your answer is the
shared library and the register — the two things that need a database and cannot
be done in a workbook. Lead with those, not with "it calculates", because Excel
also calculates.

### What I would not build yet

Live collaborative editing, a mobile app, IFC/COBie export, and integrations
with Revit. All are plausible eventually. None is why a firm would buy version
one, and each is months. The data model is deliberately kept clean enough to
allow them — the three column kinds map onto COBie Component vs Type data
specifically so an export is possible later — but building any of them now would
be building for a customer you do not have yet.

---

## Importing existing spreadsheets

You flagged this as valuable but probably hard, because every firm's format
differs. Having now looked at a real hand-made house file, **it is considerably
easier than you think**, for one specific reason.

### The finding

The supplied original — a Radiators schedule — contains **zero formulas**. Not
one, across all three sheets. Every value is hardcoded. There is no calculation
model to reverse-engineer, no `CELL("filename")` document number, no revision
logic. It is a rectangular block of typed values under a two-row header.

That reframes the problem entirely. Importing is not "understand this firm's
spreadsheet". It is "find the header row and read the grid".

### Why it stays tractable across firms

The structure this depends on is not house style, it is the standard:

- A title row, a notes block, a **header row**, usually a **units row**, then data
- One row per piece of equipment
- Columns that are recognisably reference, location, duty, dimensions, product

The wording differs between firms. `Supply Airflow (l/s)` versus `Supply Air
Volume`. The *shape* does not, because it is a schedule.

### The approach that would work

1. **Find the header row by scoring**, not by assuming row 4. The row where most
   cells are short non-numeric strings and the row beneath is mostly numbers is
   the header. This is robust across layouts and is about thirty lines of code.
   The real file's own autofilter range confirms row 4 independently when
   present.
2. **Propose a column mapping, let the user correct it.** Fuzzy-match each found
   column against the target type's columns — the same similarity scoring already
   used for spelling drift in the equipment library. Show it as two lists with
   arrows and let them drag. Never guess silently.
3. **Offer to create the type from the file** when nothing in the catalogue fits.
   The columns are right there; ask which are typed, which are product data, and
   which are calculated. This doubles as onboarding: a firm's first import
   becomes their catalogue.
4. **Import values only, never formulas.** If a source column *is* calculated,
   the user maps it to a derived column and the value is discarded in favour of
   recalculation. This is the only correct behaviour — importing somebody's stale
   arithmetic as fact is how you end up liable for it.
5. **Show a preview and a diff before committing.** Rows to be created, columns
   that could not be mapped, values that will not coerce. Same pattern as the
   renumber plans.

### What makes it worth doing early rather than later

Import is not really a data-migration feature. It is the **onboarding** feature.
A firm evaluating this will want to see their own schedule in it within five
minutes, and "upload one and see" is a far better demo than "let me configure
your house standard first". The first import creating a draft catalogue entry is
the shortest path from stranger to configured tenant.

I would still not build it before the account layer — but I would build it before
anything on the "not yet" list above.

### What it will not do

Recover branding, recover formulas that were never there, or handle a genuinely
freeform spreadsheet that is not a schedule. Those cases should fail clearly and
early rather than importing something subtly wrong.
