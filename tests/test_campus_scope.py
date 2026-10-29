# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import sqlite3

import pytest

from activity_check.store import Store
from test_engine import DETAIL, fixture_site, prepare


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
