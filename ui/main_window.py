"""
MainWindow: dark-themed tkinter Treeview showing live stock states.

Columns: Symbol | Signal | Price | EMA20 | EMA50 | EMA200 | ATR | Dist/ATR | Last Update
Row colors:
  green  — TRENDING↑
  red    — TRENDING↓
  yellow — PULLBACK↑ / PULLBACK↓
  grey   — NO_TREND
"""
import queue
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Any, Callable, Dict, Optional

from scanner import Alert, SignalState, TrendDirection

# Dark theme colors
BG_DARK = "#1e1e1e"
BG_PANEL = "#252526"
BG_ROW_ODD = "#2a2a2a"
BG_ROW_EVEN = "#252526"
FG_DEFAULT = "#cccccc"
FG_DIM = "#888888"
ACCENT = "#3a7bd5"

ROW_TAG_BULLISH = "bullish"
ROW_TAG_BEARISH = "bearish"
ROW_TAG_PULLBACK = "pullback"
ROW_TAG_NOTRENDL = "notrend"


def _format_signal(state: str, direction: str) -> str:
    """Combine state + direction into a single display string."""
    s = state.upper()
    d = direction.lower()
    if s == "TRENDING":
        return "TRENDING↑" if d == "bullish" else "TRENDING↓"
    if s == "PULLBACK":
        return "PULLBACK↑" if d == "bullish" else "PULLBACK↓"
    return "NO_TREND"

COLUMNS = ("symbol", "signal", "price", "ema20", "ema50", "ema200", "atr", "dist_atr", "updated")
COL_HEADERS = {
    "symbol":  "Symbol",
    "signal":  "Signal",
    "price":   "Price",
    "ema20":   "EMA20",
    "ema50":   "EMA50",
    "ema200":  "EMA200",
    "atr":     "ATR(14)",
    "dist_atr":"Dist/ATR",
    "updated": "Updated",
}
COL_WIDTHS = {
    "symbol":  80,
    "signal":  110,
    "price":   80,
    "ema20":   80,
    "ema50":   80,
    "ema200":  80,
    "atr":     70,
    "dist_atr":80,
    "updated": 140,
}


class MainWindow:
    """Dark-themed main application window."""

    def __init__(
        self,
        root: tk.Tk,
        alert_queue: queue.Queue,
        config,
        on_settings: Optional[Callable] = None,
        on_add_symbol: Optional[Callable[[str], None]] = None,
        on_remove_symbol: Optional[Callable[[str], None]] = None,
        refresh_interval_ms: int = 2000,
    ):
        self.root = root
        self._queue = alert_queue
        self.config = config
        self._on_settings = on_settings
        self._on_add_symbol = on_add_symbol
        self._on_remove_symbol = on_remove_symbol
        self._refresh_ms = refresh_interval_ms

        # {symbol: dict of column values}
        self._rows: Dict[str, Dict[str, Any]] = {}

        root.title("Trading Assistant")
        root.configure(bg=BG_DARK)
        root.minsize(900, 400)

        self._build_ui()
        self._populate_initial_rows()
        self._schedule_poll()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_toolbar()
        self._build_table()
        self._build_statusbar()

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self.root, bg=BG_PANEL, height=40)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.pack_propagate(False)

        title = tk.Label(bar, text="Trading Assistant  v1.0.0", bg=BG_PANEL, fg="white",
                         font=("Helvetica", 13, "bold"))
        title.pack(side=tk.LEFT, padx=14)

        # Market hours indicator
        self._market_label = tk.Label(bar, text="● Market: checking...",
                                      bg=BG_PANEL, fg=FG_DIM, font=("Helvetica", 10))
        self._market_label.pack(side=tk.LEFT, padx=10)

        # Right-side buttons
        self._btn_settings = self._make_btn(bar, "⚙ Settings", self._open_settings)
        self._btn_settings.pack(side=tk.RIGHT, padx=6, pady=6)

        self._btn_remove = self._make_btn(bar, "− Remove", self._remove_selected, bg="#c0392b")
        self._btn_remove.pack(side=tk.RIGHT, padx=2, pady=6)

        self._btn_add = self._make_btn(bar, "+ Add", self._add_symbol_dialog, bg="#27ae60")
        self._btn_add.pack(side=tk.RIGHT, padx=2, pady=6)

    def _make_btn(self, parent, text: str, command, bg: str = ACCENT) -> tk.Label:
        """
        Use a tk.Label as a button — reliable colors on macOS where tk.Button
        ignores bg/fg in native rendering mode.
        """
        lbl = tk.Label(
            parent, text=text, command=command if False else None,
            bg=bg, fg="white",
            font=("Helvetica", 10),
            cursor="hand2",
            padx=10, pady=4,
            relief=tk.FLAT,
        )
        lbl.bind("<Button-1>", lambda e: command())
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=self._darken(bg)))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=bg))
        lbl._bg = bg
        return lbl

    @staticmethod
    def _darken(hex_color: str) -> str:
        """Return a slightly darker shade of a hex color."""
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r, g, b = max(0, r - 30), max(0, g - 30), max(0, b - 30)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _build_table(self) -> None:
        frame = tk.Frame(self.root, bg=BG_DARK)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 0))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.Treeview",
                        background=BG_ROW_ODD,
                        foreground=FG_DEFAULT,
                        fieldbackground=BG_ROW_ODD,
                        borderwidth=0,
                        rowheight=26,
                        font=("Courier", 10))
        style.configure("Dark.Treeview.Heading",
                        background=BG_PANEL,
                        foreground=FG_DEFAULT,
                        relief=tk.FLAT,
                        font=("Helvetica", 10, "bold"))
        style.map("Dark.Treeview",
                  background=[("selected", "#3a7bd5")],
                  foreground=[("selected", "white")])

        self._tree = ttk.Treeview(
            frame,
            columns=COLUMNS,
            show="headings",
            style="Dark.Treeview",
            selectmode="browse",
        )

        for col in COLUMNS:
            self._tree.heading(col, text=COL_HEADERS[col])
            self._tree.column(col, width=COL_WIDTHS[col], anchor=tk.CENTER, stretch=False)

        # Symbol column left-aligned
        self._tree.column("symbol", anchor=tk.W)
        self._tree.column("updated", anchor=tk.W, width=150, stretch=True)

        # Row tags for coloring
        self._tree.tag_configure(ROW_TAG_BULLISH, foreground="#2ecc71")
        self._tree.tag_configure(ROW_TAG_BEARISH, foreground="#e74c3c")
        self._tree.tag_configure(ROW_TAG_PULLBACK, foreground="#f1c40f")
        self._tree.tag_configure(ROW_TAG_NOTRENDL, foreground=FG_DIM)

        # Scrollbars
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._tree.yview)
        hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        # Drag-to-reorder
        self._drag_item: Optional[str] = None
        self._tree.bind("<ButtonPress-1>",   self._drag_start)
        self._tree.bind("<B1-Motion>",        self._drag_motion)
        self._tree.bind("<ButtonRelease-1>",  self._drag_end)

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=BG_PANEL, height=24)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)

        self._status_label = tk.Label(
            bar, text="Ready", bg=BG_PANEL, fg=FG_DIM, font=("Helvetica", 9), anchor="w"
        )
        self._status_label.pack(side=tk.LEFT, padx=8)

        self._alert_count_label = tk.Label(
            bar, text="Alerts: 0", bg=BG_PANEL, fg=FG_DIM, font=("Helvetica", 9), anchor="e"
        )
        self._alert_count_label.pack(side=tk.RIGHT, padx=8)
        self._alert_count = 0

    # ── Initial population ────────────────────────────────────────────────────

    def _populate_initial_rows(self) -> None:
        for symbol in self.config.symbols:
            self._ensure_row(symbol)

    def _ensure_row(self, symbol: str) -> None:
        if symbol not in self._rows:
            self._rows[symbol] = {
                "symbol":   symbol,
                "signal":   "—",
                "price":    "—",
                "ema20":    "—",
                "ema50":    "—",
                "ema200":   "—",
                "atr":      "—",
                "dist_atr": "—",
                "updated":  "—",
            }
            self._tree.insert("", tk.END, iid=symbol, values=self._row_values(symbol),
                              tags=(ROW_TAG_NOTRENDL,))

    def _row_values(self, symbol: str) -> tuple:
        r = self._rows[symbol]
        return tuple(r[c] for c in COLUMNS)

    # ── Queue polling ─────────────────────────────────────────────────────────

    def _schedule_poll(self) -> None:
        self.root.after(self._refresh_ms, self._poll_queue)

    def _poll_queue(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                if isinstance(item, Alert):
                    self._handle_alert(item)
                elif isinstance(item, dict):
                    self._handle_status(item)
        except queue.Empty:
            pass
        finally:
            self._schedule_poll()

    def _handle_alert(self, alert: Alert) -> None:
        symbol = alert.symbol
        self._ensure_row(symbol)

        self._rows[symbol].update({
            "signal":   _format_signal(alert.state.name, alert.direction.value),
            "price":    f"{alert.price:.4f}",
            "ema20":    f"{alert.ema_fast:.4f}",
            "ema50":    f"{alert.ema_mid:.4f}",
            "ema200":   f"{alert.ema_slow:.4f}",
            "atr":      f"{alert.atr:.4f}",
            "dist_atr": f"{alert.distance_ratio:.3f}",
            "updated":  alert.timestamp.strftime("%H:%M:%S"),
        })

        tag = self._alert_tag(alert)
        self._tree.item(symbol, values=self._row_values(symbol), tags=(tag,))

        self._alert_count += 1
        self._alert_count_label.configure(text=f"Alerts: {self._alert_count}")
        signal_str = _format_signal(alert.state.name, alert.direction.value)
        self._status_label.configure(
            text=f"[{datetime.now().strftime('%H:%M:%S')}] {symbol}: {signal_str}"
        )

    def _handle_status(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "market_status":
            open_ = msg.get("open", False)
            text = "● Market: OPEN" if open_ else "○ Market: CLOSED"
            color = "#2ecc71" if open_ else "#e74c3c"
            self._market_label.configure(text=text, fg=color)
        elif kind == "scan_progress":
            done = msg.get("done", 0)
            total = msg.get("total", 0)
            symbol = msg.get("symbol", "")
            self._status_label.configure(
                text=f"[{datetime.now().strftime('%H:%M:%S')}] Scanning {symbol}… ({done}/{total})"
            )
        elif kind == "scan_complete":
            total = msg.get("total", 0)
            ts = msg.get("timestamp", datetime.now().strftime("%H:%M:%S"))
            self._status_label.configure(
                text=f"[{ts}] Scan complete — {total} symbol{'s' if total != 1 else ''} updated"
            )
        elif kind == "scan_status":
            # legacy fallback
            symbol = msg.get("symbol", "")
            status = msg.get("status", "")
            self._status_label.configure(
                text=f"[{datetime.now().strftime('%H:%M:%S')}] Scanning {symbol}… {status}"
            )
        elif kind == "indicator_update":
            self._handle_indicator_update(msg)

    def _handle_indicator_update(self, msg: dict) -> None:
        symbol = msg["symbol"]
        self._ensure_row(symbol)
        state = msg.get("signal_state", "NO_TREND")
        direction = msg.get("direction", "none")
        self._rows[symbol].update({
            "signal":   _format_signal(state, direction),
            "price":    f"{msg['price']:.4f}",
            "ema20":    f"{msg['ema_fast']:.4f}",
            "ema50":    f"{msg['ema_mid']:.4f}",
            "ema200":   f"{msg['ema_slow']:.4f}",
            "atr":      f"{msg['atr']:.4f}",
            "dist_atr": f"{msg['dist_atr']:.3f}",
            "updated":  datetime.now().strftime("%H:%M:%S"),
        })
        if state == "PULLBACK":
            tag = ROW_TAG_PULLBACK
        elif direction == "bullish":
            tag = ROW_TAG_BULLISH
        elif direction == "bearish":
            tag = ROW_TAG_BEARISH
        else:
            tag = ROW_TAG_NOTRENDL
        self._tree.item(symbol, values=self._row_values(symbol), tags=(tag,))

    @staticmethod
    def _alert_tag(alert: Alert) -> str:
        if alert.state == SignalState.PULLBACK:
            return ROW_TAG_PULLBACK
        if alert.direction == TrendDirection.BULLISH:
            return ROW_TAG_BULLISH
        return ROW_TAG_BEARISH

    # ── Drag-to-reorder ───────────────────────────────────────────────────────

    def _drag_start(self, event: tk.Event) -> None:
        item = self._tree.identify_row(event.y)
        if item:
            self._drag_item = item

    def _drag_motion(self, event: tk.Event) -> None:
        if not self._drag_item:
            return
        target = self._tree.identify_row(event.y)
        if not target or target == self._drag_item:
            return
        # Determine insertion position: above or below target based on cursor position
        bbox = self._tree.bbox(target)
        if not bbox:
            return
        # Insert above target when cursor is in upper half, else insert after target
        if event.y < bbox[1] + bbox[3] // 2:
            self._tree.move(self._drag_item, "", self._tree.index(target))
        else:
            self._tree.move(self._drag_item, "", self._tree.index(target) + 1)

    def _drag_end(self, event: tk.Event) -> None:
        if not self._drag_item:
            return
        self._drag_item = None
        # Persist new order to config
        new_order = list(self._tree.get_children())
        self.config.symbols = new_order

    # ── Market status update (called from main thread) ────────────────────────

    def update_market_status(self, is_open: bool) -> None:
        text = "● Market: OPEN" if is_open else "○ Market: CLOSED"
        color = "#2ecc71" if is_open else "#e74c3c"
        self._market_label.configure(text=text, fg=color)

    # ── Symbol management ─────────────────────────────────────────────────────

    def _add_symbol_dialog(self) -> None:
        dialog = _InputDialog(self.root, title="Add Symbol", prompt="Enter ticker symbol:")
        symbol = dialog.result
        if symbol:
            symbol = symbol.strip().upper()
            if symbol and symbol not in self._rows:
                self._ensure_row(symbol)
                if self._on_add_symbol:
                    self._on_add_symbol(symbol)
                self._status_label.configure(text=f"Added {symbol} to watch list")

    def _remove_selected(self) -> None:
        selected = self._tree.selection()
        if not selected:
            return
        symbol = selected[0]
        self._tree.delete(symbol)
        self._rows.pop(symbol, None)
        if self._on_remove_symbol:
            self._on_remove_symbol(symbol)
        self._status_label.configure(text=f"Removed {symbol} from watch list")

    def _open_settings(self) -> None:
        if self._on_settings:
            self._on_settings()

    def refresh_symbols(self) -> None:
        """Re-sync rows with config after settings change."""
        config_symbols = set(self.config.symbols)
        current_symbols = set(self._rows.keys())

        for sym in config_symbols - current_symbols:
            self._ensure_row(sym)
        for sym in current_symbols - config_symbols:
            self._tree.delete(sym)
            self._rows.pop(sym, None)

    def set_status(self, text: str) -> None:
        self._status_label.configure(text=text)


# ── Simple input dialog ───────────────────────────────────────────────────────

class _InputDialog(tk.Toplevel):
    def __init__(self, parent, title: str, prompt: str):
        super().__init__(parent)
        self.title(title)
        self.result: Optional[str] = None
        self.resizable(False, False)
        self.configure(bg="#1e1e1e")
        self.grab_set()

        tk.Label(self, text=prompt, bg="#1e1e1e", fg="#cccccc",
                 font=("Helvetica", 10)).pack(padx=20, pady=(16, 4))

        self._var = tk.StringVar()
        entry = tk.Entry(self, textvariable=self._var, bg="#2d2d2d", fg="white",
                         insertbackground="white", relief=tk.FLAT,
                         highlightbackground="#555555", highlightthickness=1,
                         font=("Courier", 12), width=16)
        entry.pack(padx=20, pady=4)
        entry.bind("<Return>", lambda _: self._ok())
        entry.focus_set()

        btn_frame = tk.Frame(self, bg="#1e1e1e")
        btn_frame.pack(pady=12)

        def _make_dlg_btn(parent, text, command, bg):
            lbl = tk.Label(parent, text=text, bg=bg, fg="white",
                           font=("Helvetica", 10), cursor="hand2",
                           padx=12, pady=4, relief=tk.FLAT)
            lbl.bind("<Button-1>", lambda e: command())
            lbl.bind("<Enter>", lambda e: lbl.configure(bg=_darken(bg)))
            lbl.bind("<Leave>", lambda e: lbl.configure(bg=bg))
            return lbl

        def _darken(hex_color):
            h = hex_color.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"#{max(0,r-30):02x}{max(0,g-30):02x}{max(0,b-30):02x}"

        _make_dlg_btn(btn_frame, "Add", self._ok, "#3a7bd5").pack(side=tk.LEFT, padx=4)
        _make_dlg_btn(btn_frame, "Cancel", self.destroy, "#555555").pack(side=tk.LEFT, padx=4)

        # Center
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")
        self.wait_window()

    def _ok(self) -> None:
        self.result = self._var.get()
        self.destroy()
