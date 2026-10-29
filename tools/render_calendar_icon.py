# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""Render the original calendar icon with only its check mark recolored red."""

from pathlib import Path
import struct

from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parents[1] / "activity_check" / "assets"
NAME = "calendar-red-check"


def main():
    svg = (ASSETS / (NAME + ".svg")).read_text(encoding="utf-8")
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    payloads = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(device_scale_factor=1)
        for size in (*sizes, 512):
            page.set_viewport_size({"width": size, "height": size})
            page.set_content(
                "<style>html,body{margin:0;background:transparent;overflow:hidden}"
                "svg{display:block;width:100vw;height:100vh}</style>" + svg
            )
            payload = page.screenshot(omit_background=True)
            if size in sizes:
                payloads.append((size, payload))
            if size in (32, 64, 256, 512):
                (ASSETS / f"{NAME}-{size}.png").write_bytes(payload)
        browser.close()
    offset = 6 + 16 * len(payloads)
    entries = []
    for size, payload in payloads:
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    (ASSETS / (NAME + ".ico")).write_bytes(
        struct.pack("<HHH", 0, 1, len(payloads)) + b"".join(entries) + b"".join(payload for _, payload in payloads)
    )


if __name__ == "__main__":
    main()
