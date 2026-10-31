# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import sqlite3

import pytest

from activity_check.store import Store
from test_engine import DETAIL, fixture_site, prepare
from activity_check.vault import Vault
from activity_check.worker import Worker


@pytest.mark.parametrize(
    "campus,address,booked",
    [
        ("钱湖", "钱湖校区51C218", True),
        ("回龙", "钱湖校区51C218", False),
        ("回龙", "回龙校区图书馆", True),
        ("钱湖", "线上", False),
        ("", "", True),
    ],
)
def test_auto_booking_uses_fresh_location_not_activity_name(tmp_path, campus, address, booked):
    store, account, engine = prepare(tmp_path)
    store.set_campus(account, campus)
    detail = DETAIL | {"projectName": "钱湖和回龙活动标题", "address": address}
    with fixture_site(detail=detail) as (site, calls):
        engine.process(account, DETAIL, site)
    assert ("/s/credit/hdBm" in [path for path, _data in calls]) is booked


def test_campus_changed_during_preflight_prevents_out_of_scope_submission(tmp_path):
    store, account, engine = prepare(tmp_path)
    store.set_campus(account, "钱湖")
    with fixture_site(detail=DETAIL | {"address": "钱湖校区"}) as (site, calls):
        preflight = site.preflight

        def switch_before_claim():
            reply = preflight()
            store.set_campus(account, "回龙")
            return reply

        site.preflight = switch_before_claim
        engine.process(account, DETAIL, site)
    assert "/s/credit/hdBm" not in [path for path, _data in calls]
    assert store.attempt(account, "42") is None
    assert store.account(account)["enabled"]


def test_location_filter_preserves_cached_address_and_matches_both_campuses(tmp_path):
    from activity_check.campus import matches_campus

    store, account, _engine = prepare(tmp_path)
    store.save_activities(account, [DETAIL | {"address": "<b>回 龙校区</b>、钱湖校区"}])
    store.set_campus(account, "回龙")
    store.save_activities(account, [DETAIL])
    raw = store.activities(account)[0]["raw"]
    assert matches_campus(raw, "回龙") and matches_campus(raw, "钱湖")
    assert store.claim(account, "42")


def test_new_account_defaults_to_ten_seconds_even_on_legacy_database(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE accounts(id TEXT PRIMARY KEY,name TEXT,student_id TEXT DEFAULT '',interval INTEGER DEFAULT 30)"
        )
    store = Store(path)
    account = store.add_account("默认刷新")
    assert store.account(account)["interval"] == 10
    assert store.account(account)["campus"] == ""


def scoped_store(tmp_path, campus, mode="auto"):
    store = Store(tmp_path / "scope.db")
    account = store.add_account("范围核验")
    store.bind_identity(account, "A")
    store.set_mode(account, mode)
    store.set_campus(account, campus)
    store.start_monitor(account, 10)
    return store, account


@pytest.mark.parametrize("mode", ["observe", "auto"])
@pytest.mark.parametrize("campus,expected", [("回龙", ["h"]), ("钱湖", ["q"]), ("", ["h", "q", "u"])])
def test_discovery_and_auto_targets_follow_selected_campus(tmp_path, mode, campus, expected):
    store, account = scoped_store(tmp_path, campus, mode)
    store.record_snapshot(account, [])
    rows = [
        DETAIL | {"id": "h", "projectName": "钱湖标题示例", "address": "回龙校区"},
        DETAIL | {"id": "q", "projectName": "回龙标题示例", "address": "钱湖校区"},
        DETAIL | {"id": "u", "address": ""},
    ]
    assert store.record_snapshot(account, rows) == expected
    cached = store.activities(account)
    assert [row["id"] for row in cached if row["is_new"]] == expected
    assert [row["id"] for row in cached if row["auto_target"]] == (expected if mode == "auto" else [])


@pytest.mark.parametrize("blank", [None, ""])
def test_new_unknown_location_waits_for_details_but_baseline_activity_is_not_new(tmp_path, blank):
    store, account = scoped_store(tmp_path, "回龙")
    baseline = DETAIL | {"id": "old", "address": blank}
    new = DETAIL | {"id": "new", "address": blank}
    assert store.record_snapshot(account, [baseline]) == []
    assert store.record_snapshot(account, [baseline, new]) == []
    store.save_activities(
        account, [{"id": key, "address": "回龙校区", "_detail_checked_at": 1} for key in ("old", "new")]
    )
    assert store.record_snapshot(account, [baseline, new]) == ["new"]
    assert {row["id"] for row in store.activities(account) if row["auto_target"]} == {"old", "new"}


def test_observe_notifications_count_only_new_activities_in_scope(tmp_path):
    store, account = scoped_store(tmp_path, "回龙", "observe")
    worker = Worker(store, Vault(tmp_path / "sessions"))
    rows = []
    with fixture_site(rows=rows) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
        rows.extend([DETAIL | {"id": "h", "address": "回龙校区"}, DETAIL | {"id": "q", "address": "钱湖校区"}])
        worker._refresh(account, execute=True)
    events = []
    while not worker.events.empty():
        event = worker.events.get_nowait()
        if event[0] == "new_activity":
            events.append(event)
    assert len(events) == 1 and events[0][2]["count"] == 1
    assert "/s/credit/hdBm" not in [path for path, _fields in calls]


def test_auto_poll_does_not_preflight_other_campus_activities(tmp_path):
    store, account = scoped_store(tmp_path, "回龙")
    worker = Worker(store, Vault(tmp_path / "sessions"))
    row = DETAIL | {"address": "钱湖校区", "bmStart": "2000-01-01 00:00:00", "bmEnd": "2100-01-01 00:00:00"}
    with fixture_site(rows=[row], detail=row) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
    assert not {path for path, _fields in calls} & {"/c/credit/getHdBmDetails", "/s/credit/hdLose", "/s/credit/hdBm"}
    assert not store.activities(account)[0]["auto_target"]


def test_out_of_scope_uncertain_submission_is_only_reconciled_not_resubmitted(tmp_path):
    store, account = scoped_store(tmp_path, "回龙")
    row = DETAIL | {"address": "钱湖校区"}
    store.save_activities(account, [row])
    store.finish(account, "42", "uncertain", "历史请求结果待确认")
    worker = Worker(store, Vault(tmp_path / "sessions"))
    with fixture_site(rows=[row], detail=row | {"bmState": 1}) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
    assert store.attempt(account, "42")["status"] == "success"
    assert not {path for path, _fields in calls} & {"/s/credit/hdLose", "/s/credit/hdBm"}
