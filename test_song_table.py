"""Regression tests for table ordering and row context selection."""

from __future__ import annotations

import unittest

from song_table import (
    SORTABLE_COLUMNS,
    context_actions,
    context_selection,
    location_filter_matches,
    next_sort_state,
    removal_row_status,
    song_sort_key,
)


class SongTableTests(unittest.TestCase):
    def test_every_visible_column_has_a_sort_rule(self):
        self.assertEqual(
            ("move", "removal", "location", "artist", "song", "favorite", "name", "size"),
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

    def test_removal_status_explains_manual_checks_and_saved_work(self):
        self.assertEqual("", removal_row_status(checked=False, queued=False, f10_marked=False))
        self.assertEqual(
            "Queued after close; F10 mark saved; Checked for removal",
            removal_row_status(checked=True, queued=True, f10_marked=True),
        )

    def test_right_click_keeps_multiselection_only_inside_selected_group(self):
        self.assertEqual(("a", "b"), context_selection("b", ("a", "b")))
        self.assertEqual(("c",), context_selection("c", ("a", "b")))

    def test_context_actions_match_selected_locations(self):
        self.assertEqual(("toggle_move", "move"), context_actions(("Active", "Active")))
        self.assertEqual(("restore",), context_actions(("Do Not Want",)))
        self.assertEqual((), context_actions(("Active", "Do Not Want")))
        self.assertEqual((), context_actions(()))

    def test_location_filter_exposes_active_and_do_not_want_lists(self):
        self.assertTrue(location_filter_matches("Active", "All locations"))
        self.assertTrue(location_filter_matches("Do Not Want", "All locations"))
        self.assertTrue(location_filter_matches("Active", "Active DLC"))
        self.assertFalse(location_filter_matches("Do Not Want", "Active DLC"))
        self.assertTrue(location_filter_matches("Do Not Want", "Do Not Want folder"))
        self.assertFalse(location_filter_matches("Active", "Do Not Want folder"))
        with self.assertRaises(ValueError):
            location_filter_matches("Active", "unknown")


if __name__ == "__main__":
    unittest.main()
