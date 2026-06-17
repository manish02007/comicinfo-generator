"""
ComicInfo Generator — GUI Edition
Graphical front-end for generating and embedding ComicInfo.xml metadata into CBZ files.
"""

# ── stdlib ─────────────────────────────────────────────────────────────────────
import json
import os
import queue
import re
import threading
# ── gui ────────────────────────────────────────────────────────────────────────
import tkinter as tk
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from xml.dom import minidom

# ══════════════════════════════════════════════════════════════════════════════
#  THEME
# ══════════════════════════════════════════════════════════════════════════════
BG     = "#0d0d1a"
SURF   = "#181826"
SURF2  = "#20203a"
SURF3  = "#2c2c4a"
ACC    = "#7c6ff0"
ACC2   = "#b0a8ff"
TXT    = "#dce0f5"
TDIM   = "#8080aa"
TGOOD  = "#4ade80"
TERR   = "#f87171"
TWARN  = "#fbbf24"
BDR    = "#38386a"
WHITE  = "#ffffff"

F    = ("Segoe UI", 9)
FB   = ("Segoe UI", 9, "bold")
FT   = ("Segoe UI", 12, "bold")
FH   = ("Segoe UI", 10, "bold")
FM   = ("Consolas", 9)
FS   = ("Segoe UI", 8)

AUTOSAVE_PATH = os.path.join(os.path.expanduser("~"), ".comicinfo_autosave.json")


# ══════════════════════════════════════════════════════════════════════════════
#  CORE PROCESSING LOGIC  (ported + bugs fixed from original script)
# ══════════════════════════════════════════════════════════════════════════════

def prettify(root):
    return minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ", encoding="utf-8")

def detect_file_type(filename):
    n = filename.lower()
    if re.search(r'\b(vol|volume|v)\s*\d+', n):  return "volume"
    if re.search(r'\b(ch|chapter)\s*\d+', n):    return "chapter"
    if re.search(r'\b(ep|episode)\s*\d+', n):    return "episode"
    if re.search(r'\d+\.\d+', n):                 return "chapter"
    if re.search(r'\d+', n):                      return "chapter"
    return "unknown"

def get_prefix(filename, mode, custom_prefix=""):
    if mode == "custom":  return custom_prefix or "Episode"
    if mode == "episode": return "Episode"
    if mode == "chapter": return "Chapter"
    if mode == "volume":  return "Volume"
    if mode == "auto":
        if re.search(r"\b(vol(?:ume)?)\b",      filename, re.I): return "Volume"
        if re.search(r"\b(ch(?:apter)?|ch\.)\b", filename, re.I): return "Chapter"
        return "Episode"
    return "Episode"

def extract_title_from_filename(filename):
    name = os.path.splitext(filename)[0]
    m = re.match(r"^(?:Ep\.?|Episode|Ch\.?|Chapter|Vol\.?|Volume)\s*\d+(?:\.\d+)?\s*[-:]\s*(.+)",
                 name, re.IGNORECASE)
    return m.group(1).strip() if m else None

def get_separator(prefix, use_custom=False, custom_sep=""):
    if use_custom and custom_sep: return f" {custom_sep} "
    p = prefix.lower()
    if "chapter" in p or "ch." in p: return ": "
    if "episode" in p or "ep." in p: return " - "
    if "volume"  in p or "vol." in p: return ": "
    return " - "

def sanitize_filename(name):
    name = unicodedata.normalize("NFKC", name)
    name = name.replace("<","(").replace(">",")")
    name = name.replace('"',"'")
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'[\n\r\t]', '', name)
    name = re.sub(r'\s{2,}', ' ', name)
    name = re.sub(r'[-–—]{2,}', '-', name)
    return name.strip().rstrip(".")

def natural_key(s):
    parts = re.split(r'(\d+\.\d+|\d+)', s)
    key = []
    for p in parts:
        if re.fullmatch(r'\d+\.\d+', p): key.append((0, float(p)))
        elif p.isdigit():                  key.append((1, int(p)))
        else:                              key.append((2, p.lower()))
    return key

def safe_json_load(path):
    if not path: return {}
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except: return {}

def build_xml(data, custom_fields=None):
    ORDER = ["Title","Series","Number","Volume","Writer","Penciller",
             "Publisher","LanguageISO","AlternateSeries","Web","Genre",
             "Rating","Year","Month","Day","Count","Summary"]
    root = ET.Element("ComicInfo")
    for tag in ORDER:
        # ❌ Skip Volume completely if not present
        if tag == "Volume" and not data.get("Volume"):
            continue

        ET.SubElement(root, tag).text = str(data.get(tag, "") or "")
    if custom_fields:
        for name, val in custom_fields:
            name = name.strip()
            if name and name not in ORDER:
                ET.SubElement(root, name).text = str(val)
    return root

def detect_padding(files):
    widths = []
    for f in files:
        m = re.search(r'\b\d+\b', os.path.basename(f))
        if m and "." not in m.group(0):
            widths.append(len(m.group(0)))
    return max(widths) if widths else None

def find_volume(number, rules):
    try:
        num = float(number)
        for rule in rules:
            if float(rule[0]) <= num <= float(rule[1]):
                return str(rule[2])
    except: pass
    return None

def find_date(vol_num, rules):
    try:
        v = float(vol_num)
        for rule in rules:
            if float(rule[0]) <= v <= float(rule[1]):
                return int(rule[2]), int(rule[3]), int(rule[4])
    except: pass
    return None

def find_summary(vol_num, rules):
    try:
        v = float(vol_num)
        for rule in rules:
            if float(rule[0]) <= v <= float(rule[1]):
                return rule[2]
    except: pass
    return None



# ══════════════════════════════════════════════════════════════════════════════
#  UNDO / REDO  +  WORD-DELETE  (wired to all Entry and Text widgets)
# ══════════════════════════════════════════════════════════════════════════════

def _bind_text_shortcuts(widget):
    def _undo(e):
        try: e.widget.edit_undo()
        except Exception: pass
        return "break"
    def _redo(e):
        try: e.widget.edit_redo()
        except Exception: pass
        return "break"
    def _wc(c): return c.isalnum() or c == "_"
    def _del_back(e):
        w = e.widget
        try:
            pos  = w.index(tk.INSERT)
            text = w.get("1.0", pos)
            i = len(text)
            while i > 0 and text[i-1] in (" ", "\t", "\n"): i -= 1
            if i > 0:
                if _wc(text[i-1]):
                    while i > 0 and _wc(text[i-1]): i -= 1
                else:
                    while i > 0 and not _wc(text[i-1]) and text[i-1] not in (" ", "\t", "\n"): i -= 1
            n = len(text) - i
            w.delete(f"{tk.INSERT} -{n}c", tk.INSERT)
        except Exception: pass
        return "break"
    def _del_fwd(e):
        w = e.widget
        try:
            pos  = w.index(tk.INSERT)
            text = w.get(pos, "end-1c")
            i = 0; n = len(text)
            while i < n and text[i] in (" ", "\t", "\n"): i += 1
            if i < n:
                if _wc(text[i]):
                    while i < n and _wc(text[i]): i += 1
                else:
                    while i < n and not _wc(text[i]) and text[i] not in (" ", "\t", "\n"): i += 1
            w.delete(tk.INSERT, f"{tk.INSERT} +{i}c")
        except Exception: pass
        return "break"
    widget.bind("<Control-z>",         _undo)
    widget.bind("<Control-y>",         _redo)
    widget.bind("<Control-Z>",         _redo)
    widget.bind("<Control-BackSpace>", _del_back)
    widget.bind("<Control-Delete>",    _del_fwd)
    widget.bind("<Alt-BackSpace>",     _del_back)
    widget.bind("<Alt-Delete>",        _del_fwd)


_ENTRY_ORIG = ttk.Entry.__init__
def _entry_init(self, *args, **kw):  # *args handles Spinbox passing extra positional
    var = kw.get("textvariable")
    _ENTRY_ORIG(self, *args, **kw)
    # word-delete bindings (no textvariable needed)
    def _wc(c): return c.isalnum() or c == "_"
    def _del_back(e):
        try:
            pos = self.index(tk.INSERT); text = self.get()
            i = pos
            while i > 0 and text[i-1] in (" ", "\t"): i -= 1
            if i > 0:
                if _wc(text[i-1]):
                    while i > 0 and _wc(text[i-1]): i -= 1
                else:
                    while i > 0 and not _wc(text[i-1]) and text[i-1] not in (" ", "\t"): i -= 1
            self.delete(i, pos)
        except Exception: pass
        return "break"
    def _del_fwd(e):
        try:
            pos = self.index(tk.INSERT); text = self.get(); n = len(text)
            i = pos
            while i < n and text[i] in (" ", "\t"): i += 1
            if i < n:
                if _wc(text[i]):
                    while i < n and _wc(text[i]): i += 1
                else:
                    while i < n and not _wc(text[i]) and text[i] not in (" ", "\t"): i += 1
            self.delete(pos, i)
        except Exception: pass
        return "break"
    self.bind("<Control-BackSpace>", _del_back)
    self.bind("<Control-Delete>",    _del_fwd)
    self.bind("<Alt-BackSpace>",     _del_back)
    self.bind("<Alt-Delete>",        _del_fwd)
    if var is None: return
    # undo/redo stack
    _hist = [var.get()]; _fut = []; _live = [True]
    def _push(*_):
        if not _live[0]: return
        v = var.get()
        if _hist and v == _hist[-1]: return
        _hist.append(v)
        if len(_hist) > 200: _hist.pop(0)
        _fut.clear()
    def _undo(e):
        if len(_hist) > 1:
            _fut.append(_hist.pop())
            _live[0] = False
            var.set(_hist[-1])
            try: self.icursor(tk.END)
            except Exception: pass
            _live[0] = True
        return "break"
    def _redo(e):
        if _fut:
            _hist.append(_fut.pop())
            _live[0] = False
            var.set(_hist[-1])
            try: self.icursor(tk.END)
            except Exception: pass
            _live[0] = True
        return "break"
    var.trace_add("write", _push)
    self.bind("<Control-z>", _undo)
    self.bind("<Control-y>", _redo)
    self.bind("<Control-Z>", _redo)
ttk.Entry.__init__ = _entry_init

# ══════════════════════════════════════════════════════════════════════════════
#  TOOLTIP
# ══════════════════════════════════════════════════════════════════════════════
class Tooltip:
    def __init__(self, widget, text):
        self._tw = None
        self._text = text
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event):
        w = event.widget
        x = w.winfo_rootx() + 18
        y = w.winfo_rooty() + w.winfo_height() + 4
        self._tw = tk.Toplevel(w)
        self._tw.wm_overrideredirect(True)
        self._tw.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tw, text=self._text, bg="#fefcd7", fg="#222",
                 relief="solid", borderwidth=1, font=FS,
                 wraplength=300, justify="left", padx=6, pady=3).pack()

    def _hide(self, _):
        if self._tw: self._tw.destroy(); self._tw = None


# ══════════════════════════════════════════════════════════════════════════════
#  DECIMAL CHAPTER DIALOG
# ══════════════════════════════════════════════════════════════════════════════
class DecimalDialog(tk.Toplevel):
    def __init__(self, parent, filename, raw_title):
        super().__init__(parent)
        self.title("Decimal Chapter Detected")
        self.configure(bg=SURF)
        self.grab_set()
        self.resizable(False, False)
        self.result = raw_title
        self._raw  = raw_title

        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        self.geometry(f"460x360+{px+70}+{py+70}")

        # Header
        hdr = tk.Frame(self, bg=SURF2); hdr.pack(fill="x")
        tk.Label(hdr, text="⚠  Decimal Chapter Detected",
                 bg=SURF2, fg=TWARN, font=FH).pack(padx=16, pady=(12,8), anchor="w")

        body = tk.Frame(self, bg=SURF); body.pack(padx=20, fill="both", expand=True)

        tk.Label(body, text=f"File:   {filename}", bg=SURF, fg=TDIM, font=FS,
                 anchor="w", wraplength=420).pack(anchor="w", pady=(8,2))
        tk.Label(body, text=f"Title:  {raw_title}", bg=SURF, fg=TXT, font=F,
                 anchor="w", wraplength=420).pack(anchor="w", pady=(0,10))

        ttk.Separator(body).pack(fill="x", pady=4)
        tk.Label(body, text="Choose how to label this chapter:", bg=SURF, fg=TDIM, font=FS).pack(anchor="w", pady=(4,4))

        self._c = tk.IntVar(value=1)
        opts = [
            (1, f"Raw title:      {raw_title}"),
            (2, f"Bonus Manga:    {raw_title}"),
            (3, f"Bonus Chapter:  {raw_title}"),
            (4, f"Extra Chapter:  {raw_title}"),
            (5, "Custom prefix ▸"),
        ]
        for val, lbl in opts:
            ttk.Radiobutton(body, text=lbl, variable=self._c, value=val).pack(anchor="w", pady=1)

        cf = tk.Frame(body, bg=SURF); cf.pack(anchor="w", pady=(4,0))
        tk.Label(cf, text="   Prefix:", bg=SURF, fg=TDIM, font=F).pack(side="left")
        self._cust = tk.StringVar()
        self._cust_entry = ttk.Entry(cf, textvariable=self._cust, width=22, state="disabled")
        self._cust_entry.pack(side="left", padx=6)

        # Enable custom entry only when option 5 is selected
        self._c.trace_add("write", self._on_choice)

        ttk.Button(self, text="  Confirm  ", command=self._ok).pack(pady=14)

    def _on_choice(self, *_):
        if self._c.get() == 5:
            self._cust_entry.configure(state="normal")
            self._cust_entry.focus_set()
        else:
            self._cust_entry.configure(state="disabled")

    def _ok(self):
        c = self._c.get()
        if   c == 1: self.result = self._raw
        elif c == 2: self.result = f"Bonus Manga: {self._raw}"
        elif c == 3: self.result = f"Bonus Chapter: {self._raw}"
        elif c == 4: self.result = f"Extra Chapter: {self._raw}"
        elif c == 5:
            p = self._cust.get().strip().rstrip(":")
            self.result = f"{p}: {self._raw}" if p else self._raw
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  GENERIC RULE EDIT DIALOG
# ══════════════════════════════════════════════════════════════════════════════
class RuleDialog(tk.Toplevel):
    def __init__(self, parent, columns, values=None, title="Edit Rule"):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=SURF)
        self.grab_set()
        self.resizable(True, False)
        self.result = None

        self.geometry(f"+{parent.winfo_rootx()+80}+{parent.winfo_rooty()+80}")

        self._vars = []
        for i, col in enumerate(columns):
            is_long = col.lower() in ("summary", "description", "notes")
            tk.Label(self, text=col + ":", bg=SURF, fg=TXT, font=F,
                     anchor="e", width=12).grid(row=i, column=0, padx=(14,4), pady=5, sticky="e")
            if is_long:
                t = tk.Text(self, bg=SURF2, fg=TXT, width=50, height=5, font=FM,
                            insertbackground=TXT, relief="flat", borderwidth=1,
                            highlightthickness=1, highlightbackground=BDR,
                            selectbackground=ACC, selectforeground=TXT,
                            undo=True, maxundo=-1)
                t.insert("1.0", values[i] if values else "")
                t.grid(row=i, column=1, padx=(0,14), pady=5, sticky="ew")
                _bind_text_shortcuts(t)
                self._vars.append(("text", t))
            else:
                var = tk.StringVar(value=values[i] if values else "")
                ttk.Entry(self, textvariable=var, width=34).grid(
                    row=i, column=1, padx=(0,14), pady=5, sticky="ew")
                self._vars.append(("str", var))

        bf = tk.Frame(self, bg=SURF)
        bf.grid(row=len(columns), column=0, columnspan=2, pady=10)
        ttk.Button(bf, text="  Save  ",   command=self._save).pack(side="left", padx=5)
        ttk.Button(bf, text=" Cancel ",   command=self.destroy).pack(side="left", padx=5)
        self.columnconfigure(1, weight=1)

    def _save(self):
        r = []
        for kind, w in self._vars:
            r.append(w.get("1.0", "end-1c").strip() if kind == "text" else w.get())
        self.result = r
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  RULES FRAME  (reusable Treeview editor with Add / Edit / Remove)
# ══════════════════════════════════════════════════════════════════════════════
class RulesFrame(tk.Frame):
    def __init__(self, parent, label, columns, rows_data=None, col_widths=None, height=5, **kw):
        super().__init__(parent, bg=SURF, **kw)
        self._columns  = columns
        self._label    = label

        # Header row
        hdr = tk.Frame(self, bg=SURF); hdr.pack(fill="x", padx=2, pady=(4,2))
        tk.Label(hdr, text=label, bg=SURF, fg=ACC2, font=FB).pack(side="left")
        btn_row = tk.Frame(hdr, bg=SURF); btn_row.pack(side="right")
        for txt, cmd in [("＋ Add", self._add), ("✏ Edit", self._edit), ("－ Remove", self._remove)]:
            b = tk.Button(btn_row, text=txt, command=cmd,
                          bg=SURF3, fg=TXT, font=FS, relief="flat",
                          padx=8, pady=2, cursor="hand2",
                          activebackground=ACC, activeforeground=WHITE)
            b.pack(side="left", padx=2)

        # Treeview
        tvf = tk.Frame(self, bg=SURF); tvf.pack(fill="both", expand=True, padx=2, pady=(0,4))
        col_ids = [c.lower().replace(" ", "_") for c in columns]
        self._tv = ttk.Treeview(tvf, columns=col_ids, show="headings",
                                 height=height, selectmode="browse")

        default_cw = col_widths or {}
        for cid, cname in zip(col_ids, columns):
            w = default_cw.get(cname, 240 if cname.lower() in ("summary","description","notes") else 80)
            self._tv.heading(cid, text=cname)
            self._tv.column(cid, width=w, anchor="w" if w > 100 else "center", stretch=True)

        self._tv.bind("<Double-1>", lambda _: self._edit())

        vsb = ttk.Scrollbar(tvf, orient="vertical", command=self._tv.yview)
        self._tv.configure(yscrollcommand=vsb.set)
        self._tv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        if rows_data:
            for row in rows_data:
                self._tv.insert("", "end", values=[str(v) for v in row])

    def _add(self):
        dlg = RuleDialog(self.winfo_toplevel(), self._columns, title=f"Add — {self._label}")
        self.wait_window(dlg)
        if dlg.result: self._tv.insert("", "end", values=dlg.result)

    def _edit(self):
        sel = self._tv.selection()
        if not sel: return
        vals = list(self._tv.item(sel[0], "values"))
        dlg = RuleDialog(self.winfo_toplevel(), self._columns, vals, title=f"Edit — {self._label}")
        self.wait_window(dlg)
        if dlg.result: self._tv.item(sel[0], values=dlg.result)

    def _remove(self):
        sel = self._tv.selection()
        if sel: self._tv.delete(sel[0])

    def get_rows(self):
        return [list(self._tv.item(i, "values")) for i in self._tv.get_children()]

    def set_rows(self, rows):
        for i in self._tv.get_children(): self._tv.delete(i)
        for row in rows: self._tv.insert("", "end", values=[str(v) for v in row])


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════
class ComicInfoGUI:
    UNSAFE_SEPS = set(':/\\|?*<>"')

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ComicInfo Generator")
        self.root.geometry("1020x740")
        self.root.minsize(860, 620)
        self.root.configure(bg=BG)

        # ── Threading ──────────────────────────────────────────────────────────
        self._stop_event  = threading.Event()
        self._msg_queue   = queue.Queue()
        self._resp_event  = threading.Event()
        self._response    = None
        self._running     = False

        # ── Tkinter Variables ──────────────────────────────────────────────────
        # Paths – auto-strip surrounding quotes (from Windows Ctrl+Shift+C)
        def _qvar():
            v = tk.StringVar()
            _g = [False]
            def _strip(*_):
                if _g[0]: return
                val = v.get()
                if len(val) > 1 and val[0] == '"' and val[-1] == '"':
                    _g[0] = True; v.set(val[1:-1]); _g[0] = False
            v.trace_add("write", _strip)
            return v
        self.v_folder       = _qvar()
        self.v_ch_json      = _qvar()
        self.v_vol_json     = _qvar()
        self.v_date_json    = _qvar()
        # Config
        self.v_workers      = tk.IntVar(value=4)
        self.v_dry_run      = tk.BooleanVar(value=False)
        # Processing
        self.v_use_vol      = tk.BooleanVar(value=True)
        self.v_use_vol_date = tk.BooleanVar(value=True)
        self.v_use_vol_summ = tk.BooleanVar(value=True)
        self.v_prefix_mode  = tk.StringVar(value="auto")
        self.v_custom_pfx   = tk.StringVar(value="Break")
        self.v_post_finale  = tk.StringVar(value="strip")
        self.v_csep_on      = tk.BooleanVar(value=False)
        self.v_csep         = tk.StringVar(value="...")
        self.v_zero_pad     = tk.BooleanVar(value=False)
        self.v_pad_width    = tk.IntVar(value=2)
        # Metadata
        self.v_series       = tk.StringVar()
        self.v_writer       = tk.StringVar()
        self.v_penciller    = tk.StringVar()
        self.v_publisher    = tk.StringVar()
        self.v_language     = tk.StringVar(value="en")
        self.v_alt_series   = tk.StringVar()
        self.v_web          = tk.StringVar()
        self.v_genre        = tk.StringVar()
        self.v_rating       = tk.StringVar()
        self.v_year         = tk.StringVar()
        self.v_month        = tk.StringVar()
        self.v_day          = tk.StringVar()
        self.v_count        = tk.StringVar()
        # Status
        self.v_status       = tk.StringVar(value="Ready")
        self.v_pct          = tk.StringVar(value="0%")
        # Mode
        self.v_mode         = tk.StringVar(value="manga")

        # ── Build ──────────────────────────────────────────────────────────────
        self._setup_style()
        self._build_menu()
        self._build_main()
        self._build_statusbar()

        # Traces for dependent widget states
        self.v_prefix_mode.trace_add("write", self._on_prefix_mode)
        self.v_csep_on.trace_add("write", self._on_csep)
        self.v_zero_pad.trace_add("write", self._on_zero_pad)
        self.v_csep.trace_add("write", self._refresh_sep_preview)
        self.v_csep_on.trace_add("write", self._refresh_sep_preview)
        self.v_dry_run.trace_add("write", self._refresh_dry_label)
        self.v_prefix_mode.trace_add("write", self._refresh_sep_preview)
        self.v_custom_pfx.trace_add("write", self._refresh_sep_preview)
        self.v_folder.trace_add("write", self._refresh_sep_preview)
        self.v_mode.trace_add("write", self._on_mode_change)

        # Autosave: restore last session and register close handler
        self._load_autosave()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─── Checkbox tick images ─────────────────────────────────────────────────
    def _make_check_images(self):
        """Build 14×14 PhotoImage pair: unchecked □ / checked ✓."""
        N = 14
        # ✓ shape: centred in 14x14 (x: 2..11, y: 4..10)
        tick = frozenset([
            (4,10),(5,10),                         # bottom tip
            (3,9),(4,9),(5,9),(6,9),               # junction row
            (2,8),(3,8),(2,7),(3,7),               # left arm
            (6,8),(7,8),(7,7),(8,7),(8,6),(9,6),   # right arm lower
            (9,5),(10,5),(10,4),(11,4),             # right arm upper
        ])
        def make_img(fn):
            img = tk.PhotoImage(width=N, height=N)
            # Put pixel-by-pixel: works on all Python/Tk versions
            for y in range(N):
                for x in range(N):
                    img.put(fn(x, y), to=(x, y))
            return img
        img_off = make_img(lambda x,y: BDR   if (x in (0,N-1) or y in (0,N-1)) else SURF2)
        img_on  = make_img(lambda x,y: "#dde0f5" if (x,y) in tick else ACC)
        self._cb_img_off = img_off   # prevent GC
        self._cb_img_on  = img_on
        return img_off, img_on

    # ─── Style ────────────────────────────────────────────────────────────────
    def _setup_style(self):
        s = ttk.Style()
        s.theme_use("clam")

        s.configure(".", background=SURF, foreground=TXT, font=F,
                    troughcolor=SURF2, darkcolor=SURF2, lightcolor=SURF3,
                    bordercolor=BDR, focuscolor=ACC, relief="flat")

        s.configure("TNotebook",     background=BG,    borderwidth=0)
        s.configure("TNotebook.Tab", background=SURF2, foreground=TDIM,
                    padding=[16, 7], font=F)
        s.map("TNotebook.Tab",
              background=[("selected", SURF),  ("active", SURF3)],
              foreground=[("selected", TXT),   ("active", TXT)])

        s.configure("TFrame",    background=SURF)
        s.configure("TLabel",    background=SURF, foreground=TXT)
        s.configure("TSeparator",background=BDR)

        s.configure("TEntry", fieldbackground=SURF2, foreground=TXT,
                    insertcolor=TXT, bordercolor=BDR,
                    selectforeground=TXT, selectbackground=ACC)
        s.map("TEntry", bordercolor=[("focus", ACC2), ("!focus", BDR)])

        s.configure("TButton", background=SURF3, foreground=TXT,
                    borderwidth=0, relief="flat", padding=[10, 5], font=F)
        s.map("TButton", background=[("active", SURF3), ("pressed", BDR)])

        s.configure("Accent.TButton", background=ACC, foreground=WHITE,
                    borderwidth=0, relief="flat", padding=[14, 7], font=FB)
        s.map("Accent.TButton", background=[("active", ACC2), ("disabled", BDR)])

        s.configure("Stop.TButton", background="#8b2222", foreground=WHITE,
                    borderwidth=0, relief="flat", padding=[14, 7], font=FB)
        s.map("Stop.TButton", background=[("active", "#c0392b")])

        # Custom tick-mark checkbox indicator
        # Gap spacer image — 6px transparent spacer for indicator→label spacing
        self._gap_img = tk.PhotoImage(width=6, height=14)
        for _gx in range(6):
            for _gy in range(14):
                self._gap_img.put(SURF, to=(_gx, _gy))
        s.element_create("gap6", "image", self._gap_img, sticky="")
        img_off, img_on = self._make_check_images()
        s.element_create("cb.indicator", "image", img_off,
                          ("selected !disabled", img_on), ("selected disabled", img_off),
                          sticky="", padding=(0, 0, 0, 0))
        s.layout("TCheckbutton", [
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                ("cb.indicator",      {"side": "left", "sticky": ""}),
                ("gap6",              {"side": "left", "sticky": ""}),
                ("Checkbutton.label", {"sticky": "nswe"}),
            ]})
        ])
        s.configure("TCheckbutton", background=SURF, foreground=TXT)
        s.map("TCheckbutton", background=[("active", SURF)])

        s.configure("TRadiobutton", background=SURF, foreground=TXT)
        s.map("TRadiobutton", background=[("active", SURF)],
              indicatorcolor=[("selected", ACC), ("!selected", SURF2)])
        s.layout("TRadiobutton", [
            ("Radiobutton.padding", {"sticky": "nswe", "children": [
                ("Radiobutton.indicator", {"side": "left", "sticky": ""}),
                ("gap6",                  {"side": "left", "sticky": ""}),
                ("Radiobutton.label",      {"sticky": "nswe"}),
            ]})
        ])

        s.configure("TSpinbox", fieldbackground=SURF2, foreground=TXT,
                    insertcolor=TXT, arrowcolor=ACC2, bordercolor=BDR)

        s.configure("TCombobox", fieldbackground=SURF2, foreground=TXT,
                    selectforeground=TXT, selectbackground=ACC,
                    arrowcolor=ACC2, bordercolor=BDR)
        s.map("TCombobox", fieldbackground=[("readonly", SURF2)],
              bordercolor=[("focus", ACC2)])

        s.configure("TProgressbar", troughcolor=SURF2, background=ACC,
                    thickness=8, borderwidth=0)

        s.configure("Treeview", background=SURF2, foreground=TXT,
                    fieldbackground=SURF2, borderwidth=0, rowheight=24)
        s.configure("Treeview.Heading", background=SURF3, foreground=ACC2,
                    relief="flat", font=FB)
        s.map("Treeview",
              background=[("selected", ACC)],
              foreground=[("selected", WHITE)])

        s.configure("TScrollbar", background=SURF2, troughcolor=SURF,
                    arrowcolor=TDIM, bordercolor=SURF, darkcolor=SURF, lightcolor=SURF)
        s.map("TScrollbar", background=[("active", SURF3)])

        s.configure("TLabelframe",       background=SURF, bordercolor=BDR, relief="solid", borderwidth=1)
        s.configure("TLabelframe.Label", background=SURF, foreground=ACC2, font=FB)

    # ─── Menu ─────────────────────────────────────────────────────────────────
    def _build_menu(self):
        mb = tk.Menu(self.root, bg=SURF2, fg=TXT, relief="flat", borderwidth=0,
                     activebackground=SURF3, activeforeground=TXT)
        self.root.config(menu=mb)

        fm = tk.Menu(mb, tearoff=0, bg=SURF2, fg=TXT, activebackground=SURF3)
        mb.add_cascade(label="File", menu=fm)
        fm.add_command(label="Import Metadata (.py / .json)…", command=self._import_metadata, accelerator="Ctrl+I")
        fm.add_separator()
        fm.add_command(label="Save Config…", command=self._save_config, accelerator="Ctrl+S")
        fm.add_command(label="Load Config…", command=self._load_config, accelerator="Ctrl+O")
        fm.add_separator()
        fm.add_command(label="Reset All…",  command=self._reset_all,   accelerator="Ctrl+R")
        fm.add_command(label="Clear Log",   command=self._clear_log)
        fm.add_separator()
        fm.add_command(label="Exit",        command=self.root.quit)

        hm = tk.Menu(mb, tearoff=0, bg=SURF2, fg=TXT, activebackground=SURF3)
        mb.add_cascade(label="Help", menu=hm)
        hm.add_command(label="About", command=self._show_about)

        self.root.bind_all("<Control-s>", lambda _: self._save_config())
        self.root.bind_all("<Control-o>", lambda _: self._load_config())
        self.root.bind_all("<Control-i>", lambda _: self._import_metadata())
        self.root.bind_all("<Control-r>", lambda _: self._reset_all())

    # ─── Main Layout ──────────────────────────────────────────────────────────
    def _build_main(self):
        # Toolbar
        bar = tk.Frame(self.root, bg=SURF2, height=46)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="  ☰  ComicInfo Generator", bg=SURF2, fg=TXT, font=FT).pack(side="left", padx=10)
        # Reset button (accent red, far-right)
        tk.Button(bar, text="↺  Reset", command=self._reset_all,
                  bg="#6b2222", fg=WHITE, font=FS, relief="flat",
                  padx=10, pady=4, cursor="hand2",
                  activebackground="#c0392b", activeforeground=WHITE
                  ).pack(side="right", padx=5, pady=8)
        for lbl, cmd in [("💾 Save Config",    self._save_config),
                         ("📂 Load Config",    self._load_config),
                         ("📥 Import Metadata", self._import_metadata)]:
            tk.Button(bar, text=lbl, command=cmd, bg=SURF3, fg=TXT, font=FS,
                      relief="flat", padx=10, pady=4, cursor="hand2",
                      activebackground=ACC, activeforeground=WHITE).pack(side="right", padx=5, pady=8)

        # Notebook
        self._nb = ttk.Notebook(self.root)
        self._nb.pack(fill="both", expand=True)

        self._tab_paths()
        self._tab_processing()
        self._tab_metadata()
        self._tab_rules()
        self._tab_run()

    # ─── Status Bar ───────────────────────────────────────────────────────────
    def _build_statusbar(self):
        sb = tk.Frame(self.root, bg=SURF2, height=22)
        sb.pack(fill="x", side="bottom")
        sb.pack_propagate(False)
        tk.Label(sb, textvariable=self.v_status,
                 bg=SURF2, fg=TDIM, font=FS, anchor="w").pack(side="left", padx=10)
        tk.Label(sb, text="ComicInfo Generator — GUI Edition",
                 bg=SURF2, fg=TDIM, font=FS).pack(side="right", padx=10)

    # ─── Helper: section LabelFrame ───────────────────────────────────────────
    def _section(self, parent, title):
        return ttk.LabelFrame(parent, text=f"  {title}  ")

    # ─── Helper: path row (label + entry + Browse) ────────────────────────────
    def _path_row(self, parent, row, label, var, is_dir=False, tip=""):
        tk.Label(parent, text=label, bg=SURF, fg=TXT, font=F,
                 anchor="e", width=16).grid(row=row, column=0, padx=(16,4), pady=5, sticky="e")
        e = ttk.Entry(parent, textvariable=var)
        e.grid(row=row, column=1, padx=4, pady=5, sticky="ew")
        if tip: Tooltip(e, tip)
        def _browse_dir(v=var):
            p = filedialog.askdirectory()
            if p:
                v.set(p)
                self.root.after(80, self._refresh_sep_preview)
        def _browse_file(v=var):
            p = filedialog.askopenfilename(filetypes=[("JSON","*.json"),("All","*.*")])
            if p: v.set(p)
        cmd = _browse_dir if is_dir else _browse_file
        tk.Button(parent, text="Browse", command=cmd,
                  bg=SURF3, fg=TXT, font=FS, relief="flat", padx=8, pady=2,
                  cursor="hand2", activebackground=ACC, activeforeground=WHITE).grid(
            row=row, column=2, padx=(4,16), pady=5)

    # ─── TAB 1: Paths & Config ────────────────────────────────────────────────
    def _tab_paths(self):
        outer = tk.Frame(self._nb, bg=SURF)
        self._nb.add(outer, text="  📁  Paths & Config  ")

        outer.columnconfigure(0, weight=1)

        # ── File Paths section ─────────────────────────────────────────────────
        sec1 = self._section(outer, "File Paths")
        sec1.pack(fill="x", padx=20, pady=(20,8))
        sec1.columnconfigure(1, weight=1)

        self._path_row(sec1, 0, "CBZ Folder:",      self.v_folder,    is_dir=True,
                       tip="Folder containing the .cbz files to process.")
        self._path_row(sec1, 1, "Chapter Titles:",  self.v_ch_json,
                       tip='JSON mapping: {"1": "Chapter Title", "2": "..."}')
        self._path_row(sec1, 2, "Volume Titles:",   self.v_vol_json,
                       tip='JSON mapping: {"1": "Volume Title", ...}')
        self._path_row(sec1, 3, "Episode Dates:",   self.v_date_json,
                       tip='JSON mapping: {"1": "Jul 25, 2019", ...}')

        # ── Processing Config section ──────────────────────────────────────────
        sec2 = self._section(outer, "Processing Config")
        sec2.pack(fill="x", padx=20, pady=8)

        row1 = tk.Frame(sec2, bg=SURF); row1.pack(anchor="w", padx=12, pady=(8,4))
        tk.Label(row1, text="Max Workers:", bg=SURF, fg=TXT, font=F).pack(side="left")
        sp = ttk.Spinbox(row1, from_=1, to=32, textvariable=self.v_workers, width=5)
        sp.pack(side="left", padx=(6,20))
        Tooltip(sp, "Parallel threads for batch processing. 4 is a safe default.")

        dr_cb = ttk.Checkbutton(row1, text="Dry Run  (preview — no files modified)",
                                variable=self.v_dry_run)
        dr_cb.pack(side="left")
        Tooltip(dr_cb, "Simulate the run: logs what would change without touching any file.")

        # Log directory display
        log_dir = os.path.join(os.getcwd(), "logs")
        row2 = tk.Frame(sec2, bg=SURF); row2.pack(anchor="w", padx=12, pady=(2,10))
        tk.Label(row2, text="Log directory:", bg=SURF, fg=TDIM, font=FS).pack(side="left")
        tk.Label(row2, text=log_dir, bg=SURF, fg=TDIM, font=FM).pack(side="left", padx=8)

    # ─── TAB 2: Processing ────────────────────────────────────────────────────
    def _tab_processing(self):
        outer = tk.Frame(self._nb, bg=SURF)
        self._nb.add(outer, text="  ⚙  Processing  ")
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)

        # ── LEFT ──────────────────────────────────────────────────────────────
        left = tk.Frame(outer, bg=SURF)
        left.grid(row=0, column=0, sticky="new", padx=(20,8), pady=20)

        # Mode selector
        sec_mode = self._section(left, "Mode")
        sec_mode.pack(fill="x", pady=(0,12))
        mode_row = tk.Frame(sec_mode, bg=SURF); mode_row.pack(anchor="w", padx=12, pady=(8,8))
        for val, lbl, tip in [
            ("manga",   "Manga",          "Turns ON all Volume Metadata options (default for most manga)."),
            ("manhwa",  "Manhwa / Manhua", "Turns OFF all Volume Metadata options (no volumes in manhwa)."),
        ]:
            rb = ttk.Radiobutton(mode_row, text=lbl, variable=self.v_mode, value=val)
            rb.pack(side="left", padx=(0, 18))
            Tooltip(rb, tip)

        # Volume metadata toggles
        sec_vol = self._section(left, "Volume Metadata")
        sec_vol.pack(fill="x", pady=(0,12))
        for txt, var, tip in [
            ("Include volume number in metadata",     self.v_use_vol,
             "Enables Volume field in ComicInfo.xml. Disable for manhwa."),
            ("Use volume date rules for publication", self.v_use_vol_date,
             "Overrides Year/Month/Day from Date Rules table. Disable for manhwa."),
            ("Use per-volume summary rules",          self.v_use_vol_summ,
             "Overrides Summary from Summary Rules table. Disable for manhwa."),
        ]:
            cb = ttk.Checkbutton(sec_vol, text=txt, variable=var)
            cb.pack(anchor="w", padx=12, pady=3)
            Tooltip(cb, tip)

        # Post-finale mode
        sec_fin = self._section(left, "Post-Finale Behaviour")
        sec_fin.pack(fill="x", pady=(0,12))
        pf = tk.Frame(sec_fin, bg=SURF); pf.pack(anchor="w", padx=12, pady=8)
        tk.Label(pf, text="After finale chapter:", bg=SURF, fg=TXT, font=F).pack(side="left")
        cb_pf = ttk.Combobox(pf, textvariable=self.v_post_finale,
                             values=["strip", "keep"], state="readonly", width=8)
        cb_pf.pack(side="left", padx=8)
        Tooltip(cb_pf, '"strip" removes the prefix from post-finale chapters.\n"keep" preserves it.')

        # Numbering / zero-pad
        sec_num = self._section(left, "Numbering & Zero-Pad")
        sec_num.pack(fill="x", pady=(0,12))
        r1 = tk.Frame(sec_num, bg=SURF); r1.pack(anchor="w", padx=12, pady=(8,4))
        self._cb_zp = ttk.Checkbutton(r1, text="Zero-pad numbers (e.g. 01, 02 …)",
                                       variable=self.v_zero_pad)
        self._cb_zp.pack(side="left")
        Tooltip(self._cb_zp, "Auto-detects existing pad width from filenames or uses Pad Width below.")

        r2 = tk.Frame(sec_num, bg=SURF); r2.pack(anchor="w", padx=30, pady=(0,8))
        tk.Label(r2, text="Pad width:", bg=SURF, fg=TXT, font=F).pack(side="left")
        self._sp_pw = ttk.Spinbox(r2, from_=1, to=5, textvariable=self.v_pad_width, width=4)
        self._sp_pw.pack(side="left", padx=6)
        Tooltip(self._sp_pw, "2 → Episode 01  |  3 → Episode 001")
        self._on_zero_pad()

        # ── RIGHT ─────────────────────────────────────────────────────────────
        right = tk.Frame(outer, bg=SURF)
        right.grid(row=0, column=1, sticky="new", padx=(8,20), pady=20)

        # Prefix mode
        sec_pfx = self._section(right, "Number Prefix")
        sec_pfx.pack(fill="x", pady=(0,12))
        modes = [("auto",    "Auto-detect from filename"),
                 ("episode", "Always: Episode"),
                 ("chapter", "Always: Chapter"),
                 ("volume",  "Always: Volume"),
                 ("custom",  "Custom →")]
        for val, lbl in modes:
            ttk.Radiobutton(sec_pfx, text=lbl, variable=self.v_prefix_mode, value=val).pack(
                anchor="w", padx=12, pady=2)
        cf = tk.Frame(sec_pfx, bg=SURF); cf.pack(anchor="w", padx=30, pady=(0,8))
        tk.Label(cf, text="Custom text:", bg=SURF, fg=TXT, font=F).pack(side="left")
        self._e_cpfx = ttk.Entry(cf, textvariable=self.v_custom_pfx, width=16)
        self._e_cpfx.pack(side="left", padx=6)
        Tooltip(self._e_cpfx, 'Used when prefix mode is "custom". E.g. "Break".')
        self._on_prefix_mode()

        # Separator
        sec_sep = self._section(right, "Title Separator")
        sec_sep.pack(fill="x", pady=(0,12))
        r1s = tk.Frame(sec_sep, bg=SURF); r1s.pack(anchor="w", padx=12, pady=(8,4))
        self._cb_cs = ttk.Checkbutton(r1s, text="Override separator",
                                       variable=self.v_csep_on)
        self._cb_cs.pack(side="left")
        Tooltip(self._cb_cs, "Replaces the default ' - ' or ': ' between number and title.")

        r2s = tk.Frame(sec_sep, bg=SURF); r2s.pack(anchor="w", padx=30, pady=(2,4))
        tk.Label(r2s, text="Separator:", bg=SURF, fg=TXT, font=F).pack(side="left")
        self._e_csep = ttk.Entry(r2s, textvariable=self.v_csep, width=10)
        self._e_csep.pack(side="left", padx=6)
        Tooltip(self._e_csep, 'e.g. "-" or "~"  (avoid  /\\:*?"<>| — invalid in filenames)')
        self._on_csep()

        # Preview strip
        prev = tk.Frame(sec_sep, bg=SURF2)
        prev.pack(fill="x", padx=12, pady=(2,8))
        tk.Label(prev, text=" Preview:", bg=SURF2, fg=TDIM, font=FS).pack(side="left")
        self._sep_prev = tk.Label(prev, text="", bg=SURF2, fg=ACC2, font=FM, width=42, anchor="w")
        self._sep_prev.pack(side="left")
        self._refresh_sep_preview()

    # ─── TAB 3: Metadata ─────────────────────────────────────────────────────
    def _tab_metadata(self):
        outer = tk.Frame(self._nb, bg=SURF)
        self._nb.add(outer, text="  📝  Metadata  ")

        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        # Fixed metadata fields
        sec_const = self._section(outer, "Constant Metadata  (applied to every CBZ)")
        sec_const.pack(fill="x", padx=20, pady=(18,8))
        sec_const.columnconfigure(1, weight=1)
        sec_const.columnconfigure(3, weight=1)

        def row2(r, c0_lbl, c0_var, c0_tip, c1_lbl, c1_var, c1_tip):
            for col, lbl, var, tip in [
                (0, c0_lbl, c0_var, c0_tip),
                (2, c1_lbl, c1_var, c1_tip),
            ]:
                tk.Label(sec_const, text=lbl, bg=SURF, fg=TXT, font=F, anchor="e", width=14).grid(
                    row=r, column=col, padx=(10 if col==0 else 6, 4), pady=4, sticky="e")
                e = ttk.Entry(sec_const, textvariable=var)
                e.grid(row=r, column=col+1, padx=(0, 10 if col==0 else 10), pady=4, sticky="ew")
                Tooltip(e, tip)

        row2(0, "Series:",        self.v_series,    "Comic series title.",
                "Writer:",        self.v_writer,    "Script writer / author.")
        row2(1, "Penciller:",     self.v_penciller, "Penciller / illustrator.",
                "Publisher:",     self.v_publisher, "Publisher names, comma-separated.")
        row2(2, "Language ISO:",  self.v_language,  'ISO code: "en", "ja", "ko" …',
                "Alt. Series:",   self.v_alt_series,"Original / alternate series title.")
        row2(3, "Genre:",         self.v_genre,     "Genres, comma-separated.",
                "Rating:",        self.v_rating,    "Score, e.g. 7.7")
        row2(4, "Year:",          self.v_year,      "Default publication year.",
                "Month:",         self.v_month,     "Default publication month.")
        row2(5, "Day:",           self.v_day,       "Default publication day.",
                "Count:",         self.v_count,     "Total chapter / volume count.")

        # Web (wide)
        tk.Label(sec_const, text="Web:", bg=SURF, fg=TXT, font=F, anchor="e", width=14).grid(
            row=6, column=0, padx=(10,4), pady=4, sticky="e")
        e_web = ttk.Entry(sec_const, textvariable=self.v_web)
        e_web.grid(row=6, column=1, columnspan=3, padx=(0,10), pady=4, sticky="ew")
        Tooltip(e_web, "Space-separated URLs for the series.")

        # Custom fields
        sec_custom = self._section(outer, "Custom XML Fields  (extra ComicInfo tags)")
        sec_custom.pack(fill="x", padx=20, pady=(0,8))
        self._custom_fields = RulesFrame(
            sec_custom, "Custom Fields",
            columns=["Field Name", "Value"],
            col_widths={"Field Name": 160, "Value": 460},
            height=3
        )
        self._custom_fields.pack(fill="x", padx=4, pady=(0,4))

        # Summary
        sec_summ = self._section(outer, "Default Summary  (Chapter 1 + fallback)")
        sec_summ.pack(fill="both", expand=True, padx=20, pady=(0,18))
        sec_summ.rowconfigure(0, weight=1)
        sec_summ.columnconfigure(0, weight=1)

        sf = tk.Frame(sec_summ, bg=SURF); sf.pack(fill="both", expand=True, padx=10, pady=8)
        self._summary_text = tk.Text(sf, bg=SURF2, fg=TXT, insertbackground=TXT,
                                      font=FM, wrap="word", relief="flat",
                                      borderwidth=0, highlightthickness=1,
                                      highlightbackground=BDR,
                                      selectbackground=ACC, selectforeground=TXT,
                                      undo=True, maxundo=-1)
        sum_vsb = ttk.Scrollbar(sf, orient="vertical", command=self._summary_text.yview)
        self._summary_text.configure(yscrollcommand=sum_vsb.set)
        self._summary_text.pack(side="left", fill="both", expand=True)
        sum_vsb.pack(side="right", fill="y")
        _bind_text_shortcuts(self._summary_text)

    # ─── TAB 4: Rules ─────────────────────────────────────────────────────────
    def _tab_rules(self):
        outer = tk.Frame(self._nb, bg=SURF)
        self._nb.add(outer, text="  📋  Rules  ")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        outer.rowconfigure(2, weight=2)

        # Volume Rules
        vr_sec = self._section(outer, "Volume Rules  —  maps Chapter range → Volume number")
        vr_sec.grid(row=0, column=0, sticky="nsew", padx=18, pady=(16,6))
        self._vol_rules = RulesFrame(
            vr_sec, "Volume Rules",
            columns=["Ch Start","Ch End","Volume"],
            col_widths={"Ch Start":90,"Ch End":90,"Volume":90},
            rows_data=[[1,3.5,"1"],[4,8.5,"2"],[9,13.5,"3"]]
        )
        self._vol_rules.pack(fill="both", expand=True, padx=4, pady=4)

        # Date Rules
        dr_sec = self._section(outer, "Date Rules  —  maps Volume range → Publication Date")
        dr_sec.grid(row=1, column=0, sticky="nsew", padx=18, pady=6)
        self._date_rules = RulesFrame(
            dr_sec, "Date Rules",
            columns=["Vol Start","Vol End","Year","Month","Day"],
            col_widths={"Vol Start":80,"Vol End":80,"Year":70,"Month":60,"Day":60},
            rows_data=[[1,1,2020,6,16],[2,2,2021,1,19],[3,3,2021,6,1]]
        )
        self._date_rules.pack(fill="both", expand=True, padx=4, pady=4)

        # Summary Rules
        sr_sec = self._section(outer, "Summary Rules  —  maps Volume range → Custom Summary")
        sr_sec.grid(row=2, column=0, sticky="nsew", padx=18, pady=(6,16))
        self._summ_rules = RulesFrame(
            sr_sec, "Summary Rules",
            columns=["Vol Start","Vol End","Summary"],
            col_widths={"Vol Start":80,"Vol End":80,"Summary":500}
        )
        self._summ_rules.pack(fill="both", expand=True, padx=4, pady=4)

    # ─── TAB 5: Run ───────────────────────────────────────────────────────────
    def _tab_run(self):
        outer = tk.Frame(self._nb, bg=SURF)
        self._nb.add(outer, text="  ▶  Run  ")

        # Control bar
        ctrl = tk.Frame(outer, bg=SURF2)
        ctrl.pack(fill="x")
        btns = tk.Frame(ctrl, bg=SURF2); btns.pack(side="left", padx=14, pady=8)
        self._btn_start = ttk.Button(btns, text="  ▶  Start Processing  ",
                                      command=self._start_run, style="Accent.TButton")
        self._btn_start.pack(side="left", padx=(0,8))
        self._btn_stop  = ttk.Button(btns, text="  ⏹  Stop  ",
                                      command=self._request_stop, style="Stop.TButton",
                                      state="disabled")
        self._btn_stop.pack(side="left", padx=(0,8))

        self._lbl_dry = tk.Label(ctrl, text="", bg=SURF2, fg=TWARN, font=FB)
        self._lbl_dry.pack(side="left", padx=12)
        self._refresh_dry_label()

        # Progress area
        prog = tk.Frame(outer, bg=SURF); prog.pack(fill="x", padx=16, pady=(10,4))
        top_row = tk.Frame(prog, bg=SURF); top_row.pack(fill="x")
        tk.Label(top_row, text="Progress:", bg=SURF, fg=TXT, font=F).pack(side="left")
        self._lbl_files = tk.Label(top_row, text="— / — files", bg=SURF, fg=TDIM, font=F)
        self._lbl_files.pack(side="left", padx=10)
        tk.Label(top_row, textvariable=self.v_pct, bg=SURF, fg=ACC2, font=FB).pack(side="right")
        self._pbar = ttk.Progressbar(prog, mode="determinate")
        self._pbar.pack(fill="x", pady=(4,0))

        # Log header bar
        log_hdr = tk.Frame(outer, bg=SURF2); log_hdr.pack(fill="x", padx=0, pady=(8,0))
        tk.Label(log_hdr, text="  Log Output", bg=SURF2, fg=ACC2, font=FB).pack(side="left", pady=4)
        self.v_verbose = tk.BooleanVar(value=False)
        ttk.Checkbutton(log_hdr, text="Verbose (show detection lines)",
                        variable=self.v_verbose).pack(side="left", padx=14)
        ttk.Button(log_hdr, text="🗑 Clear", command=self._clear_log).pack(side="right", padx=8, pady=2)

        # Log text area
        log_outer = tk.Frame(outer, bg=SURF); log_outer.pack(fill="both", expand=True, padx=0)
        lf = tk.Frame(log_outer, bg=SURF); lf.pack(fill="both", expand=True, padx=16, pady=(4,0))
        self._log = tk.Text(lf, bg=SURF2, fg=TXT, font=FM, wrap="none",
                             state="disabled", relief="flat", borderwidth=0,
                             highlightthickness=1, highlightbackground=BDR,
                             insertbackground=TXT, selectbackground=ACC, selectforeground=TXT,
                             padx=8, pady=6, spacing1=1, spacing3=1)
        log_vsb = ttk.Scrollbar(lf, orient="vertical", command=self._log.yview)
        log_hsb = ttk.Scrollbar(lf, orient="horizontal", command=self._log.xview)
        self._log.configure(yscrollcommand=log_vsb.set, xscrollcommand=log_hsb.set)
        log_vsb.pack(side="right", fill="y")
        log_hsb.pack(side="bottom", fill="x")
        self._log.pack(side="left", fill="both", expand=True)
        # Log tag colours
        self._log.tag_configure("ok",      foreground=TGOOD, font=FM)
        self._log.tag_configure("err",     foreground=TERR)
        self._log.tag_configure("warn",    foreground=TWARN)
        self._log.tag_configure("info",    foreground=TXT)
        self._log.tag_configure("dim",     foreground=TDIM)
        self._log.tag_configure("head",    foreground=ACC2, font=FB)
        self._log.tag_configure("sep",     foreground=BDR)
        self._log.tag_configure("ts",      foreground="#555580", font=FS)
        self._log.tag_configure("counter", foreground=TDIM, font=FS)
        self._log.tag_configure("renamed", foreground=ACC2)

        # Stats bar
        sbar = tk.Frame(outer, bg=SURF3); sbar.pack(fill="x")
        self._stat_lbls = {}
        for name, key in [("Total","total"),("Processed","processed"),("Renamed","renamed"),
                          ("Skipped","rename_skipped"),("XML Updated","xml_updated"),("Errors","errors")]:
            sf = tk.Frame(sbar, bg=SURF3); sf.pack(side="left", padx=14, pady=6)
            tk.Label(sf, text=name, bg=SURF3, fg=TDIM, font=FS).pack()
            lbl = tk.Label(sf, text="—", bg=SURF3, fg=TXT, font=FB)
            lbl.pack()
            self._stat_lbls[key] = lbl

    # ─── Trace callbacks ──────────────────────────────────────────────────────
    def _on_prefix_mode(self, *_):
        st = "normal" if self.v_prefix_mode.get() == "custom" else "disabled"
        self._e_cpfx.configure(state=st)

    def _on_csep(self, *_):
        st = "normal" if self.v_csep_on.get() else "disabled"
        self._e_csep.configure(state=st)

    def _on_zero_pad(self, *_):
        st = "normal" if self.v_zero_pad.get() else "disabled"
        self._sp_pw.configure(state=st)

    def _refresh_sep_preview(self, *_):
        try:
            mode   = self.v_prefix_mode.get()
            folder = self.v_folder.get().strip()
            ex_num = "1"
            auto_prefix = "Episode"

            # Scan folder for a real example filename
            if folder and os.path.isdir(folder):
                cbz = sorted(
                    [f for f in os.listdir(folder) if f.lower().endswith(".cbz")],
                    key=natural_key)
                if cbz:
                    sample = cbz[0]
                    auto_prefix = {"volume":"Volume","chapter":"Chapter",
                                   "episode":"Episode"}.get(detect_file_type(sample),"Episode")
                    m = re.search(r"\d+(?:\.\d+)?", sample)
                    if m: ex_num = m.group(0)

            if mode == "custom":    prefix = self.v_custom_pfx.get() or "Custom"
            elif mode == "episode": prefix = "Episode"
            elif mode == "chapter": prefix = "Chapter"
            elif mode == "volume":  prefix = "Volume"
            else:                   prefix = auto_prefix  # auto

            if self.v_csep_on.get():
                sep = self.v_csep.get() or "-"
                txt = f"{prefix} {ex_num} {sep} My Title"
            else:
                sep = ": " if prefix.lower() in ("chapter", "volume") else " - "
                txt = f"{prefix} {ex_num}{sep}My Title"
                if mode == "auto" and not os.path.isdir(folder):
                    txt += "  (auto)"
            self._sep_prev.config(text=f" {txt}")
        except AttributeError:
            pass  # widget not yet built

    def _refresh_dry_label(self, *_):
        if self.v_dry_run.get():
            self._lbl_dry.config(text="⚠  DRY RUN — no files will be modified")
        else:
            self._lbl_dry.config(text="")

    # ─── Log helpers ──────────────────────────────────────────────────────────
    def _write_log(self, msg, tag="info"):
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n", tag)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _set_status(self, msg): self.v_status.set(msg)

    # ─── Save / Load Config ───────────────────────────────────────────────────
    def _collect_config_dict(self):
        return {
            "folder": self.v_folder.get(), "ch_json": self.v_ch_json.get(),
            "vol_json": self.v_vol_json.get(), "date_json": self.v_date_json.get(),
            "workers": self.v_workers.get(), "dry_run": self.v_dry_run.get(),
            "use_vol": self.v_use_vol.get(), "use_vol_date": self.v_use_vol_date.get(),
            "use_vol_summ": self.v_use_vol_summ.get(),
            "prefix_mode": self.v_prefix_mode.get(), "custom_pfx": self.v_custom_pfx.get(),
            "post_finale": self.v_post_finale.get(),
            "csep_on": self.v_csep_on.get(), "csep": self.v_csep.get(),
            "zero_pad": self.v_zero_pad.get(), "pad_width": self.v_pad_width.get(),
            "series": self.v_series.get(), "writer": self.v_writer.get(),
            "penciller": self.v_penciller.get(), "publisher": self.v_publisher.get(),
            "language": self.v_language.get(), "alt_series": self.v_alt_series.get(),
            "web": self.v_web.get(), "genre": self.v_genre.get(),
            "rating": self.v_rating.get(), "year": self.v_year.get(),
            "month": self.v_month.get(), "day": self.v_day.get(),
            "count": self.v_count.get(),
            "summary": self._summary_text.get("1.0", "end-1c"),
            "volume_rules": self._vol_rules.get_rows(),
            "date_rules":   self._date_rules.get_rows(),
            "summ_rules":   self._summ_rules.get_rows(),
            "mode":         self.v_mode.get(),
            "custom_fields": self._custom_fields.get_rows(),
        }

    def _apply_config_dict(self, c):
        self.v_folder.set(c.get("folder",""));      self.v_ch_json.set(c.get("ch_json",""))
        self.v_vol_json.set(c.get("vol_json",""));  self.v_date_json.set(c.get("date_json",""))
        self.v_workers.set(c.get("workers",4));     self.v_dry_run.set(c.get("dry_run",False))
        self.v_use_vol.set(c.get("use_vol",True));  self.v_use_vol_date.set(c.get("use_vol_date",True))
        self.v_use_vol_summ.set(c.get("use_vol_summ",True))
        self.v_prefix_mode.set(c.get("prefix_mode","auto"))
        self.v_custom_pfx.set(c.get("custom_pfx","Break"))
        self.v_post_finale.set(c.get("post_finale","strip"))
        self.v_csep_on.set(c.get("csep_on",False)); self.v_csep.set(c.get("csep","..."))
        self.v_zero_pad.set(c.get("zero_pad",False)); self.v_pad_width.set(c.get("pad_width",2))
        self.v_series.set(c.get("series",""));     self.v_writer.set(c.get("writer",""))
        self.v_penciller.set(c.get("penciller","")); self.v_publisher.set(c.get("publisher",""))
        self.v_language.set(c.get("language","en")); self.v_alt_series.set(c.get("alt_series",""))
        self.v_web.set(c.get("web",""));            self.v_genre.set(c.get("genre",""))
        self.v_rating.set(c.get("rating",""));      self.v_year.set(c.get("year",""))
        self.v_month.set(c.get("month",""));        self.v_day.set(c.get("day",""))
        self.v_count.set(c.get("count",""))
        self._summary_text.delete("1.0","end")
        self._summary_text.insert("1.0", c.get("summary",""))
        self._vol_rules.set_rows(c.get("volume_rules",[]))
        self._date_rules.set_rows(c.get("date_rules",[]))
        self._summ_rules.set_rows(c.get("summ_rules",[]))
        # Restore mode without triggering checkbox side-effect
        try: self.v_mode.set(c.get("mode", "manga"))
        except AttributeError: pass
        try: self._custom_fields.set_rows(c.get("custom_fields", []))
        except AttributeError: pass

    def _save_config(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json", title="Save Config",
            initialfile=self._generate_config_filename(),
            filetypes=[("JSON Config","*.json"),("All","*.*")])
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._collect_config_dict(), f, indent=2, ensure_ascii=False)
            self._set_status(f"Config saved: {os.path.basename(path)}")
            messagebox.showinfo("Saved", f"Config saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def _load_config(self):
        path = filedialog.askopenfilename(
            title="Load Config", filetypes=[("JSON Config","*.json"),("All","*.*")])
        if not path: return
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            self._apply_config_dict(cfg)
            self._set_status(f"Config loaded: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    # ─── Mode selector ───────────────────────────────────────────────────────
    def _on_mode_change(self, *_):
        """Toggle Volume Metadata defaults when manga/manhwa mode changes."""
        is_manga = self.v_mode.get() == "manga"
        for var in (self.v_use_vol, self.v_use_vol_date, self.v_use_vol_summ):
            var.set(is_manga)

    # ─── Autosave ─────────────────────────────────────────────────────────────
    def _autosave(self):
        """Silently save all current settings to the autosave file."""
        try:
            cfg = self._collect_config_dict()
            cfg["mode"] = self.v_mode.get()
            with open(AUTOSAVE_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # never crash on autosave

    def _load_autosave(self):
        """Silently restore last session from the autosave file if it exists."""
        if not os.path.exists(AUTOSAVE_PATH):
            return
        try:
            with open(AUTOSAVE_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            self._apply_config_dict(cfg)
            # Restore mode without triggering the toggle side-effect
            saved_mode = cfg.get("mode", "manga")
            self.v_mode.set(saved_mode)
            self._set_status("Session restored from last run.")
        except Exception:
            pass  # corrupt/missing autosave — start fresh silently

    def _on_close(self):
        """Save state then destroy window."""
        self._autosave()
        self.root.destroy()

    # ─── Smart config filename ───────────────────────────────────────────────
    def _generate_config_filename(self):
        """Suggest a filename from folder path, falling back to Series field."""
        source = ""
        try:
            folder = self.v_folder.get().strip()
            if folder:
                source = os.path.basename(os.path.normpath(folder))
        except Exception:
            pass
        if not source:
            try: source = self.v_series.get().strip()
            except Exception: pass
        if not source:
            return "metadata_gui.json"
        has_vol = bool(re.search(r"\(vol", source, re.I))
        name = re.sub(r"\s*\([^)]*\)", "", source).strip()
        name = re.sub(r"[^\w\s]", "", name)
        name = re.sub(r"[\s\-]+", "_", name).lower().strip("_")
        if not name:
            return "metadata_gui.json"
        suffix = "_vol_metadata_gui" if has_vol else "_metadata_gui"
        return name + suffix + ".json"
    # ─── Reset all ────────────────────────────────────────────────────────────
    def _reset_all(self):
        """Prompt then reset every field to defaults."""
        if not messagebox.askyesno(
            "Reset All",
            "Clear ALL settings, metadata, paths and rules?\n\n"
            "This cannot be undone.",
            icon="warning"
        ):
            return
        # Paths
        for v in (self.v_folder, self.v_ch_json, self.v_vol_json, self.v_date_json):
            v.set("")
        # Config
        self.v_workers.set(4);        self.v_dry_run.set(False)
        self.v_use_vol.set(True);     self.v_use_vol_date.set(True)
        self.v_use_vol_summ.set(True)
        self.v_prefix_mode.set("auto"); self.v_custom_pfx.set("Break")
        self.v_post_finale.set("strip")
        self.v_csep_on.set(False);    self.v_csep.set("...")
        self.v_zero_pad.set(False);   self.v_pad_width.set(2)
        self.v_mode.set("manga")
        # Metadata
        for v in (self.v_series, self.v_writer, self.v_penciller, self.v_publisher,
                  self.v_alt_series, self.v_web, self.v_genre, self.v_rating,
                  self.v_year, self.v_month, self.v_day, self.v_count):
            v.set("")
        self.v_language.set("en")
        self._summary_text.delete("1.0", "end")
        # Rules
        self._vol_rules.set_rows([])
        self._date_rules.set_rows([])
        self._summ_rules.set_rows([])
        try: self._custom_fields.set_rows([])
        except AttributeError: pass
        self._set_status("Reset to defaults.")

    # ─── Import metadata (.py or .json) ──────────────────────────────────────
    def _import_metadata(self):
        """Import metadata from a .py config file or a .json metadata file."""
        path = filedialog.askopenfilename(
            title="Import Metadata",
            filetypes=[
                ("Supported files", "*.py *.json"),
                ("Python scripts",  "*.py"),
                ("JSON files",      "*.json"),
                ("All files",       "*.*"),
            ])
        if not path:
            return

        ext = os.path.splitext(path)[1].lower()

        if ext == ".json":
            self._import_metadata_json(path)
        else:
            self._import_metadata_py(path)

    def _import_metadata_json(self, path):
        """Import from a JSON file — either our config save or a metadata-only dict."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Import Error", f"Could not read JSON:\n{e}"); return

        # Detect format: our config save has "folder" / "series" keys at top level
        is_full_config = any(k in data for k in ("folder", "workers", "prefix_mode"))

        if is_full_config:
            self._apply_config_dict(data)
            self._set_status(f"Config loaded: {os.path.basename(path)}")
            # Build detailed report
            imported = []
            meta_keys = ["series","writer","penciller","publisher","language",
                         "alt_series","web","genre","rating","year","month","day","count"]
            for k in meta_keys:
                if data.get(k): imported.append(k.replace("_"," ").title())
            if data.get("summary"): imported.append("Summary")
            if data.get("folder"):  imported.append("Folder path")
            for k,lbl in [("ch_json","Chapter Titles JSON"),("vol_json","Volume Titles JSON"),
                          ("date_json","Episode Dates JSON")]:
                if data.get(k): imported.append(lbl)
            if data.get("volume_rules"):  imported.append(f"Volume Rules ({len(data['volume_rules'])} rows)")
            if data.get("date_rules"):    imported.append(f"Date Rules ({len(data['date_rules'])} rows)")
            if data.get("summ_rules"):    imported.append(f"Summary Rules ({len(data['summ_rules'])} rows)")
            if data.get("custom_fields"): imported.append(f"Custom Fields ({len(data['custom_fields'])} rows)")
            ok = "\n  ✓ ".join([""] + imported) if imported else "  (nothing)"
            messagebox.showinfo("Import Complete",
                f"Full config imported from {os.path.basename(path)}:{ok}")
            return

        # Treat as metadata-only JSON (CONSTANT_METADATA-shaped dict or similar)
        imported, skipped = [], []
        KEY_MAP = {
            "Series":          (self.v_series,     "Series"),
            "Writer":          (self.v_writer,      "Writer"),
            "Penciller":       (self.v_penciller,   "Penciller"),
            "Publisher":       (self.v_publisher,   "Publisher"),
            "LanguageISO":     (self.v_language,    "LanguageISO"),
            "AlternateSeries": (self.v_alt_series,  "AlternateSeries"),
            "Web":             (self.v_web,         "Web"),
            "Genre":           (self.v_genre,       "Genre"),
            "Rating":          (self.v_rating,      "Rating"),
            "Year":            (self.v_year,        "Year"),
            "Month":           (self.v_month,       "Month"),
            "Day":             (self.v_day,         "Day"),
            "Count":           (self.v_count,       "Count"),
        }
        for key, (var, label) in KEY_MAP.items():
            val = data.get(key)
            if val is not None:
                var.set(str(val)); imported.append(key)
            else:
                skipped.append(key)

        if "Summary" in data:
            self._summary_text.delete("1.0","end")
            self._summary_text.insert("1.0", str(data["Summary"]).strip())
            imported.append("Summary")

        fname = os.path.basename(path)
        ok  = "\n  ✓ ".join([""] + imported) if imported else "  (nothing)"
        msg = f"Imported from {fname}:{ok}"
        if skipped: msg += f"\n\nNot found: {', '.join(skipped)}"
        self._set_status(f"Imported: {fname}")
        messagebox.showinfo("Import Complete", msg)

    def _import_metadata_py(self, path):
        """Import metadata from an original .py config script."""
        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location("_ci_import_", path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            messagebox.showerror("Import Error", f"Could not load .py file:\n{e}"); return

        imported, skipped = [], []

        # CONSTANT_METADATA
        cm = getattr(mod, "CONSTANT_METADATA", None)
        if isinstance(cm, dict):
            KEY_MAP = {
                "Series":(self.v_series,"Series"), "Writer":(self.v_writer,"Writer"),
                "Penciller":(self.v_penciller,"Penciller"), "Publisher":(self.v_publisher,"Publisher"),
                "LanguageISO":(self.v_language,"LanguageISO"), "AlternateSeries":(self.v_alt_series,"AlternateSeries"),
                "Web":(self.v_web,"Web"), "Genre":(self.v_genre,"Genre"),
                "Rating":(self.v_rating,"Rating"), "Year":(self.v_year,"Year"),
                "Month":(self.v_month,"Month"), "Day":(self.v_day,"Day"),
                "Count":(self.v_count,"Count"),
            }
            for key,(var,lbl) in KEY_MAP.items():
                val = cm.get(key)
                if val is not None: var.set(str(val)); imported.append(key)
        else:
            skipped.append("CONSTANT_METADATA")

        summary = getattr(mod, "SUMMARY", None)
        if isinstance(summary, str):
            self._summary_text.delete("1.0","end")
            self._summary_text.insert("1.0", summary.strip()); imported.append("SUMMARY")
        else: skipped.append("SUMMARY")

        vr = getattr(mod, "VOLUME_RULES", None)
        if isinstance(vr, (list,tuple)) and vr:
            self._vol_rules.set_rows([[str(r[0]),str(r[1]),str(r[2])] for r in vr])
            imported.append(f"VOLUME_RULES ({len(vr)} rows)")
        else: skipped.append("VOLUME_RULES")

        dr = getattr(mod, "DATE_RULES", None)
        if isinstance(dr, (list,tuple)) and dr:
            self._date_rules.set_rows([[str(r[0]),str(r[1]),str(r[2]),str(r[3]),str(r[4])] for r in dr])
            imported.append(f"DATE_RULES ({len(dr)} rows)")
        else: skipped.append("DATE_RULES")

        sr = getattr(mod, "VOLUME_SUMMARY_RULES", None)
        if isinstance(sr, (list,tuple)) and sr:
            self._summ_rules.set_rows([[str(r[0]),str(r[1]),str(r[2])] for r in sr])
            imported.append(f"VOLUME_SUMMARY_RULES ({len(sr)} rows)")
        else: skipped.append("VOLUME_SUMMARY_RULES")

        for attr, var in [("FOLDER",self.v_folder),("CHAPTER_TITLE_JSON",self.v_ch_json),
                          ("VOLUME_TITLE_JSON",self.v_vol_json),("EP_OR_CH_DATE_JSON",self.v_date_json)]:
            val = getattr(mod, attr, None)
            if isinstance(val, str) and val: var.set(val); imported.append(attr)

        fname = os.path.basename(path)
        ok  = "\n  ✓ ".join([""] + imported) if imported else "  (nothing)"
        skip = "\n  — ".join([""] + skipped) if skipped else ""
        msg = f"Imported from {fname}:{ok}"
        if skipped: msg += f"\n\nNot found:{skip}"
        self._set_status(f"Imported: {fname}")
        messagebox.showinfo("Import Complete", msg)

    def _show_about(self):
        messagebox.showinfo("About",
            "ComicInfo Generator — GUI Edition\n\n"
            "Generates and embeds ComicInfo.xml metadata\n"
            "into CBZ comic archive files.\n\n"
            "All features from the original CLI script,\n"
            "plus Save/Load config and coloured log output.")

    # ─── Run controls ─────────────────────────────────────────────────────────
    def _request_stop(self):
        self._stop_event.set()
        self._btn_stop.configure(state="disabled")
        self._set_status("Stopping after current file…")

    def _start_run(self):
        if self._running:
            messagebox.showwarning("Running", "Processing is already in progress.")
            return
        folder = self.v_folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Invalid Folder", "Set a valid CBZ folder in Paths & Config.")
            self._nb.select(0); return

        cbz_files = sorted(
            [os.path.join(folder,f) for f in os.listdir(folder) if f.lower().endswith(".cbz")],
            key=lambda x: natural_key(os.path.basename(x)))
        if not cbz_files:
            messagebox.showwarning("No Files", "No .cbz files found in the selected folder.")
            return

        # Log / progress files
        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        folder_name    = os.path.basename(os.path.abspath(folder))
        progress_file  = os.path.join(log_dir, f"{folder_name}_progress.log")
        error_log_file = os.path.join(log_dir, f"{folder_name}_errors.log")

        # Resume check
        processed_files = set()
        resume_mode = False
        if os.path.exists(progress_file):
            with open(progress_file, encoding="utf-8") as f:
                processed_files = {ln.strip() for ln in f}
            if processed_files:
                ans = messagebox.askyesnocancel(
                    "Previous Session Found",
                    f"{len(processed_files)} files already processed in a previous run.\n\n"
                    "Yes  → Resume from last session\n"
                    "No   → Start fresh (clear progress)\n"
                    "Cancel → Abort")
                if ans is None: return
                if ans:
                    resume_mode = True
                    self._write_log(f"🔁  Resuming — {len(processed_files)} already done.", "dim")
                else:
                    processed_files.clear()
                    open(progress_file, "w", encoding="utf-8").close()
                    self._write_log("🆕  Starting fresh.", "dim")

        # Detect finale — prefer integer chapters (decimals are extras/specials)
        all_numbers, int_numbers = [], []
        for i, f in enumerate(cbz_files):
            m = re.search(r"\d+(?:\.\d+)?", os.path.basename(f))
            if m:
                entry = (float(m.group(0)), i, m.group(0))
                all_numbers.append(entry)
                if "." not in m.group(0):
                    int_numbers.append(entry)

        finale_index, finale_number, final_chapter_mode = None, None, "normal"
        chapter_titles = safe_json_load(self.v_ch_json.get())

        # Use integer chapters for finale detection; fall back to all if none exist
        numbers = int_numbers if int_numbers else all_numbers
        if numbers:
            max_entry = max(numbers, key=lambda x: x[0])
            _, finale_index, finale_number = max_entry

        if finale_number and chapter_titles and finale_number in chapter_titles:
            ans = messagebox.askyesno(
                "Final Chapter Detected",
                f"Chapter {finale_number} detected as the last chapter.\n\n"
                'Format as  "Final Chapter: <title>"?\n\n'
                "Yes → Final Chapter format\nNo  → Normal format")
            final_chapter_mode = "final" if ans else "normal"
            self._write_log(
                f"🏁  Final chapter {finale_number}: "
                f"{'Final Chapter format' if ans else 'normal format'}.", "dim")

        # Reset stats display
        for lbl in self._stat_lbls.values(): lbl.config(text="0", fg=TXT)
        total = len(cbz_files)
        self._pbar.configure(maximum=total, value=0)
        self._lbl_files.config(text=f"0 / {total}")
        self.v_pct.set("0%")

        # Build runtime config
        run_cfg = {
            "dry_run":          self.v_dry_run.get(),
            "use_vol":          self.v_use_vol.get(),
            "use_vol_date":     self.v_use_vol_date.get(),
            "use_vol_summ":     self.v_use_vol_summ.get(),
            "prefix_mode":      self.v_prefix_mode.get(),
            "custom_pfx":       self.v_custom_pfx.get(),
            "post_finale_mode": self.v_post_finale.get(),
            "use_csep":         self.v_csep_on.get(),
            "csep":             self.v_csep.get(),
            "zero_pad":         self.v_zero_pad.get(),
            "pad_width":        self.v_pad_width.get(),
            "series":     self.v_series.get(),   "writer":    self.v_writer.get(),
            "penciller":  self.v_penciller.get(),"publisher": self.v_publisher.get(),
            "language":   self.v_language.get(), "alt_series":self.v_alt_series.get(),
            "web":        self.v_web.get(),       "genre":     self.v_genre.get(),
            "rating":     self.v_rating.get(),    "year":      self.v_year.get(),
            "month":      self.v_month.get(),     "day":       self.v_day.get(),
            "count":      self.v_count.get(),
            "summary":       self._summary_text.get("1.0","end-1c"),
            "custom_fields": self._custom_fields.get_rows(),
            "volume_rules":  self._vol_rules.get_rows(),
            "date_rules":    self._date_rules.get_rows(),
            "summ_rules":    self._summ_rules.get_rows(),
            "chapter_titles":  chapter_titles,
            "volume_titles":   safe_json_load(self.v_vol_json.get()),
            "dates_json":      safe_json_load(self.v_date_json.get()),
            "max_workers":     self.v_workers.get(),
            "processed_files": processed_files,
            "resume_mode":     resume_mode,
            "finale_index":    finale_index,
            "finale_number":   finale_number,
            "final_ch_mode":   final_chapter_mode,
            "progress_file":   progress_file,
            "error_log_file":  error_log_file,
        }

        # Switch to Run tab and start
        self._nb.select(4)
        self._stop_event.clear()
        self._running = True
        self._btn_start.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._set_status("Processing…")
        if run_cfg["dry_run"]:
            self._write_log("━━━━━━━━  DRY RUN MODE  ━━━━━━━━  No changes will be made.", "warn")

        threading.Thread(target=self._worker, args=(cbz_files, run_cfg), daemon=True).start()
        self._poll_queue()

    # ─── Queue polling ────────────────────────────────────────────────────────
    def _poll_queue(self):
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                t = msg.get("type")
                if t == "log":
                    self._write_log(msg["text"], msg.get("tag","info"))
                elif t == "progress":
                    done, total = msg["done"], msg["total"]
                    pct = int(done / total * 100) if total else 0
                    self._pbar.configure(value=done)
                    self._lbl_files.config(text=f"{done} / {total}")
                    self.v_pct.set(f"{pct}%")
                elif t == "stats":
                    for k, v in msg["data"].items():
                        if k in self._stat_lbls:
                            col = TERR if (k=="errors" and int(v)>0) else TXT
                            self._stat_lbls[k].config(text=str(v), fg=col)
                elif t == "request":
                    self._handle_req(msg); return   # resume after dialog
                elif t == "done":
                    self._on_done(msg); return
        except queue.Empty:
            pass
        if self._running:
            self.root.after(80, self._poll_queue)

    def _handle_req(self, msg):
        if msg["kind"] == "decimal":
            dlg = DecimalDialog(self.root, msg["filename"], msg["raw_title"])
            self.root.wait_window(dlg)
            self._response = dlg.result
            self._resp_event.set()
        self.root.after(20, self._poll_queue)

    def _on_done(self, msg):
        self._running = False
        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        stats = msg.get("stats", {})
        for k, v in stats.items():
            if k in self._stat_lbls:
                col = TERR if (k=="errors" and v>0) else TGOOD if k in ("processed","xml_updated") else TXT
                self._stat_lbls[k].config(text=str(v), fg=col)
        sep = "─" * 58
        self._write_log(sep, "sep")
        ts_done = datetime.now().strftime("%H:%M:%S")
        if stats.get("errors", 0) > 0:
            self._write_log(
                f"  ⚠  Done {ts_done}  ·  {stats.get('processed',0)} processed  ·"
                f"  {stats['errors']} error(s) — check log above", "warn")
            self._set_status(f"Done — {stats['errors']} error(s).")
        else:
            self._write_log(
                f"  🎉  Done {ts_done}  ·  {stats.get('processed',0)} processed  ·"
                f"  {stats.get('renamed',0)} renamed  ·  0 errors", "ok")
            self._set_status("Done.")
        self._write_log(sep, "sep")

    # ─── Background processing worker ─────────────────────────────────────────
    def _worker(self, cbz_files, cfg):
        lock   = threading.Lock()
        stats  = {"total":0,"processed":0,"renamed":0,"rename_skipped":0,"xml_updated":0,"errors":0}

        processed_files  = cfg["processed_files"]
        resume_mode      = cfg["resume_mode"]
        progress_file    = cfg["progress_file"]
        error_log_file   = cfg["error_log_file"]
        finale_index     = cfg["finale_index"]
        finale_number    = cfg["finale_number"]
        final_ch_mode    = cfg["final_ch_mode"]
        chapter_titles   = cfg["chapter_titles"]
        UNSAFE           = self.UNSAFE_SEPS
        invalid_sep      = cfg["use_csep"] and cfg["csep"] in UNSAFE

        total_files  = len(cbz_files)
        done_count   = [0]   # mutable for inner functions

        auto_pad = detect_padding(cbz_files) if cfg["zero_pad"] else None
        if auto_pad and cfg["zero_pad"]:
            self._msg_queue.put({"type":"log","text":f"🔢  Auto-detected pad width: {auto_pad}","tag":"dim"})

        file_index_map = {os.path.basename(f): i for i, f in enumerate(cbz_files)}

        # Split into normal vs decimal
        normal_files  = [f for f in cbz_files if not re.search(r"\d+\.\d+", os.path.basename(f))]
        decimal_files = [f for f in cbz_files if     re.search(r"\d+\.\d+", os.path.basename(f))]

        ts = datetime.now().strftime("%H:%M:%S")
        sep = "─" * 58
        self._msg_queue.put({"type":"log","text":sep,"tag":"sep"})
        self._msg_queue.put({"type":"log",
            "text": f"  🚀  Started {ts}  ·  {total_files} files  ({len(normal_files)} normal · {len(decimal_files)} decimal)",
            "tag":"head"})
        self._msg_queue.put({"type":"log","text":sep,"tag":"sep"})

        def logq(text, tag="info"):
            self._msg_queue.put({"type":"log","text":text,"tag":tag})

        def mark_done(fname):
            with open(progress_file,"a",encoding="utf-8") as f: f.write(fname+"\n")

        def log_err_file(msg):
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(error_log_file,"a",encoding="utf-8") as f: f.write(f"[{ts}] {msg}\n")

        def push_progress():
            self._msg_queue.put({"type":"progress","done":done_count[0],"total":total_files})
            self._msg_queue.put({"type":"stats","data":dict(stats)})

        def process_one(path):
            if self._stop_event.is_set(): return
            file = os.path.basename(path)
            if resume_mode and file in processed_files:
                with lock: done_count[0] += 1
                push_progress(); return

            mode = detect_file_type(file)
            if self.v_verbose.get():
                logq(f"  🔍  {file}  →  {mode}", "dim")
            with lock:
                stats["total"] += 1

            try:
                num_m = re.search(r"\d+(?:\.\d+)?", file)
                if not num_m:
                    logq(f"⚠  {file}  —  no number found, skipping.", "warn")
                    return
                orig_num  = num_m.group(0)
                number    = orig_num
                is_decimal = "." in number

                # Zero-pad
                if cfg["zero_pad"] and not is_decimal:
                    try:
                        pw = auto_pad or cfg["pad_width"]
                        number = f"{int(number):0{pw}d}"
                    except ValueError: pass

                base_type = get_prefix(file, cfg["prefix_mode"], cfg["custom_pfx"])
                base      = f"{base_type.strip()} {number}"

                # Title lookup
                raw_title = (cfg["volume_titles"] if mode=="volume" else chapter_titles).get(orig_num)
                if not raw_title and is_decimal:
                    raw_title = (cfg["volume_titles"] if mode=="volume" else chapter_titles).get(number)
                if not raw_title: raw_title = extract_title_from_filename(file)
                if not raw_title: raw_title = base

                # Decimal handling — ask GUI
                xml_title_part = raw_title
                if is_decimal and mode in ("chapter","episode"):
                    self._msg_queue.put({
                        "type":"request","kind":"decimal",
                        "filename":file,"raw_title":raw_title})
                    self._resp_event.wait()
                    self._resp_event.clear()
                    xml_title_part = self._response or raw_title

                sep = get_separator(base_type, cfg["use_csep"], cfg["csep"])
                index = file_index_map.get(file)
                is_post_finale = (finale_index is not None and index is not None and index > finale_index)

                # Build XML title
                if orig_num == "0":
                    xml_title = raw_title
                elif (finale_number and orig_num == finale_number and mode == "chapter"
                      and chapter_titles and finale_number in chapter_titles):
                    xml_title = (f"Final Chapter: {xml_title_part}" if final_ch_mode=="final"
                                 else f"{base}{sep}{xml_title_part}")
                elif is_post_finale and cfg["post_finale_mode"] == "strip":
                    xml_title = xml_title_part if (is_decimal and mode in ("chapter","episode")) else raw_title
                elif raw_title == base and not (is_decimal and mode in ("chapter","episode")):
                    xml_title = base
                else:
                    xml_title = xml_title_part if (is_decimal and mode=="chapter") else f"{base}{sep}{xml_title_part}"

                # Build metadata dict
                md = {
                    "Title":xml_title, "Number":number,
                    "Series":cfg["series"], "Writer":cfg["writer"],
                    "Penciller":cfg["penciller"], "Publisher":cfg["publisher"],
                    "LanguageISO":cfg["language"], "AlternateSeries":cfg["alt_series"],
                    "Web":cfg["web"], "Genre":cfg["genre"], "Rating":cfg["rating"],
                    "Year":cfg["year"], "Month":cfg["month"], "Day":cfg["day"],
                    "Count":cfg["count"], "Summary":cfg["summary"],
                }

                # Volume
                volume = None
                if cfg["use_vol"]:
                    volume = number if mode=="volume" else find_volume(orig_num, cfg["volume_rules"])
                    if volume: md["Volume"] = volume

                # Date
                if cfg["use_vol_date"] and volume:
                    d = find_date(volume, cfg["date_rules"])
                    if d: md["Year"],md["Month"],md["Day"] = str(d[0]),str(d[1]),str(d[2])
                elif orig_num in cfg["dates_json"]:
                    try:
                        d = datetime.strptime(cfg["dates_json"][orig_num],"%b %d, %Y")
                        md["Year"],md["Month"],md["Day"] = str(d.year),str(d.month),str(d.day)
                    except: pass

                # Summary
                if mode=="chapter" and orig_num=="1":
                    md["Summary"] = cfg["summary"]
                elif cfg["use_vol_summ"] and volume:
                    md["Summary"] = find_summary(volume, cfg["summ_rules"]) or cfg["summary"]

                xml_data = prettify(build_xml(md, cfg.get("custom_fields")))
                temp = path + ".tmp"

                if cfg["dry_run"]:
                    with lock:
                        _n = done_count[0] + 1
                    ctr = f"[{_n}/{total_files}]"
                    logq(f"  ○  {ctr}  [DRY]  {file}  →  {new_name}", "warn")
                    logq(f"        XML title: {xml_title}", "dim")
                else:
                    with zipfile.ZipFile(path,"r") as zin, \
                         zipfile.ZipFile(temp,"w",compression=zipfile.ZIP_STORED) as zout:
                        for item in zin.infolist():
                            if item.filename != "ComicInfo.xml":
                                zout.writestr(item, zin.read(item.filename))
                        zout.writestr("ComicInfo.xml", xml_data)
                    os.replace(temp, path)

                # Rename
                safe_t = sanitize_filename(raw_title)
                fname_sep = f" {cfg['csep']} " if (cfg["use_csep"] and not invalid_sep) else " - "
                new_name = f"{base}.cbz" if raw_title==base else f"{base}{fname_sep}{safe_t}.cbz"
                new_path = os.path.join(os.path.dirname(path), new_name)

                if not cfg["dry_run"]:
                    if not os.path.exists(new_path):
                        os.rename(path, new_path)
                        with lock: stats["renamed"] += 1
                    else:
                        with lock: stats["rename_skipped"] += 1
                    mark_done(file)

                with lock:
                    stats["processed"]  += 1
                    stats["xml_updated"] += 1

                with lock:
                    _n = done_count[0] + 1
                ctr = f"[{_n}/{total_files}]"
                if new_name != file:
                    logq(f"  ✅  {ctr}  {file}  →  {new_name}", "ok")
                else:
                    logq(f"  ✅  {ctr}  {new_name}", "ok")

            except zipfile.BadZipFile:
                msg = f"  ❌  [{done_count[0]+1}/{total_files}]  {file}  —  Bad ZIP / corrupted CBZ"
                logq(msg,"err"); log_err_file(msg)
                with lock: stats["errors"] += 1

            except PermissionError:
                msg = f"  ❌  [{done_count[0]+1}/{total_files}]  {file}  —  Permission denied"
                logq(msg,"err"); log_err_file(msg)
                with lock: stats["errors"] += 1

            except FileNotFoundError:
                msg = f"  ❌  [{done_count[0]+1}/{total_files}]  {file}  —  File not found"
                logq(msg,"err"); log_err_file(msg)
                with lock: stats["errors"] += 1

            except Exception as e:
                msg = f"❌  {file}  →  {type(e).__name__}: {e}"
                logq(msg,"err"); log_err_file(msg)
                with lock: stats["errors"] += 1

            finally:
                with lock: done_count[0] += 1
                push_progress()

        # ── Run ────────────────────────────────────────────────────────────────
        try:
            with ThreadPoolExecutor(max_workers=cfg["max_workers"]) as ex:
                list(ex.map(process_one, normal_files))
            for f in decimal_files:
                if self._stop_event.is_set(): break
                process_one(f)
        except Exception as e:
            logq(f"⚠  Worker exception: {e}", "err")

        self._msg_queue.put({"type":"done","stats":dict(stats)})

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    try:
        root.iconbitmap("")
    except: pass
    app = ComicInfoGUI(root)
    app.run()

if __name__ == "__main__":
    main()