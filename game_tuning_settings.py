"""Rocksmith note-detection reference and first tuner-skip settings."""

from __future__ import annotations

from pathlib import Path

from ini_settings import atomic_write_ini, ini_values, read_ini_text, replace_ini_key, validate_game_ini
from song_store import StoreError, game_is_running


PITCH_REFERENCE_A440 = "a440"
PITCH_REFERENCE_ROCKSMITH = "rocksmith"
PITCH_REFERENCE_KEY = "JonsPitchReference"
SKIP_FIRST_TUNER_KEY = "JonsSkipFirstPreSongTuner"
DEFAULT_PITCH_REFERENCE = PITCH_REFERENCE_A440
DEFAULT_SKIP_FIRST_TUNER = True


def read_game_tuning_settings(ini_path: Path) -> tuple[str, bool]:
    """Return (pitch reference, skip first pre-song tuner) from the Jons INI.

    Missing keys use the user-requested standard A440 reference and first-tuner
    skip. Existing Rocksmith installs remain unchanged until the patched native
    extension is installed and loaded.
    """

    _, text, _, _ = read_ini_text(ini_path)
    values = ini_values(text)
    pitch_reference = values.get(
        ("mod settings", PITCH_REFERENCE_KEY.casefold()), DEFAULT_PITCH_REFERENCE
    ).casefold()
    if pitch_reference not in (PITCH_REFERENCE_A440, PITCH_REFERENCE_ROCKSMITH):
        raise StoreError(f"Invalid pitch reference in {ini_path}: {pitch_reference!r}")

    skip_text = values.get(
        ("toggle switches", SKIP_FIRST_TUNER_KEY.casefold()),
        "on" if DEFAULT_SKIP_FIRST_TUNER else "off",
    ).casefold()
    if skip_text not in ("on", "off"):
        raise StoreError(f"Invalid first tuner skip setting in {ini_path}: {skip_text!r}")
    return pitch_reference, skip_text == "on"


def write_game_tuning_settings(
    ini_path: Path,
    pitch_reference: str,
    skip_first_tuner: bool,
    running_check=game_is_running,
) -> Path | None:
    """Atomically save both native tuning settings, retaining a verified backup."""

    if pitch_reference not in (PITCH_REFERENCE_A440, PITCH_REFERENCE_ROCKSMITH):
        raise StoreError("Choose A440 standard or Rocksmith-authored pitch detection")
    if type(skip_first_tuner) is not bool:
        raise StoreError("The first tuner skip setting must be enabled or disabled")
    ini_path = validate_game_ini(ini_path, "JonsRockSmithMods.ini")
    if running_check():
        raise StoreError("Close Rocksmith 2014 before changing note-detection or tuner-skip settings")

    original, text, encoding, newline = read_ini_text(ini_path)
    text = replace_ini_key(text, "Mod Settings", PITCH_REFERENCE_KEY, pitch_reference, newline)
    text = replace_ini_key(
        text,
        "Toggle Switches",
        SKIP_FIRST_TUNER_KEY,
        "on" if skip_first_tuner else "off",
        newline,
    )
    return atomic_write_ini(ini_path, original, text, encoding)
