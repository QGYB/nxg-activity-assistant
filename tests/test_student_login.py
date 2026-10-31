# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import json
from types import SimpleNamespace

import pytest

from activity_check.models import LOGIN_URL, ORIGIN
from activity_check.session import SessionError, import_session, login_page_location
from activity_check.store import Store
from activity_check.vault import Vault
from activity_check.worker import Worker


@pytest.mark.parametrize("key", ["stuInfo", "userInfo", "studentInfo"])
@pytest.mark.parametrize("as_string", [False, True])
def test_student_profile_alias_is_normalized_and_kept_for_browser(key, as_string):
    info = {"id": "fixture-student", "name": "示例学生", "status": "1"}
    session = import_session(
        json.dumps(
            {
                "localStorage": {
                    "token": "fixture-token",
                    key: json.dumps(info) if as_string else info,
                }
            }
        )
    )
    assert session.student_id == info["id"] and session.display_name == info["name"]
    storage = {item["name"]: item["value"] for item in session.state["origins"][0]["localStorage"]}
    assert json.loads(storage["teaInfo"])["id"] == info["id"]
    assert json.loads(storage[key])["id"] == info["id"]


@pytest.mark.parametrize("empty", [None, "", {}, "null", "{}"])
def test_empty_legacy_profile_does_not_hide_student_profile(empty):
    session = import_session(
        json.dumps(
            {
                "token": "fixture-token",
                "localStorage": {
                    "teaInfo": empty,
                    "stuInfo": '{"id":"fixture-student","status":"1"}',
                },
            }
        )
    )
    assert session.student_id == "fixture-student"


def test_teacher_profile_can_fall_back_to_an_explicit_student_profile():
    session = import_session(
        json.dumps(
            {
                "token": "fixture-token",
                "localStorage": {
                    "teaInfo": '{"id":"fixture-teacher","status":"2"}',
                    "stuInfo": '{"id":"fixture-student","status":"1"}',
                },
            }
        )
    )
    assert session.student_id == "fixture-student"


@pytest.mark.parametrize("key", ["teaInfo", "stuInfo", "userInfo", "studentInfo"])
def test_profile_alias_never_turns_explicit_teacher_status_into_student(key):
    with pytest.raises(SessionError, match="学生账号"):
        import_session(json.dumps({"token": "fixture-token", key: {"id": "fixture-teacher", "status": "2"}}))


def test_profile_fallback_keeps_identity_mismatch_protection():
    with pytest.raises(SessionError, match="不一致"):
        import_session('{"token":"fixture-token","stuInfo":{"id":"fixture-A"}}', student_id="fixture-B")


def test_missing_profile_reports_keys_without_values_or_arbitrary_key_text():
    secret = "fixture-secret-value"
    with pytest.raises(SessionError) as caught:
        import_session(
            json.dumps(
                {
                    "localStorage": {
                        "token": secret,
                        "Home_UseProxyAccount": "fixture-private-person",
                        secret: "anything",
                        "unsafe\nfixture-private-person": "anything",
                    }
                }
            )
        )
    message = str(caught.value)
    assert "token" in message and "Home_UseProxyAccount" in message and "活动列表" in message
    assert secret not in message and "fixture-private-person" not in message


def test_login_location_hides_credentials_parameters_fragments_and_profile_path():
    storage = {"token": "fixture-secret-value", "stuInfo": '{"id":"fixture-student"}'}
    location = login_page_location(
        "https://fixture-user:fixture-password@nxg.zwu.edu.cn/student/fixture-student?ticket=fixture-ticket#fixture-fragment",
        storage,
    )
    assert location.startswith(ORIGIN + "/student/")
    assert not any(
        secret in location
        for secret in ("fixture-user", "fixture-password", "fixture-student", "fixture-ticket", "fixture-fragment")
    )


def test_valid_legacy_profile_keeps_its_precedence_over_fallbacks():
    session = import_session(
        json.dumps(
            {
                "token": "fixture-token",
                "localStorage": {
                    "teaInfo": '{"id":"fixture-legacy","status":"1"}',
                    "stuInfo": '{"id":"fixture-other","status":"1"}',
                },
            }
        )
    )
    assert session.student_id == "fixture-legacy"


class LoginPage:
    def __init__(self, url, storage):
        self.url, self.storage = url, storage

    def is_closed(self):
        return False

    def evaluate(self, _script):
        return dict(self.storage)


def waiting_worker(tmp_path, page):
    store = Store(tmp_path / "app.db")
    account = store.add_account("隔离登录等待")
    store.configure(account, status="等待网页登录")
    vault = Vault(tmp_path / "sessions")
    worker = Worker(store, vault)
    worker.login_windows[account] = {
        "context": SimpleNamespace(pages=[page]),
        "save": True,
        "last": -100,
        "signature": "",
        "retry_at": 0,
    }
    return worker, store, vault, account


@pytest.mark.parametrize("url", [ORIGIN + "/zhxg/", LOGIN_URL])
def test_login_wait_logs_location_and_keys_without_secrets_and_throttles(tmp_path, monkeypatch, url):
    page = LoginPage(
        url + "?ticket=fixture-query-secret#token=fixture-fragment-secret",
        {"Home_UseProxyAccount": "fixture-private-person"},
    )
    worker, store, vault, account = waiting_worker(tmp_path, page)
    clock = [10.0]
    monkeypatch.setattr("activity_check.worker.time.monotonic", lambda: clock[0])
    worker._check_login_windows()
    entries = store.events(account)
    assert len(entries) == 1
    message = entries[0]["message"]
    assert url.rstrip("/") in message and "Home_UseProxyAccount" in message
    assert not any(
        value in message for value in ("fixture-query-secret", "fixture-fragment-secret", "fixture-private-person")
    )
    clock[0] += 3
    worker._check_login_windows()
    assert len(store.events(account)) == 1
    clock[0] += 31
    worker._check_login_windows()
    assert len(store.events(account)) == 2
    assert store.account(account)["status"] == "等待网页登录"
    assert not vault.path(account).exists()


def test_login_wait_does_not_log_token_value_when_profile_is_missing(tmp_path):
    worker, store, vault, account = waiting_worker(
        tmp_path, LoginPage(ORIGIN + "/zhxg/", {"token": "fixture-secret-value"})
    )
    worker._check_login_windows()
    message = store.events(account)[0]["message"]
    assert "token" in message and "fixture-secret-value" not in message
    assert not vault.path(account).exists()


def test_portal_profile_alone_is_not_accepted_as_activity_login(tmp_path):
    worker, store, vault, account = waiting_worker(
        tmp_path,
        LoginPage(
            LOGIN_URL,
            {
                "token": "fixture-secret-value",
                "stuInfo": '{"id":"fixture-student"}',
            },
        ),
    )
    worker._check_login_windows()
    assert store.account(account)["status"] == "等待网页登录" and not vault.path(account).exists()
    assert not worker.clients


def test_other_site_storage_is_not_read_by_login_detection(tmp_path):
    page = LoginPage("https://unrelated.example/?ticket=fixture-private-value", {})
    reads = []
    page.evaluate = lambda script: reads.append(script)
    worker, store, vault, account = waiting_worker(tmp_path, page)
    worker._check_login_windows()
    assert not reads and not vault.path(account).exists()
    assert "fixture-private-value" not in store.events(account)[0]["message"]


def test_worker_launches_its_edge_with_software_rendering(tmp_path):
    captured = {}
    browser = SimpleNamespace(is_connected=lambda: True)

    def launch(**kwargs):
        captured.update(kwargs)
        return browser

    worker = Worker(Store(tmp_path / "app.db"), Vault(tmp_path / "sessions"))
    worker.playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    worker._ensure_browser()
    assert captured["channel"] == "msedge" and captured["headless"] is False
    assert "--disable-gpu" in captured["args"]
