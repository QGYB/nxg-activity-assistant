# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from activity_check.store import Store, IdentityMismatch
from activity_check.vault import Vault


def prepare(tmp_path):
    store = Store(tmp_path / "app.db")
    account = store.add_account("本人")
    store.bind_identity(account, "A")
    store.set_mode(account, "auto")
    store.start_monitor(account, 30)
    store.record_snapshot(account, [])
    store.record_snapshot(account, [{"id": "42", "projectName": "隔离示例"}])
    return store, account


def test_only_one_worker_can_claim_new_nonexcluded_registration(tmp_path):
    store, account = prepare(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim(account, "42"), range(4)))
    assert claims.count(True) == 1


def test_pause_and_exclusion_prevent_claim_at_last_boundary(tmp_path):
    store, account = prepare(tmp_path)
    store.configure(account, enabled=False)
    assert not store.claim(account, "42")
    store.configure(account, enabled=True)
    store.exclude(account, "42", True)
    assert not store.claim(account, "42")


def test_crash_recovery_does_not_resubmit_after_reselect(tmp_path):
    store, account = prepare(tmp_path)
    assert store.claim(account, "42")
    store.recover_interrupted()
    assert store.attempt(account, "42")["status"] == "uncertain"
    store.exclude(account, "42", True)
    store.exclude(account, "42", False)
    assert not store.claim(account, "42")
    assert not store.reset_rejected(account, "42")


def test_account_identity_cannot_be_replaced_with_another_person(tmp_path):
    store, account = prepare(tmp_path)
    with pytest.raises(IdentityMismatch):
        store.bind_identity(account, "B")


def test_selections_and_results_are_per_account(tmp_path):
    store, account = prepare(tmp_path)
    other = store.add_account("另一账号")
    store.bind_identity(other, "B")
    store.save_activities(other, [{"id": "42", "projectName": "隔离示例"}])
    assert not store.activities(other)[0]["excluded"]
    assert store.claim(account, "42")
    store.finish(account, "42", "success", "报名成功")
    assert store.attempt(other, "42") is None


def test_refresh_preserves_exclusion_and_rejected_requires_explicit_reset(tmp_path):
    store, account = prepare(tmp_path)
    store.exclude(account, "42", True)
    store.save_activities(account, [{"id": "42", "projectName": "更新名称"}])
    assert store.activities(account)[0]["excluded"]
    assert not store.claim(account, "42")
    store.exclude(account, "42", False)
    assert store.claim(account, "42")
    store.finish(account, "42", "rejected", "服务器明确拒绝")
    assert not store.claim(account, "42")
    assert store.reset_rejected(account, "42")
    assert store.claim(account, "42")


def test_real_windows_vault_encrypts_and_reads_without_plaintext(tmp_path):
    vault = Vault(tmp_path)
    account = "a" * 32
    value = json.dumps({"token": "private-fixture", "cookie": "sid=example"})
    vault.save(account, value)
    assert vault.load(account) == value
    assert b"private-fixture" not in (tmp_path / (account + ".auth")).read_bytes()
    with pytest.raises(ValueError):
        vault.load("../escape")


def test_restart_does_not_show_old_cached_login_as_current(tmp_path):
    store, account = prepare(tmp_path)
    store.configure(account, status="已登录")
    store.recover_interrupted()
    assert store.account(account)["status"] == "未确认"
    assert not store.account(account)["enabled"]


def test_same_student_cannot_create_two_independent_submit_queues(tmp_path):
    store, _account = prepare(tmp_path)
    duplicate = store.add_account("重复 Cookie")
    with pytest.raises(IdentityMismatch):
        store.bind_identity(duplicate, "A")
