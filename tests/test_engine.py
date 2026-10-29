# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs

import pytest

from activity_check.engine import Engine
from activity_check.models import BEIJING
from activity_check.session import import_session
from activity_check.site import SiteClient, LoginExpired
from activity_check.store import Store


DETAIL = {
    "id": "42",
    "projectName": "独立测试活动",
    "state": 1,
    "bmState": 2,
    "rz": "否",
    "currentNum": 2,
    "maxNum": 10,
    "bmStart": "2026-10-30 16:00:00",
    "bmEnd": "2026-10-30 20:00:00",
}


@contextmanager
def fixture_site(mode="success", detail=None, preflight=None, listed=True, rows=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            calls.append((self.path, values))
            if self.headers.get("Authorization") != "Bearer fixture-token":
                self.send_error(401)
                return
            if mode == "expired":
                body = {"code": 401, "msg": "请先登录智慧思政"}
            elif self.path == "/c/credit/getHdTermList":
                body = {"code": 0, "data": [{"school_term": "2026秋", "current": 1}]}
            elif self.path == "/s/credit/getHdBmList":
                assert values["studentId"] == ["A"]
                body = {"code": 0, "data": rows if rows is not None else [DETAIL] if listed else []}
            elif self.path == "/c/credit/getHdBmDetails":
                assert values == {"id": ["42"], "studentId": ["A"]}
                body = {"code": 0, "data": detail if detail is not None else DETAIL}
            elif self.path == "/s/credit/hdLose":
                assert values == {"studentId": ["A"]}
                body = preflight or {"code": 0}
            elif self.path in {"/s/credit/hdBm", "/s/credit/qxHdBm"}:
                assert values == {"id": ["42"], "studentId": ["A"]}
                if mode == "dropped":
                    self.close_connection = True
                    self.connection.close()
                    return
                body = {"code": 0} if mode == "success" else {"code": 500, "msg": "名额已满"}
            else:
                raise AssertionError("Unapproved endpoint " + self.path)
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        session = import_session('{"token":"fixture-token","teaInfo":{"id":"A","status":"1"}}')
        yield SiteClient(session, base_url=f"http://127.0.0.1:{server.server_port}"), calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def prepare(tmp_path):
    store = Store(tmp_path / "app.db")
    account = store.add_account("测试")
    store.bind_identity(account, "A")
    store.set_mode(account, "auto")
    store.start_monitor(account, 30)
    store.record_snapshot(account, [])
    store.record_snapshot(account, [DETAIL])
    engine = Engine(store, clock=lambda: datetime(2026, 10, 30, 17, tzinfo=BEIJING))
    return store, account, engine


def test_success_uses_school_preflight_and_is_never_sent_twice(tmp_path):
    store, account, engine = prepare(tmp_path)
    with fixture_site() as (site, calls):
        engine.process(account, DETAIL, site)
        engine.process(account, DETAIL, site)
    assert store.attempt(account, "42")["status"] == "success"
    assert [c[0] for c in calls] == ["/c/credit/getHdBmDetails", "/s/credit/hdLose", "/s/credit/hdBm"]


def test_excluded_activity_never_makes_registration_request(tmp_path):
    store, account, engine = prepare(tmp_path)
    store.exclude(account, "42", True)
    with fixture_site() as (site, calls):
        engine.process(account, DETAIL, site)
    assert calls == []


def test_connection_lost_after_send_remains_uncertain(tmp_path):
    store, account, engine = prepare(tmp_path)
    with fixture_site(mode="dropped") as (site, calls):
        engine.process(account, DETAIL, site)
        engine.process(account, DETAIL, site)
    assert store.attempt(account, "42")["status"] == "uncertain"
    assert [c[0] for c in calls].count("/s/credit/hdBm") == 1


def test_missing_time_and_full_activity_never_submit(tmp_path):
    store, account, engine = prepare(tmp_path)
    for detail in [DETAIL | {"bmEnd": ""}, DETAIL | {"maxNum": 2}]:
        with fixture_site(detail=detail) as (site, calls):
            engine.process(account, DETAIL, site)
        assert [c[0] for c in calls] == ["/c/credit/getHdBmDetails"]
    assert store.attempt(account, "42") is None


def test_absence_warning_is_not_automatically_confirmed(tmp_path):
    store, account, engine = prepare(tmp_path)
    with fixture_site(preflight={"code": 1, "msg": "需要查看缺席提示", "data": {"loseNum": 1}}) as (site, calls):
        engine.process(account, DETAIL, site)
    assert store.attempt(account, "42")["status"] == "blocked"
    assert "/s/credit/hdBm" not in [c[0] for c in calls]


def test_unexpired_credential_is_not_inferred_from_http_200(tmp_path):
    _store, account, engine = prepare(tmp_path)
    with fixture_site(mode="expired") as (site, calls):
        with pytest.raises(LoginExpired):
            engine.process(account, DETAIL, site)
    assert len(calls) == 1


def test_server_rejection_stops_until_manual_reset(tmp_path):
    store, account, engine = prepare(tmp_path)
    with fixture_site(mode="rejected") as (site, calls):
        engine.process(account, DETAIL, site)
        engine.process(account, DETAIL, site)
    assert store.attempt(account, "42")["status"] == "rejected"
    assert [c[0] for c in calls].count("/s/credit/hdBm") == 1


def test_server_already_registered_prevents_duplicate_without_history(tmp_path):
    store, account, engine = prepare(tmp_path)
    with fixture_site(detail=DETAIL | {"bmState": 1}) as (site, calls):
        engine.process(account, DETAIL, site)
    assert store.attempt(account, "42")["status"] == "success"
    assert [c[0] for c in calls] == ["/c/credit/getHdBmDetails"]


def test_worker_does_not_submit_selected_activity_that_disappeared_from_site(tmp_path):
    from activity_check.worker import Worker
    from activity_check.vault import Vault

    store, account, engine = prepare(tmp_path)
    worker = Worker(store, Vault(tmp_path / "sessions"))
    worker.engine = engine
    store.save_activities(account, [DETAIL | {"bmStart": "2000-01-01 00:00:00", "bmEnd": "2100-01-01 00:00:00"}])
    with fixture_site(listed=False) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
    assert "/s/credit/hdBm" not in [c[0] for c in calls]
    assert store.attempt(account, "42") is None


def test_deadline_is_rechecked_after_slow_preflight(tmp_path):
    store, account, engine = prepare(tmp_path)
    moments = iter([datetime(2026, 10, 30, 19, 59, tzinfo=BEIJING), datetime(2026, 10, 30, 20, 0, tzinfo=BEIJING)])
    engine.clock = lambda: next(moments)
    with fixture_site() as (site, calls):
        engine.process(account, DETAIL, site)
    assert store.attempt(account, "42") is None
    assert [c[0] for c in calls] == ["/c/credit/getHdBmDetails", "/s/credit/hdLose"]


def test_web_login_is_captured_encrypted_and_restorable(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright
    import activity_check.worker as worker_module
    from activity_check.models import ORIGIN
    from activity_check.vault import Vault

    store = Store(tmp_path / "app.db")
    account = store.add_account("浏览器隔离验证")
    vault = Vault(tmp_path / "sessions")
    worker = worker_module.Worker(store, vault)
    with fixture_site() as (site, calls):
        monkeypatch.setattr(worker_module, "SiteClient", lambda session: SiteClient(session, base_url=site.base_url))
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context()
            context.route(
                ORIGIN + "/**",
                lambda route: route.fulfill(
                    content_type="text/html", body="<html><body>Local authentication fixture</body></html>"
                ),
            )
            page = context.new_page()
            page.goto(ORIGIN + "/zhxg/")
            page.evaluate(
                "() => { localStorage.setItem('token','fixture-token'); localStorage.setItem('teaInfo',JSON.stringify({id:'A',status:'1'})); }"
            )
            worker.login_windows[account] = {
                "context": context,
                "save": True,
                "last": 0,
                "signature": "",
                "retry_at": 0,
            }
            worker._check_login_windows()
            saved = import_session(vault.load(account))
            assert saved.student_id == "A"
            assert store.account(account)["status"] == "已登录"
            assert account not in worker.login_windows
            restored = browser.new_context(storage_state=saved.state)
            restored.route(ORIGIN + "/**", lambda route: route.fulfill(body="<html>Restored</html>"))
            restored_page = restored.new_page()
            restored_page.goto(ORIGIN + "/zhxg/#/lifePractice/signup/list")
            assert restored_page.evaluate("() => JSON.parse(localStorage.getItem('teaInfo')).id") == "A"
            browser.close()
    assert "/s/credit/hdBm" not in [c[0] for c in calls]


def test_running_worker_processes_import_command_on_python313(tmp_path, monkeypatch):
    import time
    import activity_check.worker as worker_module
    from activity_check.vault import Vault

    store = Store(tmp_path / "app.db")
    account = store.add_account("真实线程验证")
    vault = Vault(tmp_path / "sessions")
    with fixture_site() as (site, calls):
        monkeypatch.setattr(worker_module, "SiteClient", lambda session: SiteClient(session, base_url=site.base_url))
        worker = worker_module.Worker(store, vault)
        worker.start()
        try:
            worker.send(
                "import",
                account,
                raw='{"cookie":"","localStorage":{"token":"fixture-token","teaInfo":"{\\"id\\":\\"A\\",\\"status\\":\\"1\\"}"}}',
            )
            deadline = time.monotonic() + 3
            while worker.is_alive() and store.account(account)["status"] != "已登录" and time.monotonic() < deadline:
                time.sleep(0.02)
            assert worker.is_alive(), "后台线程在第一次处理导入时退出"
            assert store.account(account)["status"] == "已登录"
            assert vault.path(account).exists()
        finally:
            worker.stop()
            worker.join(timeout=3)
    assert "/s/credit/hdBm" not in [c[0] for c in calls]


def test_import_is_encrypted_before_worker_runs_and_resumes_after_restart(tmp_path, monkeypatch):
    import time
    import activity_check.worker as worker_module
    from activity_check.vault import Vault

    store = Store(tmp_path / "app.db")
    account = store.add_account("导入恢复")
    vault = Vault(tmp_path / "sessions")
    first = worker_module.Worker(store, vault)
    first.send("import", account, raw='{"token":"fixture-token","teaInfo":{"id":"A","status":"1"}}')
    pending_path = tmp_path / "pending-sessions" / (account + ".auth")
    assert pending_path.exists()
    assert b"fixture-token" not in pending_path.read_bytes()
    with fixture_site() as (site, calls):
        monkeypatch.setattr(worker_module, "SiteClient", lambda session: SiteClient(session, base_url=site.base_url))
        replacement = worker_module.Worker(store, vault)
        replacement.start()
        try:
            deadline = time.monotonic() + 3
            while (
                store.account(account)["status"] != "已登录" or pending_path.exists()
            ) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert store.account(account)["status"] == "已登录"
            assert vault.path(account).exists()
            assert not pending_path.exists()
        finally:
            replacement.stop()
            replacement.join(timeout=3)
    assert "/s/credit/hdBm" not in [c[0] for c in calls]


def test_unexpected_worker_error_pauses_account_and_reports_without_values(tmp_path, monkeypatch):
    import activity_check.worker as worker_module
    from activity_check.vault import Vault

    store = Store(tmp_path / "app.db")
    account = store.add_account("异常提示")
    store.configure(account, enabled=True)
    worker = worker_module.Worker(store, Vault(tmp_path / "sessions"))

    def fail(*_args):
        raise RuntimeError("private-fixture-do-not-log")

    monkeypatch.setattr(worker, "_dispatch_command", fail)
    worker.start()
    worker.send("refresh", account)
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert store.account(account)["status"] == "后台已停止"
    assert not store.account(account)["enabled"]
    assert "private-fixture-do-not-log" not in (tmp_path / "worker-error.log").read_text(encoding="utf-8")
