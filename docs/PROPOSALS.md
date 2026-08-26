# Design proposals

For the backlog items that imply a data-model change. Nothing here is built yet —
these are the smallest clean designs I can see, for you to accept or redirect
before I write code.

---

## P2.15 Numbering — audit first

You asked me to audit the current assumptions before changing anything. Two of
the three requested changes turn out to be smaller than the brief assumes, and
one is genuinely structural.

### "Numbering can start from 1 rather than 10" — already possible today

`start` is not hardcoded. It comes from the house standard's `number` token:

```json
"number": { "scope": "schedule", "width": 8, "start": 10 }
```

`services/projects.py` reads `scheme.tokens["number"].start`. Change that value
in **Settings → Document numbering → starts at** and the next allocation begins
at 1. No code change is required.

Three places carry an `or 10` fallback for when the token is missing entirely.
That is a smell — the default belongs in one constant — but it is not what
governs a configured organisation.

**Tests:** most of `test_numbering.py` passes `start=` explicitly and is testing
the function, not the default. Exactly one test pins the seeded default:
`test_end_to_end.py::test_adding_schedules_numbers_them_from_ten`. If you want
the shipped default to become 1, that is a one-line change to
`house.DEFAULT_NAMING` plus that test.

**Question:** do you want the *default for new organisations* changed to 1, or
just to set it yourself per organisation? They are different decisions.

### "Discipline follows the volume" — small, and it fits the existing model

Today `discipline` is a project-scoped token fixed at `M`. Volume is already
**type-scoped**, because an AHU is always ventilation.

The proposal is to treat discipline the same way, resolved *from* the volume:

1. The house standard gains a lookup beside `volume_lookup`:
   ```json
   "volume_discipline": { "5.2": "P", "5.3": "P", "5.4": "M", "5.6": "M", "5.7": "M" }
   ```
2. When building the token context, if the type's volume appears in that lookup,
   put the matching discipline into the **type** layer.

That is roughly ten lines, because the scope machinery already exists and
resolution is already *schedule → building → type → project → company*. It gets
you the behaviour and keeps a project-level override working for free: a project
that needs `E` on everything still sets it and wins over nothing, while a
schedule-level override still beats the type.

**Question, and it blocks this one:** you wrote "final exact mapping to be
confirmed". I need the full list. My reading of Uniclass volumes is that 5.2
(above ground drainage) and 5.3 (domestic services / hot and cold water) are
public health, and 5.4 / 5.6 / 5.7 are mechanical — but 5.4 is not currently in
the house standard's `volume_lookup` at all, and I do not want to guess what
your practice puts in it. Send me the volume list with the discipline for each
and I will implement it directly.

### "Separate sequences per volume" — this is the structural one

This is the real change. Today allocation is scoped to a **building**:
`max(numbers used in this building) + 1`. You want it scoped to
**(building, volume)**, so `5.2-00001` and `5.3-00001` can coexist.

Two things have to change together:

1. **Allocation** filters by the volume of the schedule's type, not just the
   building. `core/numbering.next_number` already takes plain lists, so the
   change is at the call site in `services/projects.add_schedule` — it passes a
   filtered list.
2. **`Building.retired_numbers`** becomes per-volume. It is currently
   `list[int]`; it needs to be `dict[volume, list[int]]`, because retiring
   `5.6-00003` must not block `5.3-00003`.

That second point is a stored-shape change, so it needs a migration that reads
the old list and files it under the building's existing volumes. Everything
downstream — the renumber operations, the plans, the audit — already works on
"a set of schedules and their numbers", so scoping *which* set is passed in is
the whole change. The uniqueness constraint on `(building_id, number)` also has
to widen to include volume.

**Consequence worth flagging before you commit to it:** per-volume sequences mean
two schedules in the same building can carry the same `number`. The document
numbers stay distinct because `volume` sits earlier in the pattern, so this is
safe — but the "No." column in the UI becomes ambiguous unless it is shown as
`5.6-10` rather than `10`. Cheap to do, worth knowing.

**Recommendation:** do the discipline lookup and the default-start question
first, since they are small and independent. Do per-volume sequences as its own
change with its own migration, and not on a database with live projects in it.

---

## P1.5 Library values that are overridable per row

**The need:** SFP differs between two uses of the same unit, so a library-backed
value has to be divergeable on one row without breaking the model.

**Smallest clean design.** One new column on `ScheduleRow`:

```python
overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
```

Why a separate column rather than putting it in `values`: today, anything the
client sends for a library column is *stripped*, because accepting it would let
a stale or forged computed value be stored and then rendered as fact. That
protection has to stay. A value in `overrides` is unambiguously deliberate,
which keeps the guard intact and makes "is this overridden?" a lookup rather
than a guess.

Three touch points:

- **`compute_row`** checks `overrides` before the library lookup and marks the
  cell `overridden: true`.
- **The grid** renders an overridden library cell as editable, in the input
  colour with a small marker, and offers "reset to library value" — which
  deletes the key.
- **The renderer** writes the literal value into that cell instead of the
  `INDEX/MATCH` formula. This is the only non-trivial part: the export currently
  writes one formula for every row of a library column, and it would become
  per-row.

The audit trail falls out: the override is stored, so a health check can list
every row that diverges from its library value.

---

## P1.6 Products that are not fully known yet

**Recommendation: allow partial products in the shared library, and do not add a
second mechanism.**

The library already accepts blank fields and raises an `INCOMPLETE` flag on the
review queue, so this mostly works today — the fix is presentational, telling
the user in the capture dialog that blanks are fine and can be completed later.
That is a Phase 1-sized change, already done in the P0.3 dialog copy.

The alternative — temporary manual values on the row, promoted to the library
later — is P1.5's override mechanism wearing a different hat. Building both
would give two ways to express "this value is not from the library", and they
would drift. Once P1.5 exists, "keep manual for now" is just an override.

**Propagation:** completing a product later should flow through automatically,
because library values are read and never copied. A row that has *overridden*
that field keeps its override, which is the correct precedence.

---

## P2.3 / P2.4 Column visibility, and project-specific columns

Both are additions to the column model and can share one change.

**Visibility (P2.3).** Add to `Column`:

```python
visibility: dict[str, bool]   # {"editor": True, "xlsx": True, "pdf": True}
```

`Column.from_dict` already tolerates missing keys, so existing catalogues need no
migration — an absent `visibility` means visible everywhere. The renderer filters
its column list per target; the grid filters on `editor`. A `Price` column set to
editor-only never reaches an issued document.

**Project-specific columns (P2.4).** One new JSON column on `Project`:

```python
type_extras: dict[str, list[dict]]   # type_code -> extra column definitions
```

Scoped to the project rather than the schedule deliberately: your example is a
`Quantity` column on a radiator schedule for one job, and a job with three
buildings wants it on all three radiator schedules, not one.

The merged column list is `type.columns + project.type_extras[code]`, computed
in one place and used by the grid, the renderer and the formula validator alike.
Because validation runs against the merged list, a project-specific derived
column can reference the base columns and will be checked properly.

**What this deliberately does not do:** let a project *remove* or *reorder* base
columns. That way lies every project having a different schedule, which is the
thing the catalogue exists to prevent. Additions only.

---

## P2.6 Revision snapshots

The most valuable item on the list and the one with real liability behind it: an
issued revision must not change meaning because somebody later corrected a
product in the library.

**Smallest clean design.** One new column on `RevisionRow`:

```python
snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

Written **once**, when a revision is issued, holding what is needed to reproduce
the document without consulting anything live:

```json
{
  "columns":  [ ...the merged column definitions at issue time... ],
  "rows":     [ { "values": {...}, "computed": {...} } ],
  "notes":    [ ...project notes then type notes, as rendered... ],
  "docnum":   "...", "building": "...", "constants": {...},
  "type_version": 3
}
```

Storing the **computed** values as well as the typed ones is the whole point: it
is what makes a later library correction or formula fix unable to alter an issued
document.

Then:

- Viewing a past revision renders from its snapshot, read-only.
- Exporting a past revision renders from its snapshot.
- The current revision keeps rendering live, as now.
- A revision with no snapshot (anything created before this change) falls back to
  live data and is labelled as such rather than pretending.

**Open question for you:** what counts as "issued"? The options are (a) adding
any revision row snapshots it, (b) only a published `C` revision snapshots, or
(c) there is an explicit "Issue this revision" action that snapshots and locks.
I would recommend (c) — it matches how a document actually leaves the office,
and it gives the issued-document lock a precise moment to attach to instead of
inferring one from the log. But it adds a button and a state, so it is your call.

---

## P2.9 Bulk revision bump

No data-model change. It is `POST /api/projects/{id}/revisions/bulk` taking a
set of schedule ids and one revision payload, applying `next_code` per schedule
so each continues its own series rather than being forced to a shared code.

Worth pairing with a preview, like the renumber plans: show which schedules would
move from what to what before applying.

---

## Suggested order

1. **Discipline-from-volume** and the **default start number** — small, and both
   are blocked only on your answers above.
2. **P2.3 visibility** — additive, no migration.
3. **P1.5 overrides** — unlocks P1.6 properly and is the most-requested
   flexibility.
4. **P2.6 snapshots** — highest value, but needs the "what is issued" decision.
5. **P2.4 project columns** — after overrides, since both touch the merged column
   list and doing them together avoids two passes.
6. **Per-volume sequences** — last, alone, with a migration.
