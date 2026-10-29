# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""Campus recognition comes only from the site's activity location field."""

from .models import plain_text

CAMPUSES = ("回龙", "钱湖")


def campuses_for_address(value):
    address = "".join(plain_text(value).split())
    return [campus for campus in CAMPUSES if campus in address]


def matches_campus(raw, campus):
    return not campus or campus in campuses_for_address(raw.get("address"))
