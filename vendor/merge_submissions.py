#!/usr/bin/env python3
"""
Review and merge equipment submissions into the central library.

    python merge_submissions.py EQUIPMENT_LIBRARY_MASTER.xlsx submissions/
    python merge_submissions.py EQUIPMENT_LIBRARY_MASTER.xlsx submissions/ --apply

Dry run by default. It prints exactly what it would do and changes nothing.
Add --apply to write to the master and archive the processed files.

What it reports
  NEW          model reference not in the master, will be added
  DUPLICATE    already present and identical, archived without change
  CONFLICT     already present with different values, listed field by field
               and SKIPPED unless you pass --overwrite
  CANNOT       fields that do not exist on that sheet, left for you
  DRIFT        a text value that nearly matches an existing one, e.g.
               GRUNDFOS vs Grundfos. Flagged so you normalise it early.
"""

import argparse
import difflib
import json
import os
import shutil
import sys
from collections import defaultdict

from openpyxl import load_workbook

HDR_ROW, UNIT_ROW, DATA_TOP = 1, 2, 3
DRIFT_RATIO = 0.86


def keynorm(k):
    """Tolerant field-key match: m2/m\u00b2, degC/\u00b0C, spacing and case."""
    k = (k.replace("\u00b2", "2").replace("\u00b3", "3")
          .replace("\u00b0C", "degC").replace("\u00b0", "deg"))
    return " ".join(k.split()).lower()


def norm(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("none", "nan") else s


def load_submissions(folder):
    subs = []
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(folder, fn)
        try:
            with open(path, encoding="utf-8-sig") as fh:
                d = json.load(fh)
            if not d.get("equipment_code") or not d.get("model_reference"):
                print(f"  SKIP {fn}: missing equipment_code or model_reference")
                continue
            d["_file"], d["_path"] = fn, path
            subs.append(d)
        except Exception as e:
            print(f"  SKIP {fn}: not readable ({e})")
    return subs


def sheet_index(ws):
    """Canonical headers ('Length (mm)') plus {model_reference_lower: row}."""
    hdrs = []
    c = 1
    while ws.cell(HDR_ROW, c).value is not None:
        name = str(ws.cell(HDR_ROW, c).value).strip()
        unit = ws.cell(UNIT_ROW, c).value
        unit = str(unit).strip() if unit is not None else ""
        hdrs.append(f"{name} ({unit})" if unit else name)
        c += 1
    idx, r = {}, DATA_TOP
    while ws.cell(r, 1).value is not None:
        idx[str(ws.cell(r, 1).value).strip().lower()] = r
        r += 1
    return hdrs, idx, r


def find_drift(ws, col_i, value):
    if not value or len(value) < 3 or value.replace(".", "").isdigit():
        return []
    seen, out, r = set(), [], DATA_TOP
    while ws.cell(r, 1).value is not None:
        ex = norm(ws.cell(r, col_i).value)
        if ex and ex != value and ex.lower() not in seen:
            seen.add(ex.lower())
            if difflib.SequenceMatcher(None, ex.lower(), value.lower()).ratio() >= DRIFT_RATIO:
                out.append(ex)
        r += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("master")
    ap.add_argument("submissions")
    ap.add_argument("--apply", action="store_true", help="write changes and archive files")
    ap.add_argument("--overwrite", action="store_true",
                    help="allow conflicting entries to replace existing rows")
    args = ap.parse_args()

    if not os.path.isfile(args.master):
        sys.exit(f"Master library not found: {args.master}")
    if not os.path.isdir(args.submissions):
        sys.exit(f"Submissions folder not found: {args.submissions}")

    subs = load_submissions(args.submissions)
    if not subs:
        print("No submissions to process.")
        return

    wb = load_workbook(args.master)
    by_code = defaultdict(list)
    for s in subs:
        by_code[s["equipment_code"]].append(s)

    plan = {"new": [], "duplicate": [], "conflict": [], "orphan": []}
    drift_notes = []

    for code, items in sorted(by_code.items()):
        if code not in wb.sheetnames:
            for s in items:
                plan["orphan"].append((s, f"master has no sheet named '{code}'"))
            continue

        ws = wb[code]
        hdrs, idx, next_row = sheet_index(ws)
        col_of = {h: i + 1 for i, h in enumerate(hdrs)}
        for i, h in enumerate(hdrs):
            bare = h.rsplit(" (", 1)[0] if h.endswith(")") else h
            for alias in (bare, keynorm(h), keynorm(bare)):
                col_of.setdefault(alias, i + 1)

        for s in items:
            ref = s["model_reference"].strip()
            raw = {k.strip(): norm(v) for k, v in (s.get("fields") or {}).items()}
            fields = {(k if k in col_of else keynorm(k)): v for k, v in raw.items()}

            unknown = [k for k in fields if k not in col_of]
            if unknown:
                plan["orphan"].append(
                    (s, f"fields not on the {code} sheet: {', '.join(unknown[:4])}"))
                continue

            for k, v in fields.items():
                for near in find_drift(ws, col_of[k], v):
                    drift_notes.append((code, k, v, near))

            if ref.lower() in idx:
                row = idx[ref.lower()]
                diffs = [(k, norm(ws.cell(row, col_of[k]).value), v)
                         for k, v in fields.items()
                         if norm(ws.cell(row, col_of[k]).value) != v]
                if diffs:
                    plan["conflict"].append((s, code, row, diffs, col_of, fields))
                else:
                    plan["duplicate"].append((s, code))
            else:
                plan["new"].append((s, code, next_row, col_of, fields))
                idx[ref.lower()] = next_row
                next_row += 1

    print()
    print("=" * 72)
    print(f"{'APPLYING' if args.apply else 'DRY RUN'}   {len(subs)} submission(s)")
    print("=" * 72)

    for s, code, *_ in plan["new"]:
        print(f"  NEW        [{code}] {s['model_reference']}  "
              f"(from {s.get('submitted_by','?')}, {s.get('source_document','')[:34]})")
    for s, code in plan["duplicate"]:
        print(f"  DUPLICATE  [{code}] {s['model_reference']}  identical, will be archived")
    for s, code, row, diffs, *_ in plan["conflict"]:
        print(f"  CONFLICT   [{code}] {s['model_reference']}  row {row}, {len(diffs)} field(s) differ:")
        for k, ex, v in diffs[:6]:
            print(f"                 {k}:  master='{ex}'  submitted='{v}'")
        if len(diffs) > 6:
            print(f"                 ...and {len(diffs) - 6} more")
        print("                 " + ("WILL OVERWRITE" if args.overwrite
                                      else "SKIPPED (use --overwrite)"))
    for s, why in plan["orphan"]:
        print(f"  CANNOT     [{s['equipment_code']}] {s['model_reference']}: {why}")

    if drift_notes:
        print()
        print("  SPELLING DRIFT (fix these before they spread through the library)")
        seen = set()
        for code, k, v, near in drift_notes:
            key = (code, k, v.lower(), near.lower())
            if key not in seen:
                seen.add(key)
                print(f"    [{code}] {k}: submitted '{v}' looks like existing '{near}'")

    if not args.apply:
        print()
        print("Nothing written. Re-run with --apply to commit.")
        return

    archive = os.path.join(args.submissions, "_processed")
    os.makedirs(archive, exist_ok=True)
    done = []

    for s, code, row, col_of, fields in plan["new"]:
        ws = wb[code]
        ws.cell(row, 1, s["model_reference"].strip())
        for k, v in fields.items():
            ws.cell(row, col_of[k], v)
        done.append(s)

    done.extend(s for s, _ in plan["duplicate"])

    if args.overwrite:
        for s, code, row, diffs, col_of, fields in plan["conflict"]:
            ws = wb[code]
            for k, v in fields.items():
                ws.cell(row, col_of[k], v)
            done.append(s)

    wb.save(args.master)
    for s in done:
        shutil.move(s["_path"], os.path.join(archive, s["_file"]))

    print()
    print(f"Wrote {len(plan['new'])} new entr(y/ies) to {args.master}")
    print(f"Archived {len(done)} file(s) to {archive}")
    left = len(subs) - len(done)
    if left:
        print(f"{left} file(s) left in the folder for you to resolve.")


if __name__ == "__main__":
    main()
