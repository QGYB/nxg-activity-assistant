# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""Durable success notifications to the local WeChat File Transfer Assistant."""

import threading
import time

from .models import plain_text
from .wechat_web import WechatSendRejected


def success_message(account_name, title):
    account_name = " ".join(str(account_name or "").split())[:60] or "我的账号"
    title = " ".join(plain_text(title).split())[:300] or "未命名活动"
    return account_name + "已报上" + title


class NotificationWorker(threading.Thread):
    def __init__(self, store, sender, *, events=None):
        super().__init__(name="wechat-notifier", daemon=False)
        self.store, self.sender, self.events = store, sender, events
        self.stopping = threading.Event()
        self.retry_at = 0
        self.wait_reported = False

    def stop(self):
        self.stopping.set()

    def _report(self, account, message):
        self.store.log(account, message)
        if self.events is not None:
            self.events.put(("wechat_notification", account, {"message": message}))

    def tick(self):
        if self.stopping.is_set() or time.monotonic() < self.retry_at:
            return False
        pending = self.store.next_notification()
        if not pending:
            return False
        if not self.sender.ready():
            self.retry_at = time.monotonic() + 30
            if not self.wait_reported:
                self._report(pending["account"], "报名已成功，微信通知等待发送；请在助手中打开“通知设置”扫码授权。")
                self.wait_reported = True
            return False
        self.wait_reported = False
        if self.stopping.is_set():
            return False
        note = self.store.claim_notification()
        if not note:
            return False
        account, activity = note["account"], note["activity"]
        try:
            self.sender.send(note["body"])
        except WechatSendRejected as error:
            if error.code in {1100, 1101, 1102, 1205}:
                state, message = "queued", "微信网页版会话已失效，通知待重新扫码后发送。"
                self.retry_at = time.monotonic() + 30
            else:
                state, message = "failed", str(error)
        except Exception:
            state, message = "uncertain", "微信通知发送结果待确认，请到文件传输助手核对；不会自动重发。"
        else:
            state, message = "sent", "微信服务器已接受通知：文件传输助手。"
        self.store.finish_notification(account, activity, state, message)
        self._report(account, message)
        return True

    def run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
                maintain = getattr(self.sender, "maintain", None)
                if maintain:
                    maintain()
            except Exception:
                # Notification faults must not stop registration or expose exception values.
                self.retry_at = time.monotonic() + 30
                if self.events is not None:
                    self.events.put(("wechat_notification", None, {"message": "微信通知暂未处理，报名监测继续运行。"}))
            self.stopping.wait(0.5)
