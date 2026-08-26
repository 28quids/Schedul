#!/usr/bin/env python3
"""
Builds a full set of standalone MEP schedule files in house format.

Outputs
  MAINPROJECTINFO.xlsx                central project record (Setup + ScheduleList)
  EQUIPMENT_LIBRARY_MASTER.xlsx       central equipment database, one sheet per type
  schedules/<docnumber>_-_<Title>.xlsx  one standalone file per schedule
  submissions/                        inbox the Save-to-DB macro writes into

Each schedule file has four visible sheets matching house format
  Metadata | Front Cover | Revision page | Schedule
and three hidden sheets
  Config | Library | Lists

No external workbook links are created. Project data is written as values.
Product data uses INDEX/MATCH against the file's own hidden Library sheet.

Usage:  python build_project.py schema.json project.json ./out
"""

import json
import os
import re
import shutil
import sys

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.comments import Comment

A4 = 9

# Parenthetical suffixes that are NOT units and must stay in the header text
NOT_A_UNIT = {"BS EN 1886", "Initials", "n"}

UNIT_PRETTY = {"degC": "\u00b0C", "m2": "m\u00b2", "m3": "m\u00b3"}

STATUS_CODES = [
    ("S0", "Work in Progress"),
    ("S1", "Suitable for Coordination"),
    ("S2", "Suitable for Information"),
    ("S3", "Suitable for Review and Comment"),
    ("S4", "Suitable for Stage Approval"),
    ("S5", "Suitable for Client Acceptance"),
    ("A1", "Authorised and Accepted"),
    ("B1", "Partial Sign-off, Accepted with Comments"),
]


# --------------------------------------------------------------- helpers ---
def split_unit(field):
    """'Supply Airflow (l/s)' -> ('Supply Airflow', 'l/s'). Handles nested parens."""
    if not field.endswith(")"):
        return field, ""
    depth = 0
    for i in range(len(field) - 1, -1, -1):
        if field[i] == ")":
            depth += 1
        elif field[i] == "(":
            depth -= 1
            if depth == 0:
                inner = field[i + 1:-1]
                if inner in NOT_A_UNIT:
                    return field, ""
                return field[:i].strip(), UNIT_PRETTY.get(inner, inner)
    return field, ""


def slug(text):
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", text)).strip("_")


class Style:
    def __init__(self, hs):
        cf, sf = hs["cover_font"], hs["schedule_font"]
        self.grey = hs["title_grey"][2:]
        self.blue = hs["title_blue"][2:]
        self.f_big_grey = Font(name=cf, size=hs["title_size"], bold=True, color=self.grey)
        self.f_big_blue = Font(name=cf, size=hs["title_size"], bold=True, color=self.blue)
        self.f_cov_lbl = Font(name=cf, size=hs["cover_body_size"], bold=False)
        self.f_cov_val = Font(name=cf, size=hs["cover_body_size"], bold=True)
        self.f_cov_sm = Font(name=cf, size=9, bold=True)
        self.f_rev_hdr = Font(name=cf, size=9, bold=True)
        self.f_rev_bod = Font(name=cf, size=9)
        self.f_rev_in = Font(name=cf, size=9, color="0000FF")
        self.f_title = Font(name=sf, size=12, bold=True)
        self.f_note = Font(name=sf, size=8)
        self.f_hdr = Font(name=sf, size=8, bold=True)
        self.f_unit = Font(name=sf, size=8, italic=True, color="595959")
        self.f_in = Font(name=sf, size=8, color="0000FF")
        self.f_pull = Font(name=sf, size=8, color="008000")
        self.f_calc = Font(name=sf, size=8, color="000000")
        self.f_sm = Font(name=sf, size=8)


FILL_IN = PatternFill("solid", fgColor="FFFFCC")
FILL_HDR = PatternFill("solid", fgColor="D9D9D9")
FILL_GRP_IN = PatternFill("solid", fgColor="DCE6F1")
FILL_GRP_LIB = PatternFill("solid", fgColor="E2EFDA")
FILL_GRP_CALC = PatternFill("solid", fgColor="FCE4D6")

THIN = Side(style="thin", color="808080")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CTR = Alignment(horizontal="center", vertical="center", wrap_text=True)
LFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
TOPL = Alignment(horizontal="left", vertical="top", wrap_text=True)


def page(ws, landscape, ncols=None):
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.paperSize = A4
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_view.showGridLines = False


# ------------------------------------------------- MAINPROJECTINFO.xlsx ----
def build_main_project_info(cfg, schedules, out_dir, st):
    wb = Workbook()
    ws = wb.active
    ws.title = "Setup"
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 52

    p = cfg["project"]
    # Rows 1/3/4 kept exactly where the existing house template expects them
    fixed = [(1, "Client", p["Client"]),
             (3, "Project Name", p["Project Name"]),
             (4, "Project Number", p["Project Number"])]
    for r, k, v in fixed:
        ws.cell(r, 1, k).font = st.f_cov_lbl
        c = ws.cell(r, 2, v)
        c.font = st.f_cov_val
        c.fill = FILL_IN

    r = 6
    extra = [k for k in p if k not in ("Client", "Project Name", "Project Number")]
    for k in extra:
        ws.cell(r, 1, k).font = st.f_cov_lbl
        c = ws.cell(r, 2, p[k])
        c.font = st.f_cov_val
        c.fill = FILL_IN
        r += 1

    r += 1
    ws.cell(r, 1, "DESIGN CONSTANTS").font = Font(name="Verdana", size=11, bold=True)
    r += 1
    for k, v in cfg["design_constants"].items():
        ws.cell(r, 1, k).font = st.f_cov_lbl
        c = ws.cell(r, 2, v)
        c.font = st.f_cov_val
        c.fill = FILL_IN
        r += 1

    r += 1
    ws.cell(r, 1, "Rows 1, 3 and 4 are fixed. Existing schedules reference Setup!$B$1, "
                  "$B$3 and $B$4 by position, so do not insert rows above row 5.").font = \
        Font(name="Verdana", size=8, italic=True, color="595959")

    sl = wb.create_sheet("ScheduleList")
    hdrs = ["DocumentNumber", "ScheduleName", "Revision", "IssueDate", "Status", "FileName"]
    for i, h in enumerate(hdrs, start=1):
        c = sl.cell(1, i, h)
        c.font = Font(name="Verdana", size=9, bold=True)
        c.fill = FILL_HDR
        c.border = BOX
        sl.column_dimensions[get_column_letter(i)].width = [46, 44, 11, 13, 34, 60][i - 1]

    for j, s in enumerate(schedules, start=2):
        for i, v in enumerate([s["docnum"], s["title"], "", "", "", s["filename"]], start=1):
            c = sl.cell(j, i, v)
            c.font = Font(name="Verdana", size=9)
            c.border = BOX
            c.alignment = LFT

    sl.cell(len(schedules) + 3, 1,
            "Revision, IssueDate and Status are populated by the Power Query register "
            "(see Register.pq). Refresh with Data > Refresh All.").font = \
        Font(name="Verdana", size=8, italic=True, color="595959")

    for w in (ws, sl):
        w.sheet_view.showGridLines = False
    path = os.path.join(out_dir, "MAINPROJECTINFO.xlsx")
    wb.save(path)
    return path


# ------------------------------------------ EQUIPMENT_LIBRARY_MASTER.xlsx --
def build_library_master(schema, out_dir, st):
    wb = Workbook()
    wb.remove(wb.active)
    idx = wb.create_sheet("INDEX")
    idx.column_dimensions["A"].width = 16
    idx.column_dimensions["B"].width = 52
    idx.column_dimensions["C"].width = 14
    idx["A1"] = "CENTRAL EQUIPMENT LIBRARY"
    idx["A1"].font = Font(name="Verdana", size=14, bold=True, color=st.grey)
    idx["A2"] = ("One sheet per equipment type. Column A is always Model Reference and is the "
                 "lookup key. Merge approved entries from the submissions folder into these sheets.")
    idx["A2"].font = Font(name="Verdana", size=9, italic=True, color="595959")
    for i, h in enumerate(["Code", "Equipment Type", "Entries"], start=1):
        c = idx.cell(4, i, h)
        c.font = Font(name="Verdana", size=9, bold=True)
        c.fill = FILL_HDR
        c.border = BOX

    for k, eq in enumerate(schema["equipment_types"]):
        ws = wb.create_sheet(eq["code"])
        cols = ["Model Reference"] + [f for f, _, _ in eq["type_fields"]]
        for i, f in enumerate(cols, start=1):
            name, unit = split_unit(f)
            c = ws.cell(1, i, name)
            c.font = st.f_hdr
            c.fill = FILL_HDR
            c.border = BOX
            c.alignment = CTR
            u = ws.cell(2, i, unit)
            u.font = st.f_unit
            u.border = BOX
            u.alignment = CTR
            ws.column_dimensions[get_column_letter(i)].width = 18
        ws.column_dimensions["A"].width = 26
        ws.cell(3, 1, f"{eq['code']}-EXAMPLE-01").font = st.f_sm
        for j, (_, _, ex) in enumerate(eq["type_fields"], start=2):
            ws.cell(3, j, ex).font = st.f_sm
        ws.freeze_panes = "B3"
        ws.sheet_view.showGridLines = False

        r = 5 + k
        idx.cell(r, 1, eq["code"]).font = Font(name="Verdana", size=9)
        idx.cell(r, 2, eq["short"]).font = Font(name="Verdana", size=9)
        idx.cell(r, 3, f"=COUNTA({eq['code']}!$A$3:$A$5000)").font = Font(name="Verdana", size=9)
        for i in range(1, 4):
            idx.cell(r, i).border = BOX

    idx.sheet_view.showGridLines = False
    path = os.path.join(out_dir, "EQUIPMENT_LIBRARY_MASTER.xlsx")
    wb.save(path)
    return path


# --------------------------------------------------- one schedule file -----
def build_schedule_file(eq, cfg, schema, meta, out_dir, st):
    hs = cfg["house_style"]
    p = cfg["project"]
    DATA_ROWS = hs["data_rows"]
    REV_ROWS = hs["revision_rows"]

    inst, typ, der = eq["instance_fields"], eq["type_fields"], eq["derived_fields"]
    n_inst = len(inst)
    mr_col = n_inst + 1
    typ_start = mr_col + 1
    der_start = typ_start + len(typ)
    n_cols = der_start + len(der) - 1

    colmap = {f: get_column_letter(i) for i, (f, _, _) in enumerate(inst, start=1)}
    colmap["Model Reference"] = get_column_letter(mr_col)
    for i, (f, _, _) in enumerate(typ, start=typ_start):
        colmap[f] = get_column_letter(i)

    wb = Workbook()
    wb.remove(wb.active)

    # ------------------------------------------------------------- Config --
    cf = wb.create_sheet("Config")
    cf.column_dimensions["A"].width = 40
    cf.column_dimensions["B"].width = 70
    cf["A1"] = "Key"
    cf["B1"] = "Value"
    for c in ("A1", "B1"):
        cf[c].font = Font(name="Arial", size=9, bold=True)

    conf_rows = {}
    r = 2

    def put(k, v):
        nonlocal r
        cf.cell(r, 1, k).font = Font(name="Arial", size=9)
        cf.cell(r, 2, v).font = Font(name="Arial", size=9)
        conf_rows[k] = r
        r += 1

    put("EquipmentCode", eq["code"])
    put("ScheduleName", eq["title"])
    put("DocumentNumber", meta["docnum"])
    for k, v in p.items():
        put(k, v)
    for k, v in cfg["central_paths"].items():
        if not k.startswith("_"):
            put("path_" + k, v)
    for k, v in cfg["design_constants"].items():
        put(k, v)

    alias = {
        "SETUP_LPHWF": "LPHW Flow Temperature (degC)",
        "SETUP_LPHWR": "LPHW Return Temperature (degC)",
        "SETUP_CHWF": "CHW Flow Temperature (degC)",
        "SETUP_CHWR": "CHW Return Temperature (degC)",
        "SETUP_CP": "Specific Heat Capacity of Water (kJ/kgK)",
        "SETUP_N": "EN 442 Radiator Exponent (n)",
        "SETUP_AMBIENT": "Design Ambient Temperature (degC)",
    }
    for a, k in alias.items():
        wb.defined_names.add(DefinedName(a, attr_text=f"Config!$B${conf_rows[k]}"))

    cf.cell(r + 1, 1, "Written at build time as values. 'Refresh Project Data' updates the "
                      "project rows from the central MAINPROJECTINFO file.").font = \
        Font(name="Arial", size=8, italic=True, color="595959")

    # -------------------------------------------------------------- Lists --
    ls = wb.create_sheet("Lists")
    ls.column_dimensions["A"].width = 40
    ls.column_dimensions["C"].width = 14
    ls["A1"] = "Status"
    ls["C1"] = "Revision"
    for i, (code, desc) in enumerate(STATUS_CODES, start=2):
        ls.cell(i, 1, f"{code} - {desc}")
    revs = [f"P{n:02d}" for n in range(1, 21)] + [f"C{n:02d}" for n in range(1, 11)]
    for i, rv in enumerate(revs, start=2):
        ls.cell(i, 3, rv)
    st_last = len(STATUS_CODES) + 1
    rv_last = len(revs) + 1

    # ------------------------------------------------------------ Library --
    lb = wb.create_sheet("Library")
    lib_cols = ["Model Reference"] + [f for f, _, _ in typ]
    for i, f in enumerate(lib_cols, start=1):
        name, unit = split_unit(f)
        c = lb.cell(1, i, name)
        c.font = st.f_hdr
        c.fill = FILL_HDR
        c.border = BOX
        lb.cell(2, i, unit).font = st.f_unit
        lb.column_dimensions[get_column_letter(i)].width = 18
    lb.column_dimensions["A"].width = 26
    ex_ref = f"{eq['code']}-EXAMPLE-01"
    lb.cell(3, 1, ex_ref).font = st.f_sm
    for j, (_, _, ex) in enumerate(typ, start=2):
        lb.cell(3, j, ex).font = st.f_sm
    LIB_TOP, LIB_BOT = 3, 2000
    lb.freeze_panes = "B3"

    # ----------------------------------------------------------- Metadata --
    md = wb.create_sheet("Metadata")
    md.column_dimensions["A"].width = 22
    md.column_dimensions["B"].width = 60
    md["A1"], md["B1"] = "Column1", "Column2"
    pairs = [("DocumentNumber", "='Revision page'!B19"),
             ("ScheduleName", "='Revision page'!B22"),
             ("Revision", "='Revision page'!B14"),
             ("IssueDate", "='Revision page'!B15"),
             ("Status", "='Revision page'!B20"),
             ("StatusDescription", "='Revision page'!B21"),
             ("EquipmentCode", f"=Config!$B${conf_rows['EquipmentCode']}")]
    for i, (k, v) in enumerate(pairs, start=2):
        md.cell(i, 1, k)
        md.cell(i, 2, v)
    md.cell(4, 2).number_format = "General"
    md.cell(5, 2).number_format = "DD/MM/YYYY"
    md.sheet_view.showGridLines = False

    # -------------------------------------------------------- Front Cover --
    fcv = wb.create_sheet("Front Cover")
    for i in range(1, 11):
        fcv.column_dimensions[get_column_letter(i)].width = 11.5
    fcv["A11"] = "Intended for"
    fcv["A11"].font = st.f_cov_sm
    fcv["A12"] = f"=Config!$B${conf_rows['Client']}"
    fcv["A12"].font = st.f_cov_sm
    fcv["A14"] = "Date"
    fcv["A14"].font = st.f_cov_sm
    fcv["A15"] = "='Revision page'!B15"
    fcv["A15"].font = st.f_cov_sm
    fcv["A15"].number_format = "DD/MM/YYYY"
    fcv["A17"] = "Document number"
    fcv["A17"].font = st.f_cov_sm
    fcv["A18"] = "='Revision page'!B19"
    fcv["A18"].font = st.f_cov_sm
    fcv["A20"] = "Revision"
    fcv["A20"].font = st.f_cov_sm
    fcv["A21"] = "='Revision page'!B14&\"  \"&'Revision page'!B20"
    fcv["A21"].font = st.f_cov_sm

    fcv.merge_cells("A41:G41")
    fcv["A41"] = f"=Config!$B${conf_rows['Project Name']}"
    fcv["A41"].font = st.f_big_grey
    fcv["A41"].alignment = LFT
    fcv.merge_cells("A42:G42")
    fcv["A42"] = eq["title"].upper()
    fcv["A42"].font = st.f_big_blue
    fcv["A42"].alignment = LFT
    fcv.row_dimensions[41].height = 40
    fcv.row_dimensions[42].height = 40
    page(fcv, landscape=False)
    fcv.print_area = "A1:G50"

    # ------------------------------------------------------ Revision page --
    rv = wb.create_sheet("Revision page")
    for col, w in zip("ABCDEFG", [16, 34, 12, 13, 13, 13, 48]):
        rv.column_dimensions[col].width = w

    rv.merge_cells("A3:G3")
    rv["A3"] = f"=Config!$B${conf_rows['Project Name']}"
    rv["A3"].font = st.f_big_grey
    rv["A3"].alignment = LFT
    rv.merge_cells("A4:G4")
    rv["A4"] = eq["title"].upper()
    rv["A4"].font = st.f_big_blue
    rv["A4"].alignment = LFT
    rv.row_dimensions[3].height = 40
    rv.row_dimensions[4].height = 40

    RT, RB = 42, 42 + REV_ROWS - 1        # revision log rows
    last_rev = f'INDEX($A${RT}:$A${RB},MAX(1,COUNTA($A${RT}:$A${RB})))'
    last_sta = f'INDEX($B${RT}:$B${RB},MAX(1,COUNTA($A${RT}:$A${RB})))'
    last_dat = f'INDEX($C${RT}:$C${RB},MAX(1,COUNTA($A${RT}:$A${RB})))'
    last_pre = f'INDEX($D${RT}:$D${RB},MAX(1,COUNTA($A${RT}:$A${RB})))'
    last_chk = f'INDEX($E${RT}:$E${RB},MAX(1,COUNTA($A${RT}:$A${RB})))'
    last_app = f'INDEX($F${RT}:$F${RB},MAX(1,COUNTA($A${RT}:$A${RB})))'

    def pull(expr, fmt=None):
        """INDEX into the revision log, returning blank rather than 0 for empty cells."""
        return f'=IFERROR(IF({expr}="","",IF({expr}=0,"",{expr})),"")'

    summary = [
        (10, "Project Name", f"=Config!$B${conf_rows['Project Name']}", None),
        (11, "Project no.", f"=Config!$B${conf_rows['Project Number']}", None),
        (12, "Recipient", f"=Config!$B${conf_rows['Client']}", None),
        (13, "Document type", cfg["document_number"]["doc_type"], None),
        (14, "Revision", pull(last_rev), None),
        (15, "Date", pull(last_dat), "DD/MM/YYYY"),
        (16, "Prepared by", pull(last_pre), None),
        (17, "Checked by", pull(last_chk), None),
        (18, "Approved by", pull(last_app), None),
        (19, "Document no", f"=Config!$B${conf_rows['DocumentNumber']}", None),
        (20, "Suitability Status", f'=IFERROR(LEFT({last_sta},FIND(" -",{last_sta})-1),"")', None),
        (21, "Suitability Description",
         f'=IFERROR(MID({last_sta},FIND("- ",{last_sta})+2,200),"")', None),
        (22, "Schedule name", eq["title"], None),
    ]
    for row, lbl, val, fmt in summary:
        rv.cell(row, 1, lbl).font = st.f_cov_lbl
        c = rv.cell(row, 2, val)
        c.font = st.f_cov_val
        c.alignment = LFT
        if fmt:
            c.number_format = fmt

    rv.cell(24, 1, "Delref Classification").font = st.f_cov_lbl
    rv.cell(24, 2, cfg["document_number"]["classification"]).font = st.f_cov_val
    rv.cell(25, 1, "BSUID").font = st.f_cov_lbl
    rv.cell(25, 2, "").font = st.f_cov_val
    rv.cell(26, 1, "Trigger Events").font = st.f_cov_lbl
    rv.cell(26, 2, "").font = st.f_cov_val

    rv.cell(28, 1, "Rows 10 to 22 derive from the last completed row of the revision log below. "
                   "Do not type into them.").font = \
        Font(name="Verdana", size=8, italic=True, color="595959")

    hdrs = ["Revision", "Status", "Date", "Prepared by", "Checked by", "Approved by", "Description"]
    for i, h in enumerate(hdrs, start=1):
        c = rv.cell(RT - 1, i, h)
        c.font = st.f_rev_hdr
        c.fill = FILL_HDR
        c.border = BOX
        c.alignment = CTR

    for k in range(REV_ROWS):
        r0 = RT + k
        for i in range(1, 8):
            c = rv.cell(r0, i)
            c.border = BOX
            c.font = st.f_rev_in
            c.fill = FILL_IN
            c.alignment = LFT
        rv.cell(r0, 3).number_format = "DD/MM/YYYY"
    from datetime import date as _d
    rv.cell(RT, 1, "P01")
    rv.cell(RT, 2, "S2 - Suitable for Information")
    rv.cell(RT, 4, p["Prepared By"])
    rv.cell(RT, 5, p["Checked By"])
    rv.cell(RT, 6, p["Approved By"])
    rv.cell(RT, 3, _d.today())
    rv.cell(RT, 7, "First issue")

    dv_s = DataValidation(type="list", formula1=f"=Lists!$A$2:$A${st_last}", allow_blank=True)
    dv_r = DataValidation(type="list", formula1=f"=Lists!$C$2:$C${rv_last}", allow_blank=True)
    rv.add_data_validation(dv_s)
    rv.add_data_validation(dv_r)
    dv_s.add(f"B{RT}:B{RB}")
    dv_r.add(f"A{RT}:A{RB}")

    page(rv, landscape=False)
    rv.print_area = f"A1:G{RB}"

    # ---------------------------------------------------------- Schedule ---
    sc = wb.create_sheet("Schedule")
    HDR, UNIT, DAT = 4, 5, 6

    for i, (_, w, _) in enumerate(inst, start=1):
        sc.column_dimensions[get_column_letter(i)].width = max(w * 0.8, 7)
    sc.column_dimensions[get_column_letter(mr_col)].width = 20
    for i, (_, w, _) in enumerate(typ, start=typ_start):
        sc.column_dimensions[get_column_letter(i)].width = max(w * 0.8, 8)
    for i, (_, w, _, _) in enumerate(der, start=der_start):
        sc.column_dimensions[get_column_letter(i)].width = max(w * 0.8, 8)

    last_col = get_column_letter(n_cols)
    sc.merge_cells(f"A1:{last_col}1")
    sc["A1"] = eq["title"]
    sc["A1"].font = st.f_title
    sc["A1"].alignment = LFT

    notes = "General Notes:\n" + "\n".join(
        f"[{i}] {n}" for i, n in enumerate(cfg["general_notes"], start=1))
    sc.merge_cells(f"A2:{last_col}3")
    sc["A2"] = notes
    sc["A2"].font = st.f_note
    sc["A2"].alignment = TOPL
    sc.row_dimensions[2].height = 46
    sc.row_dimensions[3].height = 46

    all_fields = ([f for f, _, _ in inst] + ["Model Reference"]
                  + [f for f, _, _ in typ] + [f for f, _, _, _ in der])
    fills = ([FILL_GRP_IN] * mr_col + [FILL_GRP_LIB] * len(typ) + [FILL_GRP_CALC] * len(der))
    for i, f in enumerate(all_fields, start=1):
        name, unit = split_unit(f)
        h = sc.cell(HDR, i, name)
        h.font = st.f_hdr
        h.fill = fills[i - 1]
        h.border = BOX
        h.alignment = CTR
        u = sc.cell(UNIT, i, unit)
        u.font = st.f_unit
        u.fill = fills[i - 1]
        u.border = BOX
        u.alignment = CTR
    sc.row_dimensions[HDR].height = 46

    for i, (f, _, _, note) in enumerate(der, start=der_start):
        sc.cell(HDR, i).comment = Comment(note, "Schedule Generator", width=330, height=130)

    def resolve(expr, row):
        def sub(m):
            fld = m.group(1)
            if fld not in colmap:
                raise KeyError(f"{eq['code']}: unknown field in formula: {fld}")
            return f"${colmap[fld]}{row}"
        return re.sub(r"\{([^{}]+)\}", sub, expr)

    ref_col = "A"
    mrl = get_column_letter(mr_col)
    for k in range(DATA_ROWS):
        r0 = DAT + k
        for i in range(1, n_cols + 1):
            c = sc.cell(r0, i)
            c.border = BOX
            c.alignment = LFT
            c.font = st.f_sm
        for i in range(1, mr_col + 1):
            sc.cell(r0, i).font = st.f_in
            sc.cell(r0, i).fill = FILL_IN
        for j in range(len(typ)):
            lc = get_column_letter(2 + j)
            c = sc.cell(r0, typ_start + j)
            c.value = (f'=IF(${mrl}{r0}="","",IFERROR(INDEX(Library!${lc}${LIB_TOP}:${lc}${LIB_BOT},'
                       f'MATCH(${mrl}{r0},Library!$A${LIB_TOP}:$A${LIB_BOT},0)),"NOT FOUND"))')
            c.font = st.f_pull
        for j, (f, _, expr, _) in enumerate(der):
            body = resolve(expr, r0).lstrip("=")
            c = sc.cell(r0, der_start + j)
            c.value = f'=IF(${ref_col}{r0}="","",IFERROR({body},""))'
            c.font = st.f_calc
            c.number_format = "0.00"

    for i, (_, _, ex) in enumerate(inst, start=1):
        sc.cell(DAT, i, ex)
    sc.cell(DAT, mr_col, ex_ref)

    dv_m = DataValidation(type="list",
                          formula1=f"=Library!$A${LIB_TOP}:$A${LIB_BOT}", allow_blank=True)
    sc.add_data_validation(dv_m)
    dv_m.add(f"{mrl}{DAT}:{mrl}{DAT + DATA_ROWS - 1}")

    page(sc, landscape=True)
    sc.print_title_rows = f"$1:${UNIT}"
    sc.print_area = f"A1:{last_col}{DAT + DATA_ROWS - 1}"
    sc.freeze_panes = sc.cell(DAT, 1).coordinate

    for hidden in ("Config", "Lists", "Library"):
        wb[hidden].sheet_state = "hidden"
    wb._sheets = [wb["Metadata"], wb["Front Cover"], wb["Revision page"], wb["Schedule"],
                  wb["Config"], wb["Lists"], wb["Library"]]
    wb.active = 3

    path = os.path.join(out_dir, "schedules", meta["filename"])
    wb.save(path)
    return path


# ------------------------------------------------------------------ main ---
def main():
    schema_p = sys.argv[1] if len(sys.argv) > 1 else "schema.json"
    proj_p = sys.argv[2] if len(sys.argv) > 2 else "project.json"
    out = sys.argv[3] if len(sys.argv) > 3 else "out"

    schema = json.load(open(schema_p))
    cfg = json.load(open(proj_p))
    st = Style(cfg["house_style"])

    if os.path.isdir(out):
        shutil.rmtree(out)
    for sub in ("", "schedules", "submissions"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)

    dn = cfg["document_number"]
    schedules = []
    for i, eq in enumerate(schema["equipment_types"]):
        num = str(dn["number_start"] + i).zfill(dn["number_width"])
        docnum = dn["pattern"].format(
            project_number=cfg["project"]["Project Number"], originator=dn["originator"],
            volume=dn["volume"], client_ref=dn["client_ref"], doc_type=dn["doc_type"],
            discipline=dn["discipline"], number=num, classification=dn["classification"],
            level=dn["level"], location=dn["location"])
        schedules.append({"code": eq["code"], "docnum": docnum, "title": eq["title"],
                          "filename": f"{docnum}_-_{slug(eq['title'])}.xlsx"})

    for eq, meta in zip(schema["equipment_types"], schedules):
        build_schedule_file(eq, cfg, schema, meta, out, st)

    build_main_project_info(cfg, schedules, out, st)
    build_library_master(schema, out, st)

    with open(os.path.join(out, "submissions", "_README.txt"), "w") as fh:
        fh.write("The Save Type to Library macro writes one JSON file per submitted equipment "
                 "entry into this folder.\n\nNothing writes to EQUIPMENT_LIBRARY_MASTER.xlsx "
                 "directly, which is what prevents write conflicts.\n\nReview entries here, then "
                 "merge approved ones into the matching sheet of the master and delete the file.\n")

    print(f"Built {len(schedules)} schedule files plus central workbooks in {out}/")
    for s in schedules:
        print("  ", s["filename"])


if __name__ == "__main__":
    main()
