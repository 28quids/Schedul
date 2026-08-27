#!/usr/bin/env python3
"""
MagiCAD Project Info Fixer (GUI)
=================================

Problem this solves: MagiCAD calculation reports (Ductwork Sizing, Pressure
Drop, etc.) all share the same "Project Information" header table. When a
report gets copied/re-run early in a project, that header can end up stale
(wrong project number, placeholder address, missing author) while a later
report has the correct values. This tool writes the correct values into any
number of PDFs, without touching anything else on the page.

Where the correct values come from
----------------------------------
Two input modes, and they can be combined:

- **Reference PDF** - you pick ONE PDF that already has the correct Project
  Information and the values are read out of it. Use this when a good report
  already exists.
- **Typed values** - you type the values in yourself. Use this when no report
  is correct yet, or when the project has just been renumbered and *no* PDF
  has the new value. Nothing has to exist on disk first.

In the GUI, switching to "Type values in" makes the value boxes editable; in
reference mode the same boxes show, read-only, exactly what was read out of
the reference PDF, so you can always see what is about to be written. A
"Prefill from a PDF..." button loads a PDF's values into the boxes for
editing, which is the usual way to change one field and keep the rest.

On the command line, `--ref` reads from a PDF and `--set "Label:=value"`
(repeatable) or `--values file.json` types values in. If both are given, the
typed values win for the fields they name and the reference supplies the rest.

How it works
------------
- You pick one or more target PDFs (the ones that need fixing).
- You choose which fields to sync (all ticked by default except
  "Calculation date:", since that is a per-report timestamp, not project
  identity, and is usually correct even when the rest of the header is not).
- For each target, only fields that actually differ from the new value are
  touched. The old value is properly redacted (removed from the underlying
  PDF content, not just painted over) and the correct value is inserted in
  its place, in the same position, size, and colour as the surrounding text.
- A blank new value never overwrites a populated target field. If the
  reference is missing data, or you left a box empty, the target keeps what
  it has and the run reports it as skipped.
- Fixed files are saved to a separate output folder using the same
  filename. Originals are never modified.

This only works on PDFs with real selectable text (which MagiCAD reports
are). It will not work on scanned/flattened PDFs, since there is no text to
find or replace.

Install
-------
    py -m pip install PyMuPDF

Run
---
    py fix_project_info.py

CLI (no GUI)
------------
    py fix_project_info.py --cli --ref REFERENCE.pdf --out OUT_DIR file1.pdf file2.pdf
    py fix_project_info.py --cli --set "Project number:=1620012345" \
        --set "Project name:=Beacon NB17" --out OUT_DIR *.pdf
    py fix_project_info.py --cli --ref good.pdf --set "Author:=A Gamble" \
        --out OUT_DIR *.pdf
    py fix_project_info.py --cli --read good.pdf     # print a PDF's values
    py fix_project_info.py --cli --list-fields       # print the exact labels
"""

import os
import sys
import json
import queue
import argparse
import threading
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.stderr.write(
        "PyMuPDF is required. Install it with:  py -m pip install PyMuPDF\n"
    )
    raise

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

appdata = os.environ.get("APPDATA") or str(Path.home())
config_dir = Path(appdata) / "RamblyStamp"
config_dir.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = config_dir / "stamp_gui_config.json"

# Labels as they appear verbatim in the MagiCAD "Project Information" table.
# Order here is just the order fields are shown/synced in; it doesn't need to
# match the on-page layout.
ALL_FIELDS = [
    "Software version:",
    "Calculation date:",
    "Project name:",
    "Project number:",
    "Project address:",
    "Client name:",
    "Project issue date:",
    "Organization name:",
    "Organization description:",
    "Author:",
]

# Ticked by default. Calculation date is deliberately excluded: it records
# when that specific report was run, not project identity, so it's usually
# correct even when the rest of the header is stale.
DEFAULT_SYNC_FIELDS = [f for f in ALL_FIELDS if f != "Calculation date:"]

# Column membership is a template constant, not something to infer from a
# label's x-coordinate at runtime. A geometric "x0 < page_width/2" check is
# fragile: labels that happen to sit within a fraction of a point of the
# midpoint (observed: "Client name:" at x0=395.98 vs a 396.0 threshold) get
# misclassified, which then clips the value-extraction box at the wrong
# boundary and silently reads the value as blank even though it's printed
# right there in the PDF. Every MagiCAD report of this type puts these
# labels in the same columns, so classify by label text instead.
LEFT_COLUMN_FIELDS = {
    "Software version:", "Project name:", "Project address:",
    "Project issue date:", "Organization description:",
}
RIGHT_COLUMN_FIELDS = {
    "Calculation date:", "Project number:", "Client name:",
    "Organization name:", "Author:",
}

# Where the value column starts when a page gives us nothing to measure from
# (every row in that column is blank). Standard MagiCAD template positions.
FALLBACK_LEFT_ANCHOR = 184.6
FALLBACK_RIGHT_ANCHOR = 538.1

MODE_REFERENCE = "reference"
MODE_MANUAL = "manual"

DEFAULT_CONFIG = {
    "reference_file": "",
    "last_targets": [],
    "last_output": "",
    "sync_fields": DEFAULT_SYNC_FIELDS,
    "font_size": 8.5,
    "input_mode": MODE_REFERENCE,
    "manual_values": {},
}

ASCII_LOGO = r"""
 ____            _           _     ____            _
|  _ \ _ __ ___ (_) ___  ___| |_  / ___| ___  _ __ | |_
| |_) | '__/ _ \| |/ _ \/ __| __|| |   / _ \| '_ \| __|
|  __/| | | (_) | |  __/ (__| |_ | |__| (_) | | | | |_
|_|   |_|  \___// |\___|\___|\__(_)____\___/|_| |_|\__|
              |__/            f i x e r
""".strip("\n")


# --------------------------------------------------------------------------- #
# Config persistence
# --------------------------------------------------------------------------- #

def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                cfg.update({k: data[k] for k in data if k in DEFAULT_CONFIG})
    except (OSError, json.JSONDecodeError):
        pass
    # An older config (or a hand-edited one) can carry the wrong type here;
    # the GUI would then fail on startup rather than on use.
    if not isinstance(cfg.get("manual_values"), dict):
        cfg["manual_values"] = {}
    if cfg.get("input_mode") not in (MODE_REFERENCE, MODE_MANUAL):
        cfg["input_mode"] = MODE_REFERENCE
    return cfg


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    clean = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Field labels
# --------------------------------------------------------------------------- #

def normalize_label(raw: str) -> str:
    """
    Accept a label typed loosely on the command line ("project number",
    "Project Number:") and return the exact template label. Anything that
    doesn't match a known field is passed through with a trailing colon
    added, so one-off custom labels still work.
    """
    s = " ".join(raw.split())
    for field in ALL_FIELDS:
        if s.lower().rstrip(":") == field.lower().rstrip(":"):
            return field
    return s if s.endswith(":") else s + ":"


def order_fields(labels) -> list:
    """Template order first, then any custom labels in the order given."""
    seen = list(dict.fromkeys(labels))
    known = [f for f in ALL_FIELDS if f in seen]
    return known + [f for f in seen if f not in ALL_FIELDS]


# --------------------------------------------------------------------------- #
# Core field extraction / replacement
# --------------------------------------------------------------------------- #

def _text_spans(page) -> list:
    """[(span_rect, baseline_y), ...] for every span on the page."""
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                spans.append((fitz.Rect(span["bbox"]), span["origin"][1]))
    return spans


def _baseline_for(rect, spans):
    """
    The text baseline of the span that best overlaps rect, or None.

    Replacement text has to sit on the SAME baseline as the label it belongs
    to. Deriving it from the label's bounding box instead (y1 minus a nudge)
    lands the new text a point or two low, which looks fine on its own but
    makes the row overlap the next row's extraction box -- so a second pass
    over an already-fixed file reads the wrong value, and its redaction box
    can clip text belonging to the row above.
    """
    best, best_area = None, 0.0
    for span_rect, baseline in spans:
        inter = span_rect & rect
        area = 0.0 if inter.is_empty else inter.get_area()
        if area > best_area:
            best, best_area = baseline, area
    return best


def extract_fields(page, fields=ALL_FIELDS) -> dict:
    """
    Locate each label on the page and read the value sitting to its right,
    up to the boundary of the next column (or the page edge for the right
    column). Returns {label: {"value": str, "label_rect": Rect,
    "value_rect": Rect, "is_left": bool, "value_x0": float|None}}.
    value_x0 is the actual left edge of the existing value text (None if the
    value is blank) -- this is what makes column alignment possible, since
    it is NOT the same as the label's right edge.
    """
    W = page.rect.width
    out = {}
    all_words = page.get_text("words")
    spans = _text_spans(page)
    for label in fields:
        hits = page.search_for(label)
        if not hits:
            continue
        r = hits[0]
        if label in LEFT_COLUMN_FIELDS:
            is_left = True
        elif label in RIGHT_COLUMN_FIELDS:
            is_left = False
        else:
            # Unknown label (not part of the standard template): fall back
            # to geometry, better than nothing for a one-off custom field.
            is_left = r.x0 < W / 2
        boundary = 396.0 if is_left else (W - 10)
        value_rect = fitz.Rect(r.x1 + 4, r.y0 - 1, boundary - 4, r.y1 + 1)
        text = page.get_textbox(value_rect).strip()
        value_words = [w for w in all_words
                       if r.y0 - 1 <= w[1] <= r.y1 + 1 and w[0] > r.x1]
        value_x0 = min((w[0] for w in value_words), default=None)
        out[label] = {"value": text, "label_rect": r, "value_rect": value_rect,
                      "is_left": is_left, "value_x0": value_x0,
                      "baseline_y": _baseline_for(r, spans)}
    return out


def column_anchors(fields_dict: dict) -> tuple:
    """
    Derive the true left/right value-column x-position from a fields dict,
    using whichever rows actually have text (so a blank field, like an empty
    Author, doesn't break alignment). Falls back to the standard MagiCAD
    template positions if a column has no populated rows at all.
    """
    left_xs = [v["value_x0"] for v in fields_dict.values()
              if v["is_left"] and v["value_x0"] is not None]
    right_xs = [v["value_x0"] for v in fields_dict.values()
               if not v["is_left"] and v["value_x0"] is not None]
    left = min(left_xs) if left_xs else FALLBACK_LEFT_ANCHOR
    right = min(right_xs) if right_xs else FALLBACK_RIGHT_ANCHOR
    return left, right


def read_pdf_values(pdf_path, fields=ALL_FIELDS) -> dict:
    """
    Read the Project Information values out of a PDF's first page as a plain
    {label: value} dict. This is what turns a reference PDF into the same
    shape as typed-in values, so both input modes feed one code path.
    """
    doc = fitz.open(pdf_path)
    try:
        return {k: v["value"] for k, v in extract_fields(doc[0], fields).items()}
    finally:
        doc.close()


def read_reference(ref_path, fields=ALL_FIELDS) -> tuple:
    """
    Read a reference PDF: returns (values, anchors). The anchors are the
    reference's own value-column positions, which is what makes a reference
    run land text exactly where the good report has it.
    """
    doc = fitz.open(ref_path)
    try:
        ref_fields = extract_fields(doc[0], fields)
    finally:
        doc.close()
    values = {k: v["value"] for k, v in ref_fields.items()}
    return values, column_anchors(ref_fields)


def resolve_values(ref_path=None, manual_values=None, fields=ALL_FIELDS) -> tuple:
    """
    Work out the values to write and the column anchors to write them at,
    from either input mode or both. Returns (values, anchors) where anchors
    may be None, meaning "measure each target page instead".

    Typed values take precedence over the reference for the fields they name
    (blank ones are dropped, so an empty box means "leave this alone" rather
    than "erase it"), and the reference supplies everything else.
    """
    values, anchors = {}, None
    if ref_path:
        values, anchors = read_reference(ref_path, fields)
    if manual_values:
        typed = {normalize_label(k): (v or "").strip()
                 for k, v in manual_values.items()}
        values.update({k: v for k, v in typed.items() if v})
    return values, anchors


def diff_fields(new_values: dict, tgt_fields: dict, sync_fields: list) -> tuple:
    """
    Return (changes, skipped) where changes = {label: new_value} for fields
    that differ and should sync, and skipped = [label, ...] for fields where
    there is no new value to write. A blank new value NEVER overwrites a
    populated target value -- if the reference is missing data (or a typed
    box was left empty), that's an input problem, not license to erase
    something correct. Fields with no new value offered at all (typed mode,
    where you fill in only the boxes you care about) are passed over
    silently rather than reported as skipped.
    """
    changes = {}
    skipped = []
    for label in sync_fields:
        # A label absent from new_values means nothing was offered for that
        # field -- a box you simply didn't fill in. That's the normal case in
        # typed mode and isn't worth reporting. A label that IS present but
        # blank is different: the input claims to cover the field and doesn't,
        # which is what the skipped list is for.
        if label not in tgt_fields or label not in new_values:
            continue
        new_val = new_values[label]
        if not new_val.strip():
            if tgt_fields[label]["value"].strip():
                skipped.append(label)
            continue
        if tgt_fields[label]["value"] != new_val:
            changes[label] = new_val
    return changes, skipped


def fix_pdf(src_path: str, out_path: str, new_values: dict, sync_fields: list,
           font_size: float = 8.5, anchors=None) -> tuple:
    """
    Apply corrected header values to one PDF and save to out_path.
    Returns a per-field change report: {label: (old, new)} for fields
    actually changed (only page 1's header is checked/edited, matching the
    MagiCAD report layout where this table only appears once).

    anchors is the (left_x, right_x) the new text is aligned to. Pass the
    reference PDF's anchors when there is a reference; pass None (typed-in
    mode) and each target's own value column is measured instead.
    """
    doc = fitz.open(src_path)
    changed = {}
    skipped = []
    try:
        page = doc[0]
        wanted = order_fields(list(new_values.keys()) + list(sync_fields))
        tgt_fields = extract_fields(page, wanted)
        changes, skipped = diff_fields(new_values, tgt_fields, sync_fields)
        if changes:
            left_anchor, right_anchor = anchors or column_anchors(tgt_fields)
            redactions = []
            for label, new_val in changes.items():
                cur = tgt_fields[label]
                vr = cur["value_rect"]
                page.add_redact_annot(vr, fill=(1, 1, 1))
                anchor_x = left_anchor if cur["is_left"] else right_anchor
                baseline_y = cur.get("baseline_y")
                if baseline_y is None:
                    baseline_y = cur["label_rect"].y1 - 1.0
                redactions.append((anchor_x, new_val, baseline_y))
                changed[label] = (cur["value"], new_val)
            page.apply_redactions()
            for anchor_x, new_val, baseline_y in redactions:
                page.insert_text((anchor_x, baseline_y), new_val,
                                 fontsize=font_size, fontname="helv",
                                 color=(0, 0, 0))
        doc.save(out_path, garbage=3, deflate=True)
    finally:
        doc.close()
    return changed, skipped


def run_batch(new_values, targets, out_dir, sync_fields, font_size=8.5,
             anchors=None, progress_cb=None):
    """
    Fix a list of target PDFs against one set of values (read from a
    reference PDF, typed in, or a mix of the two). Returns
    (report: {filename: {"changed": {label: (old,new)}, "skipped": [label,...]}},
     errors: [(filename, msg)]).
    """
    total = len(targets)
    report = {}
    errors = []
    for i, t in enumerate(targets, start=1):
        name = Path(t).name
        try:
            out_path = str(Path(out_dir) / name)
            changed, skipped = fix_pdf(t, out_path, new_values, sync_fields,
                                       font_size, anchors)
            report[name] = {"changed": changed, "skipped": skipped}
        except Exception as exc:  # noqa: BLE001
            errors.append((name, str(exc)))
        if progress_cb:
            progress_cb(i, total, name)
    return report, errors


# --------------------------------------------------------------------------- #
# CLI mode
# --------------------------------------------------------------------------- #

def parse_set_args(pairs) -> dict:
    """
    Turn --set "Project number:=1620012345" arguments into {label: value}.
    The label may be given with or without its trailing colon.
    """
    out = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise SystemExit(
                f"--set expects LABEL=VALUE, got {raw!r}. "
                'Example: --set "Project number:=1620012345"'
            )
        label, value = raw.split("=", 1)
        if not label.strip():
            raise SystemExit(f"--set has an empty label: {raw!r}")
        out[normalize_label(label)] = value.strip()
    return out


def load_values_file(path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read --values file {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"--values file {path} must be a JSON object of "
                         '{"Label:": "value"} pairs.')
    return {normalize_label(k): ("" if v is None else str(v))
            for k, v in data.items()}


def cli_main(argv):
    p = argparse.ArgumentParser(
        prog="fix_project_info.py --cli",
        description="Write correct Project Information fields into MagiCAD PDFs, "
                    "taking the values from a reference PDF, from typed-in "
                    "values, or from both.",
    )
    p.add_argument("targets", nargs="*", help="Target PDF(s) to fix")
    p.add_argument("--ref", help="Reference PDF to read correct values from")
    p.add_argument("--set", action="append", metavar="LABEL=VALUE", dest="sets",
                   help='Type one field in, e.g. --set "Project number:=1620012345". '
                        "Repeatable. Overrides --ref for that field.")
    p.add_argument("--values", metavar="FILE.json",
                   help='JSON file of {"Project name:": "Beacon", ...} values')
    p.add_argument("--out", help="Output folder")
    p.add_argument("--fields", nargs="*", default=None,
                   help="Subset of fields to sync (default: all except "
                        "'Calculation date:'). Quote each label exactly, "
                        "e.g. --fields \"Project name:\" \"Author:\"")
    p.add_argument("--size", type=float, default=8.5, help="Font size (pt)")
    p.add_argument("--read", metavar="PDF",
                   help="Print the Project Information in a PDF and exit "
                        "(handy for building a --values file)")
    p.add_argument("--list-fields", action="store_true",
                   help="Print the exact field labels and exit")
    args = p.parse_args(argv)

    if args.list_fields:
        for f in ALL_FIELDS:
            print(f)
        return 0

    if args.read:
        for label, value in read_pdf_values(args.read).items():
            print(f"{label:30} {value}")
        return 0

    manual = load_values_file(args.values) if args.values else {}
    manual.update(parse_set_args(args.sets))

    if not args.ref and not manual:
        p.error("give values to write: --ref REFERENCE.pdf, --set "
                '"Label:=value", --values FILE.json, or a combination.')
    if not args.targets:
        p.error("give at least one target PDF to fix.")
    if not args.out:
        p.error("--out is required.")

    sync_fields = args.fields if args.fields else DEFAULT_SYNC_FIELDS
    if manual:
        # A field you typed in is a field you want written, even if it isn't
        # in the default sync set (a custom label, say). Ticking it for you
        # beats silently ignoring what you typed.
        sync_fields = order_fields(list(sync_fields) + list(manual.keys()))
    os.makedirs(args.out, exist_ok=True)

    values, anchors = resolve_values(args.ref, manual)
    if not values:
        print("Nothing to write: no values found in the reference and none typed in.")
        return 1

    def cb(done, total, name):
        print(f"[{done}/{total}] {name}")

    report, errors = run_batch(values, args.targets, args.out, sync_fields,
                               args.size, anchors, progress_cb=cb)
    print()
    for name, info in report.items():
        changes = info["changed"]
        skipped = info["skipped"]
        if not changes and not skipped:
            print(f"{name}: no changes needed")
        elif changes:
            print(f"{name}:")
            for label, (old, new) in changes.items():
                print(f"    {label:28} '{old}' -> '{new}'")
        if skipped:
            print(f"{name}: SKIPPED (new value is blank, target left unchanged):")
            for label in skipped:
                print(f"    {label:28} blank in the input -- check your "
                      f"reference PDF, or type a value in")
    if errors:
        print("\nFailed:")
        for name, msg in errors:
            print(f"  {name}: {msg}")
    return 0 if not errors else 1


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

def gui_main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    cfg = load_config()
    state = {
        "reference": cfg.get("reference_file", ""),
        "targets": list(cfg.get("last_targets", [])),
        # Column positions read from the reference PDF, or None in typed
        # mode (each target is then measured on its own).
        "anchors": None,
    }
    ui_queue = queue.Queue()

    root = tk.Tk()
    root.title("MagiCAD Project Info Fixer")
    root.minsize(720, 760)

    # ASCII logo
    logo = tk.Text(root, height=7, width=60, borderwidth=0,
                   background="white", foreground="black")
    logo.insert("1.0", ASCII_LOGO)
    logo.configure(state="disabled", font=("Courier New", 9))
    logo.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 8))

    pad = {"padx": 6, "pady": 3}

    # ---------------------------------------------------------------- mode --
    v_mode = tk.StringVar(value=cfg.get("input_mode", MODE_REFERENCE))

    mode_box = tk.LabelFrame(root, text="Where the correct values come from")
    mode_box.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
    tk.Radiobutton(mode_box, text="Copy from a reference PDF",
                   variable=v_mode, value=MODE_REFERENCE,
                   command=lambda: apply_mode()).grid(row=0, column=0, sticky="w", padx=6)
    tk.Radiobutton(mode_box, text="Type the values in",
                   variable=v_mode, value=MODE_MANUAL,
                   command=lambda: apply_mode()).grid(row=0, column=1, sticky="w", padx=6)

    # ----------------------------------------------------------- reference --
    ref_lbl = tk.Label(root, text=state["reference"] or "No reference file selected",
                       anchor="w", wraplength=520, justify="left")

    def pick_reference():
        f = filedialog.askopenfilename(title="Select the CORRECT reference PDF",
                                       filetypes=[("PDF files", "*.pdf")])
        if f:
            state["reference"] = f
            ref_lbl.config(text=f)
            load_reference_values()
            persist()

    ref_btn = tk.Button(root, text="Select reference PDF...", command=pick_reference)
    ref_btn.grid(row=2, column=0, sticky="w", **pad)
    ref_lbl.grid(row=2, column=1, sticky="w", **pad)

    # ------------------------------------------------------------- targets --
    tgt_lbl = tk.Label(root, text=f"{len(state['targets'])} target file(s) selected",
                       anchor="w")

    def pick_targets():
        files = filedialog.askopenfilenames(title="Select PDF(s) to fix",
                                            filetypes=[("PDF files", "*.pdf")])
        if files:
            state["targets"] = list(files)
            tgt_lbl.config(text=f"{len(state['targets'])} target file(s) selected")
            persist()

    tk.Button(root, text="Select target PDF(s)...", command=pick_targets)\
        .grid(row=3, column=0, sticky="w", **pad)
    tgt_lbl.grid(row=3, column=1, sticky="w", **pad)

    # -------------------------------------------------------------- output --
    v_output = tk.StringVar(value=cfg.get("last_output", ""))

    def pick_output():
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            v_output.set(d)
            persist()

    tk.Button(root, text="Output folder...", command=pick_output)\
        .grid(row=4, column=0, sticky="w", **pad)
    tk.Label(root, textvariable=v_output, anchor="w")\
        .grid(row=4, column=1, sticky="w", **pad)

    # ------------------------------------------------------ field/value grid --
    head = tk.Frame(root)
    head.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6, pady=(10, 0))
    fields_hdr = tk.Label(head, text="Fields to sync, and the values to write:",
                          anchor="w")
    fields_hdr.grid(row=0, column=0, sticky="w")

    def prefill_from_pdf():
        f = filedialog.askopenfilename(title="Load values from a PDF to edit",
                                       filetypes=[("PDF files", "*.pdf")])
        if not f:
            return
        try:
            values = read_pdf_values(f)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not read PDF", str(exc))
            return
        for label, var in value_vars.items():
            var.set(values.get(label, ""))
        v_mode.set(MODE_MANUAL)
        apply_mode()
        persist()

    prefill_btn = tk.Button(head, text="Prefill from a PDF...",
                            command=prefill_from_pdf)
    prefill_btn.grid(row=0, column=1, sticky="w", padx=12)

    field_vars = {}
    value_vars = {}
    value_entries = {}
    saved_sync = set(cfg.get("sync_fields", DEFAULT_SYNC_FIELDS))
    saved_manual = cfg.get("manual_values", {})

    box = tk.Frame(root)
    box.grid(row=6, column=0, columnspan=2, sticky="ew", padx=20)
    box.columnconfigure(1, weight=1)
    for i, label in enumerate(ALL_FIELDS):
        v = tk.BooleanVar(value=(label in saved_sync))
        field_vars[label] = v
        tk.Checkbutton(box, text=label, variable=v, command=lambda: persist())\
            .grid(row=i, column=0, sticky="w", padx=6, pady=1)
        sv = tk.StringVar(value=saved_manual.get(label, ""))
        value_vars[label] = sv
        e = tk.Entry(box, textvariable=sv, width=44)
        e.grid(row=i, column=1, sticky="ew", padx=6, pady=1)
        e.bind("<FocusOut>", lambda _e: persist())
        value_entries[label] = e

    # Progress + status
    progress = ttk.Progressbar(root, mode="determinate")
    progress.grid(row=7, column=0, columnspan=2, sticky="ew", padx=6, pady=(14, 2))
    status = tk.Label(root, text="Ready", anchor="w")
    status.grid(row=8, column=0, columnspan=2, sticky="ew", padx=6)

    # Report box
    report_box = tk.Text(root, height=12, width=78)
    report_box.grid(row=10, column=0, columnspan=2, sticky="nsew", padx=6, pady=(8, 6))
    report_box.configure(state="disabled")

    run_btn = tk.Button(root, text="Run")

    def persist():
        try:
            save_config({
                "reference_file": state["reference"],
                "last_targets": state["targets"],
                "last_output": v_output.get().strip(),
                "sync_fields": [lbl for lbl, v in field_vars.items() if v.get()],
                "font_size": cfg.get("font_size", 8.5),
                "input_mode": v_mode.get(),
                "manual_values": {lbl: v.get() for lbl, v in value_vars.items()},
            })
        except Exception:
            pass

    def set_entry_values(values: dict):
        """Write values into the boxes regardless of their current state."""
        for label, var in value_vars.items():
            entry = value_entries[label]
            was = entry.cget("state")
            entry.configure(state="normal")
            var.set(values.get(label, ""))
            entry.configure(state=was)

    def load_reference_values():
        """
        Show, in the value boxes, exactly what the reference PDF will write.
        A missing or unreadable reference is not fatal here -- it just leaves
        the boxes empty and says so; Run does the real validation.
        """
        ref = state["reference"]
        if not ref or not Path(ref).exists():
            state["anchors"] = None
            return
        try:
            values, anchors = read_reference(ref)
        except Exception as exc:  # noqa: BLE001
            state["anchors"] = None
            status.config(text=f"Could not read reference: {exc}")
            return
        state["anchors"] = anchors
        set_entry_values(values)
        status.config(text=f"Read {sum(1 for v in values.values() if v.strip())} "
                           f"value(s) from {Path(ref).name}")

    def apply_mode():
        """
        Reference mode: the boxes are a read-only preview of the reference.
        Typed mode: the boxes are the input, and the reference is irrelevant.
        """
        manual = v_mode.get() == MODE_MANUAL
        for entry in value_entries.values():
            entry.configure(state="normal" if manual else "readonly")
        ref_btn.configure(state="disabled" if manual else "normal")
        ref_lbl.configure(fg="grey" if manual else "black")
        fields_hdr.configure(
            text="Fields to sync, and the values to write:" if manual
            else "Fields to sync (values shown are read from the reference PDF):")
        if not manual:
            load_reference_values()
        persist()

    def validate():
        manual = v_mode.get() == MODE_MANUAL
        if not manual and not state["reference"]:
            messagebox.showerror("Missing reference", "Select the correct reference PDF.")
            return None
        if not state["targets"]:
            messagebox.showerror("Missing targets", "Select at least one PDF to fix.")
            return None
        out = v_output.get().strip()
        if not out:
            messagebox.showerror("Missing output", "Select an output folder.")
            return None
        sync_fields = [lbl for lbl, v in field_vars.items() if v.get()]
        if not sync_fields:
            messagebox.showerror("No fields selected", "Tick at least one field to sync.")
            return None
        if manual:
            typed = {lbl: value_vars[lbl].get().strip() for lbl in sync_fields}
            if not any(typed.values()):
                messagebox.showerror(
                    "Nothing to write",
                    "Type a value for at least one ticked field.")
                return None
            blank = [lbl for lbl, v in typed.items() if not v]
            if blank and not messagebox.askyesno(
                    "Some fields are blank",
                    "No value typed for:\n\n  " + "\n  ".join(blank) +
                    "\n\nThose fields will be left as they are in the target "
                    "PDFs (blank never overwrites). Continue?"):
                return None
            values, anchors = resolve_values(None, typed)
        else:
            try:
                values, anchors = read_reference(state["reference"])
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Could not read reference", str(exc))
                return None
            if not any(v.strip() for v in values.values()):
                messagebox.showerror(
                    "Empty reference",
                    "No Project Information values could be read from the "
                    "reference PDF. Is it a MagiCAD report with selectable text?")
                return None
        os.makedirs(out, exist_ok=True)
        return out, sync_fields, values, anchors

    def worker(values, targets, out_dir, sync_fields, anchors):
        def cb(done, total, name):
            ui_queue.put(("progress", done, total, name))
        try:
            report, errors = run_batch(values, targets, out_dir, sync_fields,
                                       cfg.get("font_size", 8.5), anchors,
                                       progress_cb=cb)
            ui_queue.put(("done", report, errors))
        except Exception as exc:
            ui_queue.put(("fatal", str(exc)))

    def write_report(report, errors):
        report_box.configure(state="normal")
        report_box.delete("1.0", "end")
        for name, info in report.items():
            changes = info["changed"]
            skipped = info["skipped"]
            if not changes and not skipped:
                report_box.insert("end", f"{name}: no changes needed\n")
            elif changes:
                report_box.insert("end", f"{name}:\n")
                for label, (old, new) in changes.items():
                    report_box.insert("end", f"    {label:28} '{old}' -> '{new}'\n")
            if skipped:
                report_box.insert("end", f"{name}: SKIPPED (no new value, left unchanged):\n")
                for label in skipped:
                    report_box.insert("end", f"    {label:28} input is blank\n")
        if errors:
            report_box.insert("end", "\nFailed:\n")
            for name, msg in errors:
                report_box.insert("end", f"  {name}: {msg}\n")
        report_box.configure(state="disabled")

    def poll_queue():
        try:
            while True:
                msg = ui_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, done, total, name = msg
                    progress["maximum"] = total
                    progress["value"] = done
                    status.config(text=f"Processed {done}/{total}: {name}")
                elif kind == "done":
                    _, report, errors = msg
                    run_btn.config(state="normal")
                    status.config(text=f"Finished. {len(report)} file(s) processed, "
                                       f"{len(errors)} failed.")
                    write_report(report, errors)
                    if errors:
                        messagebox.showwarning("Completed with errors",
                                               f"{len(errors)} file(s) failed. See the log.")
                    else:
                        messagebox.showinfo("Done", "All files processed.")
                    return
                elif kind == "fatal":
                    run_btn.config(state="normal")
                    status.config(text="Error")
                    messagebox.showerror("Error", msg[1])
                    return
        except queue.Empty:
            pass
        root.after(100, poll_queue)

    def on_run():
        result = validate()
        if not result:
            return
        out_dir, sync_fields, values, anchors = result
        persist()
        run_btn.config(state="disabled")
        progress["value"] = 0
        status.config(text="Working...")
        t = threading.Thread(target=worker,
                             args=(values, list(state["targets"]), out_dir,
                                   sync_fields, anchors),
                             daemon=True)
        t.start()
        root.after(100, poll_queue)

    run_btn.config(command=on_run)
    run_btn.grid(row=9, column=0, columnspan=2, pady=8)

    root.columnconfigure(1, weight=1)
    root.rowconfigure(10, weight=1)
    root.protocol("WM_DELETE_WINDOW", lambda: (persist(), root.destroy()))
    apply_mode()
    root.mainloop()


def main():
    if "--cli" in sys.argv:
        argv = [a for a in sys.argv[1:] if a != "--cli"]
        sys.exit(cli_main(argv))
    gui_main()


if __name__ == "__main__":
    main()
