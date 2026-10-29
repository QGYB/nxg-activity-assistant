# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

import pytest

from tools.build_source_release import public_path


@pytest.mark.parametrize(
    "path",
    [
        "sessions/account.auth",
        "activity_check/private.db",
        "work/trace.json",
        "activity_check/assets/cookies.json",
        "tests/real-account.auth",
        ".env",
        "docs/../../sessions/secret.auth",
        "/tmp/private.py",
        "C:\\Users\\example\\secret.py",
        ".github/workflows/cache.db",
        "activity_check/__pycache__/worker.pyc",
    ],
)
def test_private_runtime_files_cannot_enter_source_archive(path):
    assert not public_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "LICENSE",
        "pyproject.toml",
        "activity_check/session.py",
        "tests/test_engine.py",
        "activity_check/assets/calendar-red-check.ico",
        "docs/desktop-example.png",
        ".github/workflows/windows.yml",
        "LICENSES/pillow-LICENSE",
    ],
)
def test_required_public_source_and_license_files_are_allowed(path):
    assert public_path(path)
