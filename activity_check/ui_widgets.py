# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""Virtual desktop activity table with a small Treeview-compatible data interface."""

from bisect import bisect_right
import tkinter as tk
from tkinter import font as tkfont, ttk

from .ui_theme import SURFACE, INK, MUTED, BORDER, SOFT, RED, RED_SOFT, BLUE, FONT, status_colors


class ActivityTable(ttk.Frame):
    """Paint only visible rows. Native scrollbars and keyboard selection stay available."""

    KEYS = ("pick", "title", "state", "signup", "time", "address", "capacity", "actions")

    def __init__(self, parent, *, scale, on_detail, on_cancel, on_recheck):
        super().__init__(parent, style="Inside.TFrame")
        self.scale = scale
        self.px = lambda value: round(value * scale)
        self.row_height = self.px(52)
        self.items, self.order, self.selected = {}, [], ()
        self.headings = dict(
            zip(
                self.KEYS,
                ("不报名", "活动名称", "状态", "报名窗口", "活动时间", "地点", "剩余 / 上限", "操作"),
                strict=True,
            )
        )
        self.on_detail, self.on_cancel, self.on_recheck = on_detail, on_cancel, on_recheck
        self.font = tkfont.Font(self, family=FONT, size=10)
        self.small = tkfont.Font(self, family=FONT, size=9)
        self.tiny = tkfont.Font(self, family=FONT, size=8)
        self.hovered = None
        self._redraw_id = self._tip_id = None
        self._tip = None
        self._tip_cell = None
        self._region = None
        self._last_view = None
        self.header = tk.Canvas(self, height=self.px(36), bg=SOFT, highlightthickness=0)
        self.canvas = tk.Canvas(
            self,
            bg=SURFACE,
            highlightthickness=1,
            highlightbackground=SURFACE,
            highlightcolor=BLUE,
            takefocus=True,
            borderwidth=0,
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.yview)
        self.canvas.configure(yscrollcommand=self._scroll_changed)
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.scrollbar.grid(row=1, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.bounds = [0] * 9
        self.canvas.bind("<Configure>", self._configure)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<Return>", lambda _e: self._action("detail"))
        self.canvas.bind("<Button-3>", self._menu)
        self.canvas.bind("<Shift-F10>", self._menu)
        self.canvas.bind("<FocusIn>", lambda _e: self._schedule())
        self.canvas.bind("<FocusOut>", lambda _e: self._schedule())
        for key, delta in (("Up", -1), ("Down", 1), ("Prior", -8), ("Next", 8), ("Home", -(10**8)), ("End", 10**8)):
            self.canvas.bind("<" + key + ">", lambda _e, step=delta: self._move(step))
        self.bind("<Destroy>", self._destroy, add="+")

    def bind(self, sequence=None, func=None, add=None):
        if hasattr(self, "canvas") and sequence != "<Destroy>":
            return self.canvas.bind(sequence, func, add="+")
        return super().bind(sequence, func, add)

    def heading(self, key, **options):
        if "text" in options:
            self.headings[key] = options["text"]
            self._schedule()
        return {"text": self.headings[key]}

    def get_children(self):
        return tuple(self.order)

    def exists(self, item):
        return item in self.items

    def item(self, item):
        return self.items[item]

    def insert(self, _parent, _index, *, iid, values, tags=()):
        self.items[iid] = {"values": values, "tags": tags}
        self.order.append(iid)
        self._schedule()
        return iid

    def delete(self, *items):
        for item in items:
            self.items.pop(item, None)
        remove = set(items)
        self.order = [item for item in self.order if item not in remove]
        self.selected = tuple(item for item in self.selected if item not in remove)
        self._hide_tip()
        self._schedule()

    def selection(self):
        return self.selected

    def selection_set(self, value):
        selected = (value,) if isinstance(value, str) else tuple(value)
        selected = tuple(item for item in selected[:1] if item in self.items)
        changed = selected != self.selected
        self.selected = selected
        self._schedule()
        if changed:
            self.canvas.event_generate("<<TreeviewSelect>>", when="tail")

    def yview(self, *args):
        return self.canvas.yview(*args)

    def yview_moveto(self, fraction):
        self._set_region()
        self.canvas.yview_moveto(fraction)
        self._schedule()

    def _set_region(self):
        region = (0, 0, self.canvas.winfo_width(), max(self.canvas.winfo_height(), len(self.order) * self.row_height))
        if region != self._region:
            self._region = region
            self.canvas.configure(scrollregion=region)

    def see(self, item):
        if item not in self.items:
            return
        y = self.order.index(item) * self.row_height
        top, height = self.canvas.canvasy(0), self.canvas.winfo_height()
        total = max(1, len(self.order) * self.row_height)
        if y < top:
            self.yview_moveto(y / total)
        elif y + self.row_height > top + height:
            self.yview_moveto((y + self.row_height - height) / total)

    def identify_region(self, x, y):
        return "cell" if self.identify_row(y) and 0 <= x < self.canvas.winfo_width() else "nothing"

    def identify_column(self, x):
        return "#" + str(min(8, max(1, bisect_right(self.bounds, x))))

    def identify_row(self, y):
        index = int(self.canvas.canvasy(y) // self.row_height)
        return self.order[index] if 0 <= index < len(self.order) else ""

    def _configure(self, _event=None):
        width = max(1, self.canvas.winfo_width())
        fixed = [48, 0, 96, 116, 116, 0, 88, 106]
        available = width - self.px(sum(fixed))
        title, address = max(self.px(140), round(available * 0.62)), max(self.px(90), round(available * 0.38))
        widths = [self.px(value) for value in fixed]
        widths[1], widths[5] = title, address
        self.bounds = [0]
        for value in widths:
            self.bounds.append(self.bounds[-1] + value)
        self._schedule()

    def _scroll_changed(self, first, last):
        self.scrollbar.set(first, last)
        view = float(first), float(last)
        if view != self._last_view:
            self._last_view = view
            self._hide_tip()
            self._schedule()

    def _schedule(self):
        if self._redraw_id is None and self.winfo_exists():
            self._redraw_id = self._root().after_idle(self._draw)

    def _draw(self):
        self._redraw_id = None
        self.canvas.delete("paint")
        self.header.delete("all")
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width <= 1:
            return
        for i, key in enumerate(self.KEYS):
            x = (
                (self.bounds[i] + self.bounds[i + 1]) / 2
                if key in {"pick", "actions"}
                else self.bounds[i] + self.px(10)
            )
            self.header.create_text(
                x,
                self.px(18),
                text=self.headings[key],
                fill=MUTED,
                font=self.small,
                anchor="center" if key in {"pick", "actions"} else "w",
            )
        self.header.create_line(0, self.px(36) - 1, width + self.px(12), self.px(36) - 1, fill=BORDER)
        self._set_region()
        start = max(0, int(self.canvas.canvasy(0) // self.row_height))
        stop = min(len(self.order), start + height // self.row_height + 2)
        for index in range(start, stop):
            identity = self.order[index]
            row = self.items[identity]
            values, status = row["values"], row["tags"][0] if row["tags"] else "unknown"
            top, bottom = index * self.row_height, (index + 1) * self.row_height
            color = (
                RED_SOFT
                if identity in self.selected
                else SOFT
                if identity == self.hovered
                else "#F5FAF7"
                if status in {"registered", "success"}
                else SURFACE
            )
            self.canvas.create_rectangle(0, top, width, bottom, fill=color, outline="", tags="paint")
            self.canvas.create_line(0, bottom - 1, width, bottom - 1, fill=BORDER, tags="paint")
            cy = (top + bottom) / 2
            checked = values[0] == "☑"
            cx, size = (self.bounds[0] + self.bounds[1]) / 2, self.px(13)
            self._round(
                cx - size / 2,
                cy - size / 2,
                cx + size / 2,
                cy + size / 2,
                self.px(2),
                RED if checked else SURFACE,
                RED if checked else "#8C97A6",
            )
            if checked:
                self.canvas.create_line(
                    cx - size * 0.28,
                    cy,
                    cx - size * 0.05,
                    cy + size * 0.23,
                    cx + size * 0.30,
                    cy - size * 0.25,
                    fill="white",
                    width=self.px(1.5),
                    joinstyle="round",
                    tags="paint",
                )
            for i in (1, 3, 4, 5, 6):
                text = str(values[i])
                x, cell_width = self.bounds[i] + self.px(10), self.bounds[i + 1] - self.bounds[i] - self.px(18)
                if i in (3, 4) and "\n" in text:
                    first, rest = text.split("\n", 1)
                    self.canvas.create_text(
                        x,
                        cy - self.px(9),
                        text=self._fit(first, cell_width, self.small),
                        fill=INK,
                        font=self.small,
                        anchor="w",
                        tags="paint",
                    )
                    self.canvas.create_text(
                        x,
                        cy + self.px(10),
                        text=self._fit(rest, cell_width, self.tiny),
                        fill=MUTED,
                        font=self.tiny,
                        anchor="w",
                        tags="paint",
                    )
                else:
                    font = self.font if i == 1 else self.small
                    self.canvas.create_text(
                        x,
                        cy,
                        text=self._fit(text, cell_width, font),
                        fill=INK if i != 6 else MUTED,
                        font=font,
                        anchor="w",
                        tags="paint",
                    )
            foreground, background = status_colors(status)
            text = str(values[2])
            badge_width = min(self.bounds[3] - self.bounds[2] - self.px(13), self.small.measure(text) + self.px(14))
            bx = self.bounds[2] + self.px(6)
            self._round(bx, cy - self.px(12), bx + badge_width, cy + self.px(12), self.px(4), background)
            self.canvas.create_text(
                bx + badge_width / 2,
                cy,
                text=self._fit(text, badge_width - self.px(8), self.small),
                fill=foreground,
                font=self.small,
                tags="paint",
            )
            ax = self.bounds[7] + self.px(10)
            self.canvas.create_text(ax, cy, text="详情", fill=BLUE, font=self.small, anchor="w", tags="paint")
            action = (
                "取消报名"
                if status in {"registered", "success"}
                else "核对取消"
                if status == "cancel_uncertain"
                else "检查限制"
                if status in {"blocked", "rejected"}
                else ""
            )
            if action:
                self.canvas.create_text(
                    ax + self.px(36),
                    cy,
                    text=action,
                    fill=RED if "取消" in action else BLUE,
                    font=self.small,
                    anchor="w",
                    tags="paint",
                )

    def _round(self, x1, y1, x2, y2, radius, fill, outline=""):
        self.canvas.create_polygon(
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
            smooth=True,
            splinesteps=16,
            fill=fill,
            outline=outline,
            tags="paint",
        )

    @staticmethod
    def _fit(text, width, font):
        text = text.replace("\n", " ")
        if font.measure(text) <= width:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid] + "…") <= width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + "…"

    def _click(self, event):
        self._hide_tip()
        self.canvas.focus_set()
        identity = self.identify_row(event.y)
        if identity:
            self.selection_set(identity)
            if self.identify_column(event.x) == "#8":
                status = self.items[identity]["tags"][0]
                if event.x < self.bounds[7] + self.px(43):
                    self._action("detail")
                elif status in {"registered", "success", "cancel_uncertain"}:
                    self._action("cancel")
                elif status in {"blocked", "rejected"}:
                    self._action("recheck")
                return "break"

    def _action(self, action):
        if self.selected:
            {"detail": self.on_detail, "cancel": self.on_cancel, "recheck": self.on_recheck}[action]()
        return "break"

    def _menu(self, event):
        if event.type == tk.EventType.ButtonPress:
            identity = self.identify_row(event.y)
            if identity:
                self.selection_set(identity)
        if not self.selected:
            return "break"
        menu = tk.Menu(self, tearoff=False, font=(FONT, 10))
        menu.add_command(label="查看详情    Enter", command=lambda: self._action("detail"))
        status = self.items[self.selected[0]]["tags"][0]
        if status in {"registered", "success", "cancel_uncertain"}:
            menu.add_command(
                label="核对取消结果" if status == "cancel_uncertain" else "取消报名",
                command=lambda: self._action("cancel"),
            )
        if status in {"blocked", "rejected"}:
            menu.add_command(label="重新检查限制", command=lambda: self._action("recheck"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            menu.destroy()
        return "break"

    def _move(self, delta):
        if self.order:
            index = self.order.index(self.selected[0]) if self.selected else (-1 if delta > 0 else len(self.order))
            target = self.order[max(0, min(len(self.order) - 1, index + delta))]
            self.selection_set(target)
            self.see(target)
        return "break"

    def _wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
        return "break"

    def _motion(self, event):
        identity = self.identify_row(event.y)
        column = self.identify_column(event.x)
        if identity != self.hovered:
            self.hovered = identity
            self._schedule()
        self.canvas.configure(cursor="hand2" if identity and column in {"#1", "#8"} else "")
        key = (identity, column)
        if key != self._tip_cell:
            self._hide_tip()
            self._tip_cell = key
            if identity and column in {"#2", "#3", "#4", "#5", "#6", "#7"}:
                self._tip_id = self._root().after(
                    650, lambda: self._show_tip(identity, column, event.x_root, event.y_root)
                )

    def _show_tip(self, identity, column, x, y):
        self._tip_id = None
        if identity not in self.items:
            return
        text = str(self.items[identity]["values"][int(column[1:]) - 1])
        self._tip = tk.Toplevel(self)
        self._tip.overrideredirect(True)
        tk.Label(
            self._tip,
            text=text,
            bg=INK,
            fg=SURFACE,
            font=(FONT, 9),
            padx=self.px(10),
            pady=self.px(6),
            wraplength=self.px(430),
            justify="left",
        ).pack()
        self._tip.update_idletasks()
        x = min(x + self.px(10), self.winfo_screenwidth() - self._tip.winfo_reqwidth() - self.px(8))
        y = min(y + self.px(22), self.winfo_screenheight() - self._tip.winfo_reqheight() - self.px(8))
        self._tip.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _hide_tip(self):
        if self._tip_id is not None:
            self._root().after_cancel(self._tip_id)
            self._tip_id = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
        self._tip_cell = None

    def _leave(self, _event):
        self._hide_tip()
        self.hovered = None
        self._schedule()

    def _destroy(self, event):
        if event.widget == self:
            self._hide_tip()
            if self._redraw_id is not None:
                self._root().after_cancel(self._redraw_id)
