"""Guarded RSMods startup automation for an explicitly chosen local game profile."""

from __future__ import annotations

from pathlib import Path

from ini_settings import atomic_write_ini, ini_values, read_ini_text, replace_ini_key, validate_game_ini
from song_store import StoreError, game_is_running

ENABLED_KEY = "ForceProfileLoad"
PROFILE_KEY = "ProfileToLoad"


def read_startup_config(ini_path: Path) -> tuple[bool, str]:
    _, text, _, _ = read_ini_text(ini_path)
    values = ini_values(text)
    return (values.get(("toggle switches", ENABLED_KEY.casefold()), "off").casefold() == "on",
            values.get(("toggle switches", PROFILE_KEY.casefold()), ""))


def write_startup_config(ini_path: Path, enabled: bool, profile_name: str,
                         running_check=game_is_running) -> Path | None:
    if not isinstance(profile_name, str):
        raise StoreError("Choose the exact local Rocksmith profile name")
    profile_name = profile_name.strip()
    if (enabled and not profile_name) or len(profile_name) > 128:
        raise StoreError("Choose the exact local Rocksmith profile name before enabling startup automation")
    if any(ord(character) < 32 or ord(character) == 127 for character in profile_name):
        raise StoreError("The profile name contains unsupported INI characters")
    if running_check():
        raise StoreError("Close Rocksmith 2014 before changing startup settings")
    ini_path = validate_game_ini(ini_path, "RSMods.ini")
    original, text, encoding, newline = read_ini_text(ini_path)
    text = replace_ini_key(text, "Toggle Switches", ENABLED_KEY, "on" if enabled else "off", newline)
    text = replace_ini_key(text, "Toggle Switches", PROFILE_KEY, profile_name, newline)
    return atomic_write_ini(ini_path, original, text, encoding)
