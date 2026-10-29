# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from .models import Activity, integer, now_beijing, parse_time
from .site import LoginExpired, SiteError
from .notifications import success_message
from .campus import matches_campus


class Engine:
    def __init__(self, store, *, clock=now_beijing):
        self.store = store
        self.clock = clock

    def process(self, account, row, site):
        identity = str(row.get("id", ""))
        owner = self.store.account(account)
        if (
            not owner
            or not owner["enabled"]
            or owner["mode"] != "auto"
            or self.store.is_excluded(account, identity)
            or not self.store.is_auto_target(account, identity)
        ):
            return
        existing = self.store.attempt(account, identity)
        if existing and existing["status"] not in {"uncertain"}:
            return
        detail = site.detail(identity)
        # The current list provides state/bmState; the fresh detail owns time and capacity.
        fresh = row | detail | {"id": identity}
        for required in ("bmStart", "bmEnd", "currentNum", "maxNum"):
            if required not in detail:
                fresh[required] = None
        self.store.save_activities(account, [fresh])
        activity = Activity.from_row(fresh)
        eligibility = activity.eligibility(self.clock())
        if eligibility == "registered":
            self.store.finish(
                account,
                identity,
                "success",
                "学校网站确认当前账号已报名",
                notification=success_message(activity.title) if existing else None,
            )
            return
        if existing:  # Read-only reconciliation never causes a second send.
            return
        if eligibility != "ready":
            return
        owner = self.store.account(account)
        if not owner or not matches_campus(fresh, owner["campus"]):
            return
        preflight = site.preflight()
        if str(preflight.get("code")) != "0":
            self.store.finish(account, identity, "blocked", site.message(preflight))
            self.store.log(account, "目标活动需要人工查看网站提示，已停止自动提交。")
            return
        if activity.eligibility(self.clock()) != "ready":
            return
        if not self.store.claim(account, identity):
            return
        try:
            response = site.submit(identity)
        except Exception as error:
            # Any exception after the durable claim may follow a successful send.
            self.store.finish(account, identity, "uncertain", "提交响应未确认，请在学校网站核对；不会自动重发")
            self.store.log(account, "提交结果待确认，已停止重复请求。")
            if isinstance(error, LoginExpired):
                raise
            return
        if str(response.get("code")) == "0":
            self.store.finish(
                account,
                identity,
                "success",
                "学校接口确认报名成功，请按时参加",
                notification=success_message(activity.title),
            )
            self.store.log(account, "一项活动已收到学校报名成功回复。")
        else:
            self.store.finish(account, identity, "rejected", site.message(response))
            self.store.log(account, "一项报名未通过学校检查，已停止自动重试。")

    def cancel(self, account, identity, site):
        """User-requested cancellation, independent of monitoring mode and enabled state."""
        identity = str(identity)
        owner = self.store.account(account)
        if not owner:
            return
        self.store.exclude(account, identity, True)
        existing = self.store.cancellation(account, identity)
        if existing and existing["status"] in {"cancelled", "cancelling"}:
            return
        rows = site.activities(owner["term"], include_ended=True)
        listed = next((row for row in rows if str(row.get("id")) == identity), None)
        if not listed:
            raise SiteError("当前列表未找到此活动，未发送取消请求，请到学校网站核对。")
        detail = site.detail(identity)
        fresh = listed | detail | {"id": identity, "_term": owner["term"], "_missing": False}
        self.store.save_activities(account, [fresh])
        self.store.exclude(account, identity, True)
        state = integer(fresh.get("bmState"))
        if state == 2:
            status = "cancelled" if existing and existing["status"] == "cancel_uncertain" else "not_registered"
            self.store.finish_cancel(account, identity, status, "学校网站确认当前未报名，已保留“不报名”标记")
            return
        if state != 1:
            raise SiteError("网站尚未确认当前报名状态，未发送取消请求，请在学校网站核对。")
        if existing and existing["status"] == "cancel_uncertain":
            self.store.log(account, "上次取消结果待确认，网站仍显示已报名；未重复发送，请在官网核对。")
            return
        end = parse_time(detail.get("bmEnd"))
        if not end or self.clock() >= end:
            self.store.finish_cancel(
                account, identity, "cancel_rejected", "报名已截止或截止时间未确认，请到学校网站处理"
            )
            return
        if not self.store.claim_cancel(account, identity):
            return
        try:
            response = site.cancel(identity)
        except Exception as error:
            self.store.finish_cancel(
                account, identity, "cancel_uncertain", "取消响应未确认，请到学校网站核对；不会自动重发"
            )
            self.store.log(account, "取消报名结果待确认，已停止重复请求并保留“不报名”标记。")
            if isinstance(error, LoginExpired):
                raise
            return
        if str(response.get("code")) == "0":
            self.store.finish_cancel(account, identity, "cancelled", "学校接口确认取消报名成功，已保留“不报名”标记")
            self.store.save_activities(account, [{"id": identity, "bmState": 2}])
            self.store.log(account, "一项活动已取消报名，并已排除自动报名。")
        else:
            self.store.finish_cancel(account, identity, "cancel_rejected", site.message(response))
            self.store.log(account, "网站未接受取消报名，请查看活动详情中的原因。")
