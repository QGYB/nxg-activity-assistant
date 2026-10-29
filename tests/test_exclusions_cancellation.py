# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time

from activity_check.engine import Engine
from activity_check.store import Store
from activity_check.vault import Vault
from activity_check.worker import Worker
from test_engine import DETAIL, fixture_site


READY = DETAIL | {"bmStart": "2000-01-01 00:00:00", "bmEnd": "2100-01-01 00:00:00"}
REGISTERED = READY | {"bmState": 1}


def prepare(tmp_path):
    store = Store(tmp_path / "app.db")
    account = store.add_account("排除和取消验证")
    store.bind_identity(account, "A")
    store.set_mode(account, "auto")
    store.start_monitor(account, 30)
    return store, account, Worker(store, Vault(tmp_path / "sessions"))


def test_new_activity_is_booked_without_checking_any_box(tmp_path):
    store, account, worker = prepare(tmp_path)
    rows = []
    with fixture_site(rows=rows, detail=READY) as (site, calls):
        worker.clients[account] = site
        worker._refresh(account, execute=True)
        rows.append(READY)
        worker._refresh(account, execute=True)
    assert store.attempt(account, "42")["status"] == "success"
    assert not store.activities(account)[0]["excluded"]
    assert [p for p, _ in calls].count("/s/credit/hdBm") == 1


def test_checking_during_preflight_stops_submission_and_survives_restart(tmp_path):
    store, account, _worker = prepare(tmp_path)
    store.record_snapshot(account, [])
    store.record_snapshot(account, [READY])
    with fixture_site(detail=READY) as (site, calls):
        preflight = site.preflight

        def exclude_before_claim():
            reply = preflight()
            store.exclude(account, "42", True)
            return reply

        site.preflight = exclude_before_claim
        Engine(store).process(account, READY, site)
    assert "/s/credit/hdBm" not in [p for p, _ in calls]
    store.start_monitor(account, 30)
    store.record_snapshot(account, [READY])
    assert Store(store.path).is_excluded(account, "42")


def test_legacy_positive_selection_does_not_become_exclusion(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE activities(account TEXT,id TEXT,payload TEXT,selected INTEGER DEFAULT 0,PRIMARY KEY(account,id));
            INSERT INTO activities VALUES ('legacy','42','{"id":"42"}',1);
        """)
    store = Store(path)
    assert not store.activities("legacy")[0]["excluded"]


def test_cancel_uses_official_endpoint_and_prevents_automatic_rebooking(tmp_path):
    from activity_check import models

    store, account, _worker = prepare(tmp_path)
    store.record_snapshot(account, [])
    store.record_snapshot(account, [REGISTERED])
    store.finish(account, "42", "success", "测试中已报名")
    store.set_mode(account, "observe")  # Cancellation also works while monitoring is paused.
    engine = Engine(store)
    with fixture_site(rows=[REGISTERED], detail=REGISTERED) as (site, calls):
        engine.cancel(account, "42", site)
        engine.cancel(account, "42", site)
        engine.process(account, READY, site)
    assert store.cancellation(account, "42")["status"] == "cancelled"
    assert store.is_excluded(account, "42")
    assert models.display_status(store.activities(account)[0]) == "cancelled"
    assert store.attempt(account, "42")["status"] == "success"  # Keep registration history.
    paths = [p for p, _ in calls]
    assert paths.count("/s/credit/qxHdBm") == 1
    assert "/s/credit/hdBm" not in paths and "/s/credit/hdLose" not in paths


def test_uncertain_cancellation_is_never_blindly_retried(tmp_path):
    store, account, _worker = prepare(tmp_path)
    store.save_activities(account, [REGISTERED])
    with fixture_site(mode="dropped", rows=[REGISTERED], detail=REGISTERED) as (site, calls):
        Engine(store).cancel(account, "42", site)
        Engine(store).cancel(account, "42", site)
    assert store.cancellation(account, "42")["status"] == "cancel_uncertain"
    assert [p for p, _ in calls].count("/s/credit/qxHdBm") == 1
    with fixture_site(rows=[READY], detail=READY) as (site, calls):
        Engine(store).cancel(account, "42", site)
    assert store.cancellation(account, "42")["status"] == "cancelled"
    assert "/s/credit/qxHdBm" not in [p for p, _ in calls]


def test_expired_or_unregistered_activity_never_sends_cancellation(tmp_path):
    store, account, _worker = prepare(tmp_path)
    for row in [REGISTERED | {"bmEnd": "2000-01-02 00:00:00"}, READY]:
        with fixture_site(rows=[row], detail=row) as (site, calls):
            Engine(store).cancel(account, "42", site)
        assert "/s/credit/qxHdBm" not in [p for p, _ in calls]
        assert store.is_excluded(account, "42")


def test_only_one_cancellation_can_be_claimed_and_crash_is_uncertain(tmp_path):
    store, account, _worker = prepare(tmp_path)
    store.save_activities(account, [REGISTERED])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.claim_cancel(account, "42"), range(4)))
    assert results.count(True) == 1
    store.recover_interrupted()
    assert store.cancellation(account, "42")["status"] == "cancel_uncertain"
    assert not store.claim_cancel(account, "42")


def test_real_worker_handles_user_cancellation_command_while_paused(tmp_path):
    store, account, worker = prepare(tmp_path)
    store.set_mode(account, "observe")
    store.save_activities(account, [REGISTERED])
    with fixture_site(rows=[REGISTERED], detail=REGISTERED) as (site, calls):
        worker.clients[account] = site
        worker.start()
        try:
            worker.send("cancel", account, activity="42")
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                result = store.cancellation(account, "42")
                if result and result["status"] == "cancelled":
                    break
                time.sleep(0.02)
            assert worker.is_alive()
            assert store.cancellation(account, "42")["status"] == "cancelled"
        finally:
            worker.stop()
            worker.join(timeout=3)
    assert store.is_excluded(account, "42")
    assert [p for p, _ in calls].count("/s/credit/qxHdBm") == 1
    assert "/s/credit/hdBm" not in [p for p, _ in calls]


def test_rejected_cancellation_remains_registered_and_allows_explicit_retry(tmp_path):
    store, account, _worker = prepare(tmp_path)
    store.save_activities(account, [REGISTERED])
    with fixture_site(mode="rejected", rows=[REGISTERED], detail=REGISTERED) as (site, calls):
        Engine(store).cancel(account, "42", site)
    assert store.cancellation(account, "42")["status"] == "cancel_rejected"
    assert store.activities(account)[0]["raw"]["bmState"] == 1
    assert store.is_excluded(account, "42")
    with fixture_site(rows=[REGISTERED], detail=REGISTERED) as (site, calls):
        Engine(store).cancel(account, "42", site)
    assert store.cancellation(account, "42")["status"] == "cancelled"
    assert [p for p, _ in calls].count("/s/credit/qxHdBm") == 1
