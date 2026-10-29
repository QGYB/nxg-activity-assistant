# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import sqlite3

from activity_check.store import Store
from activity_check.worker import Worker
from activity_check.vault import Vault
from activity_check.engine import Engine
from test_engine import fixture_site, DETAIL


READY = DETAIL | {"bmStart": "2000-01-01 00:00:00", "bmEnd": "2100-01-01 00:00:00"}


def monitor(tmp_path, mode):
    store = Store(tmp_path / "app.db")
    account = store.add_account("模式验证")
    store.bind_identity(account, "A")
    store.set_mode(account, mode)
    store.start_monitor(account, 30)
    return store, account, Worker(store, Vault(tmp_path / "sessions"))


def test_observe_mode_records_new_activity_without_registration(tmp_path):
    store, account, worker = monitor(tmp_path, "observe")
    rows = []
    with fixture_site(rows=rows, detail=READY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
        rows.append(READY)
        worker._refresh(account, execute=True)
    item = store.activities(account)[0]
    assert item["is_new"]
    assert not item["auto_target"]
    assert not store.claim(account, "42")
    assert "/s/credit/hdBm" not in [c[0] for c in calls]


def test_auto_mode_submits_existing_nonexcluded_activity_on_first_poll(tmp_path):
    store, account, worker = monitor(tmp_path, "auto")
    store.save_activities(account, [READY])
    store.exclude(account, "42", False)
    with fixture_site(rows=[READY], detail=READY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
        worker._refresh(account, execute=True)
    assert store.activities(account)[0]["auto_target"]
    assert not store.activities(account)[0]["is_new"]
    assert store.attempt(account, "42")["status"] == "success"
    assert not store.claim(account, "42")
    assert [c[0] for c in calls].count("/s/credit/hdBm") == 1


def test_auto_mode_detects_and_submits_a_new_activity_once(tmp_path):
    store, account, worker = monitor(tmp_path, "auto")
    rows = []
    with fixture_site(rows=rows, detail=READY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
        rows.append(READY)
        worker._refresh(account, execute=True)
        worker._refresh(account, execute=True)
    assert not store.activities(account)[0]["excluded"]
    assert store.attempt(account, "42")["status"] == "success"
    assert [c[0] for c in calls].count("/s/credit/hdBm") == 1


def test_new_activity_waits_for_opening_without_losing_its_target(tmp_path):
    store, account, worker = monitor(tmp_path, "auto")
    rows = []
    with fixture_site(rows=rows, detail=READY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
        rows.append(READY | {"bmStart": "2099-01-01 00:00:00"})
        worker._refresh(account, execute=True)
        assert "/s/credit/hdBm" not in [c[0] for c in calls]
        assert store.activities(account)[0]["auto_target"]
        rows[:] = [READY]
        worker._refresh(account, execute=True)
    assert store.attempt(account, "42")["status"] == "success"


def test_switch_to_observe_during_preflight_stops_last_boundary(tmp_path):
    store, account, _worker = monitor(tmp_path, "auto")
    store.record_snapshot(account, [])
    store.record_snapshot(account, [READY])
    with fixture_site(detail=READY) as (site, calls):
        preflight = site.preflight

        def switch():
            result = preflight()
            store.set_mode(account, "observe")
            return result

        site.preflight = switch
        Engine(store).process(account, READY, site)
    assert not store.account(account)["enabled"]
    assert "/s/credit/hdBm" not in [c[0] for c in calls]
    assert store.attempt(account, "42") is None


def test_restart_keeps_existing_activity_eligible_without_repeating_success(tmp_path):
    store, account, worker = monitor(tmp_path, "auto")
    with fixture_site(rows=[READY], detail=READY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
        store.configure(account, enabled=False)
        store.start_monitor(account, 30)
        worker._refresh(account, execute=True)
    assert store.activities(account)[0]["auto_target"]
    assert [c[0] for c in calls].count("/s/credit/hdBm") == 1


def test_existing_excluded_activity_is_not_booked_until_exclusion_removed(tmp_path):
    store, account, worker = monitor(tmp_path, "auto")
    store.save_activities(account, [READY])
    store.exclude(account, "42", True)
    with fixture_site(rows=[READY], detail=READY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
        assert "/s/credit/hdBm" not in [c[0] for c in calls]
        store.exclude(account, "42", False)
        worker._refresh(account, execute=True)
    assert store.attempt(account, "42")["status"] == "success"
    assert not store.activities(account)[0]["is_new"]


def test_existing_full_activity_is_booked_when_a_slot_becomes_available(tmp_path):
    store, account, worker = monitor(tmp_path, "auto")
    rows = [READY | {"currentNum": 10}]
    with fixture_site(rows=rows, detail=READY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
        assert "/s/credit/hdBm" not in [c[0] for c in calls]
        rows[:] = [READY]
        worker._refresh(account, execute=True)
    assert store.attempt(account, "42")["status"] == "success"
    assert not store.activities(account)[0]["is_new"]


def test_existing_database_migration_preserves_account_and_selection(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE accounts(id TEXT PRIMARY KEY,name TEXT,student_id TEXT DEFAULT '',enabled INTEGER DEFAULT 0,interval INTEGER DEFAULT 30,status TEXT DEFAULT '未确认',checked TEXT DEFAULT '',term TEXT DEFAULT '');
            CREATE TABLE activities(account TEXT,id TEXT,payload TEXT,selected INTEGER DEFAULT 0,PRIMARY KEY(account,id));
            INSERT INTO accounts(id,name) VALUES ('A','保留备注');
            INSERT INTO activities VALUES ('A','42','{"id":"42"}',1);
        """)
    store = Store(path)
    assert store.account("A")["name"] == "保留备注"
    assert store.account("A")["mode"] == "observe"
    assert not store.activities("A")[0]["excluded"]
    with store.connection() as db:
        assert db.execute("SELECT selected FROM activities WHERE account='A'").fetchone()[0] == 1
    assert not store.activities("A")[0]["auto_target"]


def test_old_request_cannot_establish_baseline_after_scope_restart(tmp_path):
    store, account, _worker = monitor(tmp_path, "auto")
    generation = store.account(account)["watch_generation"]
    store.record_snapshot(account, [], generation=generation)
    store.change_term(account, "另一个学期")
    store.start_monitor(account, 30)
    assert store.record_snapshot(account, [READY], generation=generation) == []
    assert not store.account(account)["baseline_ready"]
    assert not store.activities(account)[0]["auto_target"]
    assert store.record_snapshot(account, [READY], generation=store.account(account)["watch_generation"]) == []
    assert store.activities(account)[0]["auto_target"]
