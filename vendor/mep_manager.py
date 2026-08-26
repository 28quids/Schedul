#!/usr/bin/env python3
"""
mep_manager.py - MEP Schedule Project Manager (tkinter front end).

Run:  python mep_manager.py

First run asks where registry.json should live. Put it on the same team
SharePoint site as the equipment library so everyone shares one project list.

Everything it actually does lives in mep_core.py. Keep the two files together.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import mep_core as core

PAD = 8


# --------------------------------------------------------------------------
# small widgets
# --------------------------------------------------------------------------

class Field(ttk.Frame):
    """Label + entry, optional Browse button."""

    def __init__(self, parent, label, width=46, browse=None, tip=""):
        super().__init__(parent)
        self.var = tk.StringVar()
        ttk.Label(self, text=label, width=26, anchor="w").grid(row=0, column=0, sticky="w")
        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.grid(row=0, column=1, sticky="ew", padx=(0, 4))
        self.columnconfigure(1, weight=1)
        col = 2
        if browse:
            ttk.Button(self, text="...", width=3,
                       command=lambda: self._browse(browse)).grid(row=0, column=col)
            col += 1
        if tip:
            ttk.Label(self, text=tip, foreground="#666").grid(
                row=1, column=1, sticky="w", pady=(0, 2))

    def _browse(self, kind):
        if kind == "dir":
            v = filedialog.askdirectory(initialdir=self.var.get() or os.path.expanduser("~"))
        else:
            v = filedialog.askopenfilename(
                initialdir=os.path.dirname(self.var.get()) or os.path.expanduser("~"),
                filetypes=[("All", "*.*")])
        if v:
            self.var.set(core.clean_path(v))

    def get(self):
        return core.clean_path(self.var.get())

    def set(self, v):
        self.var.set("" if v is None else str(v))


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, reg, on_save):
        super().__init__(parent)
        self.title("Shared settings")
        self.reg, self.on_save = reg, on_save
        self.transient(parent)
        self.grab_set()

        frm = ttk.Frame(self, padding=PAD)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Set these once. Every project points at them.",
                  font=("", 9, "bold")).pack(anchor="w", pady=(0, PAD))

        sh = reg["shared"]
        self.f_schema = Field(frm, "schema.json", browse="file")
        self.f_builder = Field(frm, "build_project.py", browse="file")
        self.f_lib = Field(frm, "Equipment library (master)", browse="file",
                           tip="Team SharePoint, not personal OneDrive. Never a project folder.")
        self.f_sub = Field(frm, "Shared submissions folder", browse="dir")
        for f, k in ((self.f_schema, "schema"), (self.f_builder, "builder"),
                     (self.f_lib, "equipment_library"), (self.f_sub, "submissions_folder")):
            f.set(sh.get(k, ""))
            f.pack(fill="x", pady=3)

        ttk.Separator(frm).pack(fill="x", pady=PAD)
        ttk.Label(frm, text=f"Registry file: {core.registry_location()}",
                  foreground="#666").pack(anchor="w")

        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(PAD, 0))
        ttk.Button(bar, text="Save", command=self._save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=4)

    def _save(self):
        self.reg["shared"].update({
            "schema": self.f_schema.get(),
            "builder": self.f_builder.get(),
            "equipment_library": self.f_lib.get(),
            "submissions_folder": self.f_sub.get(),
        })
        self.on_save()
        self.destroy()


# --------------------------------------------------------------------------
# main app
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MEP Schedule Project Manager")
        self.geometry("1120x760")
        self.minsize(940, 640)

        self.reg_path = core.registry_location()
        if not self.reg_path or not os.path.isdir(os.path.dirname(self.reg_path) or "."):
            self.reg_path = self._ask_registry()
            if not self.reg_path:
                self.destroy()
                return
            core.set_registry_location(self.reg_path)

        self.reg = core.load_registry(self.reg_path)
        self.current = None
        self._dirty = False

        self._build_menu()
        self._build_layout()
        self._refresh_list()
        self.log(f"Registry: {self.reg_path}")
        self.log(f"{len(self.reg['projects'])} project(s) loaded.")
        if not self.reg["shared"].get("builder"):
            self.log("First run: open Settings and point at schema.json, "
                     "build_project.py and the shared equipment library.")

    # ---------------------------------------------------------------- setup
    def _ask_registry(self):
        messagebox.showinfo(
            "First run",
            "Choose where registry.json lives.\n\n"
            "Put it on the team SharePoint site next to the equipment library "
            "so the whole team shares one project list.")
        return filedialog.asksaveasfilename(
            title="Registry location", defaultextension=".json",
            initialfile="registry.json", filetypes=[("JSON", "*.json")])

    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Shared settings...", command=self._settings)
        f.add_command(label="Change registry location...", command=self._relocate)
        f.add_command(label="Reload registry", command=self._reload)
        f.add_separator()
        f.add_command(label="Quit", command=self.destroy)
        m.add_cascade(label="File", menu=f)

        t = tk.Menu(m, tearoff=0)
        t.add_command(label="Import existing project.json...", command=self._import)
        t.add_command(label="Show register for this project", command=self._register)
        t.add_command(label="Open admin folder", command=lambda: self._open("admin"))
        t.add_command(label="Open schedules folder", command=lambda: self._open("schedules"))
        t.add_separator()
        t.add_command(label="Clear ledger for this project", command=self._clear_ledger)
        m.add_cascade(label="Tools", menu=t)
        self.config(menu=m)

    def _build_layout(self):
        outer = ttk.Panedwindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True, padx=PAD, pady=PAD)

        # ---- left: project list
        left = ttk.Frame(outer, width=250)
        outer.add(left, weight=0)
        ttk.Label(left, text="Projects", font=("", 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(left, exportselection=False, activestyle="none")
        self.listbox.pack(fill="both", expand=True, pady=4)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Button(row, text="New", command=self._new).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Copy", command=self._duplicate).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Delete", command=self._delete).pack(side="left", fill="x", expand=True)

        # ---- right
        right = ttk.Frame(outer)
        outer.add(right, weight=1)

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)
        self._tab_details()
        self._tab_docnum()
        self._tab_folders()
        self._tab_constants()

        bar = ttk.Frame(right)
        bar.pack(fill="x", pady=(PAD, 4))
        ttk.Button(bar, text="Save project", command=self._save).pack(side="left")
        ttk.Button(bar, text="Validate", command=self._validate).pack(side="left", padx=4)
        ttk.Button(bar, text="Set up folders + project.json",
                   command=self._scaffold).pack(side="left", padx=4)
        ttk.Button(bar, text="Preview build", command=lambda: self._build(True)).pack(side="left", padx=4)
        ttk.Button(bar, text="Build schedules", command=lambda: self._build(False)).pack(side="left", padx=4)
        ttk.Button(bar, text="Sync existing schedules",
                   command=self._sync).pack(side="left", padx=4)

        ttk.Label(right, text="Log", font=("", 9, "bold")).pack(anchor="w")
        self.logbox = tk.Text(right, height=13, wrap="none", font=("Consolas", 9))
        self.logbox.pack(fill="both", expand=False)
        sb = ttk.Scrollbar(right, orient="horizontal", command=self.logbox.xview)
        sb.pack(fill="x")
        self.logbox.config(xscrollcommand=sb.set)

    # ---- tabs
    def _tab_details(self):
        t = ttk.Frame(self.nb, padding=PAD)
        self.nb.add(t, text="Project details")
        self.f_project = {}
        for name in core.PROJECT_FIELDS:
            fld = Field(t, name)
            fld.pack(fill="x", pady=2)
            self.f_project[name] = fld

    def _tab_docnum(self):
        t = ttk.Frame(self.nb, padding=PAD)
        self.nb.add(t, text="Document number")
        self.f_dn = {}
        self.f_dn["pattern"] = Field(t, "pattern", width=70)
        self.f_dn["pattern"].pack(fill="x", pady=2)
        for tok in core.DOCNUM_TOKENS:
            f = Field(t, tok, width=24)
            f.pack(fill="x", pady=2)
            self.f_dn[tok] = f
        for tok, tip in (("number_start", "First sequence number. Keep unique per project if you care."),
                         ("number_width", "Zero padding, 8 in the sample")):
            f = Field(t, tok, width=12, tip=tip)
            f.pack(fill="x", pady=2)
            self.f_dn[tok] = f

        ttk.Separator(t).pack(fill="x", pady=PAD)
        ttk.Button(t, text="Preview document numbers",
                   command=self._preview_numbers).pack(anchor="w")
        self.numbers = tk.Text(t, height=11, wrap="none", font=("Consolas", 9))
        self.numbers.pack(fill="both", expand=True, pady=4)

    def _tab_folders(self):
        t = ttk.Frame(self.nb, padding=PAD)
        self.nb.add(t, text="Folders")
        ttk.Label(t, text="Point at the schedules folder first. Everything else "
                          "is derived from it.", foreground="#666").pack(anchor="w", pady=(0, PAD))

        self.f_sched = Field(t, "Live schedules folder", browse="dir",
                             tip="Where the issued/working schedule files actually live.")
        self.f_sched.pack(fill="x", pady=2)
        ttk.Button(t, text="Derive admin folder from it",
                   command=self._derive_admin).pack(anchor="w", pady=(2, PAD))

        self.f_admin = Field(t, "Project admin folder", browse="dir",
                             tip="Holds project.json, MAINPROJECTINFO.xlsx, issued/, pdf/.")
        self.f_admin.pack(fill="x", pady=2)

        self.sub_mode = tk.StringVar(value="shared")
        box = ttk.LabelFrame(t, text="Submissions inbox", padding=PAD)
        box.pack(fill="x", pady=PAD)
        ttk.Radiobutton(box, text="Use the shared company submissions folder",
                        variable=self.sub_mode, value="shared").pack(anchor="w")
        ttk.Radiobutton(box, text="Give this project its own submissions folder "
                                  "(inside admin)",
                        variable=self.sub_mode, value="project").pack(anchor="w")

        self.resolved = tk.Text(t, height=8, wrap="word", font=("Consolas", 9))
        self.resolved.pack(fill="both", expand=True, pady=4)
        ttk.Button(t, text="Show resolved paths",
                   command=self._show_resolved).pack(anchor="w")

    def _tab_constants(self):
        t = ttk.Frame(self.nb, padding=PAD)
        self.nb.add(t, text="Design constants")
        self.f_const = {}
        for name in core.DEFAULT_CONSTANTS:
            f = Field(t, name, width=14)
            f.pack(fill="x", pady=2)
            self.f_const[name] = f
        ttk.Label(t, text="These are written into every schedule as SETUP_ named "
                          "ranges. Changing them here then running "
                          "'Sync existing schedules' does not update formulas, "
                          "only Config values, which is what the formulas read.",
                  foreground="#666", wraplength=620).pack(anchor="w", pady=PAD)

    # ------------------------------------------------------------- plumbing
    def log(self, *lines):
        for ln in lines:
            self.logbox.insert("end", str(ln) + "\n")
        self.logbox.see("end")

    def clear_log(self):
        self.logbox.delete("1.0", "end")

    def _persist(self):
        core.save_registry(self.reg_path, self.reg)

    def _refresh_list(self, select_id=None):
        self.listbox.delete(0, "end")
        for p in self.reg["projects"]:
            self.listbox.insert("end", core.project_label(p))
        if select_id:
            for i, p in enumerate(self.reg["projects"]):
                if p["id"] == select_id:
                    self.listbox.selection_clear(0, "end")
                    self.listbox.selection_set(i)
                    self._load(p)
                    break

    def _on_select(self, _evt):
        sel = self.listbox.curselection()
        if sel:
            self._load(self.reg["projects"][sel[0]])

    def _load(self, p):
        self.current = p
        for k, f in self.f_project.items():
            f.set(p["project"].get(k, ""))
        for k, f in self.f_dn.items():
            f.set(p["document_number"].get(k, ""))
        for k, f in self.f_const.items():
            f.set(p["design_constants"].get(k, ""))
        self.f_sched.set(p["folders"].get("schedules", ""))
        self.f_admin.set(p["folders"].get("admin", ""))
        self.sub_mode.set(p.get("submissions_mode", "shared"))

    def _harvest(self):
        if not self.current:
            return False
        p = self.current
        for k, f in self.f_project.items():
            p["project"][k] = f.var.get().strip()
        for k, f in self.f_dn.items():
            v = f.var.get().strip()
            p["document_number"][k] = int(v) if k in ("number_start", "number_width") and v.isdigit() else v
        for k, f in self.f_const.items():
            v = f.var.get().strip()
            try:
                p["design_constants"][k] = float(v) if "." in v else int(v)
            except ValueError:
                p["design_constants"][k] = v
        p["folders"]["schedules"] = self.f_sched.get()
        p["folders"]["admin"] = self.f_admin.get()
        p["submissions_mode"] = self.sub_mode.get()
        p["updated"] = core.now()
        return True

    def _need(self):
        if not self.current:
            messagebox.showwarning("No project", "Select or create a project first.")
            return False
        return True

    # -------------------------------------------------------------- actions
    def _settings(self):
        SettingsDialog(self, self.reg, lambda: (self._persist(), self.log("Shared settings saved.")))

    def _relocate(self):
        new = filedialog.asksaveasfilename(defaultextension=".json",
                                           initialfile="registry.json",
                                           filetypes=[("JSON", "*.json")])
        if new:
            core.set_registry_location(new)
            messagebox.showinfo("Registry", "Restart the manager to use the new registry.")

    def _reload(self):
        self.reg = core.load_registry(self.reg_path)
        self.current = None
        self._refresh_list()
        self.log("Registry reloaded.")

    def _new(self):
        p = core.new_project(self.reg)
        self.reg["projects"].append(p)
        self._persist()
        self._refresh_list(p["id"])
        self.log("New project created. Fill in details, set the schedules folder, "
                 "then 'Set up folders + project.json'.")

    def _duplicate(self):
        if not self._need():
            return
        self._harvest()
        import copy
        p = copy.deepcopy(self.current)
        p["id"] = core.new_project(self.reg)["id"]
        p["ledger"] = {}
        p["project"]["Project Name"] += " (copy)"
        p["folders"] = {"admin": "", "schedules": ""}
        p["created"] = p["updated"] = core.now()
        self.reg["projects"].append(p)
        self._persist()
        self._refresh_list(p["id"])
        self.log("Copied. Ledger cleared and folders blanked deliberately.")

    def _delete(self):
        if not self._need():
            return
        if not messagebox.askyesno("Delete", f"Remove {core.project_label(self.current)} "
                                             "from the registry?\n\nNo files are deleted."):
            return
        self.reg["projects"].remove(self.current)
        self.current = None
        self._persist()
        self._refresh_list()
        self.log("Removed from registry. Files untouched.")

    def _derive_admin(self):
        s = self.f_sched.get()
        if not s:
            messagebox.showwarning("Schedules folder", "Set the schedules folder first.")
            return
        self.f_admin.set(os.path.join(os.path.dirname(os.path.abspath(s)),
                                      "_schedule admin"))

    def _show_resolved(self):
        if not self._need():
            return
        self._harvest()
        cp = core.central_paths(self.reg, self.current)
        self.resolved.delete("1.0", "end")
        self.resolved.insert("end",
                             f"project.json        {core.project_json_path(self.current)}\n"
                             f"MAINPROJECTINFO     {cp['project_info']}\n"
                             f"equipment library   {cp['equipment_library']}\n"
                             f"submissions         {cp['submissions_folder']}\n"
                             f"schedules           {self.current['folders']['schedules']}\n")

    def _save(self):
        if not self._need():
            return
        self._harvest()
        self._persist()
        self._refresh_list(self.current["id"])
        self.log("Saved to registry.")

    def _validate(self):
        if not self._need():
            return
        self._harvest()
        self.clear_log()
        issues = core.validate(self.reg, self.current)
        if not issues:
            self.log("Validate: clean.")
            return
        for lvl, msg in issues:
            self.log(f"{lvl:5} {msg}")
        return not any(l == "ERROR" for l, _ in issues)

    def _scaffold(self):
        if not self._need():
            return
        self._harvest()
        if any(l == "ERROR" for l, _ in core.validate(self.reg, self.current)):
            self._validate()
            self.log("Fix the ERRORs above first.")
            return
        made = core.scaffold(self.reg, self.current)
        path = core.write_project_json(self.reg, self.current)
        self._persist()
        self.clear_log()
        for m in made:
            self.log("created  " + m)
        if not made:
            self.log("Folders already existed.")
        self.log("wrote    " + path)

    def _preview_numbers(self):
        if not self._need():
            return
        self._harvest()
        self.numbers.delete("1.0", "end")
        try:
            schema = core.load_schema(self.reg)
        except Exception as exc:
            self.numbers.insert("end", f"Cannot read schema.json: {exc}\n")
            return
        planned = core.compute_docnums(self.reg, self.current, schema)
        led = self.current.get("ledger", {})
        for item in planned:
            mark = " "
            if item["code"] in led:
                mark = "=" if led[item["code"]] == item["docnum"] else "!"
            self.numbers.insert("end", f"{mark} {item['code']:<10} {item['docnum']}\n")
        shifts = core.audit_ledger(self.current, planned)
        if shifts:
            self.numbers.insert("end", "\n!! " + "\n!! ".join(shifts) + "\n")

    def _build(self, dry):
        if not self._need():
            return
        self._harvest()
        issues = core.validate(self.reg, self.current)
        if any(l == "ERROR" for l, _ in issues):
            self._validate()
            self.log("Build blocked by the ERRORs above.")
            return
        if not dry and not messagebox.askyesno(
                "Build", "Build into a temp folder and install only files that do "
                         "not already exist?\n\nExisting schedules will not be "
                         "touched. The shared library will not be touched."):
            return
        self.clear_log()
        for lvl, msg in issues:
            self.log(f"{lvl:5} {msg}")
        try:
            r = core.build(self.reg, self.current, dry_run=dry)
        except Exception as exc:
            self.log("Build error: " + str(exc))
            return
        self.log(r.text())
        if r.ok and not dry:
            self._persist()
            self.log("")
            self.log("Next: open each NEW file in Excel and run RefreshLibrary.")

    def _sync(self):
        if not self._need():
            return
        self._harvest()
        if not messagebox.askyesno(
                "Sync existing schedules",
                "Rewrite the hidden Config sheet of every schedule in this "
                "project's folder, updating the three paths and the project "
                "fields.\n\nA .bak copy is made of each file changed.\n\n"
                "Close them in Excel first. Continue?"):
            return
        self.clear_log()
        self.log("--- dry run ---")
        for ln in core.sync_schedules(self.reg, self.current, dry_run=True):
            self.log(ln)
        if not messagebox.askyesno("Apply?", "Apply the changes listed in the log?"):
            self.log("Cancelled, nothing written.")
            return
        self.log("--- applying ---")
        for ln in core.sync_schedules(self.reg, self.current, dry_run=False):
            self.log(ln)
        self._persist()

    def _register(self):
        if not self._need():
            return
        self._harvest()
        self.clear_log()
        rows = core.scan_register(self.current)
        if not rows:
            self.log("No schedules found.")
            return
        cols = ["DocumentNumber", "ScheduleName", "Revision", "Status", "IssueDate"]
        self.log(" | ".join(c[:22].ljust(22) for c in cols))
        for r in rows:
            self.log(" | ".join(str(r.get(c, ""))[:22].ljust(22) for c in cols))

    def _open(self, which):
        if not self._need():
            return
        self._harvest()
        path = self.current["folders"].get(which, "")
        if not os.path.isdir(path):
            messagebox.showwarning("Not found", path or "(not set)")
            return
        if sys.platform.startswith("win"):
            os.startfile(path)                                   # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])

    def _clear_ledger(self):
        if not self._need():
            return
        if messagebox.askyesno("Clear ledger",
                               "Forget the recorded document numbers for this "
                               "project?\n\nOnly do this if you know a number "
                               "shift is intentional."):
            self.current["ledger"] = {}
            self._persist()
            self.log("Ledger cleared.")

    def _import(self):
        jp = filedialog.askopenfilename(title="Existing project.json",
                                        filetypes=[("JSON", "*.json")])
        if not jp:
            return
        sf = filedialog.askdirectory(title="That project's schedules folder")
        if not sf:
            return
        try:
            p = core.import_from_project_json(self.reg, jp, sf)
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))
            return
        self.reg["projects"].append(p)
        self._persist()
        self._refresh_list(p["id"])
        self.clear_log()
        self.log(f"Imported {core.project_label(p)}")
        self.log(f"Ledger rebuilt from {len(p['ledger'])} existing schedule(s).")
        self.log("Check the Folders tab, then run 'Sync existing schedules' to "
                 "repoint the files at the shared library.")


if __name__ == "__main__":
    App().mainloop()
