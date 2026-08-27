# PDF Stamp

Stamps a three-line block into the top-right corner of every page of a batch of
PDFs:

```
EF-007 EXHAUST PRESSURE     <- the file name, without ".pdf"
Rev: P01                    <- set per document
Date: 27/08/2026            <- set per document
```

Each line is right-aligned to the same margin, so the block keeps a clean edge
against the page. Revision and date are held **per file**, so a folder of
calculations can go out with different revisions in a single run.

## Running it

Needs Python 3.9+ and PyMuPDF:

```bash
pip install PyMuPDF
python stamp_gui.py
```

### The document table

`Add PDFs...` fills the table, one row per file. New rows pick up whatever is in
the **Set for selected (or all)** boxes, so the common case — a whole batch at
the same revision and today's date — needs no per-row typing at all.

- Double-click a **Rev** or **Date** cell to type into it; Enter commits,
  Escape cancels.
- `Apply` / `Rev only` / `Date only` fill every selected row, or every row when
  nothing is selected.
- `Today` drops today's date into the fill box.
- Leave a cell blank and that line is left off the stamp for that file, which is
  how the old filename-only stamp is reproduced.

The table (including each file's revision and date) is saved between runs, along
with the rest of the settings, in `stamp_gui_config.json` under
`%APPDATA%\RamblyStamp`.

### Appearance

Font (built-in or a TTF/OTF file), size, bold, colour, line spacing, margins,
BLOCK CAPITALS for the filename, and an optional white box behind the block.
`Flatten output` rasterizes the pages so the stamp cannot be edited out.

## Command line

```bash
# one revision and date for the whole batch
python stamp_gui.py --cli --out ./stamped --rev P01 --date today *.pdf

# per-file revisions from a CSV
python stamp_gui.py --cli --out ./stamped --meta revisions.csv *.pdf
```

`revisions.csv` is `file,rev,date`, with an optional header:

```csv
file,rev,date
EF-007 EXHAUST PRESSURE.pdf,P01,27/08/2026
EF-008 SUPPLY PRESSURE.pdf,P02,27/08/2026
```

Files are matched on their name (the `.pdf` and any folder are ignored), and
anything not listed falls back to `--rev` / `--date`. `--rev-label` and
`--date-label` change the `Rev:` and `Date:` prefixes. `python stamp_gui.py
--cli -h` lists the rest.

## Building the .exe

```bash
pip install pyinstaller
pyinstaller stamp_gui.spec
```
