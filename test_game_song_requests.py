from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from game_song_requests import (
    discard_game_song_request,
    load_pending_game_song_requests,
    resolve_active_archive_for_song_key,
)
from song_store import SongFile, StoreError


class InGameSongRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.requests = self.root / "LocalAppData" / "JonsRockSmithMods" / "InGameSongRequests"
        self.now = 1_800_000_000_000

    def test_loads_and_acknowledges_only_same_request_file(self) -> None:
        path = self._write_request("481-992-1", "SongOne", self.now)

        batch = load_pending_game_song_requests(self.requests, now_unix_ms=self.now)

        self.assertEqual(1, len(batch.requests))
        request = batch.requests[0]
        self.assertEqual("SongOne", request.song_key)
        self.assertEqual(path, request.path)
        discard_game_song_request(request)
        self.assertFalse(path.exists())

    def test_changed_request_cannot_be_acknowledged(self) -> None:
        path = self._write_request("481-992-1", "SongOne", self.now)
        request = load_pending_game_song_requests(self.requests, now_unix_ms=self.now).requests[0]
        path.write_text(json.dumps({
            "version": 1,
            "request_id": "481-992-1",
            "song_key": "OtherSong",
            "captured_at_unix_ms": self.now,
        }), encoding="utf-8")

        with self.assertRaisesRegex(StoreError, "changed before confirmation"):
            discard_game_song_request(request)
        self.assertTrue(path.exists())

    def test_old_request_survives_app_and_game_shutdown(self) -> None:
        path = self._write_request("481-992-1", "SongOne", self.now - 365 * 24 * 60 * 60 * 1000)

        batch = load_pending_game_song_requests(self.requests, now_unix_ms=self.now)

        self.assertEqual(0, batch.expired_count)
        self.assertEqual(1, len(batch.requests))
        self.assertTrue(path.exists())
        again = load_pending_game_song_requests(self.requests, now_unix_ms=self.now + 86_400_000)
        self.assertEqual(batch.requests, again.requests)

    def test_future_request_is_discarded(self) -> None:
        path = self._write_request("future", "SongOne", self.now + 120_000)
        batch = load_pending_game_song_requests(self.requests, now_unix_ms=self.now)
        self.assertEqual(1, batch.expired_count)
        self.assertEqual((), batch.requests)
        self.assertFalse(path.exists())

    def test_repeated_song_marks_keep_oldest_intent(self) -> None:
        first = self._write_request("first", "SongOne", self.now - 100)
        duplicate = self._write_request("second", "songone", self.now)
        other = self._write_request("third", "SongTwo", self.now)
        batch = load_pending_game_song_requests(self.requests, now_unix_ms=self.now)
        self.assertEqual(["SongOne", "SongTwo"], [item.song_key for item in batch.requests])
        self.assertTrue(first.exists())
        self.assertTrue(other.exists())
        self.assertFalse(duplicate.exists())

    def test_native_deterministic_request_id_allows_full_song_key(self) -> None:
        key = "Song.Key_" + "A" * 119
        self._write_request("song-" + key.lower(), key, self.now)
        batch = load_pending_game_song_requests(self.requests, now_unix_ms=self.now)
        self.assertEqual(key, batch.requests[0].song_key)

    def test_malformed_request_is_discarded(self) -> None:
        path = self.requests / "request-481-992-1.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"version":1,"song_key":"../escape"}', encoding="utf-8")

        batch = load_pending_game_song_requests(self.requests, now_unix_ms=self.now)

        self.assertEqual(1, batch.invalid_count)
        self.assertEqual((), batch.requests)
        self.assertFalse(path.exists())

    def test_resolves_song_key_to_whole_multisong_archive(self) -> None:
        pack = self._archive("artist/pack_p.psarc")
        metadata = {
            "song_count": 2,
            "songs": [
                {"song_key": "SongOne", "artist": "Artist", "title": "First"},
                {"song_key": "SongTwo", "artist": "Artist", "title": "Second"},
            ],
            "warnings": [],
        }

        resolved = resolve_active_archive_for_song_key([pack], "songtwo", lambda _path: metadata)

        self.assertEqual(pack, resolved)

    def test_refuses_song_key_duplicated_across_archives(self) -> None:
        first = self._archive("artist/first.psarc")
        second = self._archive("artist/second.psarc")
        metadata = {"song_count": 1, "songs": [{"song_key": "SongOne"}], "warnings": []}

        with self.assertRaisesRegex(StoreError, "multiple active archives"):
            resolve_active_archive_for_song_key(
                [first, second], "SongOne", lambda _path: metadata
            )

    def test_refuses_mapping_when_other_active_archive_metadata_is_unavailable(self) -> None:
        match = self._archive("artist/match.psarc")
        unknown = self._archive("artist/unreadable.psarc")
        metadata = {"song_count": 1, "songs": [{"song_key": "SongOne"}], "warnings": []}

        with self.assertRaisesRegex(StoreError, "lack current, complete metadata"):
            resolve_active_archive_for_song_key(
                [match, unknown], "SongOne", lambda path: metadata if path == match.path else None
            )

    def test_changed_active_archive_requires_full_refresh(self) -> None:
        row = self._archive("artist/changed.psarc")
        row.path.write_bytes(b"replacement with new identity")
        metadata = {"song_count": 1, "songs": [{"song_key": "SongOne"}], "warnings": []}

        with self.assertRaisesRegex(StoreError, "changed since the library scan"):
            resolve_active_archive_for_song_key([row], "SongOne", lambda _path: metadata)

    def _write_request(self, request_id: str, song_key: str, captured_at: int) -> Path:
        path = self.requests / f"request-{request_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1,
            "request_id": request_id,
            "song_key": song_key,
            "captured_at_unix_ms": captured_at,
        }), encoding="utf-8")
        return path

    def _archive(self, relative: str) -> SongFile:
        path = self.root / "game" / "dlc" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
        info = path.stat()
        identity = info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino
        return SongFile(relative, path, info.st_size, "Active", identity)


if __name__ == "__main__":
    unittest.main()
