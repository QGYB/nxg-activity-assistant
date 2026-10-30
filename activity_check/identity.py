# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path


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


def _instance_name(directory):
    key = hashlib.sha256(str(Path(directory).resolve()).casefold().encode()).hexdigest()[:24]
    return "NXGActivityCheck-" + key


def activate_existing_window(directory):
    """Restore the instance for this data directory, including an iconic window."""
    if os.name != "nt":
        return False
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.GetPropW.restype = wintypes.HANDLE
    user.IsIconic.argtypes = [wintypes.HWND]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    name = _instance_name(directory)
    found = []

    @callback_type
    def collect(handle, _data):
        if user.GetPropW(handle, name):
            found.append(handle)
            return False
        return True

    user.EnumWindows(collect, 0)
    if not found:
        return False
    handle = found[0]
    if user.IsIconic(handle):
        user.ShowWindowAsync(handle, 9)  # SW_RESTORE; leave a maximized window maximized.
    elif not user.IsWindowVisible(handle):
        user.ShowWindowAsync(handle, 5)  # SW_SHOW
    user.SetForegroundWindow(handle)
    return True


class SingleInstance:
    def __init__(self, directory):
        self.handle = None
        self.window_handle = None
        self.window_property = _instance_name(directory)
        if os.name != "nt":
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        self.handle = kernel.CreateMutexW(None, False, "Local\\" + self.window_property)
        if not self.handle:
            raise OSError("无法创建程序实例锁。")
        self.already_running = ctypes.get_last_error() == 183
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NXG.ActivityCheck.Desktop.v1")

    def attach_window(self, window):
        if os.name != "nt":
            return
        window.update_idletasks()
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
        user.SetPropW.restype = wintypes.BOOL
        user.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user.RemovePropW.restype = wintypes.HANDLE
        handle = int(window.wm_frame(), 0)
        if not user.SetPropW(handle, self.window_property, 1):
            raise OSError("无法注册助手窗口。")
        self.window_handle = handle

        def destroyed(event):
            if event.widget is window and self.window_handle:
                user.RemovePropW(self.window_handle, self.window_property)
                self.window_handle = None

        window.bind("<Destroy>", destroyed, add="+")

    def close(self):
        if self.handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self.handle)
            self.handle = None
