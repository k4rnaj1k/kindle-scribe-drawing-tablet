from __future__ import annotations

"""Cross-platform GUI for Kindle Tablet.

Provides a native-looking window with:
  - Start / Stop connection button with live status indicator
  - Connection settings (host, port, SSH credentials, mode)
  - Tablet settings (pressure curve, tilt, screen region)
  - Scrollable log output
  - Optional system-tray icon (requires pystray + Pillow)
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
from tkinter import filedialog, messagebox, scrolledtext, ttk
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

# ── optional system-tray support ─────────────────────────────────────────────
# Disabled on macOS: pystray's Darwin backend calls AppKit from a background
# thread which crashes when tkinter already owns the main thread.
# On macOS the Dock icon serves the same purpose.
try:
    if sys.platform == "darwin":
        raise ImportError("tray disabled on macOS")
    import pystray
    from PIL import Image as PILImage, ImageDraw

    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

log = logging.getLogger("kindle_tablet.gui")

# ── colour palette (matches both light and dark OS themes reasonably well) ───
CLR_BG = "#1e1e2e"
CLR_PANEL = "#2a2a3e"
CLR_ACCENT = "#7c3aed"
CLR_ACCENT_HOVER = "#6d28d9"
CLR_TEXT = "#e2e8f0"
CLR_TEXT_DIM = "#94a3b8"
CLR_GREEN = "#22c55e"
CLR_RED = "#ef4444"
CLR_YELLOW = "#eab308"
CLR_ENTRY_BG = "#1a1a2e"
CLR_BORDER = "#3f3f5a"
CLR_LOG_BG = "#0f0f1a"
CLR_LOG_FG = "#a0c4ff"


# ── queue-based logging handler so background threads can write to the GUI ───

class _QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__()
        self._q = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self._q.put(self.format(record))


# ── helper: make a simple coloured circle image for tray icon ────────────────

def _make_tray_icon(colour: str = "green") -> "PILImage.Image":  # type: ignore[name-defined]
    img = PILImage.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = (34, 197, 94) if colour == "green" else (239, 68, 68)
    draw.ellipse((4, 4, 60, 60), fill=fill)
    return img


# ─────────────────────────────────────────────────────────────────────────────
#  Main application window
# ─────────────────────────────────────────────────────────────────────────────

class KindleTabletApp(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()

        self.title("Kindle Tablet")
        self.resizable(True, True)
        self.minsize(520, 640)

        # Centre on screen
        self.update_idletasks()
        w, h = 560, 700
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._apply_theme()

        # ── state ────────────────────────────────────────────────────────────
        self._cfg: Config = load_config(CONFIG_PATH)
        self._connector: Optional[KindleConnector] = None
        self._handler: Optional[TabletHandler] = None
        self._backend = None
        self._running = False
        self._stop_evt = threading.Event()
        self._log_queue: queue.Queue = queue.Queue()
        self._tray_icon = None

        # ── install queue logging handler ────────────────────────────────────
        root_log = logging.getLogger()
        root_log.setLevel(logging.DEBUG)
        qh = _QueueHandler(self._log_queue)
        qh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                                          datefmt="%H:%M:%S"))
        root_log.addHandler(qh)

        self._build_ui()
        self._populate_fields()

        # ── poll log queue every 100 ms ───────────────────────────────────────
        self._poll_log()

        # ── window close ─────────────────────────────────────────────────────
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # macOS: set dock icon via Tk's wm_iconphoto (tk 8.6+)
        self._set_icon()

    # ── theming ──────────────────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        self.configure(bg=CLR_BG)
        style = ttk.Style(self)
        # Use a cross-platform base theme
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=CLR_BG, foreground=CLR_TEXT,
                        fieldbackground=CLR_ENTRY_BG, bordercolor=CLR_BORDER,
                        lightcolor=CLR_BORDER, darkcolor=CLR_BORDER,
                        troughcolor=CLR_PANEL, font=("Helvetica", 12))
        style.configure("TFrame", background=CLR_BG)
        style.configure("Panel.TFrame", background=CLR_PANEL,
                        relief="flat", borderwidth=1)
        style.configure("TLabel", background=CLR_BG, foreground=CLR_TEXT)
        style.configure("Dim.TLabel", background=CLR_BG, foreground=CLR_TEXT_DIM,
                        font=("Helvetica", 10))
        style.configure("Section.TLabel", background=CLR_PANEL, foreground=CLR_TEXT_DIM,
                        font=("Helvetica", 10, "bold"))
        style.configure("TEntry", fieldbackground=CLR_ENTRY_BG, foreground=CLR_TEXT,
                        insertcolor=CLR_TEXT, bordercolor=CLR_BORDER)
        style.configure("TCombobox", fieldbackground=CLR_ENTRY_BG, foreground=CLR_TEXT,
                        selectbackground=CLR_ACCENT, selectforeground=CLR_TEXT,
                        arrowcolor=CLR_TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", CLR_ENTRY_BG)])
        style.configure("TCheckbutton", background=CLR_BG, foreground=CLR_TEXT)
        style.configure("TNotebook", background=CLR_BG, bordercolor=CLR_BORDER)
        style.configure("TNotebook.Tab", background=CLR_PANEL, foreground=CLR_TEXT_DIM,
                        padding=(12, 6))
        style.map("TNotebook.Tab",
                  background=[("selected", CLR_BG)],
                  foreground=[("selected", CLR_TEXT)])
        style.configure("TScale", background=CLR_BG, troughcolor=CLR_PANEL,
                        slidercolor=CLR_ACCENT)
        style.configure("TSeparator", background=CLR_BORDER)

    # ── icon ─────────────────────────────────────────────────────────────────

    def _set_icon(self) -> None:
        """Set window icon using a simple generated image."""
        try:
            if HAS_TRAY:
                img = _make_tray_icon("red")
                from PIL import ImageTk
                self._icon_img = ImageTk.PhotoImage(img)
                self.wm_iconphoto(True, self._icon_img)
        except Exception:
            pass

    # ── build UI ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=CLR_BG)
        root.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        # ── Status bar ───────────────────────────────────────────────────────
        self._build_status_bar(root)

        # ── Tabs: Connection / Tablet ─────────────────────────────────────────
        nb = ttk.Notebook(root)
        nb.pack(fill=tk.BOTH, expand=False, pady=(12, 0))
        self._nb = nb

        conn_tab = ttk.Frame(nb, style="TFrame")
        tablet_tab = ttk.Frame(nb, style="TFrame")
        nb.add(conn_tab, text="  Connection  ")
        nb.add(tablet_tab, text="  Tablet  ")

        self._build_connection_tab(conn_tab)
        self._build_tablet_tab(tablet_tab)

        # ── Log area ─────────────────────────────────────────────────────────
        sep = ttk.Separator(root, orient="horizontal")
        sep.pack(fill=tk.X, pady=(12, 4))

        log_header = tk.Frame(root, bg=CLR_BG)
        log_header.pack(fill=tk.X)
        tk.Label(log_header, text="LOG", bg=CLR_BG, fg=CLR_TEXT_DIM,
                 font=("Helvetica", 10, "bold")).pack(side=tk.LEFT)
        tk.Button(log_header, text="Clear", bg=CLR_PANEL, fg=CLR_TEXT_DIM,
                  relief="flat", padx=6, pady=1, cursor="hand2",
                  command=self._clear_log,
                  font=("Helvetica", 9)).pack(side=tk.RIGHT)

        self._log_box = scrolledtext.ScrolledText(
            root, height=10,
            bg=CLR_LOG_BG, fg=CLR_LOG_FG,
            insertbackground=CLR_TEXT,
            relief="flat", borderwidth=0,
            font=("Courier", 10),
            state=tk.DISABLED,
        )
        self._log_box.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    def _build_status_bar(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=CLR_PANEL, padx=14, pady=10)
        bar.pack(fill=tk.X)
        bar.columnconfigure(1, weight=1)

        # Status dot
        self._status_canvas = tk.Canvas(bar, width=16, height=16,
                                        bg=CLR_PANEL, highlightthickness=0)
        self._status_canvas.grid(row=0, column=0, padx=(0, 10))
        self._dot = self._status_canvas.create_oval(2, 2, 14, 14, fill=CLR_RED, outline="")

        # Status label
        self._status_var = tk.StringVar(value="Disconnected")
        tk.Label(bar, textvariable=self._status_var,
                 bg=CLR_PANEL, fg=CLR_TEXT,
                 font=("Helvetica", 13, "bold")).grid(row=0, column=1, sticky="w")

        # Connect / Disconnect button
        self._connect_btn = tk.Button(
            bar, text="Connect",
            bg=CLR_ACCENT, fg="white", activebackground=CLR_ACCENT_HOVER,
            activeforeground="white",
            relief="flat", padx=18, pady=6,
            font=("Helvetica", 12, "bold"),
            cursor="hand2",
            command=self._toggle_connection,
        )
        self._connect_btn.grid(row=0, column=2)
        self._bind_hover(self._connect_btn, CLR_ACCENT_HOVER, CLR_ACCENT)

    def _build_connection_tab(self, parent: ttk.Frame) -> None:
        f = tk.Frame(parent, bg=CLR_BG, padx=8, pady=12)
        f.pack(fill=tk.BOTH, expand=True)

        def row(label: str, widget_factory, row_n: int, **kw):
            tk.Label(f, text=label, bg=CLR_BG, fg=CLR_TEXT_DIM,
                     font=("Helvetica", 11), anchor="e",
                     width=14).grid(row=row_n, column=0, sticky="e", pady=5, padx=(0, 8))
            w = widget_factory(f, **kw)
            w.grid(row=row_n, column=1, sticky="ew", pady=5)
            return w

        f.columnconfigure(1, weight=1)

        self._host_var = tk.StringVar()
        row("Host:", _EntryWidget, 0, textvariable=self._host_var)

        self._port_var = tk.StringVar()
        row("SSH Port:", _EntryWidget, 1, textvariable=self._port_var, width=8)

        self._user_var = tk.StringVar()
        row("Username:", _EntryWidget, 2, textvariable=self._user_var)

        self._pass_var = tk.StringVar()
        row("Password:", _EntryWidget, 3, textvariable=self._pass_var, show="•")

        # SSH Key row (entry + browse button)
        key_frame = tk.Frame(f, bg=CLR_BG)
        self._key_var = tk.StringVar()
        key_entry = _EntryWidget(key_frame, textvariable=self._key_var)
        key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        browse_btn = tk.Button(
            key_frame, text="Browse…", bg=CLR_PANEL, fg=CLR_TEXT,
            relief="flat", padx=8, pady=3, cursor="hand2",
            font=("Helvetica", 10),
            command=self._browse_key,
        )
        browse_btn.pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(f, text="SSH Key:", bg=CLR_BG, fg=CLR_TEXT_DIM,
                 font=("Helvetica", 11), anchor="e",
                 width=14).grid(row=4, column=0, sticky="e", pady=5, padx=(0, 8))
        key_frame.grid(row=4, column=1, sticky="ew", pady=5)

        # Mode combobox
        self._mode_var = tk.StringVar(value="ssh")
        mode_combo = ttk.Combobox(f, textvariable=self._mode_var,
                                  values=["ssh", "tcp"],
                                  state="readonly", width=10)
        tk.Label(f, text="Mode:", bg=CLR_BG, fg=CLR_TEXT_DIM,
                 font=("Helvetica", 11), anchor="e",
                 width=14).grid(row=5, column=0, sticky="e", pady=5, padx=(0, 8))
        mode_combo.grid(row=5, column=1, sticky="w", pady=5)

        self._clear_screen_var = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(f, text="Clear Kindle screen on connect",
                               variable=self._clear_screen_var)
        chk.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_tablet_tab(self, parent: ttk.Frame) -> None:
        f = tk.Frame(parent, bg=CLR_BG, padx=8, pady=12)
        f.pack(fill=tk.BOTH, expand=True)
        f.columnconfigure(1, weight=1)

        def lbl(text: str, row_n: int) -> None:
            tk.Label(f, text=text, bg=CLR_BG, fg=CLR_TEXT_DIM,
                     font=("Helvetica", 11), anchor="e",
                     width=18).grid(row=row_n, column=0, sticky="e", pady=5, padx=(0, 8))

        # Pressure curve slider
        self._pressure_var = tk.DoubleVar(value=0.7)
        lbl("Pressure Curve:", 0)
        pc_frame = tk.Frame(f, bg=CLR_BG)
        pc_frame.grid(row=0, column=1, sticky="ew", pady=5)
        pc_frame.columnconfigure(0, weight=1)
        ttk.Scale(pc_frame, from_=0.1, to=3.0, variable=self._pressure_var,
                  orient="horizontal",
                  command=lambda v: self._pressure_lbl_var.set(f"{float(v):.2f}")
                  ).grid(row=0, column=0, sticky="ew")
        self._pressure_lbl_var = tk.StringVar(value="0.70")
        tk.Label(pc_frame, textvariable=self._pressure_lbl_var,
                 bg=CLR_BG, fg=CLR_TEXT, width=5,
                 font=("Courier", 11)).grid(row=0, column=1, padx=(8, 0))

        # Enable tilt
        self._tilt_var = tk.BooleanVar(value=True)
        lbl("Enable Tilt:", 1)
        ttk.Checkbutton(f, variable=self._tilt_var).grid(row=1, column=1, sticky="w", pady=5)

        # Screen region (4 floats)
        lbl("Screen Region:", 2)
        region_frame = tk.Frame(f, bg=CLR_BG)
        region_frame.grid(row=2, column=1, sticky="w", pady=5)
        self._reg_vars = []
        for i, placeholder in enumerate(["x", "y", "w", "h"]):
            v = tk.StringVar()
            self._reg_vars.append(v)
            tk.Label(region_frame, text=f" {placeholder}:", bg=CLR_BG, fg=CLR_TEXT_DIM,
                     font=("Helvetica", 10)).pack(side=tk.LEFT)
            e = _EntryWidget(region_frame, textvariable=v, width=5)
            e.pack(side=tk.LEFT, padx=(2, 4))

        tk.Label(f, text="(0.0 – 1.0 fractions of screen)",
                 bg=CLR_BG, fg=CLR_TEXT_DIM,
                 font=("Helvetica", 9)).grid(row=3, column=1, sticky="w")

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _bind_hover(widget: tk.Widget, hover_bg: str, normal_bg: str) -> None:
        widget.bind("<Enter>", lambda _: widget.config(bg=hover_bg))
        widget.bind("<Leave>", lambda _: widget.config(bg=normal_bg))

    def _populate_fields(self) -> None:
        cfg = self._cfg
        self._host_var.set(cfg.kindle.host)
        self._port_var.set(str(cfg.kindle.port))
        self._user_var.set(cfg.kindle.username)
        self._pass_var.set(cfg.kindle.password)
        self._key_var.set(cfg.kindle.key_path)
        self._mode_var.set(cfg.mode)
        self._pressure_var.set(cfg.tablet.pressure_curve)
        self._pressure_lbl_var.set(f"{cfg.tablet.pressure_curve:.2f}")
        self._tilt_var.set(cfg.tablet.enable_tilt)
        rx, ry, rw, rh = cfg.tablet.screen_region
        for v, val in zip(self._reg_vars, [rx, ry, rw, rh]):
            v.set(f"{val:.2f}")

    def _collect_config(self) -> Config:
        """Read all form fields into a Config object."""
        try:
            port = int(self._port_var.get())
        except ValueError:
            port = 22
        try:
            region = tuple(float(v.get()) for v in self._reg_vars)
            if len(region) != 4:
                raise ValueError
        except ValueError:
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
        path = filedialog.askopenfilename(
            title="Select SSH private key",
            initialdir=str(Path.home() / ".ssh"),
        )
        if path:
            self._key_var.set(path)

    # ── log helpers ───────────────────────────────────────────────────────────

    def _poll_log(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.after(120, self._poll_log)

    def _append_log(self, text: str) -> None:
        self._log_box.config(state=tk.NORMAL)
        self._log_box.insert(tk.END, text + "\n")
        self._log_box.see(tk.END)
        self._log_box.config(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self._log_box.config(state=tk.NORMAL)
        self._log_box.delete("1.0", tk.END)
        self._log_box.config(state=tk.DISABLED)

    # ── status helpers ────────────────────────────────────────────────────────

    def _set_status(self, text: str, colour: str) -> None:
        self._status_var.set(text)
        self._status_canvas.itemconfig(self._dot, fill=colour)
        if HAS_TRAY and self._tray_icon:
            try:
                self._tray_icon.icon = _make_tray_icon(
                    "green" if colour == CLR_GREEN else "red"
                )
                self._tray_icon.title = f"Kindle Tablet – {text}"
            except Exception:
                pass

    # ── connection lifecycle ──────────────────────────────────────────────────

    def _toggle_connection(self) -> None:
        if self._running:
            self._do_stop()
        else:
            self._do_connect()

    def _do_connect(self) -> None:
        self._cfg = self._collect_config()
        host = self._cfg.kindle.host
        if not host:
            messagebox.showerror("Missing host", "Please enter the Kindle IP address.")
            return

        # Persist settings immediately
        try:
            save_config(self._cfg, CONFIG_PATH)
        except Exception as e:
            log.warning("Could not save config: %s", e)

        self._connect_btn.config(text="Connecting…", state=tk.DISABLED, bg=CLR_YELLOW)
        self._set_status("Connecting…", CLR_YELLOW)

        threading.Thread(target=self._connect_thread, daemon=True).start()

    def _connect_thread(self) -> None:
        cfg = self._cfg
        clear_screen = self._clear_screen_var.get()

        try:
            connector = KindleConnector(cfg)
            connector.connect()
        except Exception as e:
            self._schedule(self._on_connect_failed, str(e))
            return

        if clear_screen:
            setup_kindle_tablet_mode(connector)

        try:
            backend = create_input_backend()
        except Exception as e:
            connector.stop()
            self._schedule(self._on_connect_failed, f"Input backend error: {e}")
            return

        handler = TabletHandler(cfg, backend)
        connector.on_pen = handler.on_pen
        connector.on_control = handler.on_control

        try:
            connector.start_streaming()
            # Refresh axis limits after auto-detect
            handler._original_max_x = cfg.tablet.kindle_max_x
            handler._original_max_y = cfg.tablet.kindle_max_y
            handler._compute_mapping()
        except Exception as e:
            connector.stop()
            self._schedule(self._on_connect_failed, str(e))
            return

        self._connector = connector
        self._handler = handler
        self._backend = backend
        self._running = True
        self._stop_evt.clear()

        # Save with auto-detected values
        try:
            save_config(cfg, CONFIG_PATH)
        except Exception:
            pass

        self._schedule(self._on_connected)

    def _do_stop(self) -> None:
        self._connect_btn.config(text="Stopping…", state=tk.DISABLED, bg=CLR_YELLOW)
        self._set_status("Stopping…", CLR_YELLOW)
        threading.Thread(target=self._stop_thread, daemon=True).start()

    def _stop_thread(self) -> None:
        if self._connector:
            if self._clear_screen_var.get():
                restore_kindle(self._connector)
            self._connector.stop()
            self._connector = None
        self._running = False
        self._schedule(self._on_disconnected)

    # ── UI state callbacks (always run on main thread) ────────────────────────

    def _on_connected(self) -> None:
        host = self._cfg.kindle.host
        self._set_status(f"Connected  ·  {host}", CLR_GREEN)
        self._connect_btn.config(text="Disconnect", state=tk.NORMAL, bg=CLR_RED,
                                 activebackground="#dc2626")
        self._bind_hover(self._connect_btn, "#dc2626", CLR_RED)
        log.info("Tablet active!  Mode: %s | Pen: %s",
                 self._cfg.mode, self._cfg.pen_device or "(auto)")
        if HAS_TRAY and self._tray_icon:
            self._tray_icon.icon = _make_tray_icon("green")

    def _on_disconnected(self) -> None:
        self._set_status("Disconnected", CLR_RED)
        self._connect_btn.config(text="Connect", state=tk.NORMAL, bg=CLR_ACCENT,
                                 activebackground=CLR_ACCENT_HOVER)
        self._bind_hover(self._connect_btn, CLR_ACCENT_HOVER, CLR_ACCENT)
        if HAS_TRAY and self._tray_icon:
            self._tray_icon.icon = _make_tray_icon("red")

    def _on_connect_failed(self, reason: str) -> None:
        self._running = False
        self._set_status("Connection failed", CLR_RED)
        self._connect_btn.config(text="Connect", state=tk.NORMAL, bg=CLR_ACCENT,
                                 activebackground=CLR_ACCENT_HOVER)
        log.error("Connection failed: %s", reason)
        messagebox.showerror("Connection failed", reason)

    # ── threading helper ──────────────────────────────────────────────────────

    def _schedule(self, fn, *args) -> None:
        """Schedule a callback on the Tk main thread."""
        self.after(0, lambda: fn(*args))

    # ── window close ─────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        if self._running:
            if not messagebox.askyesno(
                "Quit?",
                "Kindle Tablet is connected.\nDisconnect and quit?",
            ):
                return
            self._do_stop()
        # Save config
        try:
            cfg = self._collect_config()
            save_config(cfg, CONFIG_PATH)
        except Exception:
            pass
        self.after(500, self.destroy)

    # ── system tray ───────────────────────────────────────────────────────────

    def setup_tray(self) -> None:
        """Create a system-tray icon (call after mainloop starts)."""
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
        icon = pystray.Icon(
            "kindle-tablet",
            icon=_make_tray_icon("red"),
            title="Kindle Tablet",
            menu=menu,
        )
        self._tray_icon = icon
        threading.Thread(target=icon.run, daemon=True).start()


# ── tiny styled entry widget ─────────────────────────────────────────────────

class _EntryWidget(tk.Entry):
    def __init__(self, parent, **kwargs):
        kwargs.setdefault("bg", CLR_ENTRY_BG)
        kwargs.setdefault("fg", CLR_TEXT)
        kwargs.setdefault("insertbackground", CLR_TEXT)
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("highlightbackground", CLR_BORDER)
        kwargs.setdefault("highlightcolor", CLR_ACCENT)
        kwargs.setdefault("font", ("Helvetica", 11))
        kwargs.setdefault("bd", 4)
        super().__init__(parent, **kwargs)


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    # On macOS make the app appear in the Dock
    if sys.platform == "darwin":
        try:
            from AppKit import NSApp, NSApplicationActivationPolicyRegular
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        except Exception:
            pass

    app = KindleTabletApp()
    if HAS_TRAY:
        app.setup_tray()
    app.mainloop()


if __name__ == "__main__":
    main()
