# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import os
import tkinter as tk

import pytest

from activity_check import identity


@pytest.mark.skipif(os.name != "nt", reason="Windows single-instance window activation")
def test_duplicate_launch_restores_only_its_own_data_directory(tmp_path):
    identity.enable_dpi_awareness()
    root = tk.Tk()
    lock = identity.SingleInstance(tmp_path / "one")
    try:
        root.title("活动报名助手")
        lock.attach_window(root)
        root.update()
        root.iconify()
        root.update()
        assert not identity.activate_existing_window(tmp_path / "other")
        assert root.state() == "iconic"
        duplicate = identity.SingleInstance(tmp_path / "one")
        try:
            assert duplicate.already_running
            assert identity.activate_existing_window(tmp_path / "one")
            root.update()
            assert root.state() == "normal"
        finally:
            duplicate.close()
    finally:
        root.destroy()
        lock.close()
    assert not identity.activate_existing_window(tmp_path / "one")
