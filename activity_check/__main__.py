# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import argparse
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
import traceback

from .identity import SingleInstance, activate_existing_window, enable_dpi_awareness
from .notifications import NotificationWorker
from .wechat_web import WebWechatSender
from .store import Store
from .ui import App
from .vault import Vault
from .worker import Worker


def main():
    enable_dpi_awareness()
    parser = argparse.ArgumentParser(description="学校活动报名桌面助手")
    parser.add_argument("--data-dir", type=Path, help="独立本机数据目录")
    args = parser.parse_args()
    directory = (
        args.data_dir
        or Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "NXGActivityCheck"
    )
    directory.mkdir(parents=True, exist_ok=True)
    lock = SingleInstance(directory)
    if getattr(lock, "already_running", False):
        if activate_existing_window(directory):
            lock.close()
            return
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("活动报名助手", "助手已经运行，请从任务栏打开现有窗口。")
        root.destroy()
        lock.close()
        return
    worker = notifier = None
    try:
        store = Store(directory / "activities.db")
        store.recover_interrupted()
        vault = Vault(directory / "sessions")
        worker = Worker(store, vault)
        notifier = NotificationWorker(store, WebWechatSender(directory), events=worker.events)
        worker.start()
        if notifier:
            notifier.start()
        root = tk.Tk()
        App(root, store, vault, worker, notifier=notifier)
        lock.attach_window(root)
        root.mainloop()
    except Exception as error:
        # Record locations and exception type, without exception values or credentials.
        frames = traceback.extract_tb(error.__traceback__)
        report = (
            type(error).__name__ + "\n" + "\n".join(f"{frame.filename}:{frame.lineno} {frame.name}" for frame in frames)
        )
        (directory / "startup-error.log").write_text(report, encoding="utf-8")
        try:
            messagebox.showerror("活动报名助手", "启动未完成，请查看本机数据目录中的 startup-error.log。")
        except tk.TclError:
            pass
    finally:
        if notifier:
            notifier.stop()
        if worker:
            for account in store.accounts():
                store.configure(account["id"], enabled=False)
            worker.stop()
            worker.join(timeout=30)
        if notifier:
            notifier.join(timeout=50)
        lock.close()


if __name__ == "__main__":
    main()
