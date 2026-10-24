import unittest

from track_selection import TrackCheckboxSelection


class TrackCheckboxSelectionTests(unittest.TestCase):
    def test_rows_toggle_independently_for_multi_song_edits(self):
        selection = TrackCheckboxSelection(3)
        self.assertTrue(selection.toggle(0))
        self.assertTrue(selection.toggle(2))
        self.assertEqual((0, 2), selection.selected_indices())
        self.assertFalse(selection.toggle(0))
        self.assertEqual((2,), selection.selected_indices())

    def test_select_all_and_clear(self):
        selection = TrackCheckboxSelection(4)
        selection.select_all()
        self.assertEqual((0, 1, 2, 3), selection.selected_indices())
        selection.clear()
        self.assertEqual((), selection.selected_indices())

    def test_invalid_count_and_index_are_rejected(self):
        for count in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                TrackCheckboxSelection(count)
        selection = TrackCheckboxSelection(1)
        with self.assertRaises(IndexError):
            selection.toggle(1)


if __name__ == "__main__":
    unittest.main()
