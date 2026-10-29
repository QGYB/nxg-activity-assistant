# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""Authenticated HTTP transport used by the official File Transfer Assistant web client."""

import html
from http.cookiejar import Cookie, CookieJar
import json
from pathlib import Path
import re
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener

from .vault import Vault

ORIGIN = "https://filehelper.weixin.qq.com"
LOGIN_ORIGIN = "https://login.wx.qq.com"
APP_ID = "wx_webfilehelper"
SESSION_KEY = "f" * 32
WEB_HOSTS = {"filehelper.weixin.qq.com", "szfilehelper.weixin.qq.com", "cmfilehelper.weixin.qq.com"}
AUTH_HOSTS = {"login.wx.qq.com", "login.wx2.qq.com", "login.wx8.qq.com", "login.weixin.qq.com"}


class WechatWebError(RuntimeError):
    pass


class WechatLoginExpired(WechatWebError):
    pass


class WechatSendRejected(WechatWebError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"微信未接受通知（返回码 {code}）")


class SafeRedirect(HTTPRedirectHandler):
    def __init__(self, check):
        super().__init__()
        self.check = check

    def redirect_request(self, request, reply, code, message, headers, newurl):
        self.check(newurl)
        return super().redirect_request(request, reply, code, message, headers, newurl)


class FileHelperClient:
    def __init__(self, *, state=None, origin=ORIGIN, login_origin=LOGIN_ORIGIN, timeout=12):
        self.test_hosts = set()
        for value in (origin, login_origin):
            parsed = urlparse(value)
            if parsed.scheme == "http" and parsed.hostname == "127.0.0.1":
                self.test_hosts.add(parsed.netloc)
        self.origin = origin.rstrip("/")
        self.login_origin = login_origin.rstrip("/")
        self.timeout = timeout
        self.jar = CookieJar()
        self.state = dict(state or {})
        if self.state:
            self.origin = str(self.state.get("origin", self.origin))
            self.login_origin = str(self.state.get("login_origin", self.login_origin))
        self._check(self.origin)
        self._check(self.login_origin)
        for item in self.state.get("cookies", []):
            self.jar.set_cookie(
                Cookie(
                    0,
                    item["name"],
                    item["value"],
                    None,
                    False,
                    item["domain"],
                    True,
                    item["domain"].startswith("."),
                    item.get("path", "/"),
                    True,
                    item.get("secure", True),
                    item.get("expires"),
                    item.get("expires") is None,
                    None,
                    None,
                    {},
                    False,
                )
            )
        self.opener = build_opener(SafeRedirect(self._check), HTTPCookieProcessor(self.jar))
        self.qr_id = ""

    def _check(self, url):
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            raise WechatWebError("微信地址校验失败")
        if parsed.scheme == "http" and parsed.netloc in self.test_hosts:
            return
        if parsed.scheme != "https" or parsed.hostname not in WEB_HOSTS | AUTH_HOSTS or parsed.port not in (None, 443):
            raise WechatWebError("微信地址校验失败")

    def _request(self, url, data=None, *, timeout=None):
        self._check(url)
        request = Request(
            url,
            data=json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0.0.0 Safari/537.36",
                "mmweb_appid": APP_ID,
                "Content-Type": "application/json;charset=UTF-8",
                "Referer": self.origin + "/",
            },
        )
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                raw = response.read(8_000_001)
        except (HTTPError, URLError, OSError):
            raise WechatWebError("微信连接暂时失败，请稍后检查") from None
        if len(raw) > 8_000_000:
            raise WechatWebError("微信响应过大")
        return raw

    def _json(self, path, data, params=None):
        raw = self._request(self.origin + path + ("?" + urlencode(params) if params else ""), data)
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, ValueError):
            raise WechatWebError("微信响应格式未识别") from None
        if not isinstance(value, dict):
            raise WechatWebError("微信响应格式未识别")
        return value

    def begin_login(self):
        params = {
            "appid": APP_ID,
            "redirect_uri": self.origin + "/cgi-bin/mmwebwx-bin/webwxnewloginpage",
            "fun": "new",
            "lang": "zh_CN",
            "_": int(time.time() * 1000),
        }
        script = self._request(self.login_origin + "/jslogin?" + urlencode(params)).decode("utf-8")
        found = re.search(r'QRLogin\.uuid\s*=\s*["\x27]([A-Za-z0-9_=-]+)["\x27]', script)
        if not found:
            raise WechatWebError("暂时无法获取微信二维码")
        self.qr_id = found[1]
        image_url = "https://login.weixin.qq.com/qrcode/" + self.qr_id
        if self.test_hosts:
            image_url = self.login_origin + "/qrcode/" + self.qr_id
        return self._request(image_url)

    def poll_login(self):
        params = {
            "loginicon": "true",
            "uuid": self.qr_id,
            "tip": 1,
            "r": ~int(time.time() * 1000),
            "_": int(time.time() * 1000),
            "appid": APP_ID,
        }
        text = self._request(self.login_origin + "/cgi-bin/mmwebwx-bin/login?" + urlencode(params), timeout=35).decode(
            "utf-8"
        )
        found = re.search(r"window\.code\s*=\s*(\d+)", text)
        if not found:
            raise WechatWebError("微信扫码状态未识别")
        code = int(found[1])
        if code == 201:
            return "scanned"
        if code == 200:
            found = re.search(r'window\.redirect_uri\s*=\s*["\x27]([^"\x27]+)["\x27]', text)
            if not found:
                raise WechatWebError("微信授权结果缺少会话地址")
            redirect = html.unescape(found[1]).replace("\\/", "/")
            self._check(redirect)
            parsed = urlparse(redirect)
            if parsed.hostname not in WEB_HOSTS and parsed.netloc not in self.test_hosts:
                raise WechatWebError("微信会话地址不符合文件传输助手范围")
            if parsed.path != "/cgi-bin/mmwebwx-bin/webwxnewloginpage":
                raise WechatWebError("微信会话路径未识别")
            self.origin = parsed.scheme + "://" + parsed.netloc
            values = parse_qs(parsed.query)
            params = {
                key: values.get(key, [default])[0]
                for key, default in (("ticket", ""), ("uuid", self.qr_id), ("lang", "zh_CN"), ("scan", "1"))
            }
            params.update(fun="new", version="v2")
            xml = self._request(self.origin + parsed.path + "?" + urlencode(params)).decode("utf-8")

            def field(tag):
                match = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.DOTALL)
                return html.unescape(match[1]) if match else ""

            if field("ret") != "0" or any(not field(key) for key in ("skey", "wxsid", "wxuin", "pass_ticket")):
                raise WechatWebError("微信未确认网页版授权，请重新扫码")
            self.state.update(
                skey=field("skey"), sid=field("wxsid"), uin=field("wxuin"), pass_ticket=field("pass_ticket")
            )
            self.verify()
            return "confirmed"
        return "expired" if code in {0, 400, 408, 500} else "waiting"

    def base_request(self):
        try:
            return {
                "Uin": int(self.state["uin"]),
                "Sid": self.state["sid"],
                "Skey": self.state["skey"],
                "DeviceID": str(secrets.randbelow(10**15)).zfill(15),
            }
        except (KeyError, ValueError, TypeError):
            raise WechatLoginExpired("尚未连接微信网页版，请扫码授权") from None

    def verify(self):
        result = self._json(
            "/cgi-bin/mmwebwx-bin/webwxinit",
            {"BaseRequest": self.base_request()},
            {"r": ~int(time.time() * 1000), "lang": "zh_CN", "pass_ticket": self.state["pass_ticket"]},
        )
        if result.get("BaseResponse", {}).get("Ret") != 0:
            raise WechatLoginExpired("微信网页版登录已失效，请重新扫码")
        user = result.get("User", {})
        if not user.get("UserName"):
            raise WechatWebError("微信未返回有效账号信息")
        self.state.update(user_name=user["UserName"], nickname=str(user.get("NickName") or ""))
        return self.state["nickname"]

    def send(self, body):
        message_id = str(int(time.time() * 1000)) + str(secrets.randbelow(1000)).zfill(3)
        result = self._json(
            "/cgi-bin/mmwebwx-bin/webwxsendmsg",
            {
                "BaseRequest": self.base_request(),
                "Msg": {
                    "ClientMsgId": message_id,
                    "LocalID": message_id,
                    "FromUserName": self.state["user_name"],
                    "ToUserName": "filehelper",
                    "Type": 1,
                    "Content": body,
                },
                "Scene": 0,
            },
            {"lang": "zh_CN", "pass_ticket": self.state["pass_ticket"]},
        )
        code = result.get("BaseResponse", {}).get("Ret")
        if code != 0:
            raise WechatSendRejected(code)
        return str(result.get("MsgID") or result.get("LocalID") or "")

    def export(self):
        cookies = [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "secure": c.secure,
                "expires": c.expires,
            }
            for c in self.jar
        ]
        return json.dumps(
            self.state | {"origin": self.origin, "login_origin": self.login_origin, "cookies": cookies},
            ensure_ascii=False,
        )


class WebWechatSender:
    def __init__(self, directory):
        self.vault = Vault(Path(directory) / "wechat-web")
        self.client = None
        self.checked_at = 0
        self.signature = None
        self.status = "未连接"

    def maintain(self):
        path = self.vault.path(SESSION_KEY)
        signature = path.stat().st_mtime_ns if path.exists() else None
        if signature is None:
            self.status = "未连接"
            self.client = None
            return
        if signature == self.signature and time.monotonic() - self.checked_at < 120:
            return
        self.signature, self.checked_at = signature, time.monotonic()
        try:
            client = FileHelperClient(state=json.loads(self.vault.load(SESSION_KEY)))
            client.verify()
        except WechatLoginExpired:
            self.client, self.status = None, "登录已失效"
        except (OSError, ValueError, WechatWebError):
            self.client, self.status = None, "连接待恢复"
        else:
            self.client, self.status = client, "已连接"

    def ready(self):
        self.maintain()
        return self.client is not None and self.status == "已连接"

    def send(self, body):
        if self.client is None:
            raise WechatLoginExpired("微信尚未连接")
        try:
            self.client.send(body)
        except WechatSendRejected as error:
            if error.code in {1100, 1101, 1102, 1205}:
                self.client, self.status = None, "登录已失效"
            raise
