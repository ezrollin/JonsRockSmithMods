"""Read-only presentation helpers for songs stored inside PSARC archives."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ArchiveTrack:
    artist: str
    title: str
    song_key: str
    favorite_status: str


def metadata_song_records(metadata) -> tuple[dict, ...]:
    """Return only manifest song records with a usable mapping shape."""
    if not isinstance(metadata, dict):
        return ()
    songs = metadata.get("songs")
    if not isinstance(songs, list):
        return ()
    return tuple(song for song in songs if isinstance(song, dict))


def archive_table_values(metadata) -> tuple[str, str]:
    """Keep a pack row compact while retaining a useful first-song label."""
    songs = metadata_song_records(metadata)
    if not songs:
        return ("Unknown", "Unknown title" if metadata is not None else "Unknown")

    artists = []
    titles = []
    for song in songs:
        artist = " ".join(str(song.get("artist") or "").split())
        title = " ".join(str(song.get("title") or "").split())
        if artist and artist.casefold() not in {value.casefold() for value in artists}:
            artists.append(artist)
        if title and title.casefold() not in {value.casefold() for value in titles}:
            titles.append(title)

    count = len(songs)
    if count == 1:
        return (artists[0] if artists else "Unknown", titles[0] if titles else "Unknown title")

    artist_label = (
        artists[0] if len(artists) == 1 else f"Multiple artists ({len(artists)})"
    )
    if len(artists) == 1:
        artist_label = f"{artist_label} ({count} tracks)"
    title_label = titles[0] if titles else "Untitled archive"
    title_label = f"{title_label} (+{count - 1} more tracks)"
    return artist_label, title_label


def archive_search_text(metadata) -> str:
    """Search every listed record even when the main table shows a compact label."""
    fields = []
    for song in metadata_song_records(metadata):
        for key in ("artist", "title", "song_key", "dlc_key"):
            value = song.get(key)
            if value:
                fields.append(str(value))
    return " ".join(fields)


def archive_track_rows(metadata, favorite_keys: Iterable[str] | None) -> tuple[ArchiveTrack, ...]:
    """Build detail rows; None favorite_keys means unavailable or stale profile evidence."""
    keys = {str(key).casefold() for key in favorite_keys} if favorite_keys is not None else None
    rows = []
    for song in metadata_song_records(metadata):
        song_key = str(song.get("song_key") or "").strip()
        if keys is None or not song_key:
            favorite = "Unknown"
        else:
            favorite = "Favorite" if song_key.casefold() in keys else "Not favorite"
        rows.append(ArchiveTrack(
            artist=" ".join(str(song.get("artist") or "Unknown").split()) or "Unknown",
            title=" ".join(str(song.get("title") or "Unknown title").split()) or "Unknown title",
            song_key=song_key or "Unknown",
            favorite_status=favorite,
        ))
    return tuple(rows)


def duplicate_song_keys(metadata) -> tuple[tuple[str, int], ...]:
    """Return repeated profile SongKeys that make distinct records share favorite state."""
    counts = Counter(
        str(song.get("song_key") or "").strip().casefold()
        for song in metadata_song_records(metadata)
        if str(song.get("song_key") or "").strip()
    )
    display = {}
    for song in metadata_song_records(metadata):
        key = str(song.get("song_key") or "").strip()
        if key:
            display.setdefault(key.casefold(), key)
    return tuple(
        (display[key], count)
        for key, count in counts.items()
        if count > 1
    )


def format_archive_move_line(archive_path: str | Path, size_bytes: int, metadata) -> str:
    """Describe one full-file move without suggesting individual tracks can be split."""
    songs = metadata_song_records(metadata)
    size_mib = max(0, int(size_bytes)) / 1048576
    if not songs:
        return f"{archive_path} | {size_mib:.1f} MiB | track list unknown | entire PSARC moves"
    examples = []
    for song in songs[:2]:
        artist = " ".join(str(song.get("artist") or "Unknown").split()) or "Unknown"
        title = " ".join(str(song.get("title") or "Unknown title").split()) or "Unknown title"
        examples.append(f"{artist} - {title}")
    sample_text = "; examples: " + "; ".join(examples) if examples else ""
    return (
        f"{archive_path} | {size_mib:.1f} MiB | {len(songs)} metadata-listed song records"
        f"{sample_text} | entire PSARC moves"
    )
