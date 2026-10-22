#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Desktop always-on-top HUD for DeepSeek Harness.

A small Tkinter window that stays above other applications while DSH is
running. The browser-side HUD plugin pushes session snapshots to the local HTTP
bridge below; this window renders them and sends stop/chat commands back.

Protocol (localhost only):
  POST /status   {"title": ..., "running": ..., "items": [...]}
  GET  /state    -> last status snapshot (or {"connected": false})
  GET  /command  -> one queued command: {"type": "stop"} / {"type": "send", ...}
  POST /command  -> enqueue a command (used by tests)
"""

import argparse
import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tkinter import font as tkfont

APP_TITLE = "执行浮窗"
PANEL = "#15171d"
PANEL_2 = "#101217"
CARD = "#1b2740"
CARD_BORDER = "#2b3d5e"
BORDER = "#2a2e39"
TEXT = "#e6e9f0"
MUTED = "#8d93a3"
DIM = "#6f7686"
ACCENT = "#6ea8fe"
BLUE = "#3b6ef6"
GREEN = "#42d392"
RED = "#ff8e8e"
MAGIC = "#010203"
HOVER = "#23262f"

STATE_LOCK = threading.Lock()
STATE = {"payload": None, "ts": 0.0}
COMMANDS = queue.Queue()
HOST_PID = 0
LOG_PATH = ""
ROOT = None

BASE_W = 340
BASE_H = 480
BASE_HEADER_H = 42
ORB_SIZE = 58


def log(message):
    try:
        if not LOG_PATH:
            return
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(message) + "\n")
    except OSError:
        pass


def prefs_path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.path.expanduser("~")
    return os.path.join(base, "dsh-hud", "prefs.json")


def load_prefs():
    try:
        with open(prefs_path(), "r", encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(patch):
    try:
        value = load_prefs()
        value.update(patch)
        path = prefs_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False)
    except OSError:
        pass


def host_alive(pid):
    if not pid:
        return True
    if os.name == "nt":
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def fmt_time(value):
    try:
        if not value:
            return ""
        return time.strftime("%H:%M:%S", time.localtime(float(value) / 1000.0))
    except (ValueError, OSError):
        return ""


def fmt_elapsed(seconds):
    try:
        total = max(0, int(seconds))
        return "%02d:%02d" % (total // 60, total % 60)
    except (TypeError, ValueError):
        return "00:00"


class Handler(BaseHTTPRequestHandler):
    server_version = "DSHHud/1.0"

    def log_message(self, fmt, *args):
        return

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Cache-Control", "no-store")

    def reply_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self.reply_json({"ok": True, "ts": time.time()})
            return
        if path == "/state":
            with STATE_LOCK:
                payload = STATE["payload"]
                ts = STATE["ts"]
            if payload is None:
                self.reply_json({"connected": False, "serverTs": ts})
            else:
                self.reply_json(payload)
            return
        if path == "/command":
            try:
                command = COMMANDS.get_nowait()
            except queue.Empty:
                command = {"type": None}
            self.reply_json(command)
            return
        self.reply_json({"error": "not-found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if path == "/status":
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("status must be an object")
            except (ValueError, UnicodeDecodeError):
                self.reply_json({"ok": False, "error": "bad-json"}, 400)
                return
            with STATE_LOCK:
                STATE["payload"] = payload
                STATE["ts"] = time.time()
            self.reply_json({"ok": True})
            return
        if path == "/command":
            try:
                command = json.loads(raw.decode("utf-8"))
                if not isinstance(command, dict):
                    raise ValueError("command must be an object")
            except (ValueError, UnicodeDecodeError):
                self.reply_json({"ok": False, "error": "bad-json"}, 400)
                return
            COMMANDS.put(command)
            self.reply_json({"ok": True})
            return
        self.reply_json({"error": "not-found"}, 404)


def round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    r = radius
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=36, **kwargs)


class Hud:
    def __init__(self, root, args):
        self.root = root
        self.args = args
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.w = int(BASE_W * self.scale)
        self.h = int(BASE_H * self.scale)
        self.header_h = int(BASE_HEADER_H * self.scale)
        self.inset = int(9 * self.scale)
        self.collapsed = False
        self.alpha_index = 0
        self.alphas = [0.98, 0.92, 0.84]
        self.steer = False
        self.last_updated = None
        self.run_start = None
        self.connected = False
        self.drag_offset = None
        self.detail_windows = []
        self.hotkey_down = False
        self.running = False
        self.orb_mode = False
        self.animating = False
        self.orb_hover = False
        self.orb_size = int(ORB_SIZE * self.scale)
        self.orb_rect = None
        self.panel_rect = None

        prefs = load_prefs()
        self.alpha_index = int(prefs.get("alpha", 0)) % len(self.alphas)
        root.attributes("-alpha", self.alphas[self.alpha_index])
        root.attributes("-topmost", True)
        root.overrideredirect(True)
        root.configure(bg=MAGIC)
        try:
            root.wm_attributes("-transparentcolor", MAGIC)
        except tk.TclError:
            root.configure(bg=PANEL)

        x = int(prefs.get("x", root.winfo_screenwidth() - self.w - 28))
        y = int(prefs.get("y", 56))
        x = max(0, min(x, root.winfo_screenwidth() - self.w))
        y = max(0, min(y, max(0, root.winfo_screenheight() - self.h - 20)))
        root.geometry("%dx%d+%d+%d" % (self.w, self.h, x, y))

        self.canvas = tk.Canvas(root, bg=MAGIC, highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.canvas.bind("<Configure>", lambda _event: self.redraw_surface())

        self.content = tk.Frame(root, bg=PANEL)
        self.content.place(x=self.inset, y=self.inset,
                           width=self.w - 2 * self.inset,
                           height=self.h - 2 * self.inset)

        self.build_header()
        self.body = tk.Frame(self.content, bg=PANEL)
        self.body.pack(fill="both", expand=True)
        self.build_status()
        self.build_current()
        self.build_activity()
        self.build_footer()

        self.root.update_idletasks()
        self.root.geometry("%dx%d+%d+%d" % (self.w, self.h, x, y))
        self.root.resizable(False, False)
        self.draw_background()
        self.root.after(120, self.poll_state)
        self.root.after(150, self.poll_hotkey)
        self.root.after(200, self.orb_tick)
        self.root.after(2000, self.check_host)

    # ---------- look ----------

    def redraw_surface(self):
        if self.orb_mode:
            self.draw_orb()
        else:
            self.draw_background()

    def draw_background(self):
        self.canvas.delete("bg")
        w = self.canvas.winfo_width() or self.w
        h = self.canvas.winfo_height() or self.h
        round_rect(self.canvas, 0, 0, w - 1, h - 1, int(18 * self.scale),
                   fill=PANEL, outline=BORDER, width=1, tags="bg")
        self.canvas.tag_lower("bg")

    def font(self, size, bold=False):
        family = "Microsoft YaHei UI"
        try:
            families = set(tkfont.families(self.root))
            if family not in families:
                family = "Microsoft YaHei"
        except tk.TclError:
            family = "Microsoft YaHei UI"
        return (family, size, "bold") if bold else (family, size)

    def build_header(self):
        header = tk.Frame(self.content, bg=PANEL, height=self.header_h)
        header.pack(fill="x")
        header.pack_propagate(False)

        self.dot = tk.Canvas(header, width=int(10 * self.scale), height=int(10 * self.scale),
                             bg=PANEL, highlightthickness=0, bd=0)
        self.dot.pack(side="left", padx=(int(12 * self.scale), int(6 * self.scale)))
        self.dot_id = self.dot.create_oval(1, 1, int(9 * self.scale), int(9 * self.scale),
                                           fill=MUTED, outline="")

        brand = tk.Label(header, text=APP_TITLE, bg=PANEL, fg=TEXT, font=self.font(10, True),
                         anchor="w")
        brand.pack(side="left")

        self.btn_close = self.make_header_button(header, "×", self.close_overlay)
        self.btn_orb = self.make_header_button(header, "◉", self.collapse_to_orb)
        self.btn_orb.configure(fg=ACCENT)
        self.btn_orb.bind("<Leave>", lambda _event: self.btn_orb.configure(bg=PANEL, fg=ACCENT))
        self.btn_reset = self.make_header_button(header, "⌂", self.reset_pos)
        self.btn_alpha = self.make_header_button(header, "◐", self.cycle_alpha)
        self.btn_collapse = self.make_header_button(header, "—", self.toggle_collapse)
        self.btn_new = self.make_header_button(header, "＋", self.new_session)

        for widget in (header, brand, self.dot):
            widget.bind("<ButtonPress-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.drag)
            widget.bind("<ButtonRelease-1>", self.end_drag)
            widget.bind("<Double-Button-1>", self.reset_pos)

    def make_header_button(self, parent, text, command):
        label = tk.Label(parent, text=text, bg=PANEL, fg=MUTED, font=self.font(12),
                         cursor="hand2", padx=int(7 * self.scale), pady=0)
        label.pack(side="right", padx=(0, int(2 * self.scale)))
        label.bind("<Button-1>", lambda _event: (command(), "break")[1])
        label.bind("<Enter>", lambda _event: label.configure(bg=HOVER, fg=TEXT))
        label.bind("<Leave>", lambda _event: label.configure(bg=PANEL, fg=MUTED))
        return label

    def build_status(self):
        row = tk.Frame(self.body, bg=PANEL)
        row.pack(fill="x", padx=int(12 * self.scale), pady=(int(2 * self.scale), 0))
        self.status_label = tk.Label(row, text="等待连接", bg=PANEL, fg=TEXT,
                                     font=self.font(10, True), anchor="w")
        self.status_label.pack(side="left")
        self.step_label = tk.Label(row, text="", bg=PANEL, fg=MUTED,
                                   font=self.font(9), anchor="w")
        self.step_label.pack(side="left", padx=(int(8 * self.scale), 0))
        self.elapsed_label = tk.Label(row, text="", bg=PANEL, fg=MUTED,
                                      font=self.font(9), anchor="e")
        self.elapsed_label.pack(side="right")

        self.session_label = tk.Label(self.body, text="", bg=PANEL, fg=MUTED,
                                      font=self.font(9), anchor="w")
        self.session_label.pack(fill="x", padx=int(12 * self.scale), pady=(0, int(4 * self.scale)))

    def build_current(self):
        self.current_frame = tk.Frame(self.body, bg=CARD, highlightbackground=CARD_BORDER,
                                      highlightthickness=1)
        self.current_frame.pack(fill="x", padx=int(12 * self.scale), pady=(0, int(6 * self.scale)))
        accent = tk.Frame(self.current_frame, bg=ACCENT, width=int(3 * self.scale))
        accent.pack(side="left", fill="y")
        inner = tk.Frame(self.current_frame, bg=CARD)
        inner.pack(side="left", fill="both", expand=True, padx=int(9 * self.scale),
                   pady=int(7 * self.scale))
        self.current_title = tk.Label(inner, text="", bg=CARD, fg=TEXT,
                                      font=self.font(9, True), anchor="w")
        self.current_title.pack(fill="x")
        self.current_sub = tk.Label(inner, text="", bg=CARD, fg="#b7bcc9",
                                    font=self.font(8), anchor="w", justify="left",
                                    wraplength=max(120, self.w - 2 * self.inset - 40))
        self.current_sub.pack(fill="x")

    def build_activity(self):
        wrapper = tk.Frame(self.body, bg=PANEL)
        wrapper.pack(fill="both", expand=True, padx=int(8 * self.scale), pady=0)
        self.activity = tk.Text(wrapper, bg=PANEL_2, fg=TEXT, bd=0, relief="flat",
                                highlightthickness=0, wrap="word", cursor="arrow",
                                font=self.font(9), padx=int(6 * self.scale),
                                pady=int(4 * self.scale), spacing1=2, spacing3=2,
                                height=6, width=34)
        self.activity.pack(fill="both", expand=True)
        self.activity.tag_configure("time", foreground=DIM, font=self.font(8))
        self.activity.tag_configure("title", foreground=TEXT, font=self.font(9, True))
        self.activity.tag_configure("sub", foreground=MUTED, font=self.font(8))
        self.activity.tag_configure("live", foreground="#8bb6ff")
        self.activity.tag_configure("err", foreground=RED)
        self.activity.tag_configure("ok", foreground=GREEN)
        self.activity.tag_configure("empty", foreground=MUTED, font=self.font(9))
        self.activity.bind("<Button-1>", self.on_activity_click)
        self.activity.configure(state="disabled")
        self.row_ranges = []

    def build_footer(self):
        footer = tk.Frame(self.body, bg=PANEL)
        footer.pack(fill="x", padx=int(10 * self.scale), pady=(int(4 * self.scale), int(10 * self.scale)))

        self.stop_btn = tk.Button(footer, text="■ 停止", command=self.stop,
                                  bg="#3a1d22", fg=RED, activebackground="#4a252b",
                                  activeforeground="#ffb0b0", relief="flat", bd=0,
                                  padx=int(7 * self.scale), pady=int(3 * self.scale),
                                  font=self.font(9, True), cursor="hand2")
        self.stop_btn.pack(side="left")
        self.mode_btn = tk.Button(footer, text="排队", command=self.toggle_mode,
                                  bg="#20242d", fg=MUTED, activebackground=HOVER,
                                  activeforeground=TEXT, relief="flat", bd=0,
                                  padx=int(7 * self.scale), pady=int(3 * self.scale),
                                  font=self.font(9), cursor="hand2")
        self.mode_btn.pack(side="left", padx=(int(5 * self.scale), 0))

        self.send_btn = tk.Button(footer, text="发送", command=self.send,
                                  bg=BLUE, fg="#ffffff", activebackground="#4f7df7",
                                  activeforeground="#ffffff", relief="flat", bd=0,
                                  padx=int(10 * self.scale), pady=int(3 * self.scale),
                                  font=self.font(9, True), cursor="hand2")
        self.send_btn.pack(side="right")

        self.entry = tk.Entry(footer, bg="#0d0f13", fg=TEXT, insertbackground=TEXT,
                              relief="flat", bd=0, highlightthickness=1,
                              highlightbackground=BORDER, highlightcolor=ACCENT,
                              font=self.font(9))
        self.entry.pack(side="left", fill="x", expand=True,
                        padx=(int(6 * self.scale), int(6 * self.scale)), ipady=int(3 * self.scale))
        self.entry.bind("<Return>", lambda _event: (self.send(), "break")[1])
        self.entry.bind("<FocusIn>", lambda _event: self.entry.configure(highlightbackground=ACCENT))
        self.entry.bind("<FocusOut>", lambda _event: self.entry.configure(highlightbackground=BORDER))

        self.notice = tk.Label(self.body, text="Alt+Shift+H 显隐", bg=PANEL, fg=DIM,
                               font=self.font(8), anchor="w")
        self.notice.pack(fill="x", padx=int(12 * self.scale), pady=(0, int(4 * self.scale)))

    # ---------- window actions ----------

    def start_drag(self, event):
        self.drag_offset = (event.x_root - self.root.winfo_x(),
                            event.y_root - self.root.winfo_y())
        return "break"

    def drag(self, event):
        if not self.drag_offset:
            return
        x = event.x_root - self.drag_offset[0]
        y = event.y_root - self.drag_offset[1]
        self.root.geometry("+%d+%d" % (x, y))
        return "break"

    def end_drag(self, _event):
        if self.drag_offset:
            save_prefs({"x": self.root.winfo_x(), "y": self.root.winfo_y()})
        self.drag_offset = None
        return "break"

    def reset_pos(self, _event=None):
        height = (self.header_h + 2 * self.inset) if self.collapsed else self.h
        x = self.root.winfo_screenwidth() - self.w - 28
        y = 56
        self.root.geometry("%dx%d+%d+%d" % (self.w, height, x, y))
        save_prefs({"x": x, "y": y})
        return "break"

    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.body.pack_forget()
            height = self.header_h + 2 * self.inset
            self.root.geometry("%dx%d" % (self.w, height))
            self.btn_collapse.configure(text="▢")
        else:
            self.body.pack(fill="both", expand=True)
            self.root.geometry("%dx%d" % (self.w, self.h))
            self.btn_collapse.configure(text="—")
        self.root.after(30, self.draw_background)

    def cycle_alpha(self):
        self.alpha_index = (self.alpha_index + 1) % len(self.alphas)
        value = self.alphas[self.alpha_index]
        self.root.attributes("-alpha", value)
        save_prefs({"alpha": self.alpha_index})

    def fade_to(self, target, duration, done=None):
        try:
            start = float(self.root.attributes("-alpha"))
        except (tk.TclError, ValueError):
            start = 1.0
        steps = max(1, int(duration / 16))

        def step(index):
            if index >= steps:
                try:
                    self.root.attributes("-alpha", target)
                except tk.TclError:
                    return
                if done:
                    done()
                return
            progress = index / steps
            eased = progress * progress * (3 - 2 * progress)
            value = start + (target - start) * eased
            try:
                self.root.attributes("-alpha", max(0.0, min(1.0, value)))
            except tk.TclError:
                return
            self.root.after(16, lambda: step(index + 1))

        step(0)

    def collapse_to_orb(self):
        if self.animating or self.orb_mode:
            return
        self.animating = True
        self.panel_rect = (self.root.winfo_x(), self.root.winfo_y(),
                           self.root.winfo_width(), self.root.winfo_height())
        target_alpha = max(0.96, min(0.99, self.alphas[self.alpha_index] + 0.01))
        self.fade_to(0.0, 140, lambda: self.show_orb(target_alpha))

    def show_orb(self, target_alpha):
        self.orb_mode = True
        self.content.place_forget()
        self.canvas.delete("bg")
        self.canvas.configure(cursor="hand2")
        self.canvas.bind("<Button-1>", self.on_orb_click)
        self.canvas.bind("<Enter>", self.on_orb_enter)
        self.canvas.bind("<Leave>", self.on_orb_leave)
        self.position_orb()
        self.root.update_idletasks()
        self.draw_orb()
        self.animate_orb_pop(0, target_alpha)

    def position_orb(self):
        x, y, w, h = self.panel_rect or (self.root.winfo_x(), self.root.winfo_y(), self.w, self.h)
        size = self.orb_size
        px = x + w - size
        py = y + h - size
        px = max(0, min(px, self.root.winfo_screenwidth() - size))
        py = max(0, min(py, self.root.winfo_screenheight() - size - 40))
        self.orb_rect = (px, py, size, size)
        self.root.geometry("%dx%d+%d+%d" % (size, size, px, py))

    def animate_orb_pop(self, index, target_alpha):
        size = self.orb_size
        start = max(30, int(size * 0.62))
        steps = 9
        x0, y0, _, _ = self.orb_rect

        def step(i):
            progress = i / steps
            eased = 1 - (1 - progress) ** 3
            current = int(start + (size - start) * eased)
            x = x0 + (size - current) // 2
            y = y0 + (size - current) // 2
            self.root.geometry("%dx%d+%d+%d" % (current, current, x, y))
            self.root.update_idletasks()
            self.draw_orb()
            self.root.attributes("-alpha", target_alpha * (0.35 + 0.65 * eased))
            if i < steps:
                self.root.after(16, lambda: step(i + 1))
            else:
                self.root.geometry("%dx%d+%d+%d" % (size, size, x0, y0))
                self.root.attributes("-alpha", target_alpha)
                self.animating = False

        step(0)

    def draw_orb(self):
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
        except tk.TclError:
            return
        if w <= 2 or h <= 2:
            return
        self.canvas.delete("orb")
        cx = w / 2.0
        cy = h / 2.0
        radius = max(8.0, min(w, h) / 2.0 - 7.0)
        phase = (time.time() * 1.1) % 1.0

        for factor, color in ((1.16, "#1b2854"), (1.09, "#2946a4"), (1.03, "#3b6ef6")):
            rr = radius * factor
            self.canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                                    outline=color, width=1, tags="orb")
        if self.running:
            rr = radius + 3 + 3 * phase
            self.canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                                    outline="#8bb6ff", width=1, tags="orb")
        self.canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                                fill=("#4f7df7" if self.orb_hover else "#3b6ef6"),
                                outline=("#e1ebff" if self.orb_hover else "#9cc0ff"),
                                width=1, tags="orb")
        self.canvas.create_arc(cx - radius + 2, cy - radius + 2,
                               cx + radius - 2, cy + radius - 2,
                               start=40, extent=95, style="arc",
                               outline="#dce8ff", width=2, tags="orb")
        self.canvas.create_text(cx, cy, text="AI", fill="#ffffff",
                                font=self.font(11, True), tags="orb")
        dot_r = max(3.0, radius * 0.15)
        dx = cx + radius * 0.62
        dy = cy - radius * 0.62
        self.canvas.create_oval(dx - dot_r, dy - dot_r, dx + dot_r, dy + dot_r,
                                fill=("#8bb6ff" if self.running else "#42d392"),
                                outline=PANEL, width=1, tags="orb")

    def on_orb_click(self, _event=None):
        self.expand_from_orb()

    def on_orb_enter(self, _event=None):
        self.orb_hover = True
        self.draw_orb()

    def on_orb_leave(self, _event=None):
        self.orb_hover = False
        self.draw_orb()

    def orb_tick(self):
        try:
            if self.orb_mode and not self.animating and self.root.state() != "withdrawn":
                self.draw_orb()
            self.root.after(50, self.orb_tick)
        except tk.TclError:
            pass

    def expand_from_orb(self):
        if self.animating or not self.orb_mode:
            return
        self.animating = True
        self.fade_to(0.0, 110, self.show_panel)

    def show_panel(self):
        self.orb_mode = False
        self.canvas.unbind("<Button-1>")
        self.canvas.unbind("<Enter>")
        self.canvas.unbind("<Leave>")
        self.canvas.configure(cursor="")
        self.canvas.delete("orb")
        x, y, w, h = self.panel_rect or (self.root.winfo_x(), self.root.winfo_y(), self.w, self.h)
        x = max(0, min(x, self.root.winfo_screenwidth() - w))
        y = max(0, min(y, self.root.winfo_screenheight() - h - 20))
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.content.place(x=self.inset, y=self.inset,
                           width=w - 2 * self.inset,
                           height=h - 2 * self.inset)
        self.root.after(5, self.draw_background)
        target = self.alphas[self.alpha_index]
        self.fade_to(target, 160, self.finish_panel)

    def finish_panel(self):
        self.animating = False
        self.draw_background()

    def toggle_visible(self):
        if self.animating:
            return
        try:
            if self.root.state() == "withdrawn":
                self.root.deiconify()
                self.root.lift()
                self.root.attributes("-topmost", True)
                self.root.attributes("-alpha", 0.0)
                self.orb_mode = False
                self.content.place_forget()
                self.canvas.delete("orb")
                self.animating = True
                self.show_panel()
                return
            if self.orb_mode:
                self.expand_from_orb()
            else:
                self.collapse_to_orb()
        except tk.TclError:
            pass

    def new_session(self):
        COMMANDS.put({"type": "new-session"})
        self.notice.configure(text="正在新建对话…", fg=ACCENT)
        self.root.after(2600, self.reset_notice)

    def reset_notice(self):
        try:
            if self.notice.cget("text") == "正在新建对话…":
                self.notice.configure(text="Alt+Shift+H 显隐", fg=DIM)
        except tk.TclError:
            pass

    def close_overlay(self):
        if self.animating:
            return
        self.animating = True
        if not self.orb_mode:
            self.panel_rect = (self.root.winfo_x(), self.root.winfo_y(),
                               self.root.winfo_width(), self.root.winfo_height())
        self.fade_to(0.0, 140, self.finish_close)

    def finish_close(self):
        self.animating = False
        self.orb_mode = False
        self.orb_hover = False
        try:
            self.canvas.delete("bg")
            self.canvas.delete("orb")
            self.content.place_forget()
            self.root.withdraw()
        except tk.TclError:
            pass

    def poll_hotkey(self):
        try:
            if os.name == "nt":
                user32 = ctypes.windll.user32
                alt = bool(user32.GetAsyncKeyState(0x12) & 0x8000)
                shift = bool(user32.GetAsyncKeyState(0x10) & 0x8000)
                key = bool(user32.GetAsyncKeyState(0x48) & 0x8000)
                if alt and shift and key:
                    if not self.hotkey_down:
                        self.hotkey_down = True
                        self.toggle_visible()
                else:
                    self.hotkey_down = False
        except Exception:
            pass
        try:
            self.root.after(120, self.poll_hotkey)
        except tk.TclError:
            pass

    # ---------- state ----------

    def poll_state(self):
        try:
            now = time.time()
            with STATE_LOCK:
                payload = STATE["payload"]
                ts = STATE["ts"]
            if payload and now - ts < 20:
                self.apply_state(payload, now - ts)
                self.connected = True
            else:
                self.set_disconnected()
            if self.run_start is not None:
                self.elapsed_label.configure(text=fmt_elapsed(now - self.run_start))
            self.root.attributes("-topmost", True)
            self.root.after(400, self.poll_state)
        except tk.TclError:
            return

    def set_disconnected(self):
        if self.connected:
            self.connected = False
            self.running = False
            self.run_start = None
            self.status_label.configure(text="等待 DSH 窗口连接…", fg=MUTED)
            self.step_label.configure(text="")
            self.elapsed_label.configure(text="")
            self.dot.itemconfigure(self.dot_id, fill=MUTED)
            self.stop_btn.configure(state="disabled")
            self.current_frame.pack_forget()

    def apply_state(self, payload, age):
        if payload.get("connected") is False:
            self.connected = True
            self.running = False
            self.run_start = None
            self.status_label.configure(text="无活动会话", fg=MUTED)
            self.step_label.configure(text="")
            self.elapsed_label.configure(text="")
            self.dot.itemconfigure(self.dot_id, fill=MUTED)
            self.stop_btn.configure(state="disabled")
            self.current_frame.pack_forget()
            updated = payload.get("updatedAt")
            if updated != self.last_updated:
                self.last_updated = updated
                self.render_activity([])
            return
        running = bool(payload.get("running"))
        self.running = running
        status = "正在执行" if running else "待命"
        if payload.get("error"):
            status = "执行异常"
        self.status_label.configure(text=status, fg=(RED if payload.get("error") else TEXT))
        self.step_label.configure(text=payload.get("step") or "")
        self.dot.itemconfigure(self.dot_id, fill=(ACCENT if running else GREEN))

        if running and self.run_start is None:
            self.run_start = time.time()
        if not running:
            self.run_start = None
            self.elapsed_label.configure(text="")

        title = payload.get("title") or ""
        if len(title) > 29:
            title = title[:28] + "…"
        self.session_label.configure(text=title)
        self.stop_btn.configure(state=("normal" if running else "disabled"))

        current = payload.get("current")
        if current:
            self.current_title.configure(text=current.get("title") or "")
            self.current_sub.configure(text=current.get("subtitle") or "")
            if not self.current_frame.winfo_ismapped():
                self.current_frame.pack(fill="x", padx=int(12 * self.scale),
                                        pady=(0, int(6 * self.scale)))
            # keep the card above the activity area
            self.current_frame.pack_configure(before=self.activity.master)
        else:
            self.current_frame.pack_forget()

        updated = payload.get("updatedAt")
        if updated != self.last_updated:
            self.last_updated = updated
            self.render_activity(payload.get("items") or [])

    def render_activity(self, items):
        self.activity.configure(state="normal")
        self.activity.delete("1.0", "end")
        self.row_ranges = []
        if not items:
            self.activity.insert("end", "还没有执行记录。\n", "empty")
        else:
            for item in items[-30:]:
                start = int(self.activity.index("end-1c").split(".")[0])
                title = item.get("title") or ""
                if len(title) > 40:
                    title = title[:39] + "…"
                subtitle = item.get("subtitle") or ""
                if len(subtitle) > 86:
                    subtitle = subtitle[:85] + "…"
                kind = item.get("kind") or ""
                live = bool(item.get("live"))
                status = item.get("status") or ""
                glyph = "▸" if live and kind == "tool" else "✓"
                if kind == "assistant":
                    glyph = "✎"
                elif kind == "command":
                    glyph = "⌘"
                elif status == "error":
                    glyph = "!"
                tag = "err" if status == "error" else ("live" if live else "ok")
                self.activity.insert("end", (item.get("time") and fmt_time(item.get("time"))) or "  ", "time")
                self.activity.insert("end", "  " + glyph + "  ", tag)
                self.activity.insert("end", title + "\n", "title")
                if subtitle:
                    self.activity.insert("end", "        " + subtitle + "\n", "sub")
                end = int(self.activity.index("end-1c").split(".")[0])
                self.row_ranges.append((start, end, item.get("detail") or subtitle or title))
        self.activity.configure(state="disabled")
        self.activity.see("end")

    def on_activity_click(self, event):
        try:
            line = int(self.activity.index("@%d,%d" % (event.x, event.y)).split(".")[0])
        except (tk.TclError, ValueError):
            return
        for start, end, detail in self.row_ranges:
            if start <= line <= end:
                self.show_detail(detail)
                return

    def show_detail(self, text):
        win = tk.Toplevel(self.root)
        win.title("执行详情")
        win.configure(bg=PANEL)
        win.attributes("-topmost", True)
        win.geometry("%dx%d" % (int(480 * self.scale), int(320 * self.scale)))
        box = tk.Text(win, bg=PANEL_2, fg=TEXT, bd=0, relief="flat", highlightthickness=0,
                      wrap="word", font=self.font(9), padx=12, pady=10)
        box.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        box.insert("1.0", text or "(无详情)")
        box.configure(state="disabled")
        button = tk.Button(win, text="关闭", command=win.destroy, bg="#20242d", fg=TEXT,
                           relief="flat", bd=0, padx=16, pady=4, font=self.font(9),
                           cursor="hand2")
        button.pack(pady=(0, 10))
        self.detail_windows.append(win)
        win.bind("<Destroy>", lambda _event: self.detail_windows.remove(win)
                 if win in self.detail_windows else None)

    # ---------- commands ----------

    def stop(self):
        COMMANDS.put({"type": "stop"})
        self.notice.configure(text="已请求停止", fg=GREEN)

    def toggle_mode(self):
        self.steer = not self.steer
        self.mode_btn.configure(text=("插话" if self.steer else "排队"),
                                fg=(ACCENT if self.steer else MUTED))
        self.notice.configure(text=("插话模式：发送会打断当前步骤" if self.steer else "排队模式：等当前回合结束"),
                              fg=MUTED)

    def send(self):
        text = self.entry.get().strip()
        if not text:
            return
        COMMANDS.put({"type": "send", "text": text, "mode": ("steer" if self.steer else "queue")})
        self.entry.delete(0, "end")
        self.notice.configure(text=("已插话" if self.steer else "已发送"), fg=GREEN)

    def check_host(self):
        try:
            if not host_alive(HOST_PID):
                self.root.destroy()
                return
            self.root.after(2000, self.check_host)
        except tk.TclError:
            return


def main():
    global HOST_PID, LOG_PATH, ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-pid", type=int, default=0)
    parser.add_argument("--port", type=int, default=38471)
    args = parser.parse_args()
    HOST_PID = args.host_pid

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.path.expanduser("~")
    LOG_PATH = os.path.join(base, "dsh-hud", "hud.log")

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log("bridge listening on 127.0.0.1:%d" % args.port)
    except OSError as error:
        log("bridge bind failed: %r" % (error,))
        return 0

    try:
        ROOT = tk.Tk()
        Hud(ROOT, args)
        ROOT.mainloop()
    except Exception as error:
        log("ui failed: %r" % (error,))
        return 1
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
