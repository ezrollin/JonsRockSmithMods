"""Regression tests for table ordering and row context selection."""

from __future__ import annotations

import unittest

from song_table import (
    SORTABLE_COLUMNS,
    context_actions,
    context_selection,
    next_sort_state,
    song_sort_key,
)


class SongTableTests(unittest.TestCase):
    def test_every_visible_column_has_a_sort_rule(self):
        self.assertEqual(
            ("move", "location", "artist", "song", "favorite", "name", "size"),
            SORTABLE_COLUMNS,
        )

    def test_header_clicks_start_ascending_then_toggle_direction(self):
        self.assertEqual(("artist", False), next_sort_state(None, False, "artist"))
        self.assertEqual(("artist", True), next_sort_state("artist", False, "artist"))
        self.assertEqual(("size", False), next_sort_state("artist", True, "size"))
        with self.assertRaises(ValueError):
            next_sort_state(None, False, "unknown")

    def test_mib_size_uses_numeric_value_and_text_is_casefolded(self):
        self.assertLess(song_sort_key("size", 2.0), song_sort_key("size", 10.0))
        self.assertEqual(
            song_sort_key("artist", "aUDioslave", stable_path="a.psarc"),
            song_sort_key("artist", "Audioslave", stable_path="a.psarc"),
        )

    def test_move_column_orders_unchecked_checked_then_reviewed(self):
        unchecked = song_sort_key("move", "☐", checked=False, location="Active")
        checked = song_sort_key("move", "☑", checked=True, location="Active")
        reviewed = song_sort_key("move", "", checked=False, location="Do Not Want")
        self.assertLess(unchecked, checked)
        self.assertLess(checked, reviewed)

    def test_right_click_keeps_multiselection_only_inside_selected_group(self):
        self.assertEqual(("a", "b"), context_selection("b", ("a", "b")))
        self.assertEqual(("c",), context_selection("c", ("a", "b")))

    def test_context_actions_match_selected_locations(self):
        self.assertEqual(("toggle_move", "move"), context_actions(("Active", "Active")))
        self.assertEqual(("restore",), context_actions(("Do Not Want",)))
        self.assertEqual((), context_actions(("Active", "Do Not Want")))
        self.assertEqual((), context_actions(()))


if __name__ == "__main__":
    unittest.main()
