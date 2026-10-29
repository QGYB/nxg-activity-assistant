# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""The exact form endpoints used by the school's own activity registration page."""

from http.cookiejar import Cookie, CookieJar
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor, HTTPSHandler, HTTPRedirectHandler, Request

from .models import ORIGIN

READ_PATHS = {
    "/c/credit/getHdTermList",
    "/c/credit/getCreditModuleList",
    "/s/credit/getHdBmList",
    "/c/credit/getHdBmDetails",
    "/s/credit/hdLose",
}
SUBMIT_PATH = "/s/credit/hdBm"
CANCEL_PATH = "/s/credit/qxHdBm"


class SiteError(RuntimeError):
    pass


class LoginExpired(SiteError):
    pass


class NetworkError(SiteError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NetworkError("网站返回跳转，请重新登录检查；请求未自动重发。")


class SiteClient:
    def __init__(self, session, *, base_url=ORIGIN + "/api", timeout=12):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        jar = CookieJar()
        for c in session.state.get("cookies", []):
            expires = c.get("expires")
            jar.set_cookie(
                Cookie(
                    0,
                    c["name"],
                    c["value"],
                    None,
                    False,
                    c["domain"],
                    True,
                    c["domain"].startswith("."),
                    c.get("path", "/"),
                    True,
                    c.get("secure", True),
                    int(expires) if expires and expires > 0 else None,
                    not expires or expires <= 0,
                    None,
                    None,
                    {},
                    False,
                )
            )
        self.opener = build_opener(
            NoRedirect(), HTTPCookieProcessor(jar), HTTPSHandler(context=ssl.create_default_context())
        )

    def message(self, response):
        text = str(response.get("msg") or response.get("message") or "网站返回了未识别的结果")
        for value in [self.session.token, self.session.student_id] + [
            c.get("value", "") for c in self.session.state["cookies"]
        ]:
            if value:
                text = text.replace(value, "[已隐藏]")
        return text.replace("\r", " ").replace("\n", " ")[:300]

    def post(self, path, fields=None):
        if path not in READ_PATHS | {SUBMIT_PATH, CANCEL_PATH}:
            raise SiteError("不支持该网站操作。")
        form = {key: value for key, value in (fields or {}).items() if value is not None and value != ""}
        request = Request(
            self.base_url + path,
            data=urlencode(form).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer " + self.session.token,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Origin": ORIGIN,
                "Referer": ORIGIN + "/zhxg/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0.0.0 Safari/537.36",
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as reply:
                content = reply.read(8_000_001)
                if len(content) > 8_000_000:
                    raise NetworkError("网站响应过大，请稍后检查。")
                response = json.loads(content.decode("utf-8-sig"))
        except HTTPError as error:
            if error.code in (401, 403):
                raise LoginExpired("登录已失效，请重新保存个人 Cookie。") from None
            raise NetworkError(f"网站连接异常（HTTP {error.code}）。") from None
        except (URLError, OSError, ValueError):
            raise NetworkError("网络中断或网站响应无法识别，请检查连接。") from None
        if not isinstance(response, dict) or "code" not in response:
            raise NetworkError("网站响应结构有变化，请查看原网页。")
        if str(response["code"]) in {"401", "403", "-2"}:
            raise LoginExpired("登录已失效，请重新保存个人 Cookie。")
        return response

    def _read(self, path, fields=None):
        response = self.post(path, fields)
        if str(response["code"]) != "0":
            raise SiteError(self.message(response))
        return response.get("data")

    def terms(self):
        value = self._read("/c/credit/getHdTermList")
        if not isinstance(value, list):
            raise SiteError("学期信息格式有变化，请查看原网页。")
        return value

    def activities(self, term="", *, include_ended=False):
        rows = {}
        for state in [0, 1, 2] if include_ended else [0, 1]:
            value = self._read(
                "/s/credit/getHdBmList",
                {"studentId": self.session.student_id, "moduleId": "", "rz": "", "schoolTerm": term, "state": state},
            )
            if not isinstance(value, list):
                raise SiteError("活动列表结构有变化，请查看原网页。")
            for row in value:
                if isinstance(row, dict) and row.get("id") is not None:
                    rows[str(row["id"])] = row
        return list(rows.values())

    def detail(self, activity):
        value = self._read("/c/credit/getHdBmDetails", {"id": str(activity), "studentId": self.session.student_id})
        if not isinstance(value, dict):
            raise SiteError("活动详情结构有变化，请查看原网页。")
        if value.get("id") is not None and str(value["id"]) != str(activity):
            raise SiteError("活动详情标识不一致，已停止处理。")
        return value

    def preflight(self):
        return self.post("/s/credit/hdLose", {"studentId": self.session.student_id})

    def submit(self, activity):
        return self.post(SUBMIT_PATH, {"id": str(activity), "studentId": self.session.student_id})

    def cancel(self, activity):
        return self.post(CANCEL_PATH, {"id": str(activity), "studentId": self.session.student_id})
