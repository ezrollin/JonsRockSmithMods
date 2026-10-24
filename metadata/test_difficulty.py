"""Schema validation tests for on-demand SNG difficulty metadata."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from metadata.difficulty import _validate_difficulty_document


class DifficultyMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.archive = Path(self.folder.name) / "song.psarc"
        self.archive.write_bytes(b"synthetic archive placeholder")

    def _document(self) -> dict:
        return {
            "schema_version": 1,
            "archive_path": str(self.archive.resolve()),
            "manifest_count": 1,
            "arrangements": [
                {
                    "arrangement_id": "ArrangementId",
                    "arrangement_name": "Lead",
                    "phrase_max_difficulties": {"intro": 4, "verse": 8},
                }
            ],
            "warnings": [],
        }

    def test_accepts_verified_shape(self) -> None:
        _validate_difficulty_document(self._document(), self.archive.resolve())

    def test_rejects_wrong_archive_or_schema(self) -> None:
        document = self._document()
        document["archive_path"] += ".other"
        with self.assertRaisesRegex(ValueError, "does not match"):
            _validate_difficulty_document(document, self.archive.resolve())
        document = self._document()
        document["schema_version"] = 2
        with self.assertRaisesRegex(ValueError, "unsupported schema"):
            _validate_difficulty_document(document, self.archive.resolve())

    def test_rejects_duplicate_ids_and_invalid_phrase_maxima(self) -> None:
        document = self._document()
        document["arrangements"].append(dict(document["arrangements"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate arrangement ids"):
            _validate_difficulty_document(document, self.archive.resolve())
        document = self._document()
        document["arrangements"][0]["phrase_max_difficulties"]["intro"] = True
        with self.assertRaisesRegex(ValueError, "invalid phrase maximum"):
            _validate_difficulty_document(document, self.archive.resolve())

    def test_warned_partial_output_remains_visible_to_caller(self) -> None:
        document = self._document()
        document["warnings"] = ["SNG was missing"]
        _validate_difficulty_document(document, self.archive.resolve())
        self.assertTrue(document["warnings"])


if __name__ == "__main__":
    unittest.main()
