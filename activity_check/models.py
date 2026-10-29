# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re

BEIJING = timezone(timedelta(hours=8))
ORIGIN = "https://nxg.zwu.edu.cn"
LIST_URL = ORIGIN + "/zhxg/#/lifePractice/signup/list"
LOGIN_URL = ORIGIN + "/zhxg/"


def now_beijing():
    return datetime.now(BEIJING)


def parse_time(value):
    if not value or not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.strip().replace("/", "-").replace("Z", "+00:00"))
        return result.replace(tzinfo=BEIJING) if result.tzinfo is None else result.astimezone(BEIJING)
    except ValueError:
        return None


def integer(value):
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        return int(value) if float(value) == int(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def plain_text(value):
    import html

    return html.unescape(re.sub("<[^>]*>", "", str(value or "").replace("</br >", "\n").replace("<br>", "\n")))


@dataclass
class Activity:
    id: str
    title: str
    raw: dict = field(repr=False)

    @classmethod
    def from_row(cls, row):
        return cls(str(row.get("id", "")), str(row.get("projectName") or "未命名活动"), dict(row))

    @property
    def address(self):
        return plain_text(self.raw.get("address")).strip() or self.detail_placeholder

    @property
    def detail_placeholder(self):
        if self.raw.get("_detail_checked_at"):
            return "网站未提供"
        return "详情待重试" if self.raw.get("_detail_error") else "详情待加载"

    @property
    def window(self):
        return f"{self.raw.get('bmStart') or '未提供'} → {self.raw.get('bmEnd') or '未提供'}"

    @property
    def times(self):
        start, end = self.raw.get("startTime"), self.raw.get("endTime")
        if not start and not end:
            return self.detail_placeholder
        return f"{start or self.detail_placeholder} → {end or self.detail_placeholder}"

    @property
    def capacity(self):
        count, limit = integer(self.raw.get("currentNum")), integer(self.raw.get("maxNum"))
        return (
            f"{count if count is not None else '?'} / {'不限' if limit == 0 else limit if limit is not None else '?'}"
        )

    def eligibility(self, now=None):
        now = now or now_beijing()
        if integer(self.raw.get("bmState")) == 1:
            return "registered"
        if self.raw.get("_missing"):
            return "unavailable"
        start, end = parse_time(self.raw.get("bmStart")), parse_time(self.raw.get("bmEnd"))
        if start is None or end is None or start >= end:
            return "unknown"
        if now >= end:
            return "ended"
        if integer(self.raw.get("state")) != 1 or self.raw.get("rz") != "否":
            return "blocked"
        if now < start:
            return "pending"
        if integer(self.raw.get("bmState")) != 2:
            return "unknown"
        count, limit = integer(self.raw.get("currentNum")), integer(self.raw.get("maxNum"))
        if count is None or limit is None or count < 0 or limit < 0:
            return "unknown"
        return "full" if limit > 0 and count >= limit else "ready"


def display_status(row, now=None):
    status = row.get("cancel_status")
    if status in {"cancelled", "not_registered"} and integer(row["raw"].get("bmState")) == 1:
        return "registered"
    return status or row.get("attempt_status") or Activity.from_row(row["raw"]).eligibility(now)


LABELS = {
    "pending": "待开启",
    "ready": "可报名",
    "ended": "已结束",
    "full": "名额已满",
    "registered": "已报名",
    "blocked": "需查看限制",
    "unknown": "信息待确认",
    "submitting": "正在提交",
    "success": "报名成功",
    "uncertain": "结果待确认",
    "rejected": "未通过",
    "paused": "已暂停",
    "waiting": "等待开放",
    "unavailable": "本次列表未找到",
    "cancelling": "正在取消报名",
    "cancelled": "已取消报名",
    "cancel_uncertain": "取消结果待确认",
    "cancel_rejected": "取消未通过",
    "not_registered": "当前未报名",
}
