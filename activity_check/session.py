# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from dataclasses import dataclass, field
from http.cookies import SimpleCookie, CookieError
import json
import math
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from .models import ORIGIN


class SessionError(ValueError):
    pass


PROFILE_KEYS = ("teaInfo", "stuInfo", "userInfo", "studentInfo")


def _private_storage_values(storage):
    private = {value for value in storage.values() if isinstance(value, str) and value}
    for key in PROFILE_KEYS:
        info = storage.get(key)
        if isinstance(info, str):
            try:
                info = json.loads(info)
            except ValueError:
                continue
        if isinstance(info, dict):
            private.update(str(value) for value in info.values() if isinstance(value, (str, int)) and value)
    return private


def storage_key_names(storage):
    """Bound diagnostics to short key identifiers, excluding values used as keys."""
    private = _private_storage_values(storage)
    keys = sorted(
        key
        for key in storage
        if isinstance(key, str)
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,47}", key)
        and not any(key == value or (len(value) >= 8 and value in key) for value in private)
    )[:12]
    omitted = len(storage) - len(keys)
    return (", ".join(keys) or "无可显示键名") + (f"（另有 {omitted} 项名称已省略）" if omitted else "")


def login_page_location(url, storage):
    """Never put URL credentials, OAuth queries/fragments or known secrets in logs."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return "尚未打开学校页面"
        host = parts.hostname + (":" + str(parts.port) if parts.port else "")
    except ValueError:
        return "页面地址暂不可读"
    private = _private_storage_values(storage)
    segments = []
    for segment in (parts.path or "/").split("/"):
        if unquote(segment) in private:
            segment = "[已隐藏]"
        else:
            for value in private:
                if len(value) >= 8:
                    segment = segment.replace(value, "[已隐藏]")
            segment = re.sub(r"[A-Za-z0-9_-]{24,}", "[已隐藏]", segment)
        segments.append(segment)
    location = urlunsplit((parts.scheme, host, "/".join(segments), "", ""))
    return re.sub(r"[\x00-\x1f\x7f]", "", location)[:180]


@dataclass(repr=False)
class Session:
    state: dict = field(repr=False)
    token: str = field(repr=False)
    student_id: str
    display_name: str = ""

    def __repr__(self):
        return "Session(<private>)"

    def to_json(self):
        return json.dumps(self.state, ensure_ascii=False)


def school_domain(domain):
    return str(domain).lstrip(".") in {"nxg.zwu.edu.cn", "zwu.edu.cn"}


def _storage(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        result = {}
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or "value" not in item:
                raise SessionError("登录数据中的本地存储格式不正确。")
            result[item["name"]] = item["value"]
        return result
    raise SessionError("登录数据中的本地存储格式不正确。")


def import_session(raw, token="", student_id=""):
    """Accept the site's exported local storage, a browser state, or Cookie plus identity."""
    if len(raw) > 4_000_000:
        raise SessionError("登录数据过大，请重新复制学校网站的登录数据。")
    raw = raw.strip()
    data = {}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise SessionError("登录数据不是有效的 JSON。") from None
        if not isinstance(data, dict):
            raise SessionError("登录数据应为 JSON 对象。")
    elif raw:
        data = {"cookie": raw.removeprefix("Cookie:").removeprefix("cookie:").strip()}
    storage = _storage(data.get("localStorage", {}))
    origins = data.get("origins", [])
    if not isinstance(origins, list) or any(not isinstance(item, dict) for item in origins):
        raise SessionError("登录数据中的网站列表格式不正确。")
    for origin in origins:
        if origin.get("origin") == ORIGIN:
            storage.update(_storage(origin.get("localStorage", [])))
    token = str(token or data.get("token") or storage.get("token") or "").strip()
    token = token.removeprefix("Bearer ").strip()
    info, profile_key, profile_error = {}, None, None
    for key in PROFILE_KEYS:
        candidate = data.get(key) or storage.get(key)
        if candidate is None or candidate == "":
            continue
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except ValueError:
                profile_error = profile_error or "登录数据中的个人信息格式不正确。"
                continue
        if candidate is None:
            continue
        if not isinstance(candidate, dict):
            profile_error = profile_error or "登录数据中的个人信息格式不正确。"
            continue
        if str(candidate.get("status", "1")) != "1":
            profile_error = profile_error or "请选择学生账号的登录数据。"
            continue
        if candidate.get("id"):
            info, profile_key = candidate, key
            break
    if not profile_key and profile_error:
        raise SessionError(profile_error + "；当前存储键：" + storage_key_names(storage))
    identity = str(info.get("id") or student_id or "").strip()
    if not token or not identity or any(c in token + identity for c in "\r\n"):
        raise SessionError(
            "还需要完整登录数据（Token 和个人标识）。请先从学校门户进入智慧思政活动报名，"
            "看到活动列表后，再在该页面运行“复制登录数据命令”；门户首页的数据不能用于报名"
            "；当前存储键：" + storage_key_names(storage)
        )
    if student_id and str(student_id).strip() != identity:
        raise SessionError("填写的个人标识与登录数据不一致。")
    info = info | {"id": identity, "status": "1"}
    storage["token"] = token
    storage["teaInfo"] = json.dumps(info, ensure_ascii=False)
    if profile_key and profile_key != "teaInfo":
        storage[profile_key] = json.dumps(info, ensure_ascii=False)
    cookies = []
    cookie_items = data.get("cookies", [])
    if not isinstance(cookie_items, list):
        raise SessionError("Cookie 列表格式不正确。")
    for cookie in cookie_items:
        if not isinstance(cookie, dict) or not school_domain(cookie.get("domain", "")):
            raise SessionError("登录数据包含其他网站的 Cookie，请只导出学校网站。")
        if "name" not in cookie or "value" not in cookie:
            raise SessionError("Cookie 格式不完整。")
        if not isinstance(cookie["name"], str) or not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", cookie["name"]):
            raise SessionError("Cookie 名称格式不正确。")
        if not isinstance(cookie["value"], str) or any(ord(c) < 32 or ord(c) == 127 for c in cookie["value"]):
            raise SessionError("Cookie 中包含无效的控制字符。")
        path = cookie.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/") or any(ord(c) < 32 for c in path):
            raise SessionError("Cookie 路径格式不正确。")
        try:
            expires = float(cookie.get("expires", -1))
            if not math.isfinite(expires):
                raise ValueError()
        except (TypeError, ValueError):
            raise SessionError("Cookie 有效期格式不正确。") from None
        cookie = cookie | {"expires": expires, "path": path}
        cookies.append(
            {
                k: v
                for k, v in cookie.items()
                if k in {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
            }
        )
    cookie_header = data.get("cookie", "")
    if cookie_header:
        if not isinstance(cookie_header, str) or any(ord(c) < 32 for c in cookie_header):
            raise SessionError("Cookie 内容格式不正确，请只复制 Cookie 值。")
        try:
            jar = SimpleCookie()
            jar.load(str(cookie_header))
            if not jar:
                raise CookieError()
            cookies.extend(
                {
                    "name": name,
                    "value": morsel.value,
                    "domain": "nxg.zwu.edu.cn",
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                    "sameSite": "Lax",
                }
                for name, morsel in jar.items()
            )
        except CookieError:
            raise SessionError("Cookie 格式不正确。") from None
    state = {
        "cookies": cookies,
        "origins": [
            {"origin": ORIGIN, "localStorage": [{"name": str(k), "value": str(v)} for k, v in storage.items()]}
        ],
    }
    return Session(state, token, identity, str(info.get("name") or info.get("studentName") or ""))


EXPORT_COMMAND = (
    "copy(JSON.stringify({cookie:document.cookie,localStorage:Object.fromEntries(Object.entries(localStorage))}))"
)
