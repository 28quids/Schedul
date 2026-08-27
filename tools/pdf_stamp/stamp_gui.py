#!/usr/bin/env python3
"""
PDF Filename Stamping Tool (GUI)
================================

Stamps each selected PDF with a three-line block in the TOP-RIGHT corner of the
page(s), using PyMuPDF:

    EF-007 EXHAUST PRESSURE     <- the file name (without ".pdf")
    Rev: P01                    <- set per document
    Date: 27/08/2026            <- set per document

Revision and date are held per file in a table, so a batch of drawings can each
carry their own revision without running the tool once per file. A blank
revision or date simply omits that line.

Features
--------
- Batch process many PDFs at once, each with its own revision and date.
- Editable file table: double-click a Rev/Date cell to type, or fill the whole
  selection at once from the "apply to selected" row.
- Output to a chosen folder; originals are never touched unless output == source.
- Filename rendered in BLOCK CAPITALS (toggleable); Rev/Date keep their case.
- Font: a supplied TTF/OTF file OR a built-in font (helv / times / cour).
- Configurable font size, line spacing, hex colour, bold, right/top margins in mm.
- Stamp all pages or only the first N pages.
- Optional white background box behind the whole block for legibility.
- Overwrite toggle; when off, existing files get a "_stamped" suffix.
- Persistent settings (including each file's revision and date) saved to
  stamp_gui_config.json under %APPDATA%\\RamblyStamp.
- Determinate progress bar + status text; stamping runs on a background thread
  so the UI stays responsive (updates polled via a thread-safe queue).

Install
-------
    py -m pip install PyMuPDF

Run
---
    py stamp_gui.py

CLI (no GUI)
------------
    py stamp_gui.py --cli --out OUTPUT_DIR --rev P01 --date 27/08/2026 a.pdf b.pdf
    py stamp_gui.py --cli --out OUTPUT_DIR --meta revisions.csv *.pdf
    (see: py stamp_gui.py --cli -h)

The --meta CSV carries per-file overrides, one row per file:

    file,rev,date
    EF-007 EXHAUST PRESSURE.pdf,P01,27/08/2026
    EF-008 SUPPLY PRESSURE.pdf,P02,27/08/2026

Matching is on the file name (with or without the ".pdf"), so the same CSV works
whatever folder the PDFs are read from. Anything not listed falls back to the
--rev / --date values.

Config
------
Saved as "stamp_gui_config.json" under %APPDATA%\\RamblyStamp (or the home
directory on non-Windows).

Font / bold notes
-----------------
Built-in fonts DO have bold variants (helv->hebo, times->tibo, cour->cobo), so
bold works for built-ins. For any specific typeface, pick a bold TTF/OTF to be
certain of the exact appearance you want.
"""

import os
import csv
import sys
import json
import math
import queue
import argparse
import threading
from pathlib import Path
from datetime import date as _date

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

# Built-in font -> (regular code, bold code) as understood by PyMuPDF.
BUILTIN_FONTS = {
    "helv": ("helv", "hebo"),   # Helvetica
    "times": ("tiro", "tibo"),  # Times-Roman
    "cour": ("cour", "cobo"),   # Courier
}

DATE_FORMAT = "%d/%m/%Y"        # the format the Date: line is written in

DEFAULT_CONFIG = {
    "font_file": "",            # path to TTF/OTF, "" means use built-in
    "font_builtin": "helv",
    "font_size": 12.0,
    "line_spacing": 1.25,       # baseline-to-baseline, as a multiple of size
    "color": "#000000",
    "margin_right_mm": 10.0,
    "margin_top_mm": 10.0,
    "all_pages": True,
    "num_pages": 1,
    "caps": True,
    "bold": False,
    "bg_box": False,            # draw white rectangle behind text
    "overwrite": True,
    "flatten": False,           # rasterize pages so nothing can be edited
    "flatten_dpi": 200,
    "rev_label": "Rev:",
    "date_label": "Date:",
    "default_rev": "P01",       # pre-filled for newly added files
    "last_files": [],
    "file_meta": {},            # path -> {"rev": ..., "date": ...}
    "last_output": "",
}

ASCII_LOGO = r""" ____  ____  _____   ____ _____  _    __  __ ____  
|  _ \|  _ \|  ___| / ___|_   _|/ \  |  \/  |  _ \ 
| |_) | | | | |_    \___ \ | | / _ \ | |\/| | |_) |
|  __/| |_| |  _|    ___) || |/ ___ \| |  | |  __/ 
|_|   |____/|_|     |____/ |_/_/   \_\_|  |_|_|    """


def today_str() -> str:
    """Today's date in the format the Date: line uses."""
    return _date.today().strftime(DATE_FORMAT)


def build_logo(parent):
    """
    Build the top banner: black "PDF STAMP" ASCII text on white, with two
    small spinning stars flanking it, and a slide-up entrance animation.
    Returns the Canvas widget (ready to be grid()'d by the caller).
    """
    import tkinter as tk
    W, H = 620, 100
    canvas = tk.Canvas(parent, width=W, height=H, bg="white",
                       highlightthickness=0, bd=0)
    center_x = W // 2
    target_y = H // 2
    start_y = H + 40  # off-screen below the canvas; slides up into place

    text_id = canvas.create_text(center_x, start_y, text=ASCII_LOGO,
                                 font=("Courier New", 10, "bold"),
                                 fill="black", justify="center")

    def make_star(cx, cy, outer=11, inner=5):
        item = canvas.create_polygon(0, 0, fill="black", outline="")
        return {"id": item, "cx": cx, "cy": cy, "outer": outer,
                "inner": inner, "angle": 0.0}

    star_left = make_star(48, target_y)
    star_right = make_star(W - 48, target_y)

    def star_points(star):
        cx, cy = star["cx"], star["cy"]
        outer, inner, angle = star["outer"], star["inner"], star["angle"]
        pts = []
        for i in range(10):
            r = outer if i % 2 == 0 else inner
            a = angle + i * (math.pi / 5)
            pts.append(cx + r * math.sin(a))
            pts.append(cy - r * math.cos(a))
        return pts

    def spin():
        if not canvas.winfo_exists():
            return
        for star in (star_left, star_right):
            star["angle"] += 0.35
            canvas.coords(star["id"], *star_points(star))
        canvas.after(60, spin)

    def slide_step(y):
        if not canvas.winfo_exists():
            return
        if y <= target_y:
            canvas.coords(text_id, center_x, target_y)
            spin()
            return
        step = max(2, int((y - target_y) * 0.18))
        new_y = y - step
        canvas.coords(text_id, center_x, new_y)
        canvas.after(16, slide_step, new_y)

    slide_step(start_y)
    return canvas


# --------------------------------------------------------------------------- #
# Config persistence
# --------------------------------------------------------------------------- #

def load_config(path: Path = CONFIG_PATH) -> dict:
    """Load settings from JSON, filling any missing keys with defaults."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                cfg.update({k: data[k] for k in data if k in DEFAULT_CONFIG})
    except (OSError, json.JSONDecodeError):
        # Corrupt/unreadable config: silently fall back to defaults.
        pass
    if not isinstance(cfg.get("file_meta"), dict):
        cfg["file_meta"] = {}
    return cfg


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    """Persist settings to JSON. Only known keys are written."""
    clean = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
    except OSError:
        pass  # never crash the app because settings could not be written


# --------------------------------------------------------------------------- #
# Geometry / measurement helpers
# --------------------------------------------------------------------------- #

def mm_to_points(mm: float) -> float:
    """Convert millimetres to PDF points (1 pt = 1/72 inch)."""
    return mm * 72.0 / 25.4


def hex_to_rgb01(hex_color: str):
    """Convert '#RRGGBB' to a normalised (r, g, b) tuple in 0..1."""
    s = hex_color.lstrip("#")
    if len(s) == 3:  # allow shorthand like #f00
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (0.0, 0.0, 0.0)
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return (r, g, b)


def measure_text_width(text: str, size: float, font_file: str = "",
                       builtin_code: str = "helv") -> float:
    """
    Measure rendered text width in points.

    Uses a loaded TTF/OTF font when font_file is given; otherwise the built-in
    font code. Falls back to an approximation if measurement fails.
    """
    try:
        if font_file:
            font = fitz.Font(fontfile=font_file)
            return font.text_length(text, size)
        return fitz.get_text_length(text, fontname=builtin_code, fontsize=size)
    except Exception:
        # Rough fallback: average glyph ~= 0.55 * size for Latin text.
        return len(text) * size * 0.55


# --------------------------------------------------------------------------- #
# Core stamping
# --------------------------------------------------------------------------- #

def resolve_font(font_file: str, builtin: str, bold: bool):
    """
    Decide which font to use for insertion.

    Returns (fontname, fontfile) where:
      - fontfile is a path string (custom TTF/OTF) or None.
      - fontname is either an internal reference tag (for a custom file) or a
        PyMuPDF built-in code.
    """
    if font_file:
        return ("customfont", font_file)
    regular, bold_code = BUILTIN_FONTS.get(builtin, BUILTIN_FONTS["helv"])
    return ((bold_code if bold else regular), None)


def build_stamp_lines(in_path: str, rev: str = "", date: str = "", *,
                      caps: bool = True,
                      rev_label: str = "Rev:",
                      date_label: str = "Date:"):
    """
    Build the lines of the stamp block for one file, top line first.

    The first line is always the file name (BLOCK CAPS when caps is set). The
    revision and date lines are added only when a value was supplied, so a file
    left blank in the table stamps exactly as the old single-line version did.
    """
    name = Path(in_path).stem
    if caps:
        name = name.upper()
    lines = [name]
    rev = (rev or "").strip()
    date = (date or "").strip()
    if rev:
        lines.append(f"{rev_label} {rev}".strip())
    if date:
        lines.append(f"{date_label} {date}".strip())
    return lines


def stamp_pdf(in_path: str, out_path: str, *,
              rev: str = "",
              date: str = "",
              caps: bool = True,
              font_file: str = "",
              builtin: str = "helv",
              bold: bool = False,
              font_size: float = 12.0,
              line_spacing: float = 1.25,
              color_hex: str = "#000000",
              margin_right_mm: float = 10.0,
              margin_top_mm: float = 10.0,
              all_pages: bool = True,
              num_pages: int = 1,
              bg_box: bool = False,
              flatten: bool = False,
              flatten_dpi: int = 200,
              rev_label: str = "Rev:",
              date_label: str = "Date:") -> int:
    """
    Stamp one PDF and write it to out_path.

    Every line of the block is right-aligned to the same right margin, so the
    filename, revision and date form a clean edge in the top-right corner.

    Returns the number of pages stamped. Raises on unrecoverable errors so the
    caller can report which file failed.
    """
    lines = build_stamp_lines(in_path, rev, date, caps=caps,
                              rev_label=rev_label, date_label=date_label)

    color = hex_to_rgb01(color_hex)
    right_pts = mm_to_points(margin_right_mm)
    top_pts = mm_to_points(margin_top_mm)
    fontname, fontfile = resolve_font(font_file, builtin, bold)
    widths = [measure_text_width(t, font_size, font_file, builtin) for t in lines]
    block_w = max(widths)
    line_h = font_size * max(1.0, line_spacing)

    doc = fitz.open(in_path)
    try:
        total = doc.page_count
        limit = total if all_pages else max(0, min(num_pages, total))
        stamped = 0

        for pno in range(limit):
            page = doc[pno]
            rect = page.rect
            # y is the baseline of the first line (page origin top-left, y down)
            first_baseline = rect.y0 + top_pts + font_size
            right_edge = rect.x1 - right_pts

            if bg_box:
                pad = font_size * 0.2
                last_baseline = first_baseline + (len(lines) - 1) * line_h
                box = fitz.Rect(right_edge - block_w - pad,
                                first_baseline - font_size - pad,
                                right_edge + pad,
                                last_baseline + pad)
                page.draw_rect(box, color=None, fill=(1, 1, 1),
                               fill_opacity=0.7, overlay=True)

            for i, (line, width) in enumerate(zip(lines, widths)):
                x = right_edge - width
                y = first_baseline + i * line_h
                try:
                    page.insert_text(
                        (x, y), line,
                        fontsize=font_size,
                        fontname=fontname,
                        fontfile=fontfile,
                        color=color,
                        overlay=True,
                    )
                except Exception:
                    # Fallback: default Helvetica if the chosen font failed.
                    page.insert_text((x, y), line, fontsize=font_size,
                                     color=color, overlay=True)
            stamped += 1

        if flatten:
            # Rasterize every page into an image-only PDF. This bakes the
            # stamp AND all original content into pixels, so nothing (text,
            # the stamp, or form fields) can be edited or removed downstream.
            flat = fitz.open()
            try:
                for page in doc:
                    pix = page.get_pixmap(dpi=flatten_dpi)
                    np = flat.new_page(width=page.rect.width,
                                       height=page.rect.height)
                    np.insert_image(np.rect, pixmap=pix)
                flat.save(out_path, garbage=3, deflate=True)
            finally:
                flat.close()
        else:
            # incremental=False writes a fresh, self-consistent file (safe).
            doc.save(out_path, garbage=3, deflate=True)
        return stamped
    finally:
        doc.close()


def build_output_path(in_path: str, out_dir: str, overwrite: bool) -> str:
    """
    Compute destination path in out_dir using the source filename.

    If overwrite is False and the target exists, append '_stamped' (and a
    counter if needed) to avoid clobbering.
    """
    src = Path(in_path)
    dest = Path(out_dir) / src.name
    if overwrite or not dest.exists():
        return str(dest)
    candidate = dest.with_name(f"{src.stem}_stamped{src.suffix}")
    n = 2
    while candidate.exists():
        candidate = dest.with_name(f"{src.stem}_stamped_{n}{src.suffix}")
        n += 1
    return str(candidate)


def as_job(item, settings: dict) -> dict:
    """
    Normalise one entry of the batch list into {"path", "rev", "date"}.

    Accepts a plain path (revision/date then come from the batch settings) or a
    dict carrying that file's own revision and date.
    """
    if isinstance(item, dict):
        return {
            "path": item.get("path", ""),
            "rev": item.get("rev", settings.get("rev", "")) or "",
            "date": item.get("date", settings.get("date", "")) or "",
        }
    return {
        "path": item,
        "rev": settings.get("rev", "") or "",
        "date": settings.get("date", "") or "",
    }


def run_batch(files, out_dir, settings, progress_cb=None, error_mode="continue"):
    """
    Stamp a list of files. Each entry is either a path or a dict with "path",
    "rev" and "date". progress_cb(done, total, current_name) is called after
    each file. Returns (ok_count, errors[list of (file, message)]).

    error_mode: "continue" keeps going after a failure; "stop" re-raises.
    """
    total = len(files)
    ok = 0
    errors = []
    for i, item in enumerate(files, start=1):
        job = as_job(item, settings)
        name = Path(job["path"]).name
        try:
            out_path = build_output_path(job["path"], out_dir,
                                         settings.get("overwrite", True))
            stamp_pdf(
                job["path"], out_path,
                rev=job["rev"],
                date=job["date"],
                caps=settings["caps"],
                font_file=settings["font_file"],
                builtin=settings["font_builtin"],
                bold=settings["bold"],
                font_size=settings["font_size"],
                line_spacing=settings.get("line_spacing", 1.25),
                color_hex=settings["color"],
                margin_right_mm=settings["margin_right_mm"],
                margin_top_mm=settings["margin_top_mm"],
                all_pages=settings["all_pages"],
                num_pages=settings["num_pages"],
                bg_box=settings["bg_box"],
                flatten=settings.get("flatten", False),
                flatten_dpi=settings.get("flatten_dpi", 200),
                rev_label=settings.get("rev_label", "Rev:"),
                date_label=settings.get("date_label", "Date:"),
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report per file
            errors.append((name, str(exc)))
            if error_mode == "stop":
                if progress_cb:
                    progress_cb(i, total, name)
                raise
        if progress_cb:
            progress_cb(i, total, name)
    return ok, errors


# --------------------------------------------------------------------------- #
# Per-file revision/date metadata (CLI)
# --------------------------------------------------------------------------- #

def load_meta_csv(path: str) -> dict:
    """
    Read a "file,rev,date" CSV into {lowercased file stem: {"rev", "date"}}.

    A header row is optional. The file column may carry a full path, a file
    name, or a bare stem; all three are matched on the stem so the same sheet
    works no matter where the PDFs are read from.
    """
    meta = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            cells = [c.strip() for c in row]
            if not cells or not cells[0]:
                continue
            if cells[0].lower() in ("file", "filename", "name"):
                continue  # header
            key = Path(cells[0]).stem.lower()
            meta[key] = {
                "rev": cells[1] if len(cells) > 1 else "",
                "date": cells[2] if len(cells) > 2 else "",
            }
    return meta


def apply_meta(files, meta: dict, default_rev: str, default_date: str):
    """Pair each input file with its revision/date, falling back to defaults."""
    jobs = []
    for f in files:
        entry = meta.get(Path(f).stem.lower(), {})
        jobs.append({
            "path": f,
            "rev": entry.get("rev") or default_rev,
            "date": entry.get("date") or default_date,
        })
    return jobs


# --------------------------------------------------------------------------- #
# CLI mode
# --------------------------------------------------------------------------- #

def cli_main(argv):
    p = argparse.ArgumentParser(
        prog="stamp_gui.py --cli",
        description="Stamp filename, revision and date into the top-right "
                    "corner of PDFs (batch).",
    )
    p.add_argument("files", nargs="+", help="Input PDF files")
    p.add_argument("--out", required=True, help="Output folder")
    p.add_argument("--rev", default="",
                   help="Revision for every file, e.g. P01 (blank = no Rev line)")
    p.add_argument("--date", default="",
                   help="Date for every file, e.g. 27/08/2026 "
                        "(use 'today' for today's date; blank = no Date line)")
    p.add_argument("--meta", default="",
                   help="CSV of per-file overrides: file,rev,date")
    p.add_argument("--rev-label", default="Rev:", help="Label before the revision")
    p.add_argument("--date-label", default="Date:", help="Label before the date")
    p.add_argument("--builtin", default="helv", choices=list(BUILTIN_FONTS),
                   help="Built-in font (ignored if --font-file given)")
    p.add_argument("--font-file", default="", help="TTF/OTF font file")
    p.add_argument("--size", type=float, default=12.0, help="Font size (pt)")
    p.add_argument("--line-spacing", type=float, default=1.25,
                   help="Baseline-to-baseline spacing, as a multiple of size")
    p.add_argument("--color", default="#000000", help="Hex colour, e.g. #FF0000")
    p.add_argument("--margin-right", type=float, default=10.0, help="mm")
    p.add_argument("--margin-top", type=float, default=10.0, help="mm")
    p.add_argument("--bold", action="store_true")
    p.add_argument("--no-caps", action="store_true", help="Keep original case")
    p.add_argument("--first", type=int, default=0,
                   help="Stamp only first N pages (0 = all pages)")
    p.add_argument("--bg-box", action="store_true", help="White box behind text")
    p.add_argument("--no-overwrite", action="store_true")
    p.add_argument("--flatten", action="store_true",
                   help="Rasterize output so it cannot be edited")
    p.add_argument("--dpi", type=int, default=200,
                   help="Rasterization DPI (only used with --flatten)")
    args = p.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    default_date = today_str() if args.date.strip().lower() == "today" else args.date
    meta = load_meta_csv(args.meta) if args.meta else {}
    jobs = apply_meta(args.files, meta, args.rev, default_date)

    settings = {
        "caps": not args.no_caps,
        "font_file": args.font_file,
        "font_builtin": args.builtin,
        "bold": args.bold,
        "font_size": args.size,
        "line_spacing": args.line_spacing,
        "color": args.color,
        "margin_right_mm": args.margin_right,
        "margin_top_mm": args.margin_top,
        "all_pages": args.first <= 0,
        "num_pages": max(1, args.first),
        "bg_box": args.bg_box,
        "overwrite": not args.no_overwrite,
        "flatten": args.flatten,
        "flatten_dpi": args.dpi,
        "rev_label": args.rev_label,
        "date_label": args.date_label,
    }

    def cb(done, total, name):
        print(f"[{done}/{total}] {name}")

    ok, errors = run_batch(jobs, args.out, settings, progress_cb=cb)
    print(f"\nDone. {ok} succeeded, {len(errors)} failed.")
    for name, msg in errors:
        print(f"  FAILED {name}: {msg}")
    return 0 if not errors else 1


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

def gui_main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, colorchooser

    cfg = load_config()

    root = tk.Tk()
    root.title("PDF Filename Stamper")
    root.minsize(860, 700)
    root.geometry("900x820")

    ui_queue = queue.Queue()

    # ---- the per-file table: one row per PDF, each with its own rev + date ----
    saved_meta = cfg.get("file_meta", {})
    state = {"rows": []}
    for path in cfg.get("last_files", []):
        entry = saved_meta.get(path, {})
        state["rows"].append({
            "path": path,
            "rev": entry.get("rev", cfg.get("default_rev", "")),
            "date": entry.get("date", ""),
        })

    # ---- tk variables seeded from config ----
    v_font_file = tk.StringVar(value=cfg["font_file"])
    v_builtin = tk.StringVar(value=cfg["font_builtin"])
    v_size = tk.StringVar(value=str(cfg["font_size"]))
    v_spacing = tk.StringVar(value=str(cfg["line_spacing"]))
    v_color = tk.StringVar(value=cfg["color"])
    v_mr = tk.StringVar(value=str(cfg["margin_right_mm"]))
    v_mt = tk.StringVar(value=str(cfg["margin_top_mm"]))
    v_all = tk.BooleanVar(value=cfg["all_pages"])
    v_num = tk.StringVar(value=str(cfg["num_pages"]))
    v_caps = tk.BooleanVar(value=cfg["caps"])
    v_bold = tk.BooleanVar(value=cfg["bold"])
    v_bg = tk.BooleanVar(value=cfg["bg_box"])
    v_overwrite = tk.BooleanVar(value=cfg["overwrite"])
    v_flatten = tk.BooleanVar(value=cfg["flatten"])
    v_dpi = tk.StringVar(value=str(cfg["flatten_dpi"]))
    v_output = tk.StringVar(value=cfg["last_output"])
    v_rev_label = tk.StringVar(value=cfg["rev_label"])
    v_date_label = tk.StringVar(value=cfg["date_label"])
    v_fill_rev = tk.StringVar(value=cfg.get("default_rev", "P01"))
    v_fill_date = tk.StringVar(value=today_str())

    def collect_settings() -> dict:
        return {
            "font_file": v_font_file.get().strip(),
            "font_builtin": v_builtin.get(),
            "font_size": float(v_size.get()),
            "line_spacing": float(v_spacing.get()),
            "color": v_color.get(),
            "margin_right_mm": float(v_mr.get()),
            "margin_top_mm": float(v_mt.get()),
            "all_pages": v_all.get(),
            "num_pages": int(float(v_num.get())),
            "caps": v_caps.get(),
            "bold": v_bold.get(),
            "bg_box": v_bg.get(),
            "overwrite": v_overwrite.get(),
            "flatten": v_flatten.get(),
            "flatten_dpi": int(float(v_dpi.get())),
            "rev_label": v_rev_label.get().strip(),
            "date_label": v_date_label.get().strip(),
            "default_rev": v_fill_rev.get().strip(),
            "last_files": [r["path"] for r in state["rows"]],
            "file_meta": {r["path"]: {"rev": r["rev"], "date": r["date"]}
                          for r in state["rows"]},
            "last_output": v_output.get().strip(),
        }

    def persist():
        try:
            save_config(collect_settings())
        except (ValueError, KeyError):
            # invalid numeric field mid-edit: skip this save, don't nag.
            pass

    # ---- layout ----
    pad = {"padx": 6, "pady": 3}

    # ASCII logo: black "PDF STAMP" on white, slide-up entrance, spinning stars
    logo = build_logo(root)
    logo.grid(row=0, column=0, columnspan=4, sticky="ew", padx=6, pady=(6, 8))

    # ---------------- Files table ---------------- #
    files_frame = ttk.LabelFrame(root, text="Documents")
    files_frame.grid(row=1, column=0, columnspan=4, sticky="nsew", padx=6, pady=4)
    files_frame.columnconfigure(0, weight=1)
    files_frame.rowconfigure(1, weight=1)

    btn_bar = tk.Frame(files_frame)
    btn_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 2))

    tree = ttk.Treeview(files_frame, columns=("file", "rev", "date"),
                        show="headings", selectmode="extended", height=6)
    tree.heading("file", text="File")
    tree.heading("rev", text="Rev")
    tree.heading("date", text="Date")
    tree.column("file", width=430, anchor="w", stretch=True)
    tree.column("rev", width=90, anchor="w", stretch=False)
    tree.column("date", width=110, anchor="w", stretch=False)
    tree.grid(row=1, column=0, sticky="nsew", padx=(4, 0), pady=2)

    scroll = ttk.Scrollbar(files_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.grid(row=1, column=1, sticky="ns", padx=(0, 4), pady=2)

    hint = tk.Label(files_frame, anchor="w", fg="#555555",
                    text="Double-click a Rev or Date cell to edit it. "
                         "Leave a cell blank to leave that line off the stamp.")
    hint.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

    def refresh_tree(keep_selection=True):
        selected = set(tree.selection()) if keep_selection else set()
        tree.delete(*tree.get_children())
        for i, row in enumerate(state["rows"]):
            iid = str(i)
            tree.insert("", "end", iid=iid,
                        values=(Path(row["path"]).name, row["rev"], row["date"]))
            if iid in selected:
                tree.selection_add(iid)

    def add_files():
        paths = filedialog.askopenfilenames(
            title="Select PDF files", filetypes=[("PDF files", "*.pdf")])
        if not paths:
            return
        known = {r["path"] for r in state["rows"]}
        for p in paths:
            if p in known:
                continue
            state["rows"].append({
                "path": p,
                "rev": v_fill_rev.get().strip(),
                "date": v_fill_date.get().strip(),
            })
        refresh_tree(keep_selection=False)
        persist()

    def remove_selected():
        doomed = {int(iid) for iid in tree.selection()}
        if not doomed:
            return
        state["rows"] = [r for i, r in enumerate(state["rows"]) if i not in doomed]
        refresh_tree(keep_selection=False)
        persist()

    def clear_files():
        state["rows"] = []
        refresh_tree(keep_selection=False)
        persist()

    def target_indices():
        """Selected rows, or every row when nothing is selected."""
        chosen = [int(iid) for iid in tree.selection()]
        return chosen if chosen else list(range(len(state["rows"])))

    def apply_rev():
        value = v_fill_rev.get().strip()
        for i in target_indices():
            state["rows"][i]["rev"] = value
        refresh_tree()
        persist()

    def apply_date():
        value = v_fill_date.get().strip()
        for i in target_indices():
            state["rows"][i]["date"] = value
        refresh_tree()
        persist()

    def apply_both():
        apply_rev()
        apply_date()

    tk.Button(btn_bar, text="Add PDFs...", command=add_files)\
        .pack(side="left", padx=(0, 4))
    tk.Button(btn_bar, text="Remove selected", command=remove_selected)\
        .pack(side="left", padx=4)
    tk.Button(btn_bar, text="Clear all", command=clear_files)\
        .pack(side="left", padx=4)

    fill_bar = tk.Frame(files_frame)
    fill_bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 6))
    tk.Label(fill_bar, text="Set for selected (or all):").pack(side="left")
    tk.Label(fill_bar, text="Rev").pack(side="left", padx=(8, 2))
    tk.Entry(fill_bar, textvariable=v_fill_rev, width=10).pack(side="left")
    tk.Label(fill_bar, text="Date").pack(side="left", padx=(8, 2))
    tk.Entry(fill_bar, textvariable=v_fill_date, width=12).pack(side="left")
    tk.Button(fill_bar, text="Today",
              command=lambda: v_fill_date.set(today_str()))\
        .pack(side="left", padx=4)
    tk.Button(fill_bar, text="Apply", command=apply_both)\
        .pack(side="left", padx=(8, 2))
    tk.Button(fill_bar, text="Rev only", command=apply_rev).pack(side="left", padx=2)
    tk.Button(fill_bar, text="Date only", command=apply_date).pack(side="left", padx=2)

    # ---- in-place cell editing ----
    editor = {"widget": None}

    def close_editor(commit: bool):
        widget = editor.get("widget")
        if widget is None:
            return
        if commit:
            row_index, field = editor["row"], editor["field"]
            state["rows"][row_index][field] = widget.get().strip()
        editor["widget"] = None
        widget.destroy()
        refresh_tree()
        if commit:
            persist()

    def edit_cell(event):
        if tree.identify("region", event.x, event.y) != "cell":
            return
        column = tree.identify_column(event.x)
        iid = tree.identify_row(event.y)
        if not iid or column not in ("#2", "#3"):
            return  # the File column is not editable
        field = "rev" if column == "#2" else "date"
        x, y, w, h = tree.bbox(iid, column)
        close_editor(commit=False)

        entry = tk.Entry(tree)
        entry.insert(0, state["rows"][int(iid)][field])
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus_set()
        entry.bind("<Return>", lambda e: close_editor(True))
        entry.bind("<KP_Enter>", lambda e: close_editor(True))
        entry.bind("<Escape>", lambda e: close_editor(False))
        entry.bind("<FocusOut>", lambda e: close_editor(True))
        editor.update({"widget": entry, "row": int(iid), "field": field})

    tree.bind("<Double-1>", edit_cell)
    refresh_tree(keep_selection=False)

    # ---------------- Options ---------------- #
    # Output folder
    def pick_output():
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            v_output.set(d)
            persist()

    tk.Button(root, text="Output folder...", command=pick_output)\
        .grid(row=2, column=0, sticky="w", **pad)
    tk.Label(root, textvariable=v_output, anchor="w")\
        .grid(row=2, column=1, columnspan=3, sticky="w", **pad)

    # Font file
    def pick_font():
        f = filedialog.askopenfilename(
            title="Select TTF/OTF font (optional)",
            filetypes=[("Font files", "*.ttf *.otf"), ("All files", "*.*")])
        if f:
            v_font_file.set(f)
            persist()

    tk.Label(root, text="TTF/OTF font:").grid(row=3, column=0, sticky="e", **pad)
    tk.Entry(root, textvariable=v_font_file).grid(row=3, column=1, columnspan=2,
                                                  sticky="ew", **pad)
    tk.Button(root, text="Browse...", command=pick_font)\
        .grid(row=3, column=3, sticky="w", **pad)

    tk.Label(root, text="Built-in font:").grid(row=4, column=0, sticky="e", **pad)
    ttk.Combobox(root, textvariable=v_builtin, values=list(BUILTIN_FONTS),
                 state="readonly", width=10)\
        .grid(row=4, column=1, sticky="w", **pad)

    tk.Label(root, text="Font size (pt):").grid(row=4, column=2, sticky="e", **pad)
    tk.Entry(root, textvariable=v_size, width=8)\
        .grid(row=4, column=3, sticky="w", **pad)

    # Colour
    def pick_color():
        rgb, hx = colorchooser.askcolor(color=v_color.get(), title="Pick colour")
        if hx:
            v_color.set(hx)
            color_btn.config(text=hx)
            persist()

    color_btn = tk.Button(root, text=v_color.get(), command=pick_color)
    tk.Label(root, text="Colour:").grid(row=5, column=0, sticky="e", **pad)
    color_btn.grid(row=5, column=1, sticky="w", **pad)

    # Checkboxes
    tk.Checkbutton(root, text="BLOCK CAPITALS", variable=v_caps, command=persist)\
        .grid(row=5, column=2, sticky="w", **pad)
    tk.Checkbutton(root, text="Bold", variable=v_bold, command=persist)\
        .grid(row=5, column=3, sticky="w", **pad)

    # Margins
    tk.Label(root, text="Margin right (mm):").grid(row=6, column=0, sticky="e", **pad)
    tk.Entry(root, textvariable=v_mr, width=8).grid(row=6, column=1, sticky="w", **pad)
    tk.Label(root, text="Margin top (mm):").grid(row=6, column=2, sticky="e", **pad)
    tk.Entry(root, textvariable=v_mt, width=8).grid(row=6, column=3, sticky="w", **pad)

    # Labels + line spacing
    tk.Label(root, text="Rev label:").grid(row=7, column=0, sticky="e", **pad)
    tk.Entry(root, textvariable=v_rev_label, width=10)\
        .grid(row=7, column=1, sticky="w", **pad)
    tk.Label(root, text="Date label:").grid(row=7, column=2, sticky="e", **pad)
    tk.Entry(root, textvariable=v_date_label, width=10)\
        .grid(row=7, column=3, sticky="w", **pad)

    tk.Label(root, text="Line spacing:").grid(row=8, column=0, sticky="e", **pad)
    tk.Entry(root, textvariable=v_spacing, width=8)\
        .grid(row=8, column=1, sticky="w", **pad)

    # Pages
    num_entry = tk.Entry(root, textvariable=v_num, width=8)

    def toggle_pages():
        num_entry.config(state="disabled" if v_all.get() else "normal")
        persist()

    tk.Checkbutton(root, text="Apply to all pages", variable=v_all,
                   command=toggle_pages).grid(row=9, column=0, columnspan=2,
                                              sticky="w", **pad)
    tk.Label(root, text="First N pages:").grid(row=9, column=2, sticky="e", **pad)
    num_entry.grid(row=9, column=3, sticky="w", **pad)
    toggle_pages()

    tk.Checkbutton(root, text="White box behind text", variable=v_bg,
                   command=persist).grid(row=10, column=0, columnspan=2,
                                         sticky="w", **pad)
    tk.Checkbutton(root, text="Overwrite existing", variable=v_overwrite,
                   command=persist).grid(row=10, column=2, columnspan=2,
                                         sticky="w", **pad)

    # Flatten (rasterize so output cannot be edited)
    dpi_entry = tk.Entry(root, textvariable=v_dpi, width=8)

    def toggle_flatten():
        dpi_entry.config(state="normal" if v_flatten.get() else "disabled")
        persist()

    tk.Checkbutton(root, text="Flatten output (rasterize, non-editable)",
                   variable=v_flatten, command=toggle_flatten)\
        .grid(row=11, column=0, columnspan=2, sticky="w", **pad)
    tk.Label(root, text="Flatten DPI:").grid(row=11, column=2, sticky="e", **pad)
    dpi_entry.grid(row=11, column=3, sticky="w", **pad)
    toggle_flatten()

    # Progress + status
    progress = ttk.Progressbar(root, mode="determinate")
    progress.grid(row=12, column=0, columnspan=4, sticky="ew", padx=6, pady=(10, 2))
    status = tk.Label(root, text="Ready", anchor="w")
    status.grid(row=13, column=0, columnspan=4, sticky="ew", padx=6)

    run_btn = tk.Button(root, text="Run")

    # ---- validation ----
    def validate():
        close_editor(commit=True)
        if not state["rows"]:
            messagebox.showerror("Missing input", "Please select at least one PDF.")
            return None
        out = v_output.get().strip()
        if not out:
            messagebox.showerror("Missing output", "Please choose an output folder.")
            return None
        try:
            settings = collect_settings()
        except ValueError:
            messagebox.showerror(
                "Invalid input",
                "Font size, line spacing, margins and page count must be numbers.")
            return None
        if settings["font_size"] <= 0:
            messagebox.showerror("Invalid input", "Font size must be greater than 0.")
            return None
        if settings["line_spacing"] < 1.0:
            messagebox.showerror("Invalid input",
                                 "Line spacing must be at least 1.0.")
            return None
        if settings["margin_right_mm"] < 0 or settings["margin_top_mm"] < 0:
            messagebox.showerror("Invalid input", "Margins must be non-negative.")
            return None
        if not settings["all_pages"] and settings["num_pages"] < 1:
            messagebox.showerror("Invalid input", "First N pages must be >= 1.")
            return None
        if settings["font_file"] and not os.path.isfile(settings["font_file"]):
            messagebox.showerror("Invalid input", "Selected font file was not found.")
            return None
        blank = [Path(r["path"]).name for r in state["rows"]
                 if not r["rev"].strip() or not r["date"].strip()]
        if blank:
            listed = "\n".join(f"- {n}" for n in blank[:10])
            more = f"\n...and {len(blank) - 10} more" if len(blank) > 10 else ""
            if not messagebox.askyesno(
                    "Missing revision or date",
                    f"{len(blank)} file(s) have no revision or date set; that "
                    f"line will be left off:\n{listed}{more}\n\nStamp anyway?"):
                return None
        os.makedirs(out, exist_ok=True)
        return settings, out

    # ---- worker thread ----
    def worker(jobs, out_dir, settings):
        def cb(done, total, name):
            ui_queue.put(("progress", done, total, name))
        try:
            ok, errors = run_batch(jobs, out_dir, settings, progress_cb=cb)
            ui_queue.put(("done", ok, errors))
        except Exception as exc:  # safety net
            ui_queue.put(("fatal", str(exc)))

    def poll_queue():
        try:
            while True:
                msg = ui_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, done, total, name = msg
                    progress["maximum"] = total
                    progress["value"] = done
                    status.config(text=f"Stamped {done}/{total}: {name}")
                elif kind == "done":
                    _, ok, errors = msg
                    run_btn.config(state="normal")
                    status.config(text=f"Finished: {ok} ok, {len(errors)} failed.")
                    if errors:
                        detail = "\n".join(f"- {n}: {m}" for n, m in errors[:10])
                        messagebox.showwarning(
                            "Completed with errors",
                            f"{ok} file(s) stamped.\n{len(errors)} failed:\n{detail}")
                    else:
                        messagebox.showinfo("Done", f"Stamped {ok} file(s).")
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
        settings, out_dir = result
        persist()
        jobs = [dict(r) for r in state["rows"]]
        run_btn.config(state="disabled")
        progress["value"] = 0
        status.config(text="Working...")
        t = threading.Thread(target=worker, args=(jobs, out_dir, settings),
                             daemon=True)
        t.start()
        root.after(100, poll_queue)

    run_btn.config(command=on_run)
    run_btn.grid(row=14, column=0, columnspan=4, pady=10)

    for c in range(4):
        root.columnconfigure(c, weight=1)
    root.rowconfigure(1, weight=1)  # the file table takes the spare height

    root.protocol("WM_DELETE_WINDOW", lambda: (persist(), root.destroy()))
    root.mainloop()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main():
    if "--cli" in sys.argv:
        argv = [a for a in sys.argv[1:] if a != "--cli"]
        sys.exit(cli_main(argv))
    gui_main()


if __name__ == "__main__":
    main()
