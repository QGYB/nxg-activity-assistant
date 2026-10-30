# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from datetime import datetime
import json

import pytest

from activity_check.models import Activity, BEIJING, display_status
from activity_check.session import SessionError, import_session


def activity(**overrides):
    row = {
        "id": 42,
        "projectName": "示例活动",
        "state": 1,
        "bmState": 2,
        "rz": "否",
        "currentNum": 3,
        "maxNum": 10,
        "bmStart": "2026-10-30 16:00:00",
        "bmEnd": "2026-10-30 20:00:00",
        "startTime": "2026-11-01 14:00:00",
        "endTime": "2026-11-01 16:00:00",
    }
    return Activity.from_row(row | overrides)


def test_registration_opens_in_beijing_not_machine_timezone():
    a = activity()
    assert a.eligibility(datetime(2026, 10, 30, 15, 59, 59, tzinfo=BEIJING)) == "pending"
    assert a.eligibility(datetime(2026, 10, 30, 16, 0, 0, tzinfo=BEIJING)) == "ready"
    assert a.eligibility(datetime(2026, 10, 30, 20, 0, 0, tzinfo=BEIJING)) == "ended"


@pytest.mark.parametrize(
    "fields,want",
    [
        ({"maxNum": 0}, "ready"),
        ({"maxNum": 3}, "full"),
        ({"currentNum": None}, "unknown"),
        ({"bmStart": ""}, "unknown"),
        ({"bmState": 1}, "registered"),
        ({"state": 0}, "blocked"),
        ({"rz": "是"}, "blocked"),
        ({"maxNum": None}, "unknown"),
    ],
)
def test_site_constraints_never_become_fake_availability(fields, want):
    assert activity(**fields).eligibility(datetime(2026, 10, 30, 17, tzinfo=BEIJING)) == want


def test_manual_full_login_data_keeps_student_and_storage():
    session = import_session(
        json.dumps(
            {
                "cookie": "sid=demo; theme=light",
                "localStorage": {"token": "fixture-token", "teaInfo": json.dumps({"id": "student-A", "status": "1"})},
            }
        )
    )
    assert session.student_id == "student-A"
    assert session.token == "fixture-token"
    assert {c["name"] for c in session.state["cookies"]} == {"sid", "theme"}
    assert session.state["origins"][0]["origin"] == "https://nxg.zwu.edu.cn"


def test_cookie_without_website_token_is_rejected_explicitly():
    with pytest.raises(SessionError, match="登录数据"):
        import_session("sid=demo")


def test_playwright_state_preserves_http_only_cookie():
    state = {
        "cookies": [
            {
                "name": "sid",
                "value": "demo",
                "domain": "nxg.zwu.edu.cn",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [
            {
                "origin": "https://nxg.zwu.edu.cn",
                "localStorage": [
                    {"name": "token", "value": "fixture-token"},
                    {"name": "teaInfo", "value": '{"id":"A","status":"1"}'},
                ],
            }
        ],
    }
    session = import_session(json.dumps(state))
    assert session.state["cookies"][0]["httpOnly"] is True


def test_foreign_cookie_does_not_get_sent_to_school():
    raw = {
        "token": "fixture-token",
        "teaInfo": {"id": "A", "status": "1"},
        "cookies": [{"name": "secret", "value": "no", "domain": "unrelated.example", "path": "/"}],
    }
    with pytest.raises(SessionError):
        import_session(json.dumps(raw))


def test_teacher_identity_is_not_used_for_student_registration():
    with pytest.raises(SessionError):
        import_session(json.dumps({"token": "fixture", "teaInfo": {"id": "T", "status": "2"}}))


@pytest.mark.parametrize(
    "fields",
    [
        {"origins": None},
        {"origins": [1]},
        {"cookies": "invalid"},
        {"origins": [{"origin": "https://nxg.zwu.edu.cn", "localStorage": None}]},
    ],
)
def test_malformed_browser_state_is_a_clear_import_error(fields):
    with pytest.raises(SessionError):
        import_session(json.dumps({"token": "fixture", "teaInfo": {"id": "A", "status": "1"}} | fields))


def test_cookie_control_characters_are_rejected_before_networking():
    with pytest.raises(SessionError):
        import_session(
            json.dumps(
                {
                    "token": "fixture",
                    "teaInfo": {"id": "A", "status": "1"},
                    "cookies": [
                        {"name": "sid", "value": "value\r\nX-Header: bad", "domain": "nxg.zwu.edu.cn", "path": "/"}
                    ],
                }
            )
        )


def test_exported_string_cookie_expiry_is_normalized_before_transport():
    session = import_session(
        json.dumps(
            {
                "token": "fixture",
                "teaInfo": {"id": "A"},
                "cookies": [{"name": "sid", "value": "x", "domain": "nxg.zwu.edu.cn", "expires": "1999999999"}],
            }
        )
    )
    assert session.state["cookies"][0]["expires"] == 1999999999.0


def test_short_tokens_are_also_redacted_from_server_messages():
    from activity_check.site import SiteClient

    session = import_session('{"token":"xx","teaInfo":{"id":"A"}}')
    assert "xx" not in SiteClient(session).message({"msg": "无法处理 xx"})


@pytest.mark.parametrize("attempt,bm_state", [(None, 1), ("success", 2), (None, None)])
def test_past_activity_displays_ended_without_changing_engine_semantics(attempt, bm_state):
    a = activity(bmState=bm_state, bmStart=None, bmEnd=None)
    now = datetime(2026, 11, 1, 16, tzinfo=BEIJING)
    assert display_status({"raw": a.raw, "attempt_status": attempt}, now) == "ended"
    assert a.eligibility(now) == ("registered" if bm_state == 1 else "unknown")


@pytest.mark.parametrize("hour", [13, 14, 15])
def test_upcoming_and_ongoing_registrations_remain_registered(hour):
    a = activity(bmState=1)
    assert display_status({"raw": a.raw}, datetime(2026, 11, 1, hour, tzinfo=BEIJING)) == "registered"


@pytest.mark.parametrize("end", [None, "", "not-a-time"])
def test_missing_activity_end_is_not_invented_from_signup_deadline(end):
    a = activity(bmState=1, endTime=end)
    assert display_status({"raw": a.raw}, datetime(2026, 11, 3, tzinfo=BEIJING)) == "registered"


@pytest.mark.parametrize("status", ["submitting", "uncertain", "cancelling", "cancel_uncertain", "cancelled"])
def test_ended_activity_keeps_submission_and_cancellation_results_visible(status):
    a = activity(bmState=2)
    key = "cancel_status" if status.startswith("cancel") else "attempt_status"
    assert display_status({"raw": a.raw, key: status}, datetime(2026, 11, 3, tzinfo=BEIJING)) == status


def test_login_entry_and_registration_origin_are_separate():
    from activity_check.models import LIST_URL, LOGIN_URL, ORIGIN

    assert LOGIN_URL == "https://ehallapp.zwu.edu.cn:8080/"
    assert ORIGIN == "https://nxg.zwu.edu.cn"
    assert LIST_URL == ORIGIN + "/zhxg/#/lifePractice/signup/list"


def test_portal_storage_explains_where_to_export_activity_login_data():
    with pytest.raises(SessionError, match="活动列表"):
        import_session('{"localStorage":{"Home_UseProxyAccount":"fixture"}}')
