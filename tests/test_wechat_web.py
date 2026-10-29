# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlparse

import pytest

from activity_check.wechat_web import FileHelperClient, WechatWebError, WechatLoginExpired, WechatSendRejected


@contextmanager
def web_fixture(*, reject=0, foreign=False, expired=False):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = urlparse(self.path).path
            query = parse_qs(urlparse(self.path).query)
            calls.append((path, query))
            if path == "/jslogin":
                assert query["appid"] == ["wx_webfilehelper"]
                body = b'window.QRLogin.code = 200; window.QRLogin.uuid = "fixture-qr";'
            elif path == "/qrcode/fixture-qr":
                body = b"image-fixture"
            elif path.endswith("/login"):
                destination = (
                    "https://invalid.example/"
                    if foreign
                    else f"http://127.0.0.1:{self.server.server_port}/cgi-bin/mmwebwx-bin/webwxnewloginpage?ticket=fixture-ticket&uuid=fixture-qr&lang=zh_CN&scan=1"
                )
                body = f'window.code=200; window.redirect_uri="{destination}";'.encode()
            elif path.endswith("/webwxnewloginpage"):
                assert query["fun"] == ["new"] and query["version"] == ["v2"]
                body = b"<error><ret>0</ret><skey>fixture-skey</skey><wxsid>fixture-sid</wxsid><wxuin>42</wxuin><pass_ticket>fixture-pass</pass_ticket></error>"
            else:
                raise AssertionError(path)
            self.send_response(200)
            if path.endswith("/webwxnewloginpage"):
                self.send_header("Set-Cookie", "wxsid=fixture-cookie; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            path = urlparse(self.path).path
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((path, data))
            assert self.headers["mmweb_appid"] == "wx_webfilehelper"
            assert data["BaseRequest"]["Uin"] == 42
            assert data["BaseRequest"]["Sid"] == "fixture-sid"
            assert self.headers["Cookie"] == "wxsid=fixture-cookie"
            if path.endswith("/webwxinit"):
                result = {
                    "BaseResponse": {"Ret": 1101 if expired else 0},
                    "User": {"UserName": "@fixture-user", "NickName": "示例"},
                }
            elif path.endswith("/webwxsendmsg"):
                assert data["Msg"]["ToUserName"] == "filehelper"
                assert data["Msg"]["FromUserName"] == "@fixture-user"
                assert data["Msg"]["Type"] == 1
                assert data["Msg"]["ClientMsgId"] == data["Msg"]["LocalID"]
                result = {"BaseResponse": {"Ret": reject}, "MsgID": "fixture-msg"}
            else:
                raise AssertionError(path)
            body = json.dumps(result).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        yield FileHelperClient(origin=origin, login_origin=origin), calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_qr_session_sends_unicode_directly_to_filehelper_and_restores_cookies():
    with web_fixture() as (client, calls):
        assert client.begin_login() == b"image-fixture"
        assert client.poll_login() == "confirmed"
        text = '已报名---测试 "文字" ; $(不执行)'
        assert client.send(text) == "fixture-msg"
        restored = FileHelperClient(
            state=json.loads(client.export()), origin=client.origin, login_origin=client.login_origin
        )
        assert restored.verify() == "示例"
    sends = [data for path, data in calls if path.endswith("/webwxsendmsg")]
    assert len(sends) == 1 and sends[0]["Msg"]["Content"] == text


def test_scan_does_not_follow_untrusted_redirect_or_send():
    with web_fixture(foreign=True) as (client, calls):
        client.begin_login()
        with pytest.raises(WechatWebError, match="地址校验"):
            client.poll_login()
    assert not any(path.endswith("/webwxnewloginpage") for path, _data in calls)


def test_expired_login_is_never_accepted_as_connected():
    with web_fixture(expired=True) as (client, calls):
        client.begin_login()
        with pytest.raises(WechatLoginExpired):
            client.poll_login()
    assert not any(path.endswith("/webwxsendmsg") for path, _data in calls)


def test_explicit_server_rejection_does_not_report_sent():
    with web_fixture(reject=1101) as (client, calls):
        client.begin_login()
        client.poll_login()
        with pytest.raises(WechatSendRejected) as result:
            client.send("已报名---测试")
        assert result.value.code == 1101
    assert [path for path, _data in calls].count("/cgi-bin/mmwebwx-bin/webwxsendmsg") == 1


@pytest.mark.parametrize(
    "origin",
    ["https://invalid.example", "http://filehelper.weixin.qq.com", "https://filehelper.weixin.qq.com@invalid.example"],
)
def test_saved_session_cannot_redirect_credentials_to_another_host(origin):
    with pytest.raises(WechatWebError):
        FileHelperClient(state={"origin": origin})
