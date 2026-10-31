# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""Light desktop theme. Native ttk controls retain keyboard and focus behavior."""

from tkinter import font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

BG = "#F4F5F7"
SURFACE = "#FFFFFF"
INK = "#202631"
MUTED = "#626D7D"
RED = "#B42332"
BORDER = "#E2E6EC"
SOFT = "#F7F8FA"
RED_SOFT = "#FFF0F1"
GREEN = "#186345"
GREEN_SOFT = "#ECF7F0"
AMBER = "#82500C"
AMBER_SOFT = "#FFF4DF"
BLUE = "#315A8D"
FONT = "Microsoft YaHei UI"


def setup_style(root):
    scale = root.winfo_fpixels("1i") / 96
    if getattr(root, "_theme_scale", None) == scale:
        return

    def px(value):
        return round(value * scale)

    style = ttk.Style(root)
    style.theme_use("clam")
    tkfont.nametofont("TkDefaultFont", root=root).configure(family=FONT, size=10)
    tkfont.nametofont("TkTextFont", root=root).configure(family=FONT, size=10)
    style.configure(".", font=(FONT, 10), foreground=INK, background=BG)
    style.configure("TFrame", background=BG)
    style.configure("Inside.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG)
    style.configure("Muted.TLabel", foreground=MUTED, font=(FONT, 9))
    style.configure("Title.TLabel", font=(FONT, 20, "bold"))
    style.configure("Section.TLabel", font=(FONT, 15, "bold"))
    style.configure("Card.TLabel", background=SURFACE)
    style.configure("CardMuted.TLabel", foreground=MUTED, background=SURFACE, font=(FONT, 9))
    style.configure("CardTitle.TLabel", background=SURFACE, font=(FONT, 11, "bold"))
    style.configure("Success.TLabel", foreground=GREEN, font=(FONT, 9))
    style.configure("Status.TLabel", background=SURFACE, foreground=GREEN, font=(FONT, 10, "bold"))
    style.configure("Badge.TLabel", background=GREEN_SOFT, foreground=GREEN, font=(FONT, 9), padding=(px(8), px(4)))
    style.configure("TSeparator", background=BORDER)

    # Nine-slice images give native buttons rounded borders at the window's DPI.
    # Only small geometric assets are generated; the application icon is unchanged.
    images = []

    def patch(fill, border, radius=6, size=24):
        side, factor = px(size), 3
        picture = Image.new("RGBA", (side * factor, side * factor))
        draw = ImageDraw.Draw(picture)
        draw.rounded_rectangle(
            (factor, factor, side * factor - factor - 1, side * factor - factor - 1),
            radius=px(radius) * factor,
            fill=fill,
            outline=border,
            width=factor,
        )
        photo = ImageTk.PhotoImage(picture.resize((side, side), Image.Resampling.LANCZOS), master=root)
        images.append(photo)
        return photo

    for name, fill, line, active, pressed, foreground in (
        ("TButton", SURFACE, BORDER, "#F0F2F5", "#E7EAF0", INK),
        ("Primary.TButton", RED, RED, "#9E202E", "#861B27", "white"),
        ("Selected.TButton", RED_SOFT, RED_SOFT, "#FBE2E5", "#F4CDD3", RED),
        ("Quiet.TButton", BG, BG, "#E9ECF1", "#E1E6EE", MUTED),
        ("Inline.TButton", SURFACE, SURFACE, SOFT, "#E9ECF1", MUTED),
    ):
        element = name.replace(".", "_") + ".rounded"
        normal = patch(fill, line)
        hover = patch(active, line if name == "TButton" else active)
        down = patch(pressed, pressed)
        disabled = patch("#EBEDF0", BORDER)
        style.element_create(
            element,
            "image",
            normal,
            ("disabled", disabled),
            ("pressed", down),
            ("active", hover),
            border=px(8),
            padding=0,
            sticky="nsew",
        )
        style.layout(
            name,
            [
                (
                    element,
                    {
                        "sticky": "nsew",
                        "children": [
                            (
                                "Button.focus",
                                {
                                    "sticky": "nsew",
                                    "children": [
                                        (
                                            "Button.padding",
                                            {
                                                "sticky": "nsew",
                                                "children": [
                                                    ("Button.label", {"sticky": "nsew"}),
                                                ],
                                            },
                                        ),
                                    ],
                                },
                            ),
                        ],
                    },
                )
            ],
        )
        style.configure(
            name,
            padding=(px(12), px(7)),
            foreground=foreground,
            background=BG,
            focuscolor=BLUE,
            focusthickness=1,
            anchor="center",
            width=0,
        )
        style.map(name, foreground=[("disabled", "#7A8493")])
    style.configure("Inline.TButton", padding=(px(7), px(3)), font=(FONT, 9))
    style.configure("Quiet.TButton", padding=(px(8), px(5)), font=(FONT, 9))
    style.configure("Primary.TButton", font=(FONT, 10, "bold"))

    # ttk tiles the center rather than scaling it. A 4px center required tens of
    # thousands of Windows GDI draws for a large panel, blocking the event loop.
    # Keep the corners, use a larger tile, and let content determine minimum size.
    card = patch(SURFACE, BORDER, radius=10, size=128)
    style.element_create("Card.rounded", "image", card, border=px(10), padding=0, sticky="nsew", width=0, height=0)
    style.layout("Card.TFrame", [("Card.rounded", {"sticky": "nsew"})])
    style.configure("Card.TFrame", background=BG)
    style.configure("TEntry", padding=(px(9), px(7)), fieldbackground=SURFACE, bordercolor=BORDER)
    style.configure("TCombobox", padding=(px(7), px(6)), fieldbackground=SURFACE, bordercolor=BORDER)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)], foreground=[("readonly", INK)])
    style.configure("TSpinbox", padding=(px(7), px(6)), fieldbackground=SURFACE, bordercolor=BORDER)
    style.configure("Vertical.TScrollbar", width=px(12), arrowsize=px(10), background="#D8DEE7", troughcolor=SOFT)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(px(16), px(8)))
    style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", RED)])
    style.configure("TCheckbutton", background=BG)
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.selectBackground", RED_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", INK)
    root.option_add("*Button.cursor", "hand2")
    root._theme_images = images  # Tk keeps image names; Python must keep their objects.
    root._theme_scale = scale


def status_colors(status):
    if status in {"registered", "success", "ready"}:
        return GREEN, GREEN_SOFT
    if status in {"pending", "waiting", "blocked"}:
        return AMBER, AMBER_SOFT
    if status in {"uncertain", "rejected", "cancel_uncertain", "cancel_rejected"}:
        return RED, RED_SOFT
    if status in {"submitting", "cancelling"}:
        return BLUE, "#EDF3FC"
    return MUTED, "#EEF0F3"


def label(parent, text, *, muted=False, card=False, **kwargs):
    return ttk.Label(
        parent,
        text=text,
        style="CardMuted.TLabel"
        if muted and card
        else "Card.TLabel"
        if card
        else "Muted.TLabel"
        if muted
        else "TLabel",
        **kwargs,
    )
