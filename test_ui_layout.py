"""Headless checks for screen bounds, wheel handling, and main view integration."""

import ast
import inspect
import textwrap
import unittest
import tkinter as tk
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from app import App
from ui_layout import ScrollablePage, initial_window_size
from library_health_view import LibraryHealthWindow


class Value:
    def __init__(self, *args, value="", **kwargs):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def trace_add(self, *args):
        return "trace"


class LayoutTests(unittest.TestCase):
    def test_widget_subclasses_never_replace_inherited_tk_methods_with_attributes(self):
        reserved = {name for name in dir(tk.Misc) if callable(getattr(tk.Misc, name, None))}
        for widget_class in (App, ScrollablePage, LibraryHealthWindow):
            tree = ast.parse(textwrap.dedent(inspect.getsource(widget_class)))
            collisions = [(node.attr, node.lineno) for node in ast.walk(tree)
                          if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                          and isinstance(node.value, ast.Name) and node.value.id == "self"
                          and node.attr in reserved]
            self.assertEqual([], collisions, widget_class.__name__)

    def test_real_tk_widget_lookup_and_exception_root_remain_callable(self):
        # Exercise Tk's real Python lookup path without loading Tk or opening a window.
        page = ScrollablePage.__new__(ScrollablePage)
        root = SimpleNamespace(master=None, children={})
        page.master = root
        page._w = ".page"
        child = SimpleNamespace(master=page)
        page.children = {"label": child}
        root.children["page"] = page
        page._wheel_toplevel = root
        self.assertIs(root, page._root())
        self.assertIs(child, page.nametowidget(".page.label"))
        self.assertIs(child, page.nametowidget("label"))
        root.report_callback_exception = mock.Mock()
        try:
            raise ValueError("event failure")
        except ValueError:
            page._report_exception()
        root.report_callback_exception.assert_called_once()
        self.assertIs(ValueError, root.report_callback_exception.call_args.args[0])

    def test_destroy_unbinds_from_wheel_owner_without_shadowing_root(self):
        page = ScrollablePage.__new__(ScrollablePage)
        page._wheel_toplevel = mock.Mock()
        page._wheel_id = "wheel-binding"
        page._destroyed(SimpleNamespace(widget=page))
        page._wheel_toplevel.unbind.assert_called_once_with("<MouseWheel>", "wheel-binding")
        self.assertIsNone(page._wheel_id)
        self.assertTrue(callable(page._root))

    def test_initial_window_fits_small_and_large_screens(self):
        for screen in ((640, 480), (800, 600), (1366, 768), (1920, 1080)):
            width, height = initial_window_size(*screen)
            self.assertLess(width, screen[0])
            self.assertLess(height, screen[1])
        self.assertEqual(initial_window_size(1920, 1080), (1180, 820))

    def test_high_resolution_wheel_events_accumulate_and_other_pages_stay_put(self):
        page = ScrollablePage.__new__(ScrollablePage)
        page._w = ".page"
        page._wheel_remainder = 0
        page.canvas = mock.Mock()
        page.winfo_ismapped = mock.Mock(return_value=True)
        for _ in range(4):
            page._wheel(SimpleNamespace(widget=".page.label", delta=30))
        page.canvas.yview_scroll.assert_called_once_with(-1, "units")
        page._wheel(SimpleNamespace(widget=".other.label", delta=120))
        page._wheel(SimpleNamespace(widget=".page2.label", delta=120))
        self.assertEqual(page.canvas.yview_scroll.call_count, 1)

    def test_main_view_build_and_first_empty_refresh_have_all_required_state(self):
        app = App.__new__(App)
        # Use model variables only; no Tk root, window, or device is created.
        tree = ast.parse(textwrap.dedent(inspect.getsource(App.__init__)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr.endswith("_var"):
                        setattr(app, target.attr, Value())
        app.tooltips = []
        app.protocol = mock.Mock()
        app._store_destination_label = mock.Mock(return_value="backup/songs")
        app._update_action_states = mock.Mock()
        with ExitStack() as patches:
            for name in ("Frame", "LabelFrame", "Notebook", "Label", "Entry", "Button",
                         "Checkbutton", "Spinbox", "Combobox", "Treeview", "Scrollbar", "Progressbar"):
                if name == "Notebook":
                    patches.enter_context(mock.patch("app.ttk.Notebook", side_effect=lambda *a, **k: mock.MagicMock()))
                else:
                    patches.enter_context(mock.patch(f"app.ttk.{name}"))
            patches.enter_context(mock.patch("app.ScrollablePage"))
            patches.enter_context(mock.patch("app.HoverTooltip"))
            patches.enter_context(mock.patch("app.Menu"))
            patches.enter_context(mock.patch("app.tk.StringVar", side_effect=Value))
            app._build()
        app.rows = []
        app.visible_rows = {}
        app.checked_paths = set()
        app.tree.selection.return_value = ()
        app.tree.focus.return_value = ""
        app.tree.yview.return_value = (0.0, 1.0)
        app._apply_tree_sort = mock.Mock()
        app._render()
        self.assertEqual(
            app.checked_count_var.get(),
            "0 archives checked for removal; 0 queued after close; 0 saved F10 mark(s)",
        )
        self.assertEqual(app.pages.add.call_count, 3)
