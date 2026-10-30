# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from activity_check.models import Activity
from activity_check.site import NetworkError
from activity_check.store import Store
from activity_check.vault import Vault
from activity_check.worker import Worker
from test_engine import DETAIL, fixture_site
import pytest


DISPLAY = {"startTime": "2026-11-01 14:00:00", "endTime": "2026-11-01 16:00:00", "address": "测试图书馆"}


def setup_account(tmp_path):
    store = Store(tmp_path / "app.db")
    account = store.add_account("详情验证")
    store.bind_identity(account, "A")
    return store, account, Worker(store, Vault(tmp_path / "sessions"))


def test_list_is_enriched_from_read_only_detail_and_survives_next_poll(tmp_path):
    store, account, worker = setup_account(tmp_path)
    with fixture_site(rows=[DETAIL], detail=DETAIL | DISPLAY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account)
        assert Activity.from_row(store.activities(account)[0]["raw"]).address == "详情待加载"
        assert worker._hydrate_next_detail()
        item = store.activities(account)[0]
        assert {k: item["raw"][k] for k in DISPLAY} == DISPLAY
        store.exclude(account, "42", True)
        worker._refresh(account)
        worker.next_detail_at = 0
        assert not worker._hydrate_next_detail()
    item = store.activities(account)[0]
    assert item["excluded"]
    assert not item["is_new"] and not item["auto_target"]
    assert item["raw"]["startTime"] == DISPLAY["startTime"]
    assert store.attempt(account, "42") is None
    paths = [c[0] for c in calls]
    assert paths.count("/c/credit/getHdBmDetails") == 1
    assert "/s/credit/hdBm" not in paths and "/s/credit/hdLose" not in paths


def test_confirmed_empty_detail_is_distinguished_from_not_loaded(tmp_path):
    store, account, worker = setup_account(tmp_path)
    with fixture_site(rows=[DETAIL], detail=DETAIL) as (site, _calls):
        worker.clients[account] = site
        worker._refresh(account)
        assert "详情待加载" in Activity.from_row(store.activities(account)[0]["raw"]).times
        assert worker._hydrate_next_detail()
    activity = Activity.from_row(store.activities(account)[0]["raw"])
    assert activity.address == "网站未提供"
    assert activity.times == "网站未提供"


def test_detail_failure_retains_cached_values_and_throttles_retry(tmp_path):
    store, account, worker = setup_account(tmp_path)
    store.configure(account, status="已登录")
    store.save_activities(account, [DETAIL | DISPLAY | {"_detail_checked_at": 1}])

    class OfflineDetail:
        calls = 0

        def detail(self, _identity):
            self.calls += 1
            raise NetworkError("测试网络中断")

    site = OfflineDetail()
    worker.clients[account] = site
    assert worker._hydrate_next_detail()
    worker.next_detail_at = 0
    assert not worker._hydrate_next_detail()
    assert site.calls == 1
    assert store.account(account)["status"] == "已登录"
    assert {k: store.activities(account)[0]["raw"][k] for k in DISPLAY} == DISPLAY


def test_due_monitor_poll_takes_priority_over_background_details(tmp_path):
    store, account, worker = setup_account(tmp_path)
    store.configure(account, status="已登录")
    store.save_activities(account, [DETAIL])
    store.start_monitor(account, 30)
    with fixture_site(detail=DETAIL | DISPLAY) as (site, calls):
        worker.clients[account] = site
        worker.next_poll[account] = 0
        assert not worker._hydrate_next_detail()
    assert calls == []


@pytest.mark.parametrize("blank", [None, ""])
def test_details_fill_missing_signup_fields_without_submitting(tmp_path, blank):
    store, account, worker = setup_account(tmp_path)
    row = {key: blank for key in DETAIL} | {"id": "42", "projectName": "示例活动"}
    with fixture_site(rows=[row], detail=DETAIL | DISPLAY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account)
        assert worker._hydrate_next_detail()
    raw = store.activities(account)[0]["raw"]
    for key in ("bmStart", "bmEnd", "state", "bmState", "rz", "maxNum", "currentNum"):
        assert raw[key] == DETAIL[key]
    assert not {path for path, _ in calls} & {"/s/credit/hdBm", "/s/credit/hdLose", "/s/credit/qxHdBm"}
    assert not store.account(account)["enabled"]


def test_details_never_replace_nonempty_list_constraints_or_zero_counts(tmp_path):
    store, account, worker = setup_account(tmp_path)
    row = DETAIL | {"state": 0, "bmState": 0, "maxNum": 0, "currentNum": 0}
    with fixture_site(rows=[row], detail=DETAIL | DISPLAY | {"currentNum": 9, "maxNum": 99}) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account)
        assert worker._hydrate_next_detail()
    raw = store.activities(account)[0]["raw"]
    for key in ("bmStart", "bmEnd", "state", "bmState", "rz", "maxNum", "currentNum"):
        assert raw[key] == row[key]
    assert not {path for path, _ in calls} & {"/s/credit/hdBm", "/s/credit/hdLose", "/s/credit/qxHdBm"}
