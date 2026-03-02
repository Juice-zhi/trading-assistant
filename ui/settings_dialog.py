"""
SettingsDialog: tabbed modal for all user-configurable settings.
Tabs: Symbols | Indicators | Discord
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

from config import ConfigManager
from notifier import DiscordNotifier


class SettingsDialog(tk.Toplevel):
    """Modal settings window with three tabs."""

    def __init__(self, parent: tk.Widget, config: ConfigManager, on_save: Optional[Callable] = None):
        super().__init__(parent)
        self.config = config
        self.on_save = on_save

        self.title("Settings")
        self.resizable(False, False)
        self.configure(bg="#1e1e1e")
        self.grab_set()  # modal

        self._build_ui()
        self._load_values()
        self._center_on_parent(parent)

    def _center_on_parent(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        pw = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pwidth = parent.winfo_width()
        pheight = parent.winfo_height()
        w = self.winfo_width()
        h = self.winfo_height()
        x = pw + (pwidth - w) // 2
        y = py + (pheight - h) // 2
        self.geometry(f"+{x}+{y}")

    # ── Style helpers ─────────────────────────────────────────────────────────

    def _style_label(self, parent, text: str, **kw) -> tk.Label:
        kw.setdefault("bg", "#1e1e1e")
        kw.setdefault("fg", "#cccccc")
        kw.setdefault("font", ("Helvetica", 10))
        return tk.Label(parent, text=text, **kw)

    def _style_entry(self, parent, textvariable=None, width: int = 30) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=textvariable,
            bg="#2d2d2d",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            highlightbackground="#555555",
            highlightthickness=1,
            width=width,
        )

    def _style_button(self, parent, text: str, command, bg: str = "#3a7bd5") -> tk.Label:
        """Label-based button — reliable colors on macOS."""
        lbl = tk.Label(
            parent,
            text=text,
            bg=bg,
            fg="white",
            font=("Helvetica", 10),
            cursor="hand2",
            padx=10,
            pady=4,
            relief=tk.FLAT,
        )
        lbl.bind("<Button-1>", lambda e: command())
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=self._darken(bg)))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=bg))
        return lbl

    @staticmethod
    def _darken(hex_color: str) -> str:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r, g, b = max(0, r - 30), max(0, g - 30), max(0, b - 30)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Notebook
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background="#1e1e1e", borderwidth=0)
        style.configure("TNotebook.Tab", background="#2d2d2d", foreground="#cccccc",
                        padding=[12, 6], font=("Helvetica", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", "#3a7bd5")],
                  foreground=[("selected", "white")])

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self._tab_symbols = tk.Frame(nb, bg="#1e1e1e")
        self._tab_indicators = tk.Frame(nb, bg="#1e1e1e")
        self._tab_discord = tk.Frame(nb, bg="#1e1e1e")

        nb.add(self._tab_symbols, text="  Symbols  ")
        nb.add(self._tab_indicators, text="  Indicators  ")
        nb.add(self._tab_discord, text="  Discord  ")

        self._build_symbols_tab()
        self._build_indicators_tab()
        self._build_discord_tab()

        # Bottom buttons
        btn_frame = tk.Frame(self, bg="#1e1e1e")
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self._style_button(btn_frame, "Save", self._on_save, bg="#2ecc71").pack(side=tk.RIGHT, padx=4)
        self._style_button(btn_frame, "Cancel", self.destroy, bg="#555555").pack(side=tk.RIGHT, padx=4)

    # ── Symbols tab ───────────────────────────────────────────────────────────

    def _build_symbols_tab(self) -> None:
        tab = self._tab_symbols
        self._style_label(tab, "Watch list (one symbol per line):", anchor="w").pack(
            fill=tk.X, padx=10, pady=(10, 2)
        )

        frame = tk.Frame(tab, bg="#1e1e1e")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        self._symbols_text = tk.Text(
            frame,
            bg="#2d2d2d",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            highlightbackground="#555555",
            highlightthickness=1,
            width=30,
            height=12,
            font=("Courier", 11),
        )
        scrollbar = tk.Scrollbar(frame, command=self._symbols_text.yview, bg="#2d2d2d")
        self._symbols_text.configure(yscrollcommand=scrollbar.set)
        self._symbols_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._style_label(tab, "Enter uppercase ticker symbols (e.g. AAPL, TSLA, SPY)",
                          fg="#888888", font=("Helvetica", 9)).pack(padx=10, pady=(2, 8), anchor="w")

    # ── Indicators tab ────────────────────────────────────────────────────────

    def _build_indicators_tab(self) -> None:
        tab = self._tab_indicators
        pad = {"padx": 10, "pady": 6}

        # EMA periods
        self._style_label(tab, "EMA Periods", font=("Helvetica", 11, "bold")).pack(anchor="w", padx=10, pady=(12, 4))

        self._ema_fast_var = tk.StringVar()
        self._ema_mid_var = tk.StringVar()
        self._ema_slow_var = tk.StringVar()

        for label, var in [("Fast EMA:", self._ema_fast_var),
                           ("Mid EMA:", self._ema_mid_var),
                           ("Slow EMA:", self._ema_slow_var)]:
            row = tk.Frame(tab, bg="#1e1e1e")
            row.pack(fill=tk.X, **pad)
            self._style_label(row, label, width=14, anchor="w").pack(side=tk.LEFT)
            self._style_entry(row, textvariable=var, width=8).pack(side=tk.LEFT)

        # ATR
        self._style_label(tab, "ATR", font=("Helvetica", 11, "bold")).pack(anchor="w", padx=10, pady=(12, 4))

        self._atr_period_var = tk.StringVar()
        self._atr_multiplier_var = tk.StringVar()
        self._poll_interval_var = tk.StringVar()
        self._cooldown_var = tk.StringVar()

        for label, var, hint in [
            ("ATR Period:", self._atr_period_var, "bars (default 14)"),
            ("ATR Multiplier:", self._atr_multiplier_var, "pullback threshold (default 0.5)"),
        ]:
            row = tk.Frame(tab, bg="#1e1e1e")
            row.pack(fill=tk.X, **pad)
            self._style_label(row, label, width=16, anchor="w").pack(side=tk.LEFT)
            self._style_entry(row, textvariable=var, width=8).pack(side=tk.LEFT)
            self._style_label(row, f"  {hint}", fg="#888888", font=("Helvetica", 9)).pack(side=tk.LEFT)

        # Scanner settings
        self._style_label(tab, "Scanner", font=("Helvetica", 11, "bold")).pack(anchor="w", padx=10, pady=(12, 4))

        for label, var, hint in [
            ("Poll Interval (s):", self._poll_interval_var, "seconds between data fetches"),
            ("Alert Cooldown (min):", self._cooldown_var, "minutes between same-type alerts"),
        ]:
            row = tk.Frame(tab, bg="#1e1e1e")
            row.pack(fill=tk.X, **pad)
            self._style_label(row, label, width=20, anchor="w").pack(side=tk.LEFT)
            self._style_entry(row, textvariable=var, width=8).pack(side=tk.LEFT)
            self._style_label(row, f"  {hint}", fg="#888888", font=("Helvetica", 9)).pack(side=tk.LEFT)

    # ── Discord tab ───────────────────────────────────────────────────────────

    def _build_discord_tab(self) -> None:
        tab = self._tab_discord
        pad = {"padx": 10, "pady": 6}

        self._discord_enabled_var = tk.BooleanVar()
        cb = tk.Checkbutton(
            tab, text="Enable Discord notifications",
            variable=self._discord_enabled_var,
            bg="#1e1e1e", fg="#cccccc",
            activebackground="#1e1e1e", activeforeground="#ffffff",
            selectcolor="#2d2d2d",
            font=("Helvetica", 10),
        )
        cb.pack(anchor="w", padx=10, pady=(14, 4))

        self._style_label(tab, "Webhook URL:", anchor="w").pack(fill=tk.X, **pad)

        url_frame = tk.Frame(tab, bg="#1e1e1e")
        url_frame.pack(fill=tk.X, padx=10, pady=2)

        self._discord_url_var = tk.StringVar()
        url_entry = self._style_entry(url_frame, textvariable=self._discord_url_var, width=50)
        url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._test_btn = self._style_button(url_frame, "Test", self._test_discord, bg="#e67e22")
        self._test_btn.pack(side=tk.LEFT, padx=(6, 0))

        help_text = (
            "获取 Webhook URL 步骤：\n"
            "  1. 打开 Discord，进入目标频道\n"
            "  2. 频道设置 → 整合 → Webhooks → 创建 Webhook\n"
            "  3. 复制 Webhook URL，粘贴到上方输入框\n"
            "  4. 点击「Test」验证连通性，再点「Save」保存"
        )
        self._style_label(tab, help_text, fg="#888888", font=("Helvetica", 9),
                          justify=tk.LEFT).pack(anchor="w", padx=10, pady=(8, 4))

        self._discord_status_label = self._style_label(tab, "", fg="#2ecc71")
        self._discord_status_label.pack(anchor="w", padx=10)

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load_values(self) -> None:
        # Symbols
        symbols = self.config.symbols
        self._symbols_text.delete("1.0", tk.END)
        self._symbols_text.insert("1.0", "\n".join(symbols))

        # Indicators
        self._ema_fast_var.set(str(self.config.ema_fast))
        self._ema_mid_var.set(str(self.config.ema_mid))
        self._ema_slow_var.set(str(self.config.ema_slow))
        self._atr_period_var.set(str(self.config.atr_period))
        self._atr_multiplier_var.set(str(self.config.atr_multiplier))
        self._poll_interval_var.set(str(self.config.poll_interval_seconds))
        self._cooldown_var.set(str(self.config.alert_cooldown_minutes))

        # Discord
        self._discord_enabled_var.set(self.config.discord_enabled)
        self._discord_url_var.set(self.config.discord_webhook_url)

    def _on_save(self) -> None:
        errors = []

        # Symbols
        raw_symbols = self._symbols_text.get("1.0", tk.END).strip()
        symbols = [s.strip().upper() for s in raw_symbols.splitlines() if s.strip()]
        if not symbols:
            errors.append("At least one symbol is required.")

        # Indicators — validate numeric
        try:
            ema_fast = int(self._ema_fast_var.get())
            ema_mid = int(self._ema_mid_var.get())
            ema_slow = int(self._ema_slow_var.get())
            atr_period = int(self._atr_period_var.get())
            atr_multiplier = float(self._atr_multiplier_var.get())
            poll_interval = int(self._poll_interval_var.get())
            cooldown = int(self._cooldown_var.get())

            if not (0 < ema_fast < ema_mid < ema_slow):
                errors.append("EMA periods must satisfy: Fast < Mid < Slow (all positive).")
            if atr_period < 1:
                errors.append("ATR period must be >= 1.")
            if atr_multiplier <= 0:
                errors.append("ATR multiplier must be positive.")
            if poll_interval < 10:
                errors.append("Poll interval must be >= 10 seconds.")
            if cooldown < 1:
                errors.append("Alert cooldown must be >= 1 minute.")
        except ValueError:
            errors.append("Indicator fields must contain valid numbers.")

        if errors:
            messagebox.showerror("Validation Error", "\n".join(errors), parent=self)
            return

        # Persist
        self.config.symbols = symbols
        self.config.update_section("ema_periods", {"fast": ema_fast, "mid": ema_mid, "slow": ema_slow})
        self.config.set("atr_period", value=atr_period)
        self.config.set("atr_multiplier", value=atr_multiplier)
        self.config.set("poll_interval_seconds", value=poll_interval)
        self.config.set("alert_cooldown_minutes", value=cooldown)
        self.config.update_section("discord", {
            "enabled": self._discord_enabled_var.get(),
            "webhook_url": self._discord_url_var.get().strip(),
        })

        if self.on_save:
            self.on_save()

        self.destroy()

    def _test_discord(self) -> None:
        url = self._discord_url_var.get().strip()
        if not url:
            self._discord_status_label.configure(text="Please enter a webhook URL first.", fg="#e74c3c")
            return

        self._discord_status_label.configure(text="Sending test message...", fg="#f1c40f")
        # Visually disable the label-button while testing
        self._test_btn.configure(text="Testing...", bg="#888888")
        self._test_btn.unbind("<Button-1>")

        def _do_test():
            notifier = DiscordNotifier(webhook_url=url, enabled=True)
            ok = notifier.test_connection()
            # Schedule UI update back on the main thread
            self.after(0, lambda: self._on_test_result(ok))

        threading.Thread(target=_do_test, daemon=True).start()

    def _on_test_result(self, ok: bool) -> None:
        """Called on the main thread after the Discord test completes."""
        if not self.winfo_exists():
            return
        # Re-enable the label-button
        self._test_btn.configure(text="Test", bg="#e67e22")
        self._test_btn.bind("<Button-1>", lambda e: self._test_discord())
        if ok:
            self._discord_status_label.configure(text="Test message sent successfully!", fg="#2ecc71")
        else:
            self._discord_status_label.configure(text="Failed — check the URL and try again.", fg="#e74c3c")
