# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import queue
import time
import tkinter as tk

import pytest

from activity_check.identity import enable_dpi_awareness
from activity_check.store import Store
from activity_check.ui import AccountSettingsDialog, App
from activity_check.vault import Vault
from activity_check.worker import WorkerUnavailable


class WorkerStub:
    def __init__(self):
        self.events = queue.Queue()
        self.commands = []
        self.failed = False

    def send(self, kind, account, **payload):
        if self.failed:
            raise WorkerUnavailable("测试后台已停止")
        self.commands.append((kind, account, payload))


@pytest.fixture(scope="module")
def tk_root():
    enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def desktop(tk_root, tmp_path, monkeypatch):
    root = tk_root
    store = Store(tmp_path / "app.db")
    account = store.add_account("按钮隔离验证")
    vault = Vault(tmp_path / "sessions")
    vault.save(account, '{"token":"fixture-only","teaInfo":{"id":"A"}}')
    worker = WorkerStub()
    app = App(root, store, vault, worker)
    app.show_account(account)
    monkeypatch.setattr("activity_check.ui.messagebox.showinfo", lambda *_a, **_k: None)
    monkeypatch.setattr("activity_check.ui.messagebox.showerror", lambda *_a, **_k: None)
    yield app, store, account, worker
    app.closing = True
    for pending in root.tk.call("after", "info"):
        root.after_cancel(pending)
    for widget in root.winfo_children():
        widget.destroy()


def test_button_switches_mode_starts_and_stops_without_losing_inputs(desktop):
    app, store, account, worker = desktop
    store.save_activities(account, [{"id": "42", "projectName": "示例活动"}])
    store.exclude(account, "42", True)
    app.search_var.set("示例")
    app.interval_var.set("15")
    app.auto_button.invoke()
    assert store.account(account)["mode"] == "auto"
    assert store.account(account)["enabled"]
    assert store.account(account)["interval"] == 15
    assert not store.account(account)["baseline_ready"]
    assert worker.commands[-1] == ("monitor", account, {})
    assert app.auto_button.cget("text") == "暂停自动报名"
    assert "自动报名已开启" in app.status_var.get()
    assert app.search_var.get() == "示例" and store.is_excluded(account, "42")
    app.auto_button.invoke()
    assert not store.account(account)["enabled"]
    assert app.auto_button.cget("text") == "开启自动报名"
    assert "自动报名未开启" in app.status_var.get()


def test_invalid_interval_preserves_running_observe_mode(desktop):
    app, store, account, worker = desktop
    store.start_monitor(account, 30)
    app.interval_var.set("错误值")
    app.auto_button.invoke()
    assert store.account(account)["mode"] == "observe"
    assert store.account(account)["enabled"]
    assert not worker.commands


def test_missing_login_or_dead_worker_never_shows_auto_as_running(desktop):
    app, store, account, worker = desktop
    app.vault.remove(account)
    app.auto_button.invoke()
    assert not store.account(account)["enabled"]
    app.vault.save(account, "fixture-only")
    worker.failed = True
    app.auto_button.invoke()
    assert not store.account(account)["enabled"]
    assert app.auto_button.cget("text") == "开启自动报名"
    assert "自动报名已开启" not in app.status_var.get()


def test_mode_controls_show_one_primary_action_and_switching_pauses(desktop):
    app, store, account, _worker = desktop
    assert app.monitor_button.grid_info() and not app.auto_button.grid_info()
    app.mode_buttons["auto"].invoke()
    assert not store.account(account)["enabled"]
    assert app.auto_button.grid_info() and not app.monitor_button.grid_info()
    app.auto_button.invoke()
    assert store.account(account)["enabled"]
    app.mode_buttons["observe"].invoke()
    assert not store.account(account)["enabled"]
    assert app.monitor_button.grid_info() and not app.auto_button.grid_info()


def test_account_navigation_preserves_view_and_unsaved_interval(desktop):
    app, store, account, _worker = desktop
    store.save_activities(account, [{"id": "42", "projectName": "示例活动"}])
    app.search_var.set("示例")
    app.filter_var.set("不报名")
    store.exclude(account, "42", True)
    app.interval_var.set("尚未保存")
    app.toggle_sort("remaining")
    app.toggle_logs()
    app.table.selection_set("42")
    app.show_home()
    app.show_account(account)
    assert app.search_var.get() == "示例"
    assert app.filter_var.get() == "不报名"
    assert app.interval_var.get() == "尚未保存" and store.account(account)["interval"] == 10
    assert app.sort_preferences[account] == {"time": True, "remaining": True}
    assert app.table.selection() == ("42",) and app.log_expanded
    assert store.is_excluded(account, "42")


def test_campus_shortcut_and_clear_filters_never_start_or_broaden_booking(desktop):
    app, store, account, worker = desktop
    app.campus_buttons["钱湖"].invoke()
    app.filter_var.set("已报名")
    app.search_var.set("不存在的活动")
    app.clear_filters()
    assert store.account(account)["campus"] == "钱湖"
    assert not store.account(account)["enabled"]
    assert not worker.commands
    assert app.search_var.get() == "" and app.filter_var.get() == "全部活动"


def test_interval_settings_validate_without_starting_monitor(desktop):
    app, store, account, worker = desktop
    dialog = AccountSettingsDialog(app, account)
    dialog.interval.set("无效")
    dialog.save()
    assert dialog.winfo_exists() and store.account(account)["interval"] == 10
    dialog.interval.set("15")
    dialog.save()
    assert store.account(account)["interval"] == 15 and app.interval_var.get() == "15"
    assert not store.account(account)["enabled"] and not worker.commands


def test_large_list_scroll_settles_and_refresh_preserves_selection(desktop, monkeypatch):
    app, store, account, _worker = desktop
    store.save_activities(account, [{"id": str(i), "projectName": f"示例活动 {i}"} for i in range(1000)])
    app.render_table()
    app.table.selection_set("500")
    draws = []
    original_draw = app.table._draw

    def counted_draw():
        draws.append(True)
        original_draw()

    monkeypatch.setattr(app.table, "_draw", counted_draw)
    app.table.yview_moveto(0.6)
    app.root.after(150, app.root.quit)
    app.root.mainloop()
    assert len(draws) < 12  # An unchanged scroll range must not keep repainting.
    assert len(app.table.canvas.find_withtag("paint")) < 500
    position = app.table.yview()[0]
    app.render_table()
    assert app.table.selection() == ("500",)
    assert abs(app.table.yview()[0] - position) < 0.01


def test_visible_window_paints_without_blocking_for_seconds(desktop):
    app, store, account, worker = desktop
    store.save_activities(account, [{"id": str(i), "projectName": f"示例活动 {i}"} for i in range(141)])
    app.render_table()
    paints = []
    try:
        app.root.deiconify()
        for width in (1600, 1700, 1600):
            app.root.geometry(f"{width}x1050")
            before = time.perf_counter()
            app.root.update_idletasks()
            paints.append(time.perf_counter() - before)
        assert max(paints) < 2, f"Native background painting blocked the UI: {paints}"
        assert not store.account(account)["enabled"] and not worker.commands
    finally:
        app.root.withdraw()


def test_background_event_burst_coalesces_renders_and_keeps_latest_data(desktop, monkeypatch):
    app, store, account, worker = desktop
    renders = []
    original_render = app.render_table

    def render():
        renders.append(time.monotonic())
        original_render()

    monkeypatch.setattr(app, "render_table", render)
    app.search_var.set("示例")
    renders.clear()
    app.interval_var.set("尚未保存")
    for i in range(5):
        app.root.after(i * 100, lambda: (worker.events.put(("changed", account, {})), app.poll()))
    app.root.after(450, lambda: store.save_activities(account, [{"id": "last", "projectName": "示例活动"}]))
    app.root.after(460, lambda: worker.events.put(("changed", account, {})))
    app.root.after(1300, app.root.quit)
    app.root.mainloop()
    assert len(renders) <= 2, f"Background updates rebuilt the view {len(renders)} times"
    assert app.table.exists("last")
    assert app.search_var.get() == "示例" and app.interval_var.get() == "尚未保存"
    assert not store.account(account)["enabled"] and not worker.commands
