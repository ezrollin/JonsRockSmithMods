"""Scrollable settings pages and screen-bounded initial window geometry."""

import tkinter as tk
from tkinter import ttk


def initial_window_size(screen_width: int, screen_height: int) -> tuple[int, int]:
    return min(1180, max(480, screen_width - 80)), min(820, max(360, screen_height - 100))


class ScrollablePage(ttk.Frame):
    def __init__(self, owner, **kwargs):
        super().__init__(owner, **kwargs)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.canvas, padding=12)
        self._content_id = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.content.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._resized)
        # Misc._root() is Tk's widget lookup and callback exception hook.
        # Never replace that inherited method with a window reference.
        self._wheel_toplevel = self.winfo_toplevel()
        self._wheel_remainder = 0
        self._wheel_id = self._wheel_toplevel.bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")

    def _content_changed(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resized(self, event):
        self.canvas.itemconfigure(self._content_id, width=event.width)
        self._wrap_labels(self.content, max(180, event.width - 48))

    def _wrap_labels(self, widget, width):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Label) and child.cget("wraplength"):
                child.configure(wraplength=width)
            self._wrap_labels(child, width)

    def _wheel(self, event):
        widget_name, page_name = str(event.widget), str(self)
        if (widget_name != page_name and not widget_name.startswith(page_name + ".")) or not self.winfo_ismapped():
            return
        if isinstance(event.widget, (ttk.Combobox, ttk.Spinbox, ttk.Treeview, tk.Text)):
            return
        self._wheel_remainder += event.delta
        steps = int(self._wheel_remainder / 120)
        self._wheel_remainder -= steps * 120
        if steps:
            self.canvas.yview_scroll(-steps, "units")

    def _destroyed(self, event):
        if event.widget is self and self._wheel_id:
            try:
                self._wheel_toplevel.unbind("<MouseWheel>", self._wheel_id)
            except tk.TclError:
                pass
            self._wheel_id = None
