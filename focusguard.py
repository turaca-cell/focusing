#!/usr/bin/env python3
"""
FocusGuard — Windows Odak Koruyucusu
Dikkat dağılmasını tespit edip görsel + sesli uyarı verir.

Gereksinimler:
    pip install pywin32 psutil

Çalıştır:
    python focusguard.py
"""

import sys
import os
import threading
import time
import json
import winsound

# ── Bağımlılık kontrolü ────────────────────────────────────────────────────────
missing = []
try:
    import win32gui
    import win32process
    import win32con
    import win32api
except ImportError:
    missing.append("pywin32")
try:
    import psutil
except ImportError:
    missing.append("psutil")

if missing:
    # tkinter hata penceresi göster, sonra çık
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Eksik Kütüphane",
        f"Şu kütüphaneler eksik: {', '.join(missing)}\n\n"
        f"Komut istemi'nde çalıştırın:\n"
        f"pip install {' '.join(missing)}"
    )
    sys.exit(1)

import tkinter as tk
from tkinter import ttk, messagebox

# ── Sabitler ──────────────────────────────────────────────────────────────────
CONFIG_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "focusguard_config.json")
POLL_INTERVAL = 0.5   # saniye
DEFAULT_GRACE = 3.0   # saniye

# Renk paleti
BG          = "#080810"
SURFACE     = "#10101e"
SURFACE2    = "#191928"
BORDER      = "#2a2a40"
ACCENT      = "#7c5cfc"
ACCENT_DIM  = "#4b3a9e"
SUCCESS     = "#22c97a"
DANGER      = "#f05252"
WARNING     = "#f59e0b"
TEXT        = "#e8e8f0"
TEXT_DIM    = "#6b6b8a"
TEXT_MUTED  = "#3d3d55"
OVERLAY_BG  = "#05050e"


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def get_running_apps():
    """Görünür pencereleri olan çalışan uygulamaların listesini döndür."""
    apps = {}

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            name = proc.name().lower()
            if name not in ("explorer.exe", "focusguard.py", "python.exe", "pythonw.exe"):
                apps[name] = title
        except Exception:
            pass

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        pass
    return apps


def get_active_window():
    """(hwnd, proc_name, window_title) döndürür."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None, None, None
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        return hwnd, proc.name().lower(), title
    except Exception:
        return None, None, None


def bring_window_to_front(allowed_apps):
    """İzin listesindeki ilk görünür pencereyi öne çıkar."""
    results = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            name = proc.name().lower()
            title = win32gui.GetWindowText(hwnd).lower()
            for app in allowed_apps:
                if app.lower() in name or app.lower() in title:
                    results.append(hwnd)
                    return False
        except Exception:
            pass

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass

    if results:
        hwnd = results[0]
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass


def fmt_seconds(s):
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}s {m:02d}d {sec:02d}sn"
    return f"{m:02d}d {sec:02d}sn"


# ── Ana Uygulama ──────────────────────────────────────────────────────────────

class FocusGuard:
    def __init__(self):
        self.allowed_apps: list[str] = []
        self.grace_period: float = DEFAULT_GRACE
        self.monitoring: bool = False

        # İç durum
        self._distraction_start: float | None = None
        self._distraction_count: int = 0
        self._session_start: float | None = None
        self._break_until: float | None = None
        self._overlay: tk.Toplevel | None = None
        self._overlay_visible: bool = False
        self._break_after_id = None
        self._stats_after_id = None

        self.load_config()
        self._build_main_window()

    # ── Config ────────────────────────────────────────────────────────────────

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                data = json.load(open(CONFIG_FILE))
                self.allowed_apps = data.get("allowed_apps", [])
                self.grace_period = float(data.get("grace_period", DEFAULT_GRACE))
            except Exception:
                pass

    def save_config(self):
        try:
            json.dump(
                {"allowed_apps": self.allowed_apps, "grace_period": self.grace_period},
                open(CONFIG_FILE, "w"),
                indent=2,
            )
        except Exception:
            pass

    # ── Ana Pencere ───────────────────────────────────────────────────────────

    def _build_main_window(self):
        self.root = tk.Tk()
        self.root.title("FocusGuard")
        self.root.geometry("460x620")
        self.root.minsize(400, 560)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Scrollable canvas için yapı
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=0, pady=0)

        self._build_header(outer)
        self._build_status_bar(outer)
        self._build_app_section(outer)
        self._build_grace_section(outer)
        self._build_control_buttons(outer)
        self._build_stats_bar(outer)

    def _build_header(self, parent):
        frm = tk.Frame(parent, bg=BG, pady=24)
        frm.pack(fill="x", padx=24)

        tk.Label(frm, text="◈ FocusGuard", font=("Consolas", 22, "bold"),
                 bg=BG, fg=ACCENT).pack(anchor="w")
        tk.Label(frm, text="Dikkatini dağıtan uygulamaları tespit eder ve uyarır.",
                 font=("Consolas", 9), bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(2, 0))

        # Ayırıcı çizgi
        sep = tk.Frame(parent, bg=BORDER, height=1)
        sep.pack(fill="x", padx=0)

    def _build_status_bar(self, parent):
        frm = tk.Frame(parent, bg=SURFACE, pady=12)
        frm.pack(fill="x")

        inner = tk.Frame(frm, bg=SURFACE)
        inner.pack(padx=24)

        self._status_dot = tk.Label(inner, text="●", font=("Consolas", 11),
                                     bg=SURFACE, fg=TEXT_MUTED)
        self._status_dot.pack(side="left")
        self._status_label = tk.Label(inner, text="  Pasif — İzleme başlatılmadı",
                                       font=("Consolas", 10), bg=SURFACE, fg=TEXT_DIM)
        self._status_label.pack(side="left")

    def _build_app_section(self, parent):
        pad = tk.Frame(parent, bg=BG, pady=16)
        pad.pack(fill="x", padx=24)

        header = tk.Frame(pad, bg=BG)
        header.pack(fill="x")

        tk.Label(header, text="İzin Verilen Uygulamalar", font=("Consolas", 11, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")
        tk.Label(header,
                 text="(sadece bu uygulamalar açıkken alarm çalmaz)",
                 font=("Consolas", 8), bg=BG, fg=TEXT_DIM).pack(side="left", padx=8)

        # Liste kutusu
        list_frame = tk.Frame(pad, bg=BORDER, bd=0)
        list_frame.pack(fill="x", pady=(10, 0))

        inner = tk.Frame(list_frame, bg=SURFACE2, padx=1, pady=1)
        inner.pack(fill="x")

        self._app_listbox = tk.Listbox(
            inner,
            font=("Consolas", 10),
            bg=SURFACE2,
            fg=TEXT,
            selectbackground=ACCENT_DIM,
            selectforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            height=6,
            activestyle="none",
        )
        self._app_listbox.pack(fill="x", padx=0)
        self._refresh_listbox()

        # Alt buton çubuğu
        btn_row = tk.Frame(pad, bg=BG)
        btn_row.pack(fill="x", pady=(8, 0))

        self._btn("+ Çalışan Uygulamadan Seç", btn_row, self._pick_running_app,
                  bg=SURFACE2, fg=ACCENT, width=26).pack(side="left")
        self._btn("✕ Seçileni Kaldır", btn_row, self._remove_selected_app,
                  bg=SURFACE2, fg=DANGER, width=18).pack(side="left", padx=(6, 0))

    def _build_grace_section(self, parent):
        pad = tk.Frame(parent, bg=BG, pady=0)
        pad.pack(fill="x", padx=24)

        sep = tk.Frame(pad, bg=BORDER, height=1)
        sep.pack(fill="x", pady=(0, 14))

        row = tk.Frame(pad, bg=BG)
        row.pack(fill="x")

        tk.Label(row, text="Tepki Gecikmesi", font=("Consolas", 11, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")

        self._grace_label = tk.Label(row, text=f"  {self.grace_period:.0f}sn",
                                     font=("Consolas", 11), bg=BG, fg=ACCENT)
        self._grace_label.pack(side="left")

        tk.Label(pad,
                 text="İzin dışı uygulamada bu kadar süre geçince uyarı verilir.",
                 font=("Consolas", 8), bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(2, 8))

        self._grace_var = tk.DoubleVar(value=self.grace_period)
        self._grace_var.trace_add("write", self._on_grace_change)

        slider = tk.Scale(
            pad,
            from_=1, to=10,
            resolution=1,
            orient="horizontal",
            variable=self._grace_var,
            bg=BG,
            fg=TEXT_DIM,
            troughcolor=SURFACE2,
            activebackground=ACCENT,
            highlightthickness=0,
            bd=0,
            sliderrelief="flat",
            showvalue=False,
            length=400,
        )
        slider.pack(fill="x")

    def _build_control_buttons(self, parent):
        pad = tk.Frame(parent, bg=BG, pady=20)
        pad.pack(fill="x", padx=24)

        sep = tk.Frame(pad, bg=BORDER, height=1)
        sep.pack(fill="x", pady=(0, 18))

        row = tk.Frame(pad, bg=BG)
        row.pack()

        self._start_btn = self._btn("▶  İzlemeyi Başlat", row, self.start_monitoring,
                                     bg=ACCENT, fg="white", width=22,
                                     font=("Consolas", 12, "bold"), pady=10)
        self._start_btn.pack(side="left")

        self._stop_btn = self._btn("■  Durdur", row, self.stop_monitoring,
                                    bg=SURFACE2, fg=TEXT_DIM, width=12,
                                    font=("Consolas", 12), pady=10)
        self._stop_btn.pack(side="left", padx=(10, 0))
        self._stop_btn.config(state="disabled")

    def _build_stats_bar(self, parent):
        frm = tk.Frame(parent, bg=SURFACE, pady=10)
        frm.pack(fill="x", side="bottom")

        inner = tk.Frame(frm, bg=SURFACE)
        inner.pack(padx=24, fill="x")

        self._stat_distractions = tk.Label(inner, text="Dağılma: 0",
                                            font=("Consolas", 9), bg=SURFACE, fg=TEXT_DIM)
        self._stat_distractions.pack(side="left")

        self._stat_time = tk.Label(inner, text="",
                                   font=("Consolas", 9), bg=SURFACE, fg=TEXT_DIM)
        self._stat_time.pack(side="right")

    # ── Widget Yardımcısı ─────────────────────────────────────────────────────

    def _btn(self, text, parent, cmd, bg=SURFACE2, fg=TEXT,
             width=None, font=("Consolas", 10), pady=6, **kw):
        b = tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg, activebackground=ACCENT, activeforeground="white",
            font=font, relief="flat", bd=0, pady=pady, cursor="hand2",
            **kw,
        )
        if width:
            b.config(width=width)
        return b

    # ── Listbox ───────────────────────────────────────────────────────────────

    def _refresh_listbox(self):
        self._app_listbox.delete(0, "end")
        for app in self.allowed_apps:
            self._app_listbox.insert("end", f"  {app}")

    def _pick_running_app(self):
        apps = get_running_apps()
        if not apps:
            messagebox.showinfo("Bilgi", "Şu an görünür pencereli uygulama bulunamadı.")
            return

        picker = tk.Toplevel(self.root)
        picker.title("Uygulama Seç")
        picker.geometry("380x400")
        picker.configure(bg=BG)
        picker.grab_set()

        tk.Label(picker, text="Çalışan Uygulamalar",
                 font=("Consolas", 12, "bold"), bg=BG, fg=TEXT).pack(pady=(18, 4))
        tk.Label(picker, text="İzin listesine eklemek istediğini çift tıkla.",
                 font=("Consolas", 8), bg=BG, fg=TEXT_DIM).pack()

        lb = tk.Listbox(picker, font=("Consolas", 10),
                        bg=SURFACE2, fg=TEXT,
                        selectbackground=ACCENT_DIM, selectforeground=TEXT,
                        relief="flat", bd=0, highlightthickness=0,
                        height=14, activestyle="none")
        lb.pack(fill="both", expand=True, padx=16, pady=12)

        name_map = {}
        for proc_name, title in sorted(apps.items()):
            display = f"{proc_name}  —  {title[:40]}"
            lb.insert("end", display)
            name_map[display] = proc_name

        def add(_event=None):
            sel = lb.curselection()
            if not sel:
                return
            display = lb.get(sel[0])
            proc = name_map[display]
            if proc not in self.allowed_apps:
                self.allowed_apps.append(proc)
                self._refresh_listbox()
                self.save_config()
            picker.destroy()

        lb.bind("<Double-Button-1>", add)
        self._btn("Ekle", picker, add, bg=ACCENT, fg="white",
                  font=("Consolas", 11, "bold"), width=16, pady=8).pack(pady=(0, 14))

    def _remove_selected_app(self):
        sel = self._app_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.allowed_apps.pop(idx)
        self._refresh_listbox()
        self.save_config()

    # ── Grace period ──────────────────────────────────────────────────────────

    def _on_grace_change(self, *_):
        val = self._grace_var.get()
        self.grace_period = float(val)
        self._grace_label.config(text=f"  {val:.0f}sn")
        self.save_config()

    # ── İzleme ────────────────────────────────────────────────────────────────

    def start_monitoring(self):
        if not self.allowed_apps:
            messagebox.showwarning(
                "Uygulama Eklenmemiş",
                "Önce izin listesine en az bir uygulama eklemelisin."
            )
            return
        self.monitoring = True
        self._distraction_count = 0
        self._distraction_start = None
        self._session_start = time.time()
        self._update_status(True)
        self._update_stats_loop()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def stop_monitoring(self):
        self.monitoring = False
        self._break_until = None
        self._hide_overlay()
        self._update_status(False)
        if self._stats_after_id:
            try:
                self.root.after_cancel(self._stats_after_id)
            except Exception:
                pass

    def _update_status(self, active: bool):
        if active:
            self._status_dot.config(fg=SUCCESS)
            self._status_label.config(
                text=f"  Aktif — {len(self.allowed_apps)} uygulama izinli",
                fg=SUCCESS,
            )
            self._start_btn.config(state="disabled", bg=SURFACE2, fg=TEXT_MUTED)
            self._stop_btn.config(state="normal", bg=DANGER, fg="white")
        else:
            self._status_dot.config(fg=TEXT_MUTED)
            self._status_label.config(text="  Pasif — İzleme başlatılmadı", fg=TEXT_DIM)
            self._start_btn.config(state="normal", bg=ACCENT, fg="white")
            self._stop_btn.config(state="disabled", bg=SURFACE2, fg=TEXT_DIM)

    def _update_stats_loop(self):
        if not self.monitoring:
            return
        elapsed = time.time() - (self._session_start or time.time())
        self._stat_distractions.config(
            text=f"Dağılma: {self._distraction_count}",
            fg=WARNING if self._distraction_count > 0 else TEXT_DIM,
        )
        self._stat_time.config(text=f"Süre: {fmt_seconds(elapsed)}")
        self._stats_after_id = self.root.after(1000, self._update_stats_loop)

    # ── İzleme döngüsü (arka plan thread) ────────────────────────────────────

    def _monitor_loop(self):
        while self.monitoring:
            time.sleep(POLL_INTERVAL)

            # Mola modunda atla
            if self._break_until and time.time() < self._break_until:
                self._distraction_start = None
                self._hide_overlay()
                continue

            hwnd, proc_name, title = get_active_window()
            if proc_name is None:
                continue

            # Kendi penceremizse dikkati sayma
            own = "focusguard" in (title or "").lower() or proc_name in (
                "python.exe", "pythonw.exe"
            )
            if own:
                self._distraction_start = None
                self._hide_overlay()
                continue

            if self._is_allowed(proc_name, title or ""):
                self._distraction_start = None
                self._hide_overlay()
            else:
                if self._distraction_start is None:
                    self._distraction_start = time.time()
                elif time.time() - self._distraction_start >= self.grace_period:
                    self._trigger_alert(proc_name, title or "")

    def _is_allowed(self, proc_name: str, title: str) -> bool:
        for app in self.allowed_apps:
            a = app.lower()
            if a in proc_name.lower() or a in title.lower():
                return True
        return False

    # ── Alarm ─────────────────────────────────────────────────────────────────

    def _trigger_alert(self, proc_name: str, title: str):
        if not self._overlay_visible:
            self._distraction_count += 1
            self.root.after(0, lambda: self._show_overlay(proc_name, title))
            threading.Thread(target=self._play_sound, daemon=True).start()
        else:
            # Sadece etiketi güncelle
            self.root.after(0, lambda: self._update_overlay_info(proc_name, title))

    def _play_sound(self):
        try:
            for _ in range(3):
                winsound.Beep(900, 150)
                time.sleep(0.08)
            winsound.Beep(660, 500)
        except Exception:
            pass

    # ── Overlay ───────────────────────────────────────────────────────────────

    def _show_overlay(self, proc_name: str, title: str):
        if self._overlay and self._overlay.winfo_exists():
            self._update_overlay_info(proc_name, title)
            return

        ov = tk.Toplevel(self.root)
        ov.attributes("-fullscreen", True)
        ov.attributes("-topmost", True)
        ov.attributes("-alpha", 0.93)
        ov.configure(bg=OVERLAY_BG)
        ov.overrideredirect(True)
        self._overlay = ov
        self._overlay_visible = True

        # ── Merkez kart ──────────────────────────────────────────────────────
        card = tk.Frame(ov, bg=SURFACE, padx=52, pady=44,
                        highlightbackground=ACCENT, highlightthickness=1)
        card.place(relx=0.5, rely=0.5, anchor="center")

        # Üst ikon + başlık
        tk.Label(card, text="◈", font=("Consolas", 52),
                 bg=SURFACE, fg=ACCENT).pack()
        tk.Label(card, text="DİKKATİN DAĞILDI",
                 font=("Consolas", 26, "bold"),
                 bg=SURFACE, fg=DANGER).pack(pady=(8, 4))

        # Hangi uygulama
        self._ov_app_label = tk.Label(
            card,
            text=self._fmt_app(proc_name, title),
            font=("Consolas", 11),
            bg=SURFACE, fg=TEXT_DIM,
        )
        self._ov_app_label.pack(pady=(0, 24))

        # Motivasyon
        tk.Label(card,
                 text="Çalışmana geri dön — hedefe bir adım daha.",
                 font=("Consolas", 12),
                 bg=SURFACE, fg=TEXT).pack(pady=(0, 28))

        # Dağılma sayacı
        self._ov_count_label = tk.Label(
            card,
            text=f"Bu oturumda {self._distraction_count}. dağılma",
            font=("Consolas", 9),
            bg=SURFACE, fg=TEXT_MUTED,
        )
        self._ov_count_label.pack(pady=(0, 20))

        # Butonlar
        btn_row = tk.Frame(card, bg=SURFACE)
        btn_row.pack()

        self._btn("▶  Odağa Dön", btn_row, self._return_to_focus,
                  bg=ACCENT, fg="white",
                  font=("Consolas", 12, "bold"), width=18, pady=10).pack(side="left")
        self._btn("☕  5dk Mola", btn_row, self._take_break,
                  bg=SURFACE2, fg=TEXT_DIM,
                  font=("Consolas", 12), width=14, pady=10).pack(side="left", padx=(10, 0))

    def _fmt_app(self, proc_name: str, title: str) -> str:
        short_title = (title[:55] + "…") if len(title) > 55 else title
        return f"Şu an: {proc_name}  ·  {short_title} "

    def _update_overlay_info(self, proc_name: str, title: str):
        try:
            if self._ov_app_label and self._ov_app_label.winfo_exists():
                self._ov_app_label.config(text=self._fmt_app(proc_name, title))
            if self._ov_count_label and self._ov_count_label.winfo_exists():
                self._ov_count_label.config(
                    text=f"Bu oturumda {self._distraction_count}. dağılma"
                )
        except Exception:
            pass

    def _hide_overlay(self):
        if self._overlay_visible:
            self.root.after(0, self._destroy_overlay)

    def _destroy_overlay(self):
        try:
            if self._overlay and self._overlay.winfo_exists():
                self._overlay.destroy()
        except Exception:
            pass
        self._overlay = None
        self._overlay_visible = False

    def _return_to_focus(self):
        self._destroy_overlay()
        self._distraction_start = None
        bring_window_to_front(self.allowed_apps)

    def _take_break(self):
        self._destroy_overlay()
        self._distraction_start = None
        self._break_until = time.time() + 300  # 5 dakika
        self._show_break_window()

    # ── Mola Geri Sayım ───────────────────────────────────────────────────────

    def _show_break_window(self):
        bw = tk.Toplevel(self.root)
        bw.title("Mola Sayacı")
        bw.geometry("260x130")
        bw.resizable(False, False)
        bw.configure(bg=BG)
        bw.attributes("-topmost", True)

        tk.Label(bw, text="☕  Mola",
                 font=("Consolas", 13, "bold"), bg=BG, fg=ACCENT).pack(pady=(18, 4))

        timer_lbl = tk.Label(bw, text="5:00",
                             font=("Consolas", 22, "bold"), bg=BG, fg=TEXT)
        timer_lbl.pack()

        remaining = [300]

        def tick():
            if remaining[0] <= 0:
                try:
                    bw.destroy()
                except Exception:
                    pass
                return
            m, s = divmod(remaining[0], 60)
            try:
                timer_lbl.config(text=f"{m}:{s:02d}")
            except Exception:
                return
            remaining[0] -= 1
            bw.after(1000, tick)

        tick()

    # ── Kapat ─────────────────────────────────────────────────────────────────

    def _on_close(self):
        self.monitoring = False
        self.save_config()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ── Başlangıç ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = FocusGuard()
    app.run()
