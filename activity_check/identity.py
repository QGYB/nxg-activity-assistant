# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import ctypes
from ctypes import wintypes
import hashlib
import os


def enable_dpi_awareness():
    """Run before Tk creates any HWND, so Windows does not enlarge a 96-DPI bitmap."""
    if os.name != "nt":
        return
    user = ctypes.WinDLL("user32", use_last_error=True)
    try:
        function = user.SetProcessDpiAwarenessContext
        function.argtypes = [ctypes.c_void_p]
        function.restype = wintypes.BOOL
        function(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except AttributeError:
        user.SetProcessDPIAware()


def fit_window(window, width, height, min_width, min_height):
    """Keep the intended logical size while respecting the physical screen bounds."""
    scale = window.winfo_fpixels("1i") / 96
    available_width = max(640, window.winfo_screenwidth() - round(64 * scale))
    available_height = max(480, window.winfo_screenheight() - round(100 * scale))
    window.geometry(f"{min(round(width * scale), available_width)}x{min(round(height * scale), available_height)}")
    window.minsize(min(round(min_width * scale), available_width), min(round(min_height * scale), available_height))


def install_window_icons(window, path):
    """Load exact ICO frames with Win32 instead of Tk's resampled icon bitmap."""
    if os.name != "nt":
        return
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user.LoadImageW.restype = wintypes.HANDLE
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.restype = wintypes.LPARAM
    user.GetDpiForWindow.argtypes = [wintypes.HWND]
    user.GetDpiForWindow.restype = wintypes.UINT
    user.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
    user.GetSystemMetricsForDpi.restype = ctypes.c_int
    user.DestroyIcon.argtypes = [wintypes.HICON]
    user.DestroyIcon.restype = wintypes.BOOL
    state = {"dpi": None, "handles": []}
    path = str(path.resolve())

    def refresh(event=None, *, force=False):
        if event is not None and event.widget is not window:
            return
        if not window.winfo_exists():
            return
        hwnd = int(window.wm_frame(), 0)
        dpi = user.GetDpiForWindow(hwnd) or 96
        if not force and dpi == state["dpi"]:
            return
        sizes = [user.GetSystemMetricsForDpi(metric, dpi) for metric in (11, 49)]
        handles = [user.LoadImageW(None, path, 1, size, size, 0x10) for size in sizes]
        if not all(handles):
            for handle in handles:
                if handle:
                    user.DestroyIcon(handle)
            return
        user.SendMessageW(hwnd, 0x80, 1, handles[0])  # WM_SETICON / ICON_BIG
        user.SendMessageW(hwnd, 0x80, 0, handles[1])  # WM_SETICON / ICON_SMALL
        for handle in state["handles"]:
            user.DestroyIcon(handle)
        state.update(dpi=dpi, handles=handles)

    def mapped(event):
        if event.widget is window:
            window.after_idle(lambda: refresh(force=True))

    def destroyed(event):
        if event.widget is window:
            for handle in state["handles"]:
                user.DestroyIcon(handle)
            state["handles"] = []

    window._native_icon_state = state
    window.bind("<Map>", mapped, add="+")
    window.bind("<Configure>", refresh, add="+")
    window.bind("<Destroy>", destroyed, add="+")
    window.after_idle(lambda: refresh(force=True))


class SingleInstance:
    def __init__(self, directory):
        self.handle = None
        if os.name != "nt":
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        key = hashlib.sha256(str(directory.resolve()).casefold().encode()).hexdigest()[:24]
        self.handle = kernel.CreateMutexW(None, False, "Local\\NXGActivityCheck-" + key)
        if not self.handle:
            raise OSError("无法创建程序实例锁。")
        self.already_running = ctypes.get_last_error() == 183
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NXG.ActivityCheck.Desktop.v1")

    def close(self):
        if self.handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self.handle)
            self.handle = None
