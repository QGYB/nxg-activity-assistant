# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""Windows user-bound encryption; no plaintext authentication snapshots on disk."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import tempfile


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    if os.name != "nt":
        raise OSError("登录态加密需要 Windows。")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    func.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    func.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError(
            "无法解密登录数据，请使用保存它的 Windows 账号重新登录。" if decrypt else "无法加密保存登录数据。"
        )
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class Vault:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path(self, account):
        if not re.fullmatch("[a-f0-9]{32}", account):
            raise ValueError("无效账号标识。")
        return self.directory / (account + ".auth")

    def save(self, account, value):
        target = self.path(account)
        encrypted = b"NXGAUTH1\0" + _crypt(value.encode("utf-8"))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def load(self, account):
        value = self.path(account).read_bytes()
        if not value.startswith(b"NXGAUTH1\0"):
            raise OSError("登录数据文件格式不正确。")
        return _crypt(value[9:], decrypt=True).decode("utf-8")

    def remove(self, account):
        self.path(account).unlink(missing_ok=True)
