# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import queue

import pytest

from activity_check.store import Store
from test_engine import DETAIL, fixture_site, prepare


def notification_module():
    from activity_check import notifications

    return notifications


class LocalSender:
    available = True
    fail = False

    def __init__(self):
        self.messages = []

    def ready(self):
        return self.available

    def send(self, body):
        self.messages.append(body)
        if self.fail:
            raise RuntimeError("private-fixture-do-not-log")


def successful_booking(tmp_path):
    store, account, engine = prepare(tmp_path)
    with fixture_site() as (site, _calls):
        engine.process(account, DETAIL, site)
    return store, account


def test_confirmed_booking_queues_one_exact_message_and_does_not_resend(tmp_path):
    store, account, engine = prepare(tmp_path)
    with fixture_site() as (site, calls):
        engine.process(account, DETAIL, site)
        engine.process(account, DETAIL, site)
    note = store.notification(account, "42")
    assert note["state"] == "queued"
    assert note["body"] == "已报名---独立测试活动"
    sender = LocalSender()
    worker = notification_module().NotificationWorker(store, sender)
    assert worker.tick()
    assert not worker.tick()
    assert sender.messages == [note["body"]]
    assert store.notification(account, "42")["state"] == "sent"
    assert [path for path, _fields in calls].count("/s/credit/hdBm") == 1


@pytest.mark.parametrize("mode", ["dropped", "rejected", "blocked"])
def test_failed_or_unconfirmed_booking_never_queues_success_notification(tmp_path, mode):
    store, account, engine = prepare(tmp_path)
    with fixture_site(mode=mode, preflight={"code": 1} if mode == "blocked" else None) as (site, _calls):
        engine.process(account, DETAIL, site)
    assert store.notification(account, "42") is None


def test_existing_registration_and_old_success_are_not_replayed(tmp_path):
    store, account, engine = prepare(tmp_path)
    with fixture_site(detail=DETAIL | {"bmState": 1}) as (site, _calls):
        engine.process(account, DETAIL, site)
    assert store.notification(account, "42") is None
    sender = LocalSender()
    worker = notification_module().NotificationWorker(store, sender)
    assert not worker.tick()
    assert not sender.messages


def test_own_uncertain_submission_notifies_only_after_website_confirms(tmp_path):
    store, account, engine = prepare(tmp_path)
    with fixture_site(mode="dropped") as (site, _calls):
        engine.process(account, DETAIL, site)
    assert store.notification(account, "42") is None
    with fixture_site(detail=DETAIL | {"bmState": 1}) as (site, calls):
        engine.process(account, DETAIL, site)
    assert store.notification(account, "42")["state"] == "queued"
    assert "/s/credit/hdBm" not in [path for path, _fields in calls]


def test_missing_wechat_keeps_queue_without_reversing_booking(tmp_path):
    store, account = successful_booking(tmp_path)
    sender = LocalSender()
    sender.available = False
    worker = notification_module().NotificationWorker(store, sender)
    assert not worker.tick()
    assert store.notification(account, "42")["state"] == "queued"
    assert store.attempt(account, "42")["status"] == "success"
    sender.available = True
    worker.retry_at = 0
    assert worker.tick()
    assert len(sender.messages) == 1


def test_wechat_result_uncertain_is_not_retried_after_restart(tmp_path):
    store, account = successful_booking(tmp_path)
    sender = LocalSender()
    sender.fail = True
    events = queue.Queue()
    worker = notification_module().NotificationWorker(store, sender, events=events)
    assert worker.tick()
    assert store.notification(account, "42")["state"] == "uncertain"
    store.recover_interrupted()
    assert not notification_module().NotificationWorker(store, sender).tick()
    assert len(sender.messages) == 1
    assert store.attempt(account, "42")["status"] == "success"
    assert "private-fixture" not in str(store.events(account))


def test_interrupted_send_and_cancelled_booking_are_not_sent(tmp_path):
    store, account = successful_booking(tmp_path)
    assert store.claim_notification()["activity"] == "42"
    store.recover_interrupted()
    assert store.notification(account, "42")["state"] == "uncertain"
    other = Store(tmp_path / "cancelled.db")
    owner = other.add_account("取消后的通知")
    other.save_activities(owner, [DETAIL])
    other.finish(owner, "42", "success", "测试成功", notification="已报名---取消的测试活动")
    other.finish_cancel(owner, "42", "cancelled", "测试中取消")
    sender = LocalSender()
    assert not notification_module().NotificationWorker(other, sender).tick()
    assert not sender.messages
    assert other.notification(owner, "42")["state"] == "skipped"


@pytest.mark.parametrize("code,state", [(1101, "queued"), (500, "failed")])
def test_explicit_http_rejection_is_not_reported_as_sent(tmp_path, code, state):
    from activity_check.wechat_web import WechatSendRejected

    store, account = successful_booking(tmp_path)

    class RejectingSender(LocalSender):
        def send(self, body):
            self.messages.append(body)
            raise WechatSendRejected(code)

    sender = RejectingSender()
    worker = notification_module().NotificationWorker(store, sender)
    assert worker.tick()
    assert store.notification(account, "42")["state"] == state
    assert not worker.tick()
    assert len(sender.messages) == 1
