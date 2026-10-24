"""Global and per-SongKey speed settings for the optional native RSMods extension."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from ini_settings import atomic_write_ini, ini_values, read_ini_text, replace_ini_key, validate_game_ini
from song_store import StoreError, game_is_running


ENABLED_KEY = "JonsFixedSongSpeedEnabled"
PERCENT_KEY = "JonsFixedSongSpeedPercent"
SONG_SPEED_SECTION = "Song Speed Overrides"
DEFAULT_SPEED_PERCENT = 85
MAX_SONG_OVERRIDES = 4096
SONG_KEY_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


def _validate_percent(percent: int) -> None:
    if type(percent) is not int or not 25 <= percent <= 400:
        raise StoreError("Choose a speed from 25% to 400%")


def validate_song_key(song_key: str) -> str:
    if not isinstance(song_key, str) or not SONG_KEY_PATTERN.fullmatch(song_key):
        raise StoreError("Choose a metadata-identified song with a valid SongKey before saving song speed")
    return song_key


def _parse_percent_text(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,3}", value):
        raise StoreError("A stored song speed must be an integer from 25% to 400%")
    percent = int(value)
    _validate_percent(percent)
    return percent


def read_speed_config(ini_path: Path) -> tuple[bool, int]:
    _, text, _, _ = read_ini_text(ini_path)
    values = ini_values(text)
    enabled = values.get(("toggle switches", ENABLED_KEY.casefold()), "off").casefold() == "on"
    try:
        percent = _parse_percent_text(values.get(("mod settings", PERCENT_KEY.casefold()), str(DEFAULT_SPEED_PERCENT)))
    except (ValueError, StoreError):
        percent = DEFAULT_SPEED_PERCENT
    return enabled, percent


def read_song_speed_config(ini_path: Path) -> dict[str, int]:
    _, text, _, _ = read_ini_text(ini_path)
    return _song_overrides_from_text(text)


def _song_overrides_from_text(text: str) -> dict[str, int]:
    values = {key: value for (section, key), value in ini_values(text).items()
              if section == SONG_SPEED_SECTION.casefold()}
    if len(values) > MAX_SONG_OVERRIDES:
        raise StoreError(f"Song speed settings exceed {MAX_SONG_OVERRIDES} songs")
    result = {}
    for song_key, value in values.items():
        if not SONG_KEY_PATTERN.fullmatch(song_key):
            continue
        try:
            percent = _parse_percent_text(value)
        except (ValueError, StoreError):
            continue
        result[song_key] = percent
    return result


def effective_speed_for_song(ini_path: Path, song_key: str) -> int | None:
    song_key = validate_song_key(song_key)
    overrides = read_song_speed_config(ini_path)
    if song_key.casefold() in overrides:
        return overrides[song_key.casefold()]
    enabled, percent = read_speed_config(ini_path)
    return percent if enabled else None


def write_speed_config(ini_path: Path, enabled: bool, percent: int,
                       running_check=game_is_running) -> Path | None:
    _validate_percent(percent)
    if running_check():
        raise StoreError("Close Rocksmith 2014 before changing global speed settings")
    ini_path = validate_game_ini(ini_path, "JonsRockSmithMods.ini")
    original, text, encoding, newline = read_ini_text(ini_path)
    text = replace_ini_key(text, "Toggle Switches", ENABLED_KEY, "on" if enabled else "off", newline)
    text = replace_ini_key(text, "Mod Settings", PERCENT_KEY, str(percent), newline)
    return atomic_write_ini(ini_path, original, text, encoding)


def write_song_speed_config(ini_path: Path, song_key: str, percent: int | None,
                            running_check=game_is_running) -> Path | None:
    """A song override can change while the native DLL polls its separate INI file."""
    return write_song_speed_configs(ini_path, {song_key: percent}, running_check)


def write_song_speed_configs(ini_path: Path, overrides: Mapping[str, int | None],
                             running_check=game_is_running) -> Path | None:
    """Validate the entire selection, then commit all overrides with one atomic replacement."""
    if not isinstance(overrides, Mapping) or not overrides:
        raise StoreError("Choose at least one metadata-identified song before saving song speed")
    normalized = {}
    for song_key, percent in overrides.items():
        song_key = validate_song_key(song_key)
        if percent is not None:
            _validate_percent(percent)
        if song_key.casefold() in normalized:
            raise StoreError(f"The selected songs contain a duplicate SongKey: {song_key}")
        normalized[song_key.casefold()] = (song_key, percent)
    ini_path = validate_game_ini(ini_path, "JonsRockSmithMods.ini")
    original, text, encoding, newline = read_ini_text(ini_path)
    _song_overrides_from_text(text)
    existing = {key for (section, key) in ini_values(text)
                if section == SONG_SPEED_SECTION.casefold()}
    for canonical_key, (_, percent) in normalized.items():
        if percent is None:
            existing.discard(canonical_key)
        else:
            existing.add(canonical_key)
    if len(existing) > MAX_SONG_OVERRIDES:
        raise StoreError(f"Remove overrides before adding more than {MAX_SONG_OVERRIDES} songs")
    for song_key, percent in normalized.values():
        text = replace_ini_key(text, SONG_SPEED_SECTION, song_key, None if percent is None else str(percent), newline)
    return atomic_write_ini(ini_path, original, text, encoding)
