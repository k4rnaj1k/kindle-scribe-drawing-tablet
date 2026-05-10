from __future__ import annotations

"""Cross-platform GUI for Kindle Tablet.

Provides a polished dark-themed window with:
  - Branded header
  - Hero status card with pulsing indicator + pill connect button
  - Collapsible Connection / Tablet settings sections
  - Colour-coded scrollable log (DEBUG / INFO / WARNING / ERROR)
  - Optional system-tray icon on Windows (requires pystray + Pillow)
  - Config auto-saved on connect / window close

Run directly:
    python -m kindle_tablet.gui
Or via the installed entry-point:
    kindle-tablet-ui
"""

import logging
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .config import Config, KindleConfig, TabletConfig
from .connector import KindleConnector
from .main import (
    CONFIG_PATH,
    TabletHandler,
    create_input_backend,
    load_config,
    restore_kindle,
    save_config,
    setup_kindle_tablet_mode,
)

# ── optional system-tray (Windows only – pystray crashes on macOS main thread) ─
try:
    if sys.platform == "darwin":
        raise ImportError("tray disabled on macOS")
    import pystray
    from PIL import Image as PILImage, ImageDraw as PILDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

log = logging.getLogger("kindle_tablet.gui")

# ── palette ───────────────────────────────────────────────────────────────────
C_BG        = "#0f0f17"   # window background
C_SURFACE   = "#1a1a2e"   # card / section background
C_SURFACE2  = "#16213e"   # slightly lighter surface
C_BORDER    = "#2a2a4a"   # subtle border
C_ACCENT    = "#7c3aed"   # purple accent
C_ACCENT_HI = "#9d5cf7"   # lighter accent (hover)
C_ACCENT_LO = "#5b21b6"   # darker accent (press)
C_TEXT      = "#e2e8f0"   # primary text
C_TEXT_DIM  = "#64748b"   # secondary / label text
C_TEXT_MID  = "#94a3b8"   # mid-tone text
C_GREEN     = "#22c55e"
C_RED       = "#ef4444"
C_YELLOW    = "#f59e0b"
C_BLUE      = "#38bdf8"
C_LOG_BG    = "#080810"
C_HEADER_BG = "#13131f"

FONT_TITLE  = ("Helvetica", 22, "bold")
FONT_BODY   = ("Helvetica", 11)
FONT_LABEL  = ("Helvetica", 10)
FONT_SMALL  = ("Helvetica", 9)
FONT_MONO   = ("Courier", 10)
FONT_BTN    = ("Helvetica", 11, "bold")
FONT_SECTION= ("Helvetica", 10, "bold")


# ── queue log handler ─────────────────────────────────────────────────────────

class _QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue) -> None:
        super().__init__()
        self._q = q

    def emit(self, record: logging.LogRecord) -> None:
        self._q.put(record)


# ── tray helper ───────────────────────────────────────────────────────────────

def _make_tray_image(colour: str = "red"):
    img = PILImage.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = PILDraw.Draw(img)
    fill = (34, 197, 94) if colour == "green" else (239, 68, 68)
    d.ellipse((4, 4, 60, 60), fill=fill)
    return img


# ── pill button (Canvas-based, rounded) ───────────────────────────────────────

class PillButton(tk.Canvas):
    """A rounded-rectangle button drawn on a Canvas."""

    def __init__(self, parent, text="", command=None,
                 bg=C_ACCENT, fg=C_TEXT, hover=C_ACCENT_HI,
                 width=130, height=38, radius=19, font=FONT_BTN, **kw):
        super().__init__(parent, width=width, height=height,
                         bg=parent["bg"], highlightthickness=0,
                         cursor="hand2", **kw)
        self._bg_normal = bg
        self._bg_hover  = hover
        self._fg        = fg
        self._radius    = radius
        self._text_str  = text
        self._command   = command
        self._font      = font
        self._disabled  = False
        self._current_bg = bg

        self._draw(bg)
        self.bind("<Enter>",    self._on_enter)
        self.bind("<Leave>",    self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        # Single smooth polygon — no seams between arcs/rects on Windows.
        # Duplicate corner control points force a tight Bezier curve at each corner.
        kw.pop("outline", None)
        kw.pop("width", None)
        pts = [
            x1+r, y1,    x2-r, y1,                 # top
            x2-r, y1,    x2,   y1,    x2,   y1+r,  # top-right
            x2,   y1+r,  x2,   y2-r,               # right
            x2,   y2-r,  x2,   y2,    x2-r, y2,    # bottom-right
            x2-r, y2,    x1+r, y2,                 # bottom
            x1+r, y2,    x1,   y2,    x1,   y2-r,  # bottom-left
            x1,   y2-r,  x1,   y1+r,               # left
            x1,   y1+r,  x1,   y1,    x1+r, y1,    # top-left
        ]
        self.create_polygon(pts, smooth=True, **kw)

    def _draw(self, bg: str) -> None:
        self.delete("all")
        w, h, r = int(self["width"]), int(self["height"]), self._radius
        self._rounded_rect(1, 1, w-1, h-1, r, fill=bg)
        alpha = C_TEXT_DIM if self._disabled else self._fg
        self.create_text(w//2, h//2, text=self._text_str,
                         fill=alpha, font=self._font)

    def _on_enter(self, _=None):
        if not self._disabled:
            self._draw(self._bg_hover)

    def _on_leave(self, _=None):
        if not self._disabled:
            self._draw(self._current_bg)

    def _on_click(self, _=None):
        if not self._disabled and self._command:
            self._command()

    def config_btn(self, text=None, bg=None, disabled=None):
        if text is not None:
            self._text_str = text
        if bg is not None:
            self._current_bg = bg
            self._bg_normal  = bg
        if disabled is not None:
            self._disabled = disabled
        self._draw(self._current_bg)


# ── collapsible section ───────────────────────────────────────────────────────

class Section(tk.Frame):
    """A labelled collapsible section with an accent-line header."""

    def __init__(self, parent, title: str, expanded: bool = True, **kw):
        super().__init__(parent, bg=C_BG, **kw)

        self._expanded = expanded

        # Header row
        hdr = tk.Frame(self, bg=C_BG)
        hdr.pack(fill=tk.X, pady=(10, 0))

        # Accent left bar
        tk.Frame(hdr, bg=C_ACCENT, width=3).pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        tk.Label(hdr, text=title.upper(), bg=C_BG, fg=C_TEXT_MID,
                 font=FONT_SECTION).pack(side=tk.LEFT)

        self._arrow_var = tk.StringVar(value="▾" if expanded else "▸")
        btn = tk.Label(hdr, textvariable=self._arrow_var,
                       bg=C_BG, fg=C_TEXT_DIM, font=("Helvetica", 12),
                       cursor="hand2")
        btn.pack(side=tk.RIGHT, padx=4)
        btn.bind("<Button-1>", self._toggle)
        hdr.bind("<Button-1>", self._toggle)

        # Thin separator line
        sep = tk.Frame(self, bg=C_BORDER, height=1)
        sep.pack(fill=tk.X, pady=(4, 0))

        # Content frame
        self.content = tk.Frame(self, bg=C_BG)
        if expanded:
            self.content.pack(fill=tk.X, pady=(6, 0))

    def _toggle(self, _=None):
        self._expanded = not self._expanded
        if self._expanded:
            self.content.pack(fill=tk.X, pady=(6, 0))
            self._arrow_var.set("▾")
        else:
            self.content.forget()
            self._arrow_var.set("▸")


# ── styled entry ──────────────────────────────────────────────────────────────

class _Entry(tk.Entry):
    def __init__(self, parent, **kw):
        kw.setdefault("bg", C_SURFACE2)
        kw.setdefault("fg", C_TEXT)
        kw.setdefault("insertbackground", C_TEXT)
        kw.setdefault("relief", "flat")
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightbackground", C_BORDER)
        kw.setdefault("highlightcolor", C_ACCENT)
        kw.setdefault("font", FONT_BODY)
        kw.setdefault("bd", 6)
        super().__init__(parent, **kw)


# ── main window ───────────────────────────────────────────────────────────────

class KindleTabletApp(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("Kindle Tablet")
        self.configure(bg=C_BG)
        self.resizable(True, True)
        self.minsize(500, 660)

        w, h = 540, 760
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._apply_ttk_styles()

        # ── state ─────────────────────────────────────────────────────────────
        self._cfg: Config = load_config(CONFIG_PATH)
        self._connector: Optional[KindleConnector] = None
        self._handler = None
        self._backend = None
        self._running = False
        self._log_queue: queue.Queue = queue.Queue()
        self._tray_icon = None
        self._pulse_job = None
        self._pulse_phase = 0

        # ── logging ───────────────────────────────────────────────────────────
        root_log = logging.getLogger()
        root_log.setLevel(logging.DEBUG)
        qh = _QueueHandler(self._log_queue)
        root_log.addHandler(qh)

        self._build_ui()
        self._populate_fields()
        self._poll_log()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_window_icon()

    # ── ttk styles ────────────────────────────────────────────────────────────

    def _apply_ttk_styles(self) -> None:
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure(".",
                    background=C_BG, foreground=C_TEXT,
                    fieldbackground=C_SURFACE2,
                    bordercolor=C_BORDER,
                    lightcolor=C_BORDER, darkcolor=C_BORDER,
                    troughcolor=C_SURFACE,
                    font=FONT_BODY)
        s.configure("TFrame",      background=C_BG)
        s.configure("TLabel",      background=C_BG, foreground=C_TEXT)
        s.configure("TCheckbutton",background=C_BG, foreground=C_TEXT)
        s.configure("TScale",      background=C_BG,
                    troughcolor=C_SURFACE2, slidercolor=C_ACCENT)
        s.configure("TCombobox",
                    fieldbackground=C_SURFACE2, foreground=C_TEXT,
                    selectbackground=C_ACCENT, selectforeground=C_TEXT,
                    arrowcolor=C_TEXT_MID)
        s.map("TCombobox", fieldbackground=[("readonly", C_SURFACE2)])
        s.map("TCheckbutton",
              background=[("active", C_BG)],
              foreground=[("active", C_TEXT)])

    # ── window icon ───────────────────────────────────────────────────────────

    def _set_window_icon(self) -> None:
        try:
            if HAS_TRAY:
                from PIL import ImageTk
                self._icon_img = ImageTk.PhotoImage(_make_tray_image("red"))
                self.wm_iconphoto(True, self._icon_img)
        except Exception:
            pass

    # ── UI assembly ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Outer scroll canvas so the form can scroll on small screens
        outer = tk.Frame(self, bg=C_BG)
        outer.pack(fill=tk.BOTH, expand=True)

        # ── Header ────────────────────────────────────────────────────────────
        self._build_header(outer)

        # ── Scrollable body ───────────────────────────────────────────────────
        body_wrap = tk.Frame(outer, bg=C_BG)
        body_wrap.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(body_wrap, bg=C_BG, highlightthickness=0)
        vsb = tk.Scrollbar(body_wrap, orient="vertical", command=canvas.yview,
                           bg=C_SURFACE, troughcolor=C_BG,
                           activebackground=C_ACCENT, width=6,
                           relief="flat", bd=0)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        body = tk.Frame(canvas, bg=C_BG)
        _body_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_resize(e):
            canvas.itemconfig(_body_id, width=e.width)
        canvas.bind("<Configure>", _on_resize)

        def _on_body_resize(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        body.bind("<Configure>", _on_body_resize)

        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        pad = {"padx": 20}

        # ── Status card ───────────────────────────────────────────────────────
        self._build_status_card(body, pad)

        # ── Connection section ────────────────────────────────────────────────
        conn_sec = Section(body, "Connection", expanded=True)
        conn_sec.pack(fill=tk.X, **pad)
        self._build_connection_fields(conn_sec.content)

        # ── Tablet section ────────────────────────────────────────────────────
        tab_sec = Section(body, "Tablet", expanded=True)
        tab_sec.pack(fill=tk.X, **pad)
        self._build_tablet_fields(tab_sec.content)

        # ── Log section ───────────────────────────────────────────────────────
        self._build_log(body, pad)

        # Bottom spacer
        tk.Frame(body, bg=C_BG, height=16).pack()

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self, parent: tk.Frame) -> None:
        hdr = tk.Frame(parent, bg=C_HEADER_BG)
        hdr.pack(fill=tk.X)

        inner = tk.Frame(hdr, bg=C_HEADER_BG)
        inner.pack(padx=20, pady=14)

        # Pen glyph (Canvas drawing)
        icon_cv = tk.Canvas(inner, width=36, height=36,
                            bg=C_HEADER_BG, highlightthickness=0)
        icon_cv.pack(side=tk.LEFT, padx=(0, 12))
        _draw_pen_icon(icon_cv, 36)

        text_col = tk.Frame(inner, bg=C_HEADER_BG)
        text_col.pack(side=tk.LEFT)
        tk.Label(text_col, text="Kindle Tablet",
                 bg=C_HEADER_BG, fg=C_TEXT, font=FONT_TITLE).pack(anchor="w")
        tk.Label(text_col, text="Use your Kindle Scribe as a drawing tablet",
                 bg=C_HEADER_BG, fg=C_TEXT_DIM, font=FONT_SMALL).pack(anchor="w")

        # Thin accent underline
        tk.Frame(parent, bg=C_ACCENT, height=2).pack(fill=tk.X)

    # ── Status card ───────────────────────────────────────────────────────────

    def _build_status_card(self, parent: tk.Frame, pad: dict) -> None:
        card = tk.Frame(parent, bg=C_SURFACE, bd=0)
        card.pack(fill=tk.X, pady=(16, 0), **pad)

        inner = tk.Frame(card, bg=C_SURFACE)
        inner.pack(fill=tk.X, padx=16, pady=14)

        left = tk.Frame(inner, bg=C_SURFACE)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Dot indicator
        self._dot_cv = tk.Canvas(left, width=14, height=14,
                                 bg=C_SURFACE, highlightthickness=0)
        self._dot_cv.pack(side=tk.LEFT, padx=(0, 10))
        self._dot = self._dot_cv.create_oval(1, 1, 13, 13, fill=C_RED, outline="")

        self._status_var = tk.StringVar(value="Disconnected")
        tk.Label(left, textvariable=self._status_var,
                 bg=C_SURFACE, fg=C_TEXT,
                 font=("Helvetica", 13, "bold")).pack(side=tk.LEFT)

        # Pill connect button
        self._conn_btn = PillButton(
            inner, text="Connect",
            bg=C_ACCENT, fg=C_TEXT, hover=C_ACCENT_HI,
            width=120, height=36, radius=18, font=FONT_BTN,
            command=self._toggle_connection,
        )
        self._conn_btn.pack(side=tk.RIGHT)
        # Make the canvas bg match the card
        self._conn_btn.configure(bg=C_SURFACE)

    # ── Connection fields ─────────────────────────────────────────────────────

    def _build_connection_fields(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=1)

        def lbl(text, r, c, colspan=1):
            tk.Label(parent, text=text, bg=C_BG, fg=C_TEXT_DIM,
                     font=FONT_LABEL, anchor="w"
                     ).grid(row=r, column=c, columnspan=colspan,
                            sticky="w", padx=(0, 8), pady=(6, 1))

        def entry(var, r, c, colspan=1, **kw):
            e = _Entry(parent, textvariable=var, **kw)
            e.grid(row=r, column=c, columnspan=colspan,
                   sticky="ew", padx=(0, 8), pady=(0, 4))
            return e

        # Row 0: Host / Port / Mode labels
        lbl("Host", 0, 0)
        lbl("Port", 0, 1)
        lbl("Mode", 0, 2)

        self._host_var = tk.StringVar()
        self._port_var = tk.StringVar()
        self._mode_var = tk.StringVar(value="ssh")

        entry(self._host_var, 1, 0)
        entry(self._port_var, 1, 1, width=6)
        mode_cb = ttk.Combobox(parent, textvariable=self._mode_var,
                               values=["ssh", "tcp"],
                               state="readonly", width=6)
        mode_cb.grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=(0, 4))

        # Row 2: Username / Password labels
        lbl("Username", 2, 0)
        lbl("Password", 2, 1, colspan=2)

        self._user_var = tk.StringVar()
        self._pass_var = tk.StringVar()

        entry(self._user_var, 3, 0)
        entry(self._pass_var, 3, 1, colspan=2, show="•")

        # Row 4: SSH Key
        lbl("SSH Key", 4, 0, colspan=3)

        key_row = tk.Frame(parent, bg=C_BG)
        key_row.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        key_row.columnconfigure(0, weight=1)

        self._key_var = tk.StringVar()
        _Entry(key_row, textvariable=self._key_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))

        browse = tk.Button(
            key_row, text="Browse…",
            bg=C_SURFACE2, fg=C_TEXT_MID, activebackground=C_SURFACE,
            activeforeground=C_TEXT, relief="flat",
            padx=10, pady=4, cursor="hand2", font=FONT_LABEL,
            command=self._browse_key,
        )
        browse.grid(row=0, column=1)
        _hover(browse, C_SURFACE, C_SURFACE2)

        # Row 6: Clear screen checkbox
        self._clear_screen_var = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(parent,
                              text="Clear Kindle screen on connect",
                              variable=self._clear_screen_var)
        chk.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 2))

    # ── Tablet fields ─────────────────────────────────────────────────────────

    def _build_tablet_fields(self, parent: tk.Frame) -> None:
        parent.columnconfigure(1, weight=1)

        def lbl(text, r):
            tk.Label(parent, text=text, bg=C_BG, fg=C_TEXT_DIM,
                     font=FONT_LABEL, anchor="w"
                     ).grid(row=r, column=0, sticky="w", pady=(6, 1), padx=(0, 12))

        lbl("Pressure Curve", 0)
        pc_row = tk.Frame(parent, bg=C_BG)
        pc_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        pc_row.columnconfigure(0, weight=1)

        self._pressure_var  = tk.DoubleVar(value=0.7)
        self._pressure_lbl  = tk.StringVar(value="0.70")

        ttk.Scale(pc_row, from_=0.1, to=3.0, orient="horizontal",
                  variable=self._pressure_var,
                  command=lambda v: self._pressure_lbl.set(f"{float(v):.2f}")
                  ).grid(row=0, column=0, sticky="ew")

        tk.Label(pc_row, textvariable=self._pressure_lbl,
                 bg=C_BG, fg=C_ACCENT_HI, font=FONT_MONO, width=5
                 ).grid(row=0, column=1, padx=(10, 0))

        lbl("Enable Tilt", 2)
        self._tilt_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(parent, variable=self._tilt_var
                        ).grid(row=3, column=0, sticky="w", pady=(0, 4))

        lbl("Screen Region  (x  y  w  h,  0.0 – 1.0)", 4)
        reg_row = tk.Frame(parent, bg=C_BG)
        reg_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._reg_vars = []
        for ch in ["x", "y", "w", "h"]:
            tk.Label(reg_row, text=ch, bg=C_BG, fg=C_TEXT_DIM,
                     font=FONT_LABEL).pack(side=tk.LEFT, padx=(0, 2))
            v = tk.StringVar()
            self._reg_vars.append(v)
            _Entry(reg_row, textvariable=v, width=5).pack(side=tk.LEFT, padx=(0, 10))

    # ── Log ───────────────────────────────────────────────────────────────────

    def _build_log(self, parent: tk.Frame, pad: dict) -> None:
        sec = Section(parent, "Log", expanded=True)
        sec.pack(fill=tk.BOTH, expand=True, pady=(0, 0), **pad)

        # Clear button in section header area
        clear_btn = tk.Button(
            sec, text="✕ clear",
            bg=C_BG, fg=C_TEXT_DIM, activebackground=C_BG,
            activeforeground=C_RED, relief="flat",
            padx=4, pady=0, cursor="hand2", font=FONT_SMALL,
            command=self._clear_log,
        )
        # Place it to the right of the section header
        clear_btn.place(relx=1.0, y=14, anchor="ne", x=-4)

        self._log_box = tk.Text(
            sec.content, height=10,
            bg=C_LOG_BG, fg=C_TEXT_MID,
            insertbackground=C_TEXT,
            relief="flat", borderwidth=0,
            font=FONT_MONO,
            state=tk.DISABLED,
            wrap=tk.NONE,
        )
        # Coloured tags per log level
        self._log_box.tag_configure("DEBUG",   foreground="#475569")
        self._log_box.tag_configure("INFO",    foreground="#94a3b8")
        self._log_box.tag_configure("WARNING", foreground=C_YELLOW)
        self._log_box.tag_configure("ERROR",   foreground=C_RED)
        self._log_box.tag_configure("ts",      foreground="#334155")
        self._log_box.tag_configure("lvl_info",    foreground="#38bdf8")
        self._log_box.tag_configure("lvl_warning", foreground=C_YELLOW)
        self._log_box.tag_configure("lvl_error",   foreground=C_RED)
        self._log_box.tag_configure("lvl_debug",   foreground="#475569")

        sb = tk.Scrollbar(sec.content, command=self._log_box.yview,
                          bg=C_SURFACE, troughcolor=C_LOG_BG,
                          width=6, relief="flat", bd=0)
        self._log_box.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(4, 0))

    # ── field helpers ─────────────────────────────────────────────────────────

    def _populate_fields(self) -> None:
        c = self._cfg
        self._host_var.set(c.kindle.host)
        self._port_var.set(str(c.kindle.port))
        self._user_var.set(c.kindle.username)
        self._pass_var.set(c.kindle.password)
        self._key_var.set(c.kindle.key_path)
        self._mode_var.set(c.mode)
        self._pressure_var.set(c.tablet.pressure_curve)
        self._pressure_lbl.set(f"{c.tablet.pressure_curve:.2f}")
        self._tilt_var.set(c.tablet.enable_tilt)
        for v, val in zip(self._reg_vars, c.tablet.screen_region):
            v.set(f"{val:.2f}")

    def _collect_config(self) -> Config:
        try:
            port = int(self._port_var.get())
        except ValueError:
            port = 22
        try:
            region = tuple(float(v.get()) for v in self._reg_vars)
            assert len(region) == 4
        except Exception:
            region = (0.0, 0.0, 1.0, 1.0)
        return Config(
            kindle=KindleConfig(
                host=self._host_var.get().strip(),
                port=port,
                username=self._user_var.get().strip(),
                password=self._pass_var.get(),
                key_path=self._key_var.get().strip(),
                stream_port=self._cfg.kindle.stream_port,
            ),
            tablet=TabletConfig(
                kindle_max_x=self._cfg.tablet.kindle_max_x,
                kindle_max_y=self._cfg.tablet.kindle_max_y,
                kindle_max_pressure=self._cfg.tablet.kindle_max_pressure,
                kindle_max_tilt=self._cfg.tablet.kindle_max_tilt,
                screen_region=region,
                pressure_curve=round(self._pressure_var.get(), 3),
                enable_tilt=self._tilt_var.get(),
            ),
            mode=self._mode_var.get(),
            pen_device=self._cfg.pen_device,
        )

    def _browse_key(self) -> None:
        p = filedialog.askopenfilename(title="Select SSH private key",
                                       initialdir=str(Path.home() / ".ssh"))
        if p:
            self._key_var.set(p)

    # ── log helpers ───────────────────────────────────────────────────────────

    def _poll_log(self) -> None:
        try:
            while True:
                record = self._log_queue.get_nowait()
                self._append_log(record)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _append_log(self, record: logging.LogRecord) -> None:
        ts   = logging.Formatter(datefmt="%H:%M:%S").formatTime(record, "%H:%M:%S")
        lvl  = record.levelname
        msg  = record.getMessage()

        lvl_tag = {
            "DEBUG":   "lvl_debug",
            "INFO":    "lvl_info",
            "WARNING": "lvl_warning",
            "ERROR":   "lvl_error",
        }.get(lvl, "INFO")
        msg_tag = {
            "DEBUG":   "DEBUG",
            "INFO":    "INFO",
            "WARNING": "WARNING",
            "ERROR":   "ERROR",
        }.get(lvl, "INFO")

        self._log_box.config(state=tk.NORMAL)
        self._log_box.insert(tk.END, f"{ts} ", "ts")
        self._log_box.insert(tk.END, f"{lvl:<8}", lvl_tag)
        self._log_box.insert(tk.END, f" {msg}\n", msg_tag)
        self._log_box.see(tk.END)
        self._log_box.config(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self._log_box.config(state=tk.NORMAL)
        self._log_box.delete("1.0", tk.END)
        self._log_box.config(state=tk.DISABLED)

    # ── status / pulse animation ──────────────────────────────────────────────

    def _set_status(self, text: str, colour: str, pulse: bool = False) -> None:
        self._status_var.set(text)
        self._dot_cv.itemconfig(self._dot, fill=colour)
        self._stop_pulse()
        if pulse:
            self._start_pulse(colour)
        if HAS_TRAY and self._tray_icon:
            try:
                self._tray_icon.icon = _make_tray_image(
                    "green" if colour == C_GREEN else "red")
                self._tray_icon.title = f"Kindle Tablet – {text}"
            except Exception:
                pass

    def _start_pulse(self, colour: str) -> None:
        self._pulse_colour = colour
        self._pulse_phase  = 0
        self._pulse_tick()

    def _pulse_tick(self) -> None:
        import math
        alpha = (math.sin(self._pulse_phase) + 1) / 2        # 0 … 1
        r, g, b = _hex_to_rgb(self._pulse_colour)
        dim_r = int(r * 0.25 + r * 0.75 * alpha)
        dim_g = int(g * 0.25 + g * 0.75 * alpha)
        dim_b = int(b * 0.25 + b * 0.75 * alpha)
        colour = f"#{dim_r:02x}{dim_g:02x}{dim_b:02x}"
        self._dot_cv.itemconfig(self._dot, fill=colour)
        self._pulse_phase += 0.18
        self._pulse_job = self.after(50, self._pulse_tick)

    def _stop_pulse(self) -> None:
        if self._pulse_job:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None

    # ── connection lifecycle ──────────────────────────────────────────────────

    def _toggle_connection(self) -> None:
        if self._running:
            self._do_stop()
        else:
            self._do_connect()

    def _do_connect(self) -> None:
        self._cfg = self._collect_config()
        if not self._cfg.kindle.host:
            messagebox.showerror("Missing host", "Please enter the Kindle IP address.")
            return
        try:
            save_config(self._cfg, CONFIG_PATH)
        except Exception as e:
            log.warning("Could not save config: %s", e)

        self._conn_btn.config_btn(text="Connecting…", bg=C_YELLOW, disabled=True)
        self._set_status("Connecting…", C_YELLOW, pulse=True)
        threading.Thread(target=self._connect_thread, daemon=True).start()

    def _connect_thread(self) -> None:
        cfg = self._cfg
        try:
            connector = KindleConnector(cfg)
            connector.connect()
        except Exception as e:
            self._schedule(self._on_connect_failed, str(e))
            return

        if self._clear_screen_var.get():
            setup_kindle_tablet_mode(connector)

        try:
            backend = create_input_backend()
        except Exception as e:
            connector.stop()
            self._schedule(self._on_connect_failed, f"Input backend: {e}")
            return

        handler = TabletHandler(cfg, backend)
        connector.on_pen     = handler.on_pen
        connector.on_control = handler.on_control

        try:
            connector.start_streaming()
            # Use raw caps saved before threads start (same race fix as main.py):
            # the rotation monitor may fire on_control(CTRL_ROTATION, 90) before
            # we reach here, swapping cfg.tablet.kindle_max_x/y in-place and
            # corrupting _original_max_x/y → all landscape inputs bottom-left,
            # all portrait inputs top-left after switching back.
            if connector.raw_pen_max_x:
                handler._original_max_x = connector.raw_pen_max_x
                handler._original_max_y = connector.raw_pen_max_y
            else:
                handler._original_max_x = cfg.tablet.kindle_max_x
                handler._original_max_y = cfg.tablet.kindle_max_y
            handler._compute_mapping()
        except Exception as e:
            connector.stop()
            self._schedule(self._on_connect_failed, str(e))
            return

        self._connector = connector
        self._handler   = handler   # keep strong refs so GC never drops them
        self._backend   = backend
        self._running   = True
        try:
            save_config(cfg, CONFIG_PATH)
        except Exception:
            pass
        self._schedule(self._on_connected)

    def _do_stop(self) -> None:
        self._conn_btn.config_btn(text="Stopping…", bg=C_YELLOW, disabled=True)
        self._set_status("Stopping…", C_YELLOW, pulse=True)
        threading.Thread(target=self._stop_thread, daemon=True).start()

    def _stop_thread(self) -> None:
        if self._connector:
            if self._clear_screen_var.get():
                restore_kindle(self._connector)
            self._connector.stop()
            self._connector = None
        self._running = False
        self._schedule(self._on_disconnected)

    def _on_connected(self) -> None:
        host = self._cfg.kindle.host
        self._set_status(f"Connected  ·  {host}", C_GREEN)
        self._conn_btn.config_btn(text="Disconnect",
                                  bg=C_RED, disabled=False)
        self._conn_btn._bg_hover = "#dc2626"
        log.info("Tablet active — mode: %s | pen: %s",
                 self._cfg.mode, self._cfg.pen_device or "auto")

    def _on_disconnected(self) -> None:
        self._set_status("Disconnected", C_RED)
        self._conn_btn.config_btn(text="Connect",
                                  bg=C_ACCENT, disabled=False)
        self._conn_btn._bg_hover = C_ACCENT_HI

    def _on_connect_failed(self, reason: str) -> None:
        self._running = False
        self._set_status("Connection failed", C_RED)
        self._conn_btn.config_btn(text="Connect",
                                  bg=C_ACCENT, disabled=False)
        self._conn_btn._bg_hover = C_ACCENT_HI
        log.error("Connection failed: %s", reason)
        messagebox.showerror("Connection failed", reason)

    # ── threading util ────────────────────────────────────────────────────────

    def _schedule(self, fn, *args) -> None:
        self.after(0, lambda: fn(*args))

    # ── close ─────────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        if self._running:
            if not messagebox.askyesno("Quit?",
                                       "Kindle Tablet is connected.\nDisconnect and quit?"):
                return
            self._do_stop()
        try:
            save_config(self._collect_config(), CONFIG_PATH)
        except Exception:
            pass
        self._stop_pulse()
        self.after(500, self.destroy)

    # ── tray ──────────────────────────────────────────────────────────────────

    def setup_tray(self) -> None:
        if not HAS_TRAY:
            return

        def _show(_=None):
            self.after(0, self.deiconify)
            self.after(0, self.lift)

        def _quit(_=None):
            self.after(0, self._on_close)

        menu = pystray.Menu(
            pystray.MenuItem("Show", _show, default=True),
            pystray.MenuItem("Quit", _quit),
        )
        icon = pystray.Icon("kindle-tablet",
                            icon=_make_tray_image("red"),
                            title="Kindle Tablet", menu=menu)
        self._tray_icon = icon
        threading.Thread(target=icon.run, daemon=True).start()


# ── drawing helpers ───────────────────────────────────────────────────────────

def _draw_pen_icon(cv: tk.Canvas, size: int) -> None:
    """Draw a simple stylised pen into a Canvas."""
    import math
    s = size
    cx, cy = s * 0.55, s * 0.45
    pw, ph = s * 0.14, s * 0.58
    angle  = -38.0
    rad    = math.radians(angle)
    ca, sa = math.cos(rad), math.sin(rad)
    hw, hh = pw / 2, ph / 2

    def rot(px, py):
        return cx + px*ca - py*sa, cy + px*sa + py*ca

    body = [rot(-hw, -hh), rot(hw, -hh), rot(hw, hh), rot(-hw, hh)]
    cv.create_polygon(body, fill=C_ACCENT, outline="", smooth=False)

    tip = rot(0, hh + pw * 0.8)
    tl  = rot(-hw * 0.6, hh)
    tr  = rot(hw * 0.6, hh)
    cv.create_polygon([tl, tr, tip], fill=C_TEXT_MID, outline="")

    hl = [rot(-hw*0.1, -hh*0.85), rot(hw*0.1, -hh*0.85),
          rot(hw*0.1, hh*0.25),   rot(-hw*0.1, hh*0.25)]
    cv.create_polygon(hl, fill=C_ACCENT_HI, outline="", stipple="")


def _hex_to_rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hover(widget: tk.Widget, on: str, off: str) -> None:
    widget.bind("<Enter>", lambda _: widget.config(bg=on))
    widget.bind("<Leave>", lambda _: widget.config(bg=off))


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Surface any unhandled exceptions visually — critical on Windows where the
    # process is windowed (no console) and crashes are otherwise invisible.
    import traceback

    def _excepthook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.error("Unhandled exception:\n%s", msg)
        try:
            messagebox.showerror("Kindle Tablet — unexpected error", msg)
        except Exception:
            pass

    sys.excepthook = _excepthook

    if sys.platform == "darwin":
        try:
            from AppKit import NSApp, NSApplicationActivationPolicyRegular
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        except Exception:
            pass

    try:
        app = KindleTabletApp()
    except Exception:
        _excepthook(*sys.exc_info())
        return

    if HAS_TRAY:
        app.setup_tray()
    app.mainloop()


if __name__ == "__main__":
    main()
