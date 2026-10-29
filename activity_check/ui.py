# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from datetime import datetime
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from .models import Activity, BEIJING, LABELS, display_status, integer, plain_text
from .session import EXPORT_COMMAND, SessionError, import_session
from .identity import fit_window, install_window_icons
from .worker import WorkerUnavailable
from math import inf

from .activity_order import order_rows, remaining_slots
from .wechat_ui import WechatLoginDialog
from .campus import CAMPUSES, matches_campus

from .ui_theme import (
    BG,
    MUTED,
    SOFT,
    GREEN,
    AMBER,
    FONT,
    BORDER,
    BLUE,
    setup_style,
)
from .ui_widgets import ActivityTable


def short_time(value, fallback="未提供"):
    text = str(value or fallback)
    return text[5:16] if len(text) >= 16 and text[:4].isdigit() else text


def pair_time(row, start, end, *, multiline=False):
    fallback = Activity.from_row(row).detail_placeholder if start == "startTime" else "未提供"
    if not row.get(start) and not row.get(end):
        return fallback
    return short_time(row.get(start), fallback) + ("\n至 " if multiline else " → ") + short_time(row.get(end), fallback)


def remaining_label(row):
    value = remaining_slots(row)
    if value == inf:
        return "不限名额"
    limit = integer(row["raw"].get("maxNum"))
    if limit is not None and limit < 0:
        limit = None
    return f"{value if value is not None else '?'} / {limit if limit is not None else '?'}"


class ImportDialog(tk.Toplevel):
    def __init__(self, app, account=None):
        super().__init__(app.root)
        self.app, self.account_id = app, account
        self.title("个人 Cookie · 登录方式")
        fit_window(self, 760, 680, 680, 610)
        self.configure(bg=BG)
        self.transient(app.root)
        self.grab_set()
        if app.icon_path.exists():
            self.iconbitmap(str(app.icon_path))
            install_window_icons(self, app.icon_path)
        body = ttk.Frame(self, padding=24)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="保存个人 Cookie", style="Section.TLabel").pack(anchor="w")
        ttk.Label(body, text="登录态仅加密保存在本机，活动勾选记录按账号分别保存。", style="Muted.TLabel").pack(
            anchor="w", pady=(8, 18)
        )
        ttk.Label(body, text="账号备注").pack(anchor="w")
        self.name = tk.StringVar(value=app.store.account(account)["name"] if account else "我的账号")
        ttk.Entry(body, textvariable=self.name, state="readonly" if account else "normal").pack(fill="x", pady=(5, 16))
        tabs = ttk.Notebook(body)
        tabs.pack(fill="both", expand=True)
        manual = ttk.Frame(tabs, padding=16)
        login = ttk.Frame(tabs, padding=20)
        tabs.add(manual, text="粘贴导入")
        tabs.add(login, text="网页登录")
        actions = ttk.Frame(manual)
        actions.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(actions, text="保存并检查登录", style="Primary.TButton", command=self.save).pack(side="right")
        ttk.Label(manual, text="Cookie 或完整登录数据").pack(anchor="w")
        self.raw = tk.Text(
            manual,
            height=7,
            wrap="word",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BLUE,
            font=("Consolas", 10),
            undo=True,
        )
        self.raw.pack(fill="both", expand=True, pady=(7, 9))
        ttk.Label(
            manual,
            text="学校网站还使用登录令牌。完整登录数据可一次包含 Cookie、令牌和账号信息。",
            wraplength=590,
            style="Muted.TLabel",
        ).pack(anchor="w")
        ttk.Button(manual, text="复制登录数据命令 / 查看步骤", command=self.copy_help).pack(anchor="w", pady=(10, 8))
        self.advanced_var = tk.BooleanVar(value=False)
        advanced = self.advanced = ttk.Frame(manual)
        ttk.Checkbutton(
            manual, text="仅有 Cookie：补充 Token 和个人标识", variable=self.advanced_var, command=self.toggle_advanced
        ).pack(anchor="w", pady=(4, 8))
        ttk.Label(advanced, text="仅粘贴 Cookie 时，补充以下两项；完整登录数据无需填写。", style="Muted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        ttk.Label(advanced, text="Token").grid(row=1, column=0, sticky="w", padx=(0, 12))
        self.token = tk.StringVar()
        ttk.Entry(advanced, textvariable=self.token, show="●").grid(row=1, column=1, sticky="ew")
        ttk.Label(advanced, text="个人标识 ID").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=8)
        self.student = tk.StringVar()
        ttk.Entry(advanced, textvariable=self.student).grid(row=2, column=1, sticky="ew")
        advanced.columnconfigure(1, weight=1)
        ttk.Label(login, text="在学校官方页面完成登录", style="Section.TLabel").pack(anchor="w", pady=(12, 18))
        ttk.Label(
            login,
            text="将打开独立的 Edge 登录窗口。登录成功后，助手会验证账号并自动加密保存登录态。\n\n按学校提供的认证方式操作；如果学校要求在微信内登录，可改用“粘贴导入”。\n\n不会读取你日常浏览器中其他网站的数据。",
            wraplength=580,
            justify="left",
        ).pack(anchor="w")
        ttk.Button(login, text="打开网页登录", style="Primary.TButton", command=self.login).pack(anchor="w", pady=24)
        ttk.Label(body, text="提示：个人标识是网站 teaInfo.id，通常不同于学号。", style="Muted.TLabel").pack(
            anchor="w", pady=(14, 0)
        )
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def toggle_advanced(self):
        if self.advanced_var.get():
            self.advanced.pack(fill="x", pady=(4, 8))
            fit_window(self, 760, 780, 680, 700)
        else:
            self.advanced.pack_forget()

    def copy_help(self):
        self.clipboard_clear()
        self.clipboard_append(EXPORT_COMMAND)
        self.update()
        messagebox.showinfo(
            "已复制命令",
            "1. 在已登录的学校活动页面按 F12，切换到 Console（控制台）。\n2. 运行刚复制的命令，它只把本网站登录数据复制到剪贴板。\n3. 回到助手粘贴到上方大框，点击“保存并检查登录”。\n\n登录数据请只粘贴到本机助手。",
            parent=self,
        )

    def _account(self):
        return self.account_id or self.app.store.add_account(self.name.get())

    def save(self):
        raw = self.raw.get("1.0", "end").strip()
        try:
            import_session(raw, self.token.get(), self.student.get())
        except (SessionError, ValueError, TypeError):
            messagebox.showerror(
                "请检查登录数据",
                "尚未读到完整的学生登录数据。请使用“复制登录数据命令”，或补充正确的 Token 和个人标识 ID。",
                parent=self,
            )
            return
        account = self._account()
        self.app.store.configure(account, enabled=False)
        if not self.app.send_command("import", account, raw=raw, token=self.token.get(), student_id=self.student.get()):
            return
        self.destroy()
        self.app.show_account(account)

    def login(self):
        account = self._account()
        if not self.app.send_command("login", account):
            return
        self.destroy()
        self.app.show_account(account)


class AccountSettingsDialog(tk.Toplevel):
    def __init__(self, app, account):
        super().__init__(app.root)
        self.app, self.account_id = app, account
        owner = app.store.account(account)
        self.title("账号设置")
        fit_window(self, 470, 420, 440, 390)
        self.configure(bg=BG)
        self.transient(app.root)
        install_window_icons(self, app.icon_path)
        body = ttk.Frame(self, padding=app.px(24))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=owner["name"], style="Section.TLabel", wraplength=app.px(390)).pack(anchor="w")
        self.state = tk.StringVar(value="学校登录状态：" + owner["status"])
        ttk.Label(body, textvariable=self.state, style="Muted.TLabel").pack(anchor="w", pady=(app.px(6), app.px(20)))
        ttk.Button(body, text="更新登录态 / 网页登录", command=self.login).pack(anchor="w")
        ttk.Separator(body).pack(fill="x", pady=app.px(20))
        line = ttk.Frame(body)
        line.pack(fill="x")
        ttk.Label(line, text="检查间隔").pack(side="left")
        value = app.interval_var.get() if app.account_id == account else str(owner["interval"])
        self.interval = tk.StringVar(value=value)
        ttk.Spinbox(line, from_=5, to=3600, increment=5, textvariable=self.interval, width=7).pack(
            side="left", padx=app.px(10)
        )
        ttk.Label(line, text="秒", style="Muted.TLabel").pack(side="left")
        ttk.Label(body, text="可设置 5–3600 秒；保存后用于后续检查。", style="Muted.TLabel").pack(
            anchor="w", pady=app.px(8)
        )
        actions = ttk.Frame(body)
        actions.pack(side="bottom", fill="x", pady=(app.px(12), 0))
        ttk.Button(actions, text="移除账号", style="Quiet.TButton", command=self.remove).pack(side="left")
        ttk.Button(actions, text="保存设置", style="Primary.TButton", command=self.save).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())

    def login(self):
        self.destroy()
        ImportDialog(self.app, self.account_id)

    def save(self):
        try:
            interval = int(self.interval.get())
            if not 5 <= interval <= 3600:
                raise ValueError()
        except ValueError:
            messagebox.showinfo("检查间隔", "请输入 5–3600 秒。", parent=self)
            return
        self.app.store.configure(self.account_id, interval=interval)
        if self.app.account_id == self.account_id:
            self.app.interval_var.set(str(interval))
            self.app.render_table()
        else:
            self.app.show_home()
        self.app.footer.set(f"检查间隔已保存为 {interval} 秒。")
        self.destroy()

    def remove(self):
        if self.app.remove(self.account_id):
            self.destroy()


class NotificationSettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("微信通知设置")
        fit_window(self, 480, 370, 440, 340)
        self.configure(bg=BG)
        self.transient(app.root)
        install_window_icons(self, app.icon_path)
        body = ttk.Frame(self, padding=app.px(24))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="文件传输助手通知", style="Section.TLabel").pack(anchor="w")
        ttk.Label(body, textvariable=app.wechat_status, style="Muted.TLabel").pack(anchor="w", pady=app.px(10))
        ttk.Label(body, text="报名成功后自动发送：\n已报名---活动名称", wraplength=app.px(400)).pack(
            anchor="w", pady=app.px(12)
        )
        ttk.Label(
            body,
            text="使用已授权的网页版会话在后台发送。\n会话失效时重新扫码，待发送通知会继续保留。",
            style="Muted.TLabel",
            wraplength=app.px(400),
        ).pack(anchor="w", pady=app.px(8))
        actions = ttk.Frame(body)
        actions.pack(side="bottom", fill="x", pady=(app.px(12), 0))
        ttk.Button(actions, text="关闭", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="连接 / 重新授权", style="Primary.TButton", command=self.authorize).pack(
            side="right", padx=app.px(8)
        )
        self.bind("<Escape>", lambda _e: self.destroy())

    def authorize(self):
        self.destroy()
        WechatLoginDialog(self.app)


class App:
    def __init__(self, root, store, vault, worker, *, notifier=None):
        self.root, self.store, self.vault, self.worker = root, store, vault, worker
        self.notifier = notifier
        self.scale = root.winfo_fpixels("1i") / 96
        self.px = lambda value: round(value * self.scale)
        self.account_id = None
        self.closing = False
        self.terms, self.rows, self.sort_preferences, self.view_state = {}, {}, {}, {}
        self.busy = set()
        self.home_scroll = 0
        self.icon_path = Path(__file__).parent / "assets" / "calendar-red-check.ico"
        setup_style(root)
        root.title("活动报名助手")
        fit_window(root, 1280, 820, 1040, 680)
        root.configure(bg=BG)
        if self.icon_path.exists():
            root.iconbitmap(str(self.icon_path))
            install_window_icons(root, self.icon_path)
        self.brand_image = tk.PhotoImage(file=str(self.icon_path.parent / "calendar-red-check-32.png"))
        header = ttk.Frame(root, padding=(self.px(24), self.px(10)), style="Inside.TFrame")
        header.pack(fill="x")
        ttk.Label(header, image=self.brand_image, style="Card.TLabel").pack(side="left", padx=(0, self.px(9)))
        ttk.Label(header, text="活动报名助手", style="CardTitle.TLabel").pack(side="left")
        self.activity_nav = ttk.Button(header, text="活动", style="Inline.TButton", command=self.navigate_activities)
        self.activity_nav.pack(side="left", padx=(self.px(25), self.px(5)))
        self.account_nav = ttk.Button(header, text="个人 Cookie", style="Inline.TButton", command=self.show_home)
        self.account_nav.pack(side="left")
        ttk.Button(header, text="使用说明", style="Inline.TButton", command=self.help).pack(side="right")
        self.wechat_button = ttk.Button(
            header, text="通知设置", style="Inline.TButton", command=self.notification_settings
        )
        self.wechat_button.pack(side="right", padx=(self.px(8), self.px(12)))
        self.wechat_status = tk.StringVar(value="微信：未连接")
        self.wechat_label = ttk.Label(header, textvariable=self.wechat_status, style="CardMuted.TLabel")
        self.wechat_label.pack(side="right", padx=self.px(8))
        self.school_status = tk.StringVar(value="学校账号")
        self.school_label = ttk.Label(header, textvariable=self.school_status, style="CardMuted.TLabel")
        self.school_label.pack(side="right", padx=self.px(8))
        ttk.Separator(root).pack(fill="x")
        self.footer = tk.StringVar(value="最小化后继续运行；关闭软件或电脑休眠后停止。所有时间均为北京时间。")
        ttk.Label(
            root,
            textvariable=self.footer,
            style="Muted.TLabel",
            padding=(self.px(24), self.px(8)),
            wraplength=self.px(1100),
        ).pack(side="bottom", fill="x")
        self.body = ttk.Frame(root, padding=(self.px(24), self.px(16), self.px(24), 0))
        self.body.pack(fill="both", expand=True)
        root.bind("<F5>", lambda _event: self.refresh())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.show_home()
        self.root.after(200, self.poll)

    def _remember_view(self):
        if self.account_id and hasattr(self, "table") and self.table.winfo_exists():
            self.view_state[self.account_id] = {
                "search": self.search_var.get(),
                "filter": self.filter_var.get(),
                "interval": self.interval_var.get(),
                "selection": self.table.selection(),
                "scroll": self.table.yview()[0],
                "logs": self.log_expanded,
            }
        elif hasattr(self, "home_canvas") and self.home_canvas.winfo_exists():
            self.home_scroll = self.home_canvas.yview()[0]

    def clear_body(self):
        self._remember_view()
        for widget in self.body.winfo_children():
            widget.destroy()

    def navigate_activities(self):
        accounts = self.store.accounts()
        if self.account_id:
            return
        if accounts:
            self.enter_account(accounts[0]["id"])
        else:
            self.show_home()

    def notification_settings(self):
        NotificationSettingsDialog(self)

    def account_settings(self, account=None):
        AccountSettingsDialog(self, account or self.account_id)

    def show_home(self):
        self.clear_body()
        self.account_id = None
        self.account_nav.configure(style="Selected.TButton")
        self.activity_nav.configure(style="Inline.TButton")
        self.school_status.set("账号状态见下方")
        self.school_label.configure(foreground=MUTED)
        top = ttk.Frame(self.body)
        top.pack(fill="x", pady=(0, self.px(20)))
        left = ttk.Frame(top)
        left.pack(side="left")
        ttk.Label(left, text="个人 Cookie", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text="管理账号登录状态，分别保存活动和报名范围。", style="Muted.TLabel").pack(
            anchor="w", pady=(self.px(5), 0)
        )
        ttk.Button(top, text="＋ 添加账号", style="Primary.TButton", command=lambda: ImportDialog(self)).pack(
            side="right"
        )
        self.home_canvas = canvas = tk.Canvas(self.body, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.body, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        cards = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=cards, anchor="nw")
        cards.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        accounts, frames = self.store.accounts(), []
        for account in accounts:
            identity = account["id"]
            rows = self.store.activities(identity)
            excluded = sum(row["excluded"] for row in rows)
            card = ttk.Frame(cards, style="Card.TFrame", padding=self.px(20))
            frames.append(card)
            line = ttk.Frame(card, style="Inside.TFrame")
            line.pack(fill="x")
            ttk.Label(line, text=account["name"], style="CardTitle.TLabel", wraplength=self.px(265)).pack(side="left")
            ttk.Button(line, text="重命名", style="Inline.TButton", command=lambda a=identity: self.rename(a)).pack(
                side="left", padx=(self.px(8), 0)
            )
            ttk.Label(
                card,
                text="● " + account["status"],
                style="CardMuted.TLabel",
                foreground=GREEN if account["status"] == "已登录" else AMBER,
            ).pack(anchor="w", pady=(self.px(7), self.px(18)))
            info = ttk.Frame(card, style="Inside.TFrame")
            info.pack(fill="x", expand=True)
            running = (
                ("自动报名已开启" if account["mode"] == "auto" else "仅监测已开启")
                if account["enabled"]
                else ("自动报名未开启" if account["mode"] == "auto" else "监测未开启")
            )
            for index, (key, value) in enumerate(
                (
                    ("运行状态", running),
                    ("报名范围", account["campus"] or "全部校区"),
                    ("检查间隔", f"{account['interval']} 秒"),
                    ("不报名", f"{excluded} 项"),
                )
            ):
                item = ttk.Frame(info, style="Inside.TFrame")
                item.grid(row=index // 2, column=index % 2, sticky="nw", padx=(0, self.px(25)), pady=(0, self.px(16)))
                ttk.Label(item, text=key, style="CardMuted.TLabel").pack(anchor="w")
                ttk.Label(item, text=value, style="Card.TLabel").pack(anchor="w", pady=(self.px(4), 0))
            info.columnconfigure(0, weight=1)
            info.columnconfigure(1, weight=1)
            ttk.Separator(card).pack(fill="x", pady=(0, self.px(13)))
            actions = ttk.Frame(card, style="Inside.TFrame")
            actions.pack(fill="x")
            ttk.Button(
                actions, text="账号设置", style="Inline.TButton", command=lambda a=identity: self.account_settings(a)
            ).pack(side="left")
            ttk.Button(
                actions, text="进入活动 →", style="Primary.TButton", command=lambda a=identity: self.enter_account(a)
            ).pack(side="right")
        if not accounts:
            empty = ttk.Frame(cards, style="Card.TFrame", padding=self.px(36))
            frames.append(empty)
            ttk.Label(empty, text="从你的个人 Cookie 开始", style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(
                empty,
                text="保存登录态后，就可以查看活动、设置范围并开启监测。",
                style="CardMuted.TLabel",
                wraplength=self.px(400),
            ).pack(anchor="w", pady=self.px(15))
            ttk.Button(empty, text="添加第一个账号", style="Primary.TButton", command=lambda: ImportDialog(self)).pack(
                anchor="w"
            )
        else:
            add = ttk.Frame(cards, style="Card.TFrame", padding=self.px(24))
            frames.append(add)
            add.columnconfigure(0, weight=1)
            add.rowconfigure(0, weight=1)
            add.rowconfigure(2, weight=1)
            center = ttk.Frame(add, style="Inside.TFrame")
            center.grid(row=1, column=0)
            ttk.Button(center, text="＋ 添加另一个账号", command=lambda: ImportDialog(self)).pack()
            ttk.Label(center, text="独立登录态 · 独立活动设置", style="CardMuted.TLabel").pack(pady=(self.px(10), 0))

        def layout(event):
            canvas.itemconfigure(window, width=event.width)
            columns = 2 if event.width >= self.px(850) and len(frames) > 1 else 1
            for column in range(2):
                cards.columnconfigure(
                    column, weight=1 if column < columns else 0, uniform="cards" if column < columns else ""
                )
            for index, card in enumerate(frames):
                card.grid(
                    row=index // columns,
                    column=index % columns,
                    sticky="nsew",
                    padx=(0, self.px(14) if index % columns < columns - 1 else 0),
                    pady=(0, self.px(14)),
                )

        canvas.bind("<Configure>", layout)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units"))
        self.root.after_idle(lambda: canvas.yview_moveto(self.home_scroll) if canvas.winfo_exists() else None)

    def enter_account(self, account):
        self.show_account(account)
        if self.vault.path(account).exists():
            self.send_command("refresh", account)

    def show_account(self, account):
        owner = self.store.account(account)
        if not owner:
            self.show_home()
            return
        self.clear_body()
        self.account_id = account
        state = self.view_state.get(account, {})
        self.activity_nav.configure(style="Selected.TButton")
        self.account_nav.configure(style="Inline.TButton")
        top = ttk.Frame(self.body)
        top.pack(fill="x", pady=(0, self.px(12)))
        ttk.Label(top, text="我的活动", style="Section.TLabel").pack(side="left")
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=self.px(12), pady=self.px(5))
        ttk.Label(top, text=owner["name"], style="Muted.TLabel", wraplength=self.px(260)).pack(side="left")
        ttk.Button(top, text="重命名", style="Quiet.TButton", command=lambda: self.rename(account)).pack(
            side="left", padx=self.px(5)
        )
        ttk.Button(top, text="账号设置", style="Quiet.TButton", command=self.account_settings).pack(side="right")
        self.term_var = tk.StringVar(value=owner["term"] or "当前学期")
        self.term_combo = ttk.Combobox(
            top, textvariable=self.term_var, values=self.terms.get(account, []), state="readonly", width=20
        )
        self.term_combo.pack(side="right", padx=self.px(10))
        self.term_combo.bind("<<ComboboxSelected>>", self.change_term)

        monitor = ttk.Frame(self.body, style="Card.TFrame", padding=(self.px(16), self.px(12)))
        monitor.pack(fill="x", pady=(0, self.px(14)))
        monitor.columnconfigure(0, weight=1)
        left = ttk.Frame(monitor, style="Inside.TFrame")
        left.grid(row=0, column=0, sticky="w")
        self.run_title = tk.StringVar()
        self.status_var = tk.StringVar()
        self.monitor_info = tk.StringVar()
        self.run_label = ttk.Label(left, textvariable=self.run_title, style="Status.TLabel")
        self.run_label.pack(anchor="w")
        ttk.Label(left, textvariable=self.monitor_info, style="CardMuted.TLabel").pack(anchor="w", pady=(self.px(4), 0))
        controls = ttk.Frame(monitor, style="Inside.TFrame")
        controls.grid(row=0, column=1, sticky="e", padx=(self.px(15), 0))
        self.mode_buttons = {}
        modes = ttk.Frame(controls, style="Inside.TFrame")
        modes.pack(side="left", padx=(0, self.px(10)))
        for key, text in (("observe", "仅监测"), ("auto", "监测并自动报名")):
            button = ttk.Button(modes, text=text, command=lambda mode=key: self.switch_mode(mode))
            button.pack(side="left", padx=(0, self.px(3)))
            self.mode_buttons[key] = button
        run_controls = ttk.Frame(controls, style="Inside.TFrame")
        run_controls.pack(side="left")
        self.monitor_button = ttk.Button(run_controls, command=self.toggle_monitor, style="Primary.TButton")
        self.auto_button = ttk.Button(run_controls, command=self.toggle_auto_registration, style="Primary.TButton")
        self.monitor_button.grid(row=0, column=0)
        self.auto_button.grid(row=0, column=0)
        self.refresh_button = ttk.Button(controls, text="刷新 F5", command=self.refresh)
        self.refresh_button.pack(side="left", padx=(self.px(8), 0))
        self.interval_var = tk.StringVar(value=state.get("interval", str(owner["interval"])))
        self.mode_hint = tk.StringVar()

        filters = ttk.Frame(self.body)
        filters.pack(fill="x", pady=(0, self.px(9)))
        scope = ttk.Frame(filters)
        scope.pack(side="left")
        ttk.Label(scope, text="校区 / 报名范围", style="Muted.TLabel").pack(side="left", padx=(0, self.px(8)))
        self.campus_var = tk.StringVar(value=owner["campus"] or "全部校区")
        self.campus_buttons = {}
        for key, text in (("", "全部"), *((name, name) for name in CAMPUSES)):
            button = ttk.Button(scope, text=text, command=lambda value=key: self.select_campus(value))
            button.pack(side="left", padx=(0, self.px(3)))
            self.campus_buttons[key] = button
        right = ttk.Frame(filters)
        right.pack(side="right")
        self.search_var = tk.StringVar(value=state.get("search", ""))
        ttk.Label(right, text="搜索", style="Muted.TLabel").pack(side="left", padx=(0, self.px(7)))
        ttk.Entry(right, textvariable=self.search_var, width=25).pack(side="left")
        self.filter_var = tk.StringVar(value=state.get("filter", "全部活动"))
        combo = ttk.Combobox(
            right,
            textvariable=self.filter_var,
            values=[
                "全部活动",
                "新发现",
                "不报名",
                "待开启",
                "可报名",
                "已报名",
                "名额已满",
                "需查看限制",
                "结果待确认",
                "已取消报名",
                "已结束",
            ],
            state="readonly",
            width=11,
        )
        combo.pack(side="left", padx=(self.px(8), 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: self.render_table())

        sorting = ttk.Frame(self.body)
        sorting.pack(fill="x", pady=(0, self.px(8)))
        ttk.Label(sorting, text="已报名置顶", style="Muted.TLabel").pack(side="left", padx=(0, self.px(10)))
        self.sort_buttons = {}
        for key, text in (("time", "活动时间"), ("remaining", "剩余名额")):
            button = ttk.Button(sorting, text=text, command=lambda mode=key: self.toggle_sort(mode))
            button.pack(side="left", padx=(0, self.px(5)))
            self.sort_buttons[key] = button
        self.sort_hint = tk.StringVar()
        ttk.Label(sorting, textvariable=self.sort_hint, style="Muted.TLabel").pack(side="left", padx=self.px(5))
        ttk.Label(sorting, text="按地点筛选，同时限制报名 · 勾选表示“不报名”", style="Muted.TLabel").pack(side="right")

        self.log_expanded = state.get("logs", False)
        self.log_frame = ttk.Frame(self.body, style="Card.TFrame", padding=(self.px(12), self.px(5)))
        self.log_frame.pack(side="bottom", fill="x", pady=(self.px(10), 0))
        log_header = ttk.Frame(self.log_frame, style="Inside.TFrame")
        log_header.pack(fill="x")
        self.log_button = ttk.Button(log_header, text="运行记录 ▸", style="Inline.TButton", command=self.toggle_logs)
        self.log_button.pack(side="left")
        self.log_summary = tk.StringVar()
        ttk.Label(log_header, textvariable=self.log_summary, style="CardMuted.TLabel", anchor="e").pack(
            side="right", fill="x", expand=True, padx=self.px(8)
        )
        self.log_text = tk.Text(
            self.log_frame,
            height=4,
            wrap="word",
            bg=SOFT,
            fg=MUTED,
            relief="flat",
            padx=self.px(10),
            pady=self.px(8),
            font=(FONT, 9),
            state="disabled",
        )
        if self.log_expanded:
            self.log_text.pack(fill="x", pady=(self.px(7), 0))
        self.log_button.configure(text="运行记录 ▾" if self.log_expanded else "运行记录 ▸")

        table_frame = ttk.Frame(self.body, style="Card.TFrame", padding=self.px(1))
        table_frame.pack(fill="both", expand=True)
        self.table = ActivityTable(
            table_frame,
            scale=self.scale,
            on_detail=self.detail,
            on_cancel=self.cancel_registration,
            on_recheck=self.reset_rejected,
        )
        bottom = ttk.Frame(table_frame, style="Inside.TFrame", padding=(self.px(10), self.px(5)))
        bottom.pack(side="bottom", fill="x", padx=self.px(5), pady=(0, self.px(3)))
        self.table.pack(fill="both", expand=True, padx=self.px(2), pady=(self.px(3), 0))
        self.count_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.count_var, style="CardMuted.TLabel").pack(side="left")
        self.cancel_button = ttk.Button(
            bottom, text="取消所选报名", style="Inline.TButton", command=self.cancel_registration
        )
        self.detail_button = ttk.Button(bottom, text="所选活动详情", style="Inline.TButton", command=self.detail)
        self.detail_button.pack(side="right")
        ttk.Label(bottom, text="北京时间 · Enter 查看详情", style="CardMuted.TLabel").pack(
            side="right", padx=self.px(10)
        )
        self.empty_state = ttk.Frame(self.table.canvas, style="Inside.TFrame", padding=self.px(20))
        ttk.Label(self.empty_state, text="当前范围暂无活动", style="CardTitle.TLabel").pack()
        ttk.Label(self.empty_state, text="可清除搜索与状态筛选，或切换校区查看。", style="CardMuted.TLabel").pack(
            pady=self.px(8)
        )
        ttk.Button(self.empty_state, text="清除搜索与状态筛选", command=self.clear_filters).pack()
        self.table.bind("<Button-1>", self.table_click)
        self.table.bind("<space>", self.toggle_excluded)
        self.table.bind(
            "<Double-1>", lambda e: self.detail() if self.table.identify_column(e.x) not in {"#1", "#8"} else None
        )
        self.table.bind("<<TreeviewSelect>>", self.selection_changed)
        self.search_var.trace_add("write", lambda *_a: self.render_table())
        self.render_table()
        selected = state.get("selection", ())
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])
        self.table.yview_moveto(state.get("scroll", 0))

    def select_campus(self, campus):
        if self.store.account(self.account_id)["campus"] != campus:
            self.campus_var.set(campus or "全部校区")
            self.change_campus()

    def clear_filters(self):
        self.filter_var.set("全部活动")
        self.search_var.set("")
        self.render_table()

    def toggle_logs(self):
        self.log_expanded = not self.log_expanded
        self.log_button.configure(text="运行记录 ▾" if self.log_expanded else "运行记录 ▸")
        if self.log_expanded:
            self.log_text.pack(fill="x", pady=(self.px(7), 0))
        else:
            self.log_text.pack_forget()

    def selection_changed(self, _event=None):
        chosen = self.table.selection()
        row = self.rows.get(chosen[0]) if chosen else None
        self.detail_button.configure(state="normal" if row else "disabled")
        if row and display_status(row) in {"registered", "success", "cancel_uncertain"}:
            self.cancel_button.configure(
                text="核对取消结果" if display_status(row) == "cancel_uncertain" else "取消所选报名"
            )
            self.cancel_button.pack(side="right", before=self.detail_button)
        else:
            self.cancel_button.pack_forget()

    def render_table(self):
        if not self.account_id or self.closing:
            return
        owner = self.store.account(self.account_id)
        if not owner:
            self.show_home()
            return
        selected_row = self.table.selection()
        old_y = self.table.yview()
        rows = self.store.activities(self.account_id)
        self.rows = {r["id"]: r for r in rows}
        search = self.search_var.get().strip().casefold()
        filt = self.filter_var.get()
        self.table.delete(*self.table.get_children())
        sorting = self.sort_preferences.get(self.account_id, {"time": True, "remaining": None})
        rows = order_rows(rows, time_descending=sorting["time"], remaining_descending=sorting["remaining"])
        for mode, button in self.sort_buttons.items():
            descending = sorting[mode]
            active = descending is not None
            label = "活动时间" if mode == "time" else "剩余名额"
            button.configure(
                text=label + ((" ↓" if descending else " ↑") if active else ""),
                style="Selected.TButton" if active else "TButton",
            )
        criteria = []
        if sorting["remaining"] is not None:
            criteria.append("剩余名额多→少" if sorting["remaining"] else "剩余名额少→多")
        if sorting["time"] is not None:
            criteria.append("活动时间新→旧" if sorting["time"] else "活动时间早→晚")
        self.sort_hint.set("名额优先，时间次之" if len(criteria) == 2 else "满额后置")
        for mode, button in self.mode_buttons.items():
            button.configure(style="Selected.TButton" if mode == owner["mode"] else "TButton")
        for campus, button in self.campus_buttons.items():
            button.configure(style="Selected.TButton" if campus == owner["campus"] else "TButton")
        self.mode_hint.set(
            "只提示，不提交报名。"
            if owner["mode"] == "observe"
            else (
                "自动报名已开启；现有及新增都检查，勾选“不报名”的除外。"
                if owner["enabled"]
                else "自动报名未开启，请点击上方“开启自动报名”。"
            )
        )
        visible = 0
        for row in rows:
            activity = Activity.from_row(row["raw"])
            status = display_status(row)
            label = LABELS.get(status, "待检查")
            if not matches_campus(row["raw"], owner["campus"]):
                continue
            if search and search not in (activity.title + " " + activity.address).casefold():
                continue
            if filt == "不报名" and not row["excluded"]:
                continue
            if filt == "新发现" and not row["is_new"]:
                continue
            if filt == "已报名" and status not in {"registered", "success"}:
                continue
            if filt not in {"不报名", "全部活动", "新发现", "已报名"} and label != filt:
                continue
            if filt != "不报名" and owner["term"] and row["raw"].get("_term", owner["term"]) != owner["term"]:
                continue
            self.table.insert(
                "",
                "end",
                iid=row["id"],
                values=(
                    "☑" if row["excluded"] else "☐",
                    ("新 · " if row["is_new"] else "") + activity.title,
                    label,
                    pair_time(activity.raw, "bmStart", "bmEnd", multiline=True),
                    pair_time(activity.raw, "startTime", "endTime", multiline=True),
                    activity.address,
                    remaining_label(row),
                ),
                tags=(status,),
            )
            visible += 1
        if selected_row and self.table.exists(selected_row[0]):
            self.table.selection_set(selected_row[0])
        if old_y:
            self.table.yview_moveto(old_y[0])
        excluded = sum(r["excluded"] for r in rows)
        new_count = sum(r["is_new"] for r in rows)
        self.count_var.set(f"显示 {visible} / {len(rows)} 项  ·  新发现 {new_count}  ·  不报名 {excluded}")
        self.monitor_button.configure(text="暂停监测" if owner["enabled"] else "开启监测")
        auto_enabled = owner["mode"] == "auto" and owner["enabled"]
        self.auto_button.configure(text="暂停自动报名" if auto_enabled else "开启自动报名")
        if owner["mode"] == "auto":
            self.monitor_button.grid_remove()
            self.auto_button.grid()
        else:
            self.auto_button.grid_remove()
            self.monitor_button.grid()
        stamp = owner["checked"]
        try:
            stamp = datetime.fromisoformat(stamp).astimezone(BEIJING).strftime("%m-%d %H:%M:%S")
        except ValueError:
            stamp = "尚未检查"
        run_state = (
            ("自动报名已开启" if auto_enabled else "自动报名未开启")
            if owner["mode"] == "auto"
            else ("仅监测已开启" if owner["enabled"] else "监控未开启")
        )
        self.status_var.set(
            f"{run_state}   ·   {owner['status']}   ·   最近检查 {stamp} 北京时间   ·   报名范围：{owner['campus'] or '全部校区'}"
            + ("   ·   正在刷新…" if self.account_id in self.busy else "")
        )
        self.run_title.set(run_state + (" · 刷新中" if self.account_id in self.busy else ""))
        self.run_label.configure(
            foreground=GREEN
            if owner["enabled"] and owner["status"] == "已登录"
            else AMBER
            if owner["enabled"]
            else MUTED
        )
        self.monitor_info.set(
            f"{owner['campus'] or '全部校区'}  ·  每 {owner['interval']} 秒检查  ·  最近 {stamp[6:] if stamp != '尚未检查' else stamp}"
        )
        self.school_status.set("学校：" + owner["status"])
        self.school_label.configure(foreground=GREEN if owner["status"] == "已登录" else AMBER)
        self.refresh_button.configure(
            text="刷新中…" if self.account_id in self.busy else "刷新 F5",
            state="disabled" if self.account_id in self.busy else "normal",
        )
        if visible:
            self.empty_state.place_forget()
        else:
            self.empty_state.place(relx=0.5, rely=0.45, anchor="center")
        self.selection_changed()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        logs = self.store.events(self.account_id)
        if logs:
            latest = logs[0]
            t = datetime.fromisoformat(latest["time"]).astimezone(BEIJING).strftime("%H:%M:%S")
            message = latest["message"]
            self.log_summary.set(t + "  " + (message[:45] + "…" if len(message) > 45 else message))
        else:
            self.log_summary.set("尚无记录，开启监测后在此查看")
        for entry in logs[:6]:
            t = datetime.fromisoformat(entry["time"]).astimezone(BEIJING).strftime("%H:%M:%S")
            self.log_text.insert("end", t + "  " + entry["message"] + "\n")
        if not logs:
            self.log_text.insert("end", "尚无运行记录。保存登录态后选择模式并开始监控；勾选表示不报名。")
        self.log_text.configure(state="disabled")

    def table_click(self, event):
        if self.table.identify_region(event.x, event.y) == "cell" and self.table.identify_column(event.x) == "#1":
            row = self.table.identify_row(event.y)
            if row:
                self.table.selection_set(row)
                self.toggle_excluded()
                return "break"

    def toggle_sort(self, mode):
        sorting = self.sort_preferences.setdefault(self.account_id, {"time": True, "remaining": None})
        current = sorting[mode]
        sorting[mode] = True if current is None else False if current else None
        self.render_table()
        self.table.yview_moveto(0)

    def switch_mode(self, mode):
        if self.store.set_mode(self.account_id, mode):
            name = "现有和新增自动报名" if mode == "auto" else "仅监测新活动"
            self.store.log(self.account_id, "已切换为" + name + "，重新开始监控时建立基准。")
            start_button = "开启自动报名" if mode == "auto" else "开启监测"
            self.footer.set("已切换为" + name + f"。点击“{start_button}”启动。")
        self.render_table()

    def toggle_excluded(self, _event=None):
        chosen = self.table.selection()
        if chosen and chosen[0] in self.rows:
            row = self.rows[chosen[0]]
            excluded = not row["excluded"]
            self.store.exclude(self.account_id, row["id"], excluded)
            self.footer.set(
                "已标记不再自动报名；已报名的活动请使用“取消报名”。"
                if excluded
                else "已解除“不报名”标记，该活动符合条件时会尝试报名。"
            )
            owner = self.store.account(self.account_id)
            if not excluded and owner["enabled"]:
                self.send_command("monitor", self.account_id)
            self.render_table()
        return "break"

    def cancel_registration(self):
        chosen = self.table.selection()
        if not chosen or chosen[0] not in self.rows:
            messagebox.showinfo("取消报名", "请先点击要取消的活动行。", parent=self.root)
            return
        row = self.rows[chosen[0]]
        activity = Activity.from_row(row["raw"])
        if row.get("cancel_status") == "cancel_uncertain":
            text = f"检查“{activity.title}”的取消结果？\n只读取网站状态，不会重复发送取消请求。"
        else:
            text = f"确认取消“{activity.title}”的报名？\n该活动会同时勾选“不报名”，防止再次自动报名。"
        if not messagebox.askyesno("取消报名", text, parent=self.root):
            return
        self.store.exclude(self.account_id, activity.id, True)
        if self.send_command("cancel", self.account_id, activity=activity.id):
            self.footer.set("正在检查并取消报名，结果会显示在活动状态和详情中。")
        self.render_table()

    def toggle_monitor(self):
        owner = self.store.account(self.account_id)
        if owner["enabled"]:
            self.store.configure(self.account_id, enabled=False)
            self.store.log(self.account_id, "已暂停监控。")
        else:
            self._start_monitor()
        self.render_table()

    def toggle_auto_registration(self):
        owner = self.store.account(self.account_id)
        if owner["mode"] == "auto" and owner["enabled"]:
            self.store.configure(self.account_id, enabled=False)
            self.store.log(self.account_id, "已关闭自动报名。")
            self.footer.set("自动报名已关闭。")
        else:
            self._start_monitor(mode="auto")
        self.render_table()

    def _start_monitor(self, *, mode=None):
        if not self.vault.path(self.account_id).exists():
            messagebox.showinfo("先登录", "请先保存个人 Cookie 或完成网页登录。", parent=self.root)
            return False
        try:
            seconds = int(self.interval_var.get())
            if not 5 <= seconds <= 3600:
                raise ValueError()
        except ValueError:
            messagebox.showinfo("检查间隔", "请输入 5–3600 秒。", parent=self.root)
            return False
        if mode:
            self.store.set_mode(self.account_id, mode)
        self.store.start_monitor(self.account_id, seconds)
        if not self.send_command("monitor", self.account_id):
            self.store.configure(self.account_id, enabled=False)
            return False
        automatic = self.store.account(self.account_id)["mode"] == "auto"
        message = (
            "已开启自动报名，将检查现有和新增活动；勾选“不报名”的除外。"
            if automatic
            else "已开始监控，首次读取建立现有活动基准。"
        )
        self.store.log(self.account_id, message)
        self.footer.set(message)
        return True

    def change_term(self, _event):
        self.store.change_term(self.account_id, self.term_var.get())
        self.footer.set("学期已切换。重新开始监控时会以当前学期建立基准。")
        self.refresh()

    def change_campus(self, _event=None):
        campus = self.campus_var.get()
        self.store.set_campus(self.account_id, "" if campus == "全部校区" else campus)
        self.store.log(self.account_id, f"校区及自动报名范围已切换为：{campus}（按活动地点识别）。")
        self.footer.set(f"当前校区及报名范围：{campus}。地点未写校区的活动只在全部校区显示。")
        if self.store.account(self.account_id)["enabled"]:
            self.send_command("monitor", self.account_id)
        self.render_table()
        self.table.yview_moveto(0)

    def refresh(self):
        if self.account_id:
            self.send_command("refresh", self.account_id)
        else:
            self.show_home()

    def open_website(self):
        selected = self.table.selection()
        self.send_command("open", self.account_id, activity=selected[0] if selected else None)

    def detail(self):
        selected = self.table.selection()
        if not selected or selected[0] not in self.rows:
            return
        row = self.rows[selected[0]]
        activity = Activity.from_row(row["raw"])
        account = self.account_id
        notification = self.store.notification(account, activity.id)
        dialog = tk.Toplevel(self.root)
        dialog.title(activity.title)
        fit_window(dialog, 720, 620, 540, 420)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        if self.icon_path.exists():
            dialog.iconbitmap(str(self.icon_path))
            install_window_icons(dialog, self.icon_path)
        heading = ttk.Frame(dialog, padding=(self.px(24), self.px(18), self.px(24), self.px(12)))
        heading.pack(fill="x")
        ttk.Label(heading, text=activity.title, style="Section.TLabel", wraplength=self.px(640)).pack(anchor="w")
        ttk.Label(heading, text=LABELS.get(display_status(row), "待检查") + " · 北京时间", style="Muted.TLabel").pack(
            anchor="w", pady=(self.px(6), 0)
        )
        body = ttk.Frame(dialog, padding=(self.px(24), 0, self.px(24), 0))
        body.pack(fill="both", expand=True)
        content = tk.Text(
            body, wrap="word", relief="flat", padx=self.px(16), pady=self.px(14), font=(FONT, 10), spacing3=self.px(4)
        )
        scrollbar = ttk.Scrollbar(body, command=content.yview)
        scrollbar.pack(side="right", fill="y")
        content.configure(yscrollcommand=scrollbar.set)
        content.pack(fill="both", expand=True)
        lines = [
            "报名窗口（北京时间）",
            activity.window,
            "",
            "活动时间（北京时间）",
            activity.times,
            "",
            "地点：" + activity.address,
            "人数：" + activity.capacity,
            "模块：" + str(activity.raw.get("module") or "未提供"),
            "",
            "处理结果："
            + (row["cancel_message"] or row["attempt_message"] or LABELS.get(display_status(row), "待检查")),
            "微信通知："
            + (
                notification["detail"]
                or {"queued": "等待发送", "sending": "正在发送"}.get(notification["state"], notification["state"])
                if notification
                else "本次无待发通知"
            ),
            "",
            "活动内容",
            plain_text(activity.raw.get("content")) or "请点击“在学校网站查看”读取完整活动说明。",
            "",
            "注意事项",
            plain_text(activity.raw.get("precautions")) or "未提供",
        ]
        content.insert("1.0", "\n".join(lines))
        content.tag_configure("section", foreground=MUTED, font=(FONT, 9, "bold"))
        for index, line in enumerate(lines, 1):
            if line in {"报名窗口（北京时间）", "活动时间（北京时间）", "活动内容", "注意事项"}:
                content.tag_add("section", f"{index}.0", f"{index}.end")
        content.configure(state="disabled")
        ttk.Button(
            dialog,
            text="在学校网站查看",
            command=lambda: self.send_command("open", account, activity=activity.id),
        ).pack(pady=self.px(12))
        dialog.bind("<Escape>", lambda _e: dialog.destroy())

    def reset_rejected(self):
        selected = self.table.selection()
        if not selected:
            return
        status = self.store.attempt(self.account_id, selected[0])
        if not status or status["status"] not in {"blocked", "rejected"}:
            messagebox.showinfo(
                "无需重置",
                "仅明确未通过或需要人工查看的活动可以重新检查。结果待确认的活动不会重发，请先在学校网站核对。",
                parent=self.root,
            )
            return
        if messagebox.askyesno(
            "重新检查", "下一次监控会重新检查这项活动，符合条件时再次尝试报名。继续？", parent=self.root
        ):
            self.store.reset_rejected(self.account_id, selected[0])
            self.store.log(self.account_id, "用户已要求重新检查一项未通过的活动。")
            self.render_table()

    def remove(self, account):
        if messagebox.askyesno(
            "移除个人 Cookie", "移除此账号的本机登录态和勾选记录？不会取消学校网站上的报名。", parent=self.root
        ):
            self.store.configure(account, enabled=False)
            return self.send_command("remove", account)
        return False

    def rename(self, account):
        owner = self.store.account(account)
        if not owner:
            return
        value = simpledialog.askstring("重命名", "新的账号备注：", initialvalue=owner["name"], parent=self.root)
        if value is None:
            return
        try:
            self.store.rename_account(account, value)
        except ValueError as error:
            messagebox.showerror("账号备注", str(error), parent=self.root)
            return
        self.show_account(account) if self.account_id == account else self.show_home()

    def send_command(self, kind, account, **payload):
        try:
            self.worker.send(kind, account, **payload)
            return True
        except WorkerUnavailable as error:
            self.footer.set(str(error))
            messagebox.showerror("后台已停止", str(error), parent=self.root)
        except OSError:
            messagebox.showerror(
                "保存未完成", "无法加密保存登录数据，请检查本机数据目录。输入内容仍保留在窗口中。", parent=self.root
            )
        return False

    def help(self):
        messagebox.showinfo(
            "使用说明",
            "1. 保存个人 Cookie，进入账号。\n2. 自动报名：选择“监测并自动报名”，点击“开启自动报名”；再点“暂停自动报名”停止。\n3. 仅监测：选择“仅监测”，再点“开启监测”。\n\n仅选择自动模式尚未运行，以按钮和状态栏显示的开启状态为准。自动模式从首次检查起就处理现有和新增活动，未开放或暂时满额的继续等待；仅监测模式首次建立基准，随后提示新增。\n勾选表示“不报名”，不需要先勾选才能报名。此标记按账号保存，切换模式或重开后仍保留。勾选不会取消已有报名，需点击“取消报名”；取消后自动保留“不报名”标记。\n\n已报名和报名成功的活动固定置顶，其后是可报名活动，满额排在其余未报名活动后。两个排序按钮可以同时开启：先按剩余名额，再按活动时间，各自按“降序→升序→关闭”循环。\n校区快捷按钮同时限制自动报名范围；检查间隔在“账号设置”中修改。\n运行日志可展开，方向键选择活动，空格切换“不报名”，Enter 查看详情，右键可复查限制。\n切换模式或学期会暂停监控。\n最小化继续运行，关闭或休眠后停止。\n报名或取消结果待确认时不会自动重发，学校额外限制需在官网处理。",
            parent=self.root,
        )

    def poll(self):
        if self.closing:
            return
        if self.notifier:
            status = self.notifier.sender.status
            self.wechat_status.set("微信：" + status)
            self.wechat_label.configure(foreground=GREEN if status == "已连接" else MUTED)
        changed = False
        try:
            while True:
                kind, account, data = self.worker.events.get_nowait()
                if kind == "terms":
                    self.terms[account] = [str(t["school_term"]) for t in data["terms"] if t.get("school_term")]
                    if self.account_id == account:
                        self.term_combo.configure(values=self.terms[account])
                        owner = self.store.account(account)
                        term = owner["term"] if owner else ""
                        if term:
                            self.term_var.set(term)
                elif kind == "busy":
                    self.busy.add(account)
                elif kind == "idle":
                    self.busy.discard(account)
                elif kind in {"worker_failed", "wechat_notification"}:
                    self.footer.set(data["message"])
                elif kind == "new_activity":
                    owner = self.store.account(account)
                    self.footer.set((owner["name"] + "：" if owner else "") + data["message"])
                    self.root.bell()
                changed = True
        except queue.Empty:
            pass
        if changed:
            self.render_table() if self.account_id else self.show_home()
        self.root.after(250, self.poll)

    def close(self):
        if self.closing:
            return
        self.closing = True
        for account in self.store.accounts():
            self.store.configure(account["id"], enabled=False)
        self.footer.set("正在结束网络检查并释放登录窗口，请稍候…")
        self.worker.stop()
        if self.notifier:
            self.notifier.stop()
        self.root.after(100, self._wait_close)

    def _wait_close(self):
        if self.worker.is_alive() or (self.notifier and self.notifier.is_alive()):
            self.root.after(100, self._wait_close)
        else:
            self.root.destroy()
