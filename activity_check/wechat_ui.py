# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""One-time QR authorization for direct File Transfer Assistant HTTP notifications."""

from io import BytesIO
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from .identity import fit_window, install_window_icons
from .vault import Vault
from .wechat_web import FileHelperClient, SESSION_KEY, WechatWebError


class WechatLoginDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("连接微信通知 · 扫码授权")
        fit_window(self, 470, 520, 420, 470)
        self.transient(app.root)
        self.configure(background="#F5F6F8")
        install_window_icons(self, app.icon_path)
        body = ttk.Frame(self, padding=24)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="连接文件传输助手", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text="用手机微信扫一扫，并确认“仅传输文件”授权。\n连接后通过后台接口发送通知，不操作电脑微信。",
            wraplength=round(400 * app.scale),
        ).pack(anchor="w", pady=(12, 18))
        self.qr_label = ttk.Label(body, anchor="center")
        self.qr_label.pack(expand=True, fill="both")
        self.status = tk.StringVar(value="正在向微信官方服务器获取二维码…")
        ttk.Label(body, textvariable=self.status, wraplength=round(400 * app.scale)).pack(pady=14)
        ttk.Button(body, text="关闭", command=self.close).pack()
        self.updates = queue.Queue()
        self.stopping = threading.Event()
        self.bind("<Destroy>", lambda event: self.stopping.set() if event.widget is self else None)
        self.protocol("WM_DELETE_WINDOW", self.close)
        directory = app.vault.directory.parent
        threading.Thread(target=self._authorize, args=(directory,), name="wechat-qr-login", daemon=True).start()
        self.after(150, self._poll)

    def _authorize(self, directory):
        try:
            client = FileHelperClient()
            self.updates.put(("image", client.begin_login()))
            started = time.monotonic()
            while not self.stopping.is_set() and time.monotonic() - started < 240:
                status = client.poll_login()
                if self.stopping.is_set():
                    return
                if status == "confirmed":
                    Vault(directory / "wechat-web").save(SESSION_KEY, client.export())
                    self.updates.put(("connected", None))
                    return
                if status == "scanned":
                    self.updates.put(("status", "已扫码，请在手机微信确认授权。"))
                elif status == "expired":
                    self.updates.put(("image", client.begin_login()))
                self.stopping.wait(0.5)
            if not self.stopping.is_set():
                self.updates.put(("status", "本次扫码已超时，请关闭后重新连接。"))
        except WechatWebError as error:
            self.updates.put(("status", str(error)))
        except Exception:
            self.updates.put(("status", "连接暂未完成，请关闭后重试。"))

    def _poll(self):
        if self.stopping.is_set():
            return
        try:
            while True:
                kind, value = self.updates.get_nowait()
                if kind == "image":
                    with Image.open(BytesIO(value)) as image:
                        side = round(270 * self.app.scale)
                        self.qr_image = ImageTk.PhotoImage(
                            image.resize((side, side), Image.Resampling.NEAREST), master=self
                        )
                    self.qr_label.configure(image=self.qr_image)
                    self.status.set("请使用手机微信扫描二维码。")
                elif kind == "connected":
                    self.status.set("微信已授权。今后报名成功会通过后台发到文件传输助手。")
                    self.qr_label.configure(image="", text="已连接")
                    if self.app.notifier:
                        self.app.notifier.retry_at = 0
                        self.app.notifier.sender.checked_at = 0
                    self.app.footer.set("微信网页版已授权，后续通知使用后台接口发送。")
                else:
                    self.status.set(value)
        except queue.Empty:
            pass
        self.after(200, self._poll)

    def close(self):
        self.stopping.set()
        self.destroy()
