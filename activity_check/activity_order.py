# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""Display ordering; no monitoring or registration side effects."""

from math import inf

from .models import display_status, integer, now_beijing, parse_time


def remaining_slots(row):
    raw = row["raw"]
    limit, count = integer(raw.get("maxNum")), integer(raw.get("currentNum"))
    if limit == 0:
        return inf
    if limit is None or count is None or limit < 0 or count < 0:
        return None
    return max(0, limit - count)


def order_rows(rows, *, time_descending=True, remaining_descending=None):
    def time_key(row):
        value = parse_time(row["raw"].get("startTime"))
        return value is None, (-value.timestamp() if time_descending else value.timestamp()) if value else 0

    ordered = list(rows)
    if time_descending is not None:
        ordered.sort(key=time_key)
    if remaining_descending is not None:

        def capacity_key(row):
            value = remaining_slots(row)
            return value is None, (-value if remaining_descending else value) if value is not None else 0

        ordered.sort(key=capacity_key)
    now = now_beijing()

    def status_group(row):
        status = display_status(row, now)
        if status in {"registered", "success"}:
            return 0
        if status == "ready":
            return 1
        if status == "full":
            return 3
        if status == "ended":
            return 4
        return 2

    ordered.sort(key=status_group)
    return ordered
