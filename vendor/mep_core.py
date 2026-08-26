#!/usr/bin/env python3
"""
mep_core.py - headless core for the MEP Schedule Project Manager.

No GUI, no tkinter. Everything the manager does lives here so it can be
driven from the GUI, from a script, or later from a web backend.

Model
    registry.json      one file, shared. Holds the shared paths, house style
                       defaults, and every project record.
    project record     client / numbers / folders / doc number tokens / ledger

What it does that build_project.py does not
    - keeps many projects against ONE shared equipment library
    - never lets a build overwrite the shared library or a filled-in schedule
    - rewrites the hidden Config sheet of existing schedules in place, so
      changing a path or a client name does not mean rebuilding
    - keeps a doc-number ledger per project and screams if a schema edit
      would shift the numbers of schedules already issued
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

from openpyxl import load_workbook

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

BOOTSTRAP = os.path.join(os.path.expanduser("~"), ".mep_schedule_manager", "bootstrap.json")

PROJECT_FIELDS = [
    "Client", "Project Name", "Project Number", "Site Address", "Architect",
    "Main Contractor", "RIBA Stage", "Prepared By", "Checked By", "Approved By",
]

DOCNUM_TOKENS = [
    "originator", "volume", "client_ref", "doc_type", "discipline",
    "classification", "level", "location",
]

DEFAULT_PATTERN = ("{project_number}-{originator}-{volume}-{client_ref}-{doc_type}"
                   "-{discipline}-{number}-{classification}-{level}-{location}")

DEFAULT_CONSTANTS = {
    "LPHW Flow Temperature (degC)": 70,
    "LPHW Return Temperature (degC)": 50,
    "CHW Flow Temperature (degC)": 6,
    "CHW Return Temperature (degC)": 12,
    "Design Ambient Temperature (degC)": 21,
    "Specific Heat Capacity of Water (kJ/kgK)": 4.18,
    "EN 442 Radiator Exponent (n)": 1.3,
}

DEFAULT_HOUSE_STYLE = {
    "cover_font": "Verdana", "schedule_font": "Arial",
    "title_grey": "FF4D4D4D", "title_blue": "FF009DF0",
    "title_size": 30, "cover_body_size": 11, "schedule_body_size": 8,
    "data_rows": 40, "revision_rows": 20,
}

DEFAULT_NOTES = [
    "This equipment schedule must be read in conjunction and in compliance with "
    "the associated drawings, specification and design risk assessment.",
    "All equipment is scheduled on a performance basis. The contractor is "
    "responsible for confirming final selections with the manufacturer and for "
    "verifying that the selected equipment meets the scheduled duties.",
    "Dimensions and weights are indicative and must be confirmed against "
    "manufacturer certified drawings prior to builderswork and structural sign-off.",
    "Where a duty or dimension is amended, the contractor shall notify the "
    "designer before ordering.",
    "Calculated columns are derived from the entered design data and the project "
    "design constants. Do not overwrite them.",
]

# Config keys that hold paths, and where they come from
PATH_KEYS = {
    "path_project_info": "project_info",
    "path_equipment_library": "equipment_library",
    "path_submissions_folder": "submissions_folder",
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def clean_path(p: str) -> str:
    """Normalise a user-entered path. Kills the doubled-backslash bug that the
    sample project.json shipped with (JSON "C:\\\\\\\\x" decodes to C:\\\\x)."""
    if not p:
        return ""
    p = str(p).strip().strip('"')
    if p.startswith("\\\\\\\\") or p.startswith("//"):          # UNC, keep the pair
        head, rest = p[:2], p[2:]
        rest = rest.replace("\\\\", "\\").replace("//", "/")
        return head + rest
    return p.replace("\\\\", "\\").replace("//", "/")


def slug(text: str) -> str:
    import re
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", text)).strip("_")


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def registry_location() -> str | None:
    """Where the shared registry.json lives, from the local bootstrap file."""
    try:
        with open(BOOTSTRAP) as fh:
            return json.load(fh).get("registry")
    except Exception:
        return None


def set_registry_location(path: str) -> None:
    os.makedirs(os.path.dirname(BOOTSTRAP), exist_ok=True)
    with open(BOOTSTRAP, "w") as fh:
        json.dump({"registry": clean_path(path)}, fh, indent=2)


def blank_registry() -> dict:
    return {
        "version": 1,
        "shared": {
            "schema": "",
            "builder": "",
            "equipment_library": "",
            "submissions_folder": "",
        },
        "defaults": {
            "house_style": dict(DEFAULT_HOUSE_STYLE),
            "general_notes": list(DEFAULT_NOTES),
            "design_constants": dict(DEFAULT_CONSTANTS),
            "document_number": {
                "pattern": DEFAULT_PATTERN, "originator": "BOV", "volume": "5_6",
                "client_ref": "PROJECTNUMBER", "doc_type": "SC", "discipline": "M",
                "classification": "G00300", "level": "XX", "location": "XX",
                "number_start": 10, "number_width": 8,
            },
        },
        "projects": [],
    }


def load_registry(path: str) -> dict:
    if not os.path.isfile(path):
        reg = blank_registry()
        save_registry(path, reg)
        return reg
    with open(path, encoding="utf-8") as fh:
        reg = json.load(fh)
    base = blank_registry()
    for k, v in base.items():                        # forward-compatible merge
        if k not in reg:
            reg[k] = v
    for k, v in base["defaults"].items():
        reg["defaults"].setdefault(k, v)
    for k, v in base["shared"].items():
        reg["shared"].setdefault(k, v)
    return reg


def save_registry(path: str, reg: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# project records
# --------------------------------------------------------------------------

def new_project(reg: dict) -> dict:
    d = reg["defaults"]
    return {
        "id": uuid.uuid4().hex[:8],
        "project": {f: "" for f in PROJECT_FIELDS},
        "document_number": dict(d["document_number"]),
        "design_constants": dict(d["design_constants"]),
        "folders": {"admin": "", "schedules": ""},
        "submissions_mode": "shared",       # shared | project
        "ledger": {},                       # equipment code -> doc number
        "created": now(),
        "updated": now(),
    }


def find_project(reg: dict, pid: str) -> dict | None:
    for p in reg["projects"]:
        if p["id"] == pid:
            return p
    return None


def project_label(p: dict) -> str:
    num = p["project"].get("Project Number") or "?"
    name = p["project"].get("Project Name") or "(unnamed)"
    return f"{num}  {name}"


def submissions_path(reg: dict, p: dict) -> str:
    if p.get("submissions_mode") == "project":
        return os.path.join(p["folders"]["admin"], "submissions")
    return reg["shared"]["submissions_folder"]


def project_info_path(p: dict) -> str:
    return os.path.join(p["folders"]["admin"], "MAINPROJECTINFO.xlsx")


def project_json_path(p: dict) -> str:
    return os.path.join(p["folders"]["admin"], "project.json")


def central_paths(reg: dict, p: dict) -> dict:
    return {
        "project_info": clean_path(project_info_path(p)),
        "equipment_library": clean_path(reg["shared"]["equipment_library"]),
        "submissions_folder": clean_path(submissions_path(reg, p)),
    }


def compose_project_json(reg: dict, p: dict) -> dict:
    """Exactly the shape build_project.py expects."""
    return {
        "_comment": "Generated by MEP Schedule Project Manager. Edit through the "
                    "manager, not by hand, or the two will drift.",
        "project": dict(p["project"]),
        "document_number": dict(p["document_number"]),
        "central_paths": central_paths(reg, p),
        "design_constants": dict(p["design_constants"]),
        "house_style": dict(reg["defaults"]["house_style"]),
        "general_notes": list(reg["defaults"]["general_notes"]),
    }


# --------------------------------------------------------------------------
# schema and doc numbers
# --------------------------------------------------------------------------

def load_schema(reg: dict) -> dict:
    with open(reg["shared"]["schema"], encoding="utf-8") as fh:
        return json.load(fh)


def compute_docnums(reg: dict, p: dict, schema: dict) -> list[dict]:
    """Replicates build_project.py's numbering so the manager can preview and
    audit it without running a build."""
    dn = p["document_number"]
    out = []
    for i, eq in enumerate(schema["equipment_types"]):
        num = str(dn["number_start"] + i).zfill(dn["number_width"])
        docnum = dn["pattern"].format(
            project_number=p["project"]["Project Number"],
            originator=dn["originator"], volume=dn["volume"],
            client_ref=dn["client_ref"], doc_type=dn["doc_type"],
            discipline=dn["discipline"], number=num,
            classification=dn["classification"], level=dn["level"],
            location=dn["location"])
        out.append({"code": eq["code"], "title": eq["title"], "docnum": docnum,
                    "filename": f"{docnum}_-_{slug(eq['title'])}.xlsx"})
    return out


def audit_ledger(p: dict, planned: list[dict]) -> list[str]:
    """Loud warning if a schema change would move a doc number that this
    project has already used. This is the append-only rule, enforced."""
    problems = []
    ledger = p.get("ledger", {})
    for item in planned:
        old = ledger.get(item["code"])
        if old and old != item["docnum"]:
            problems.append(
                f"{item['code']}: number would change from {old} to {item['docnum']}. "
                "Something was inserted into schema.json instead of appended.")
    return problems


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate(reg: dict, p: dict) -> list[tuple[str, str]]:
    """Returns [(ERROR|WARN, message)]. ERROR blocks a build."""
    out = []
    sh = reg["shared"]

    for key, label in (("schema", "schema.json"), ("builder", "build_project.py")):
        if not sh.get(key):
            out.append(("ERROR", f"Shared {label} path is not set (Settings)."))
        elif not os.path.isfile(sh[key]):
            out.append(("ERROR", f"Shared {label} not found: {sh[key]}"))

    if not sh.get("equipment_library"):
        out.append(("ERROR", "Shared equipment library path is not set (Settings)."))
    elif not os.path.isfile(sh["equipment_library"]):
        out.append(("WARN", "Shared equipment library does not exist yet. The "
                            "manager can seed it on first build."))

    sub = submissions_path(reg, p)
    if not sub:
        out.append(("ERROR", "No submissions folder resolved."))
    elif not os.path.isdir(sub):
        out.append(("WARN", f"Submissions folder does not exist yet: {sub}"))

    if not p["project"].get("Project Number"):
        out.append(("ERROR", "Project Number is required, it drives the doc number."))
    if not p["project"].get("Project Name"):
        out.append(("WARN", "Project Name is empty."))
    if not p["project"].get("Client"):
        out.append(("WARN", "Client is empty, it prints on the front cover."))

    if not p["folders"].get("schedules"):
        out.append(("ERROR", "Schedules folder is not set."))
    if not p["folders"].get("admin"):
        out.append(("ERROR", "Admin folder is not set."))

    adm, sch = p["folders"].get("admin", ""), p["folders"].get("schedules", "")
    if adm and sch and os.path.abspath(adm) == os.path.abspath(sch):
        out.append(("ERROR", "Admin folder and schedules folder must be different."))

    lib = clean_path(sh.get("equipment_library", ""))
    if lib and adm and os.path.abspath(os.path.dirname(lib) or ".") == os.path.abspath(adm):
        out.append(("WARN", "The shared library sits in this project's admin folder. "
                            "Move it somewhere company-level."))

    for tok in ("pattern", "number_start", "number_width"):
        if p["document_number"].get(tok) in (None, ""):
            out.append(("ERROR", f"document_number.{tok} is missing."))

    # duplicate project numbers across the registry
    mine = p["project"].get("Project Number")
    for other in reg["projects"]:
        if other["id"] != p["id"] and other["project"].get("Project Number") == mine and mine:
            out.append(("WARN", f"Project Number {mine} is also used by "
                                f"{project_label(other)}."))

    for other in reg["projects"]:
        if other["id"] == p["id"]:
            continue
        for f in ("admin", "schedules"):
            o = other["folders"].get(f, "")
            if o and sch and os.path.abspath(o) == os.path.abspath(sch):
                out.append(("ERROR", f"Schedules folder is already the {f} folder of "
                                     f"{project_label(other)}."))
    return out


# --------------------------------------------------------------------------
# folder scaffolding
# --------------------------------------------------------------------------

def scaffold(reg: dict, p: dict) -> list[str]:
    """Create the folders a project needs. Idempotent."""
    made = []
    for path in (p["folders"]["admin"], p["folders"]["schedules"],
                 os.path.join(p["folders"]["admin"], "issued"),
                 os.path.join(p["folders"]["admin"], "pdf")):
        if path and not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            made.append(path)
    if p.get("submissions_mode") == "project":
        sub = submissions_path(reg, p)
        if sub and not os.path.isdir(sub):
            os.makedirs(sub, exist_ok=True)
            made.append(sub)
    return made


def write_project_json(reg: dict, p: dict) -> str:
    path = project_json_path(p)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(compose_project_json(reg, p), fh, indent=2, ensure_ascii=False)
    return path


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

class BuildResult:
    def __init__(self):
        self.installed: list[str] = []
        self.skipped: list[str] = []
        self.library_seeded = False
        self.info_seeded = False
        self.log: list[str] = []
        self.ok = False

    def text(self) -> str:
        return "\n".join(self.log)


def build(reg: dict, p: dict, *, force_overwrite: bool = False,
          dry_run: bool = False) -> BuildResult:
    """Build into a temp folder, then install only what is safe to install.

    Never overwrites an existing schedule file unless force_overwrite.
    Never overwrites the shared equipment library, full stop. It is only
    seeded if it does not exist.
    """
    r = BuildResult()
    schema = load_schema(reg)
    planned = compute_docnums(reg, p, schema)

    shifts = audit_ledger(p, planned)
    if shifts:
        r.log.append("!! DOC NUMBER SHIFT DETECTED - build aborted")
        r.log.extend("   " + s for s in shifts)
        r.log.append("   Fix schema.json (append only) or clear this project's "
                     "ledger deliberately.")
        return r

    tmp = tempfile.mkdtemp(prefix="mepbuild_")
    try:
        tmp_cfg = os.path.join(tmp, "project.json")
        with open(tmp_cfg, "w", encoding="utf-8") as fh:
            json.dump(compose_project_json(reg, p), fh, indent=2, ensure_ascii=False)

        out = os.path.join(tmp, "out")
        cmd = [sys.executable, reg["shared"]["builder"], reg["shared"]["schema"],
               tmp_cfg, out]
        r.log.append("$ " + " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              cwd=os.path.dirname(reg["shared"]["builder"]) or ".")
        r.log.append(proc.stdout.strip())
        if proc.returncode != 0:
            r.log.append("BUILD FAILED")
            r.log.append(proc.stderr.strip())
            return r

        # --- schedules -----------------------------------------------------
        src = os.path.join(out, "schedules")
        dst = p["folders"]["schedules"]
        if not dry_run:
            os.makedirs(dst, exist_ok=True)
        for fn in sorted(os.listdir(src)):
            target = os.path.join(dst, fn)
            if os.path.exists(target) and not force_overwrite:
                r.skipped.append(fn)
                continue
            if not dry_run:
                shutil.copy2(os.path.join(src, fn), target)
            r.installed.append(fn)

        # --- MAINPROJECTINFO ------------------------------------------------
        info_src = os.path.join(out, "MAINPROJECTINFO.xlsx")
        info_dst = project_info_path(p)
        if os.path.isfile(info_src) and not os.path.exists(info_dst):
            if not dry_run:
                os.makedirs(os.path.dirname(info_dst), exist_ok=True)
                shutil.copy2(info_src, info_dst)
            r.info_seeded = True

        # --- shared library: seed only, never overwrite ---------------------
        lib_src = os.path.join(out, "EQUIPMENT_LIBRARY_MASTER.xlsx")
        lib_dst = clean_path(reg["shared"]["equipment_library"])
        if lib_dst and os.path.isfile(lib_src) and not os.path.exists(lib_dst):
            if not dry_run:
                os.makedirs(os.path.dirname(lib_dst) or ".", exist_ok=True)
                shutil.copy2(lib_src, lib_dst)
            r.library_seeded = True

        # --- ledger ---------------------------------------------------------
        if not dry_run:
            for item in planned:
                if item["filename"] in r.installed or item["code"] not in p["ledger"]:
                    p["ledger"][item["code"]] = item["docnum"]
            p["updated"] = now()

        r.log.append("")
        r.log.append(f"{'WOULD INSTALL' if dry_run else 'INSTALLED'} "
                     f"{len(r.installed)} file(s):")
        r.log.extend("   + " + f for f in r.installed)
        if r.skipped:
            r.log.append(f"SKIPPED {len(r.skipped)} existing file(s) - not touched:")
            r.log.extend("   = " + f for f in r.skipped)
        if r.info_seeded:
            r.log.append(f"   + MAINPROJECTINFO.xlsx -> {info_dst}")
        if r.library_seeded:
            r.log.append(f"   + shared library seeded -> {lib_dst}")
        elif lib_dst:
            r.log.append("   = shared equipment library left alone (correct)")
        r.ok = True
        return r
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# in-place repair of existing schedule files
# --------------------------------------------------------------------------

def read_config(path: str) -> dict:
    wb = load_workbook(path, data_only=False)
    if "Config" not in wb.sheetnames:
        wb.close()
        return {}
    ws = wb["Config"]
    d = {}
    for row in range(2, ws.max_row + 1):
        k = ws.cell(row, 1).value
        if k:
            d[str(k)] = ws.cell(row, 2).value
    wb.close()
    return d


def sync_schedules(reg: dict, p: dict, *, do_paths: bool = True,
                   do_project: bool = True, backup: bool = True,
                   dry_run: bool = False) -> list[str]:
    """Rewrite the hidden Config sheet of every schedule in the project's
    schedules folder. This is the thing the macros cannot do: RefreshProjectData
    updates project fields only, and nothing updates the paths at all.
    """
    log = []
    folder = p["folders"]["schedules"]
    if not os.path.isdir(folder):
        return [f"Schedules folder not found: {folder}"]

    cp = central_paths(reg, p)
    wanted = {}
    if do_paths:
        for cfg_key, src in PATH_KEYS.items():
            wanted[cfg_key] = cp[src]
    if do_project:
        wanted.update({k: v for k, v in p["project"].items()})

    files = [f for f in sorted(os.listdir(folder))
             if f.lower().endswith(".xlsx") and not f.startswith("~$")]
    if not files:
        return [f"No .xlsx files in {folder}"]

    for fn in files:
        full = os.path.join(folder, fn)
        try:
            wb = load_workbook(full)
        except Exception as exc:
            log.append(f"SKIP  {fn}  (cannot open: {exc})")
            continue
        if "Config" not in wb.sheetnames:
            wb.close()
            log.append(f"SKIP  {fn}  (no Config sheet, not one of ours)")
            continue

        ws = wb["Config"]
        changes = []
        for row in range(2, ws.max_row + 1):
            key = ws.cell(row, 1).value
            if key is None:
                continue
            key = str(key)
            if key in wanted:
                old, new = ws.cell(row, 2).value, wanted[key]
                if str(old) != str(new):
                    changes.append(f"{key}: {old!r} -> {new!r}")
                    if not dry_run:
                        ws.cell(row, 2).value = new

        if not changes:
            log.append(f"ok    {fn}  (already current)")
            wb.close()
            continue

        if dry_run:
            log.append(f"WOULD {fn}")
        else:
            if backup:
                bak = full + ".bak"
                if not os.path.exists(bak):
                    shutil.copy2(full, bak)
            try:
                wb.save(full)
                log.append(f"UPD   {fn}")
            except PermissionError:
                log.append(f"LOCKED {fn}  (close it in Excel and re-run)")
                wb.close()
                continue
        log.extend("        " + c for c in changes)
        wb.close()

    if not dry_run:
        log.append("")
        log.append("Done. Open each file and run RefreshLibrary if the library "
                   "changed. Delete the .bak files once you are happy.")
    return log


# --------------------------------------------------------------------------
# register (the data model the future web viewer wants)
# --------------------------------------------------------------------------

REV_LOG_TOP = 42          # matches build_project.py


def _register_row(full: str, fn: str) -> dict | None:
    """One register row for one schedule file.

    Prefers Excel's cached values on the Metadata sheet, exactly like
    Register.pq. A file that has never been opened in Excel has no cached
    values, so it falls back to the Config sheet and the raw revision log.
    """
    d = {"File": fn}
    try:
        wb = load_workbook(full, data_only=True)
    except Exception:
        return None
    if "Metadata" in wb.sheetnames:
        ws = wb["Metadata"]
        for row in ws.iter_rows(min_row=2, max_col=2, values_only=True):
            if row and row[0]:
                d[str(row[0])] = row[1]
    if not d.get("DocumentNumber"):                    # never opened in Excel
        if "Config" in wb.sheetnames:
            cf = wb["Config"]
            for r in range(2, cf.max_row + 1):
                k = cf.cell(r, 1).value
                if k in ("DocumentNumber", "ScheduleName", "EquipmentCode"):
                    d[str(k)] = cf.cell(r, 2).value
        if "Revision page" in wb.sheetnames:
            rv = wb["Revision page"]
            last = None
            for r in range(REV_LOG_TOP, REV_LOG_TOP + 40):
                if rv.cell(r, 1).value not in (None, ""):
                    last = r
            if last:
                status = str(rv.cell(last, 2).value or "")
                d["Revision"] = rv.cell(last, 1).value
                d["IssueDate"] = rv.cell(last, 3).value
                d["Status"] = status.split(" - ")[0] if " - " in status else status
                d["StatusDescription"] = status.split(" - ", 1)[1] if " - " in status else ""
            else:
                d.setdefault("Revision", "")
                d.setdefault("Status", "")
                d.setdefault("IssueDate", "")
        d["_source"] = "config"
    else:
        d["_source"] = "metadata"
    wb.close()
    return d


def scan_register(p: dict) -> list[dict]:
    """Register for one project. Same shape Register.pq produces, in Python so
    a future web viewer can reuse it without Power Query."""
    folder = p["folders"].get("schedules", "")
    rows = []
    if not os.path.isdir(folder):
        return rows
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".xlsx") or fn.startswith("~$"):
            continue
        row = _register_row(os.path.join(folder, fn), fn)
        if row:
            rows.append(row)
    return rows


def scan_all_registers(reg: dict) -> list[dict]:
    """Every schedule across every project. This is the v1 web viewer's table."""
    out = []
    for p in reg["projects"]:
        for row in scan_register(p):
            row["Project"] = project_label(p)
            row["ProjectId"] = p["id"]
            out.append(row)
    return out


# --------------------------------------------------------------------------
# import an existing hand-made project
# --------------------------------------------------------------------------

def import_from_project_json(reg: dict, json_path: str, schedules_folder: str) -> dict:
    """Adopt a project that was set up the old manual way."""
    with open(json_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    p = new_project(reg)
    for f in PROJECT_FIELDS:
        p["project"][f] = cfg.get("project", {}).get(f, "")
    dn = {k: v for k, v in cfg.get("document_number", {}).items()
          if not k.startswith("_")}
    p["document_number"].update(dn)
    p["design_constants"].update(cfg.get("design_constants", {}))
    p["folders"]["admin"] = os.path.dirname(os.path.abspath(json_path))
    p["folders"]["schedules"] = clean_path(schedules_folder)

    # rebuild the ledger from the files that already exist
    if os.path.isdir(schedules_folder):
        for fn in sorted(os.listdir(schedules_folder)):
            if not fn.lower().endswith(".xlsx") or fn.startswith("~$"):
                continue
            cfgsheet = read_config(os.path.join(schedules_folder, fn))
            code, num = cfgsheet.get("EquipmentCode"), cfgsheet.get("DocumentNumber")
            if code and num:
                p["ledger"][str(code)] = str(num)
    return p
