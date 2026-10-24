"""Rocksmith 2014 EVAS profile reader and guarded settings writer.

Format follows RSMods GUI/Profiles.cs: 20-byte EVAS header, AES-256-ECB,
then zlib-compressed UTF-8 JSON. Scans are read-only. Explicit favorite-list
and difficulty changes use verified backups, atomic writes, and read-back checks.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import shutil
import tempfile
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


_SAVE_KEY = bytes.fromhex(
    "728B369E24ED0134768511021812AFC0"
    "A3C25D02065F166B4BCC58CD2644F29E"
)
_MAX_PROFILE_BYTES = 256 * 1024 * 1024
PROFILE_BACKUP_DIRECTORY = Path.home() / "Documents" / "JonsRockSmithMods" / "Backups" / "Profiles"
_PROFILE_BACKUPS = PROFILE_BACKUP_DIRECTORY


@dataclass(frozen=True)
class FavoriteResult:
    """Unavailable means every archive classification must remain Unknown."""

    available: bool
    favorite_song_keys: frozenset[str]
    diagnostics: str
    profile_path: Path


@dataclass(frozen=True)
class DifficultySettings:
    """Profile-level DD settings; sightreading -1 means the game's Auto mode."""

    automatic_leveling_enabled: bool
    sightreading_level: float
    override_to_max: bool

    @property
    def sightreading_percent(self) -> int | None:
        if self.sightreading_level < 0:
            return None
        return round(self.sightreading_level * 10)


@dataclass(frozen=True)
class FavoriteUpdateResult:
    """Exact selected-profile favorite change and its recoverable backup."""

    profile_path: Path
    favorited: bool
    changed_song_keys: tuple[str, ...]
    backup_path: Path | None


@dataclass(frozen=True)
class ArrangementDifficultyStatus:
    """Verified phrase-level difficulty values for one existing arrangement."""

    arrangement_id: str
    phrase_difficulties: dict[str, float]
    phrase_max_difficulties: dict[str, int]

    @property
    def mixed(self) -> bool:
        return len({round(value, 6) for value in self.phrase_difficulties.values()}) > 1

    @property
    def minimum_difficulty(self) -> float | None:
        return min(self.phrase_difficulties.values(), default=None)

    @property
    def maximum_difficulty(self) -> float | None:
        return max(self.phrase_difficulties.values(), default=None)


@dataclass(frozen=True)
class SongDifficultyReport:
    """Read-only profile difficulty snapshot for selected arrangements."""

    profile_path: Path
    profile_sha256: str
    automatic_leveling_enabled: bool
    sightreading_level: float
    arrangements: tuple[ArrangementDifficultyStatus, ...]


@dataclass(frozen=True)
class SongDifficultyUpdateResult:
    """One atomic update of selected arrangement phrase levels and profile options."""

    profile_path: Path
    level: int
    automatic_leveling_enabled: bool
    changed_arrangement_ids: tuple[str, ...]
    changed_phrase_count: int
    backup_path: Path | None
    profile_sha256: str
    unavailable_arrangement_ids: tuple[str, ...] = ()


def _decode_profile(raw: bytes) -> dict[str, Any]:
    if len(raw) < 36 or raw[:8] != b"EVAS\x01\x00\x00\x00":
        raise ValueError("Unsupported Rocksmith EVAS profile header")
    declared_size = int.from_bytes(raw[16:20], "little")
    if not 0 < declared_size <= _MAX_PROFILE_BYTES:
        raise ValueError("Invalid or oversized Rocksmith profile size")
    encrypted = raw[20:]
    if not encrypted or len(encrypted) % 16:
        raise ValueError("Invalid Rocksmith profile encrypted payload")

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(_SAVE_KEY), modes.ECB()).decryptor()
    compressed = decryptor.update(encrypted) + decryptor.finalize()
    decompressor = zlib.decompressobj()
    plain = decompressor.decompress(compressed, declared_size + 1)
    if decompressor.unconsumed_tail or len(plain) > declared_size:
        raise ValueError("Rocksmith profile exceeds declared size")
    plain += decompressor.flush()
    if len(plain) != declared_size or not decompressor.eof:
        raise ValueError("Rocksmith profile decompression size mismatch")
    # Rocksmith profiles commonly count one terminal NUL in the EVAS length.
    if plain.endswith(b"\x00"):
        plain = plain[:-1]
    if b"\x00" in plain:
        raise ValueError("Unexpected NUL inside Rocksmith profile JSON")
    document = json.loads(plain.decode("utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("Rocksmith profile root is not an object")
    return document


def _read_profile(path: str | Path) -> tuple[Path, bytes, dict[str, Any]]:
    profile_path = Path(path).resolve(strict=True)
    if not profile_path.name.casefold().endswith("_prfldb"):
        raise ValueError("Select a Rocksmith profile ending in _PRFLDB")
    if profile_path.stat().st_size > _MAX_PROFILE_BYTES + 1024:
        raise ValueError("Rocksmith profile is larger than the supported size limit")
    raw = profile_path.read_bytes()
    return profile_path, raw, _decode_profile(raw)


def read_override_to_max(profile_path: str | Path) -> bool:
    """Read Rocksmith's Override to Max difficulty setting from one profile."""

    _, _, profile = _read_profile(profile_path)
    options = profile.get("Options")
    if not isinstance(options, dict) or type(options.get("DDOverrideMax")) is not bool:
        raise ValueError("Rocksmith profile has no valid Options.DDOverrideMax setting")
    return options["DDOverrideMax"]


def read_difficulty_settings(profile_path: str | Path) -> DifficultySettings:
    """Read the selected profile's automatic, sightreading, and max settings."""

    _, _, profile = _read_profile(profile_path)
    options = profile.get("Options")
    if not isinstance(options, dict):
        raise ValueError("Rocksmith profile has no valid Options object")
    enabled = options.get("DDEnabled")
    override = options.get("DDOverrideMax")
    raw_level = options.get("DDSightreadLevel")
    if type(enabled) is not bool:
        raise ValueError("Rocksmith profile has no valid Options.DDEnabled setting")
    if type(override) is not bool:
        raise ValueError("Rocksmith profile has no valid Options.DDOverrideMax setting")
    if (isinstance(raw_level, bool) or not isinstance(raw_level, (int, float))
            or not math.isfinite(float(raw_level))):
        raise ValueError("Rocksmith profile has no valid Options.DDSightreadLevel setting")
    level = float(raw_level)
    if level != -1.0 and not 0.0 <= level <= 10.0:
        raise ValueError(f"Unsupported Rocksmith sightreading level: {level}")
    return DifficultySettings(enabled, level, override)


def read_sightreading_level(profile_path: str | Path) -> DifficultySettings:
    """Read the existing global Sight Reading and auto-level settings."""

    return read_difficulty_settings(profile_path)


def _encode_profile(document: dict[str, Any], header: bytes) -> bytes:
    """Write the EVAS envelope used by RSMods' Rocksmith profile editor."""

    if len(header) < 20 or header[:8] != b"EVAS\x01\x00\x00\x00":
        raise ValueError("Unsupported Rocksmith EVAS profile header")
    plain = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(plain) <= _MAX_PROFILE_BYTES:
        raise ValueError("Invalid or oversized Rocksmith profile JSON")
    compressed = zlib.compress(plain, level=9)
    # RSMods pads the compressed stream with zero bytes to a 512-byte boundary
    # before AES-ECB encryption. It deliberately adds a full block at alignment.
    padding = 512 - (len(compressed) % 512)
    compressed += b"\x00" * padding

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(_SAVE_KEY), modes.ECB()).encryptor()
    encrypted = encryptor.update(compressed) + encryptor.finalize()
    output_header = bytearray(header[:20])
    output_header[16:20] = len(plain).to_bytes(4, "little")
    return bytes(output_header) + encrypted


def _persist_profile_update(path: Path, original: bytes, profile: dict[str, Any],
                            backup_dir: str | Path, backup_label: str) -> Path:
    """Back up, atomically replace, and verify a modified profile document."""

    updated = _encode_profile(profile, original[:20])
    original_hash = hashlib.sha256(original).hexdigest()
    backups = Path(backup_dir)
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = backups / f"{path.stem}_before_{backup_label}_{stamp}.PRFLDB"
    shutil.copy2(path, backup)
    if hashlib.sha256(backup.read_bytes()).hexdigest() != original_hash:
        backup.unlink(missing_ok=True)
        raise OSError("Rocksmith profile backup verification failed")

    temporary: str | None = None
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != original_hash:
            raise OSError("Rocksmith profile changed during the update; no changes were made")
        fd, temporary = tempfile.mkstemp(
            prefix=".JonsRockSmithMods-profile-", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None

        written = _decode_profile(path.read_bytes())
        if written != profile:
            raise OSError("Updated Rocksmith profile failed verification")
    except Exception:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != original_hash:
            restore_fd, restore_temp = tempfile.mkstemp(
                prefix=".JonsRockSmithMods-profile-restore-", suffix=".tmp", dir=path.parent
            )
            try:
                with os.fdopen(restore_fd, "wb") as stream:
                    stream.write(backup.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(restore_temp, path)
            finally:
                Path(restore_temp).unlink(missing_ok=True)
        raise
    return backup


def set_override_to_max(profile_path: str | Path, enabled: bool,
                        backup_dir: str | Path = _PROFILE_BACKUPS,
                        running_check=None) -> Path | None:
    """Safely change only DDOverrideMax, with a verified backup and atomic replace.

    Returns the timestamped backup path, or None when no change was needed.
    Rocksmith must be closed before invoking this function.
    """

    if type(enabled) is not bool:
        raise ValueError("Override to Max must be a true or false value")
    if running_check is None:
        from song_store import game_is_running

        running_check = game_is_running
    if running_check():
        raise OSError("Close Rocksmith 2014 before changing profile difficulty settings")
    path, original, profile = _read_profile(profile_path)
    options = profile.get("Options")
    if not isinstance(options, dict) or type(options.get("DDOverrideMax")) is not bool:
        raise ValueError("Rocksmith profile has no valid Options.DDOverrideMax setting")
    if options["DDOverrideMax"] is enabled:
        return None

    options["DDOverrideMax"] = enabled
    return _persist_profile_update(path, original, profile, backup_dir, "override_to_max")


def set_difficulty_settings(profile_path: str | Path, starting_percent: int,
                            keep_saved_levels: bool,
                            backup_dir: str | Path = _PROFILE_BACKUPS,
                            running_check=None) -> Path | None:
    """Set the starting sightreading level and optionally disable automatic DD.

    The game stores its sightreading scale from 0 to 10, with -1 meaning Auto.
    Setting `keep_saved_levels` disables automatic leveling in both directions;
    it preserves each existing arrangement's stored phrase levels. New
    arrangements use the selected starting level. Override to Max is cleared
    because the game's UI says it takes precedence over Sightreading Level.
    """

    if type(starting_percent) is not int or not 0 <= starting_percent <= 100:
        raise ValueError("Starting difficulty must be a whole percentage from 0 to 100")
    if type(keep_saved_levels) is not bool:
        raise ValueError("Keep saved levels must be true or false")
    if running_check is None:
        from song_store import game_is_running

        running_check = game_is_running
    if running_check():
        raise OSError("Close Rocksmith 2014 before changing profile difficulty settings")

    path, original, profile = _read_profile(profile_path)
    options = profile.get("Options")
    if not isinstance(options, dict):
        raise ValueError("Rocksmith profile has no valid Options object")
    enabled = options.get("DDEnabled")
    override = options.get("DDOverrideMax")
    raw_level = options.get("DDSightreadLevel")
    if type(enabled) is not bool:
        raise ValueError("Rocksmith profile has no valid Options.DDEnabled setting")
    if type(override) is not bool:
        raise ValueError("Rocksmith profile has no valid Options.DDOverrideMax setting")
    if (isinstance(raw_level, bool) or not isinstance(raw_level, (int, float))
            or not math.isfinite(float(raw_level))):
        raise ValueError("Rocksmith profile has no valid Options.DDSightreadLevel setting")
    if float(raw_level) != -1.0 and not 0.0 <= float(raw_level) <= 10.0:
        raise ValueError(f"Unsupported Rocksmith sightreading level: {raw_level}")

    options["DDSightreadLevel"] = starting_percent / 10.0
    options["DDEnabled"] = not keep_saved_levels
    options["DDOverrideMax"] = False
    if (float(raw_level) == options["DDSightreadLevel"]
            and enabled is options["DDEnabled"] and override is False):
        return None
    return _persist_profile_update(path, original, profile, backup_dir, "difficulty_settings")


def _validate_expected_profile_sha256(value: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in value)):
        raise ValueError("Expected profile SHA-256 must contain exactly 64 hexadecimal characters")
    return value.casefold()


def _validated_arrangement_maxima(
    arrangements: Mapping[str, Mapping[str, int]],
) -> dict[str, tuple[str, dict[str, int]]]:
    """Validate Toolkit-derived arrangement ids and SNG phrase maxima."""

    if not isinstance(arrangements, Mapping):
        raise ValueError("Verified arrangement metadata must be supplied as an arrangement-id mapping")
    result: dict[str, tuple[str, dict[str, int]]] = {}
    for arrangement_id, phrase_maxima in arrangements.items():
        if (not isinstance(arrangement_id, str) or not arrangement_id
                or arrangement_id != arrangement_id.strip()):
            raise ValueError("Arrangement metadata contains an invalid persistent arrangement id")
        normalized_id = arrangement_id.casefold()
        if normalized_id in result:
            raise ValueError("Arrangement metadata contains duplicate persistent arrangement ids")
        if not isinstance(phrase_maxima, Mapping) or not phrase_maxima:
            raise ValueError(
                f"Arrangement {arrangement_id} has no verified SNG phrase maximum metadata; rescan its archive"
            )
        normalized_phrases: dict[str, int] = {}
        copied_maxima: dict[str, int] = {}
        for phrase_name, maximum in phrase_maxima.items():
            if (not isinstance(phrase_name, str) or not phrase_name
                    or phrase_name != phrase_name.strip()
                    or any(ord(character) < 32 for character in phrase_name)):
                raise ValueError(f"Arrangement {arrangement_id} has an invalid SNG phrase name")
            normalized_name = phrase_name.casefold()
            if normalized_name in normalized_phrases:
                raise ValueError(
                    f"Arrangement {arrangement_id} has duplicate SNG phrase names; difficulty is ambiguous"
                )
            if type(maximum) is not int or maximum < 0:
                raise ValueError(
                    f"Arrangement {arrangement_id} has an invalid maximum difficulty for phrase {phrase_name}"
                )
            normalized_phrases[normalized_name] = phrase_name
            copied_maxima[phrase_name] = maximum
        result[normalized_id] = (arrangement_id, copied_maxima)
    return result


def _profile_phrase_records(
    profile: dict[str, Any],
    arrangement_id: str,
    phrase_maxima: Mapping[str, int],
    *,
    allow_unplayed: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Return exact stored phrase records after name/range validation."""

    songs = profile.get("Songs")
    if not isinstance(songs, dict):
        raise ValueError("Rocksmith profile has no valid Songs arrangement map")
    matches = [
        value for key, value in songs.items()
        if isinstance(key, str) and key.casefold() == arrangement_id.casefold()
    ]
    if len(matches) > 1:
        raise ValueError(f"Arrangement {arrangement_id} has duplicate profile records; difficulty is ambiguous")
    record = matches[0] if matches else None
    if not isinstance(record, dict):
        if allow_unplayed:
            return None, None
        raise ValueError(
            f"Arrangement {arrangement_id} has no saved profile difficulty; play it once before setting its phrase level. "
            "The global Sight Reading level only sets the starting level for a new arrangement."
        )
    dynamic = record.get("DynamicDifficulty")
    phrases = dynamic.get("Phrases") if isinstance(dynamic, dict) else None
    if not isinstance(phrases, dict) or not phrases:
        if allow_unplayed:
            return None, None
        raise ValueError(
            f"Arrangement {arrangement_id} has no saved phrase difficulty records; play it once before setting its level. "
            "The global Sight Reading level only sets the starting level for a new arrangement."
        )

    maxima_by_name = {name.casefold(): (name, maximum) for name, maximum in phrase_maxima.items()}
    profile_by_name: dict[str, str] = {}
    for phrase_name, phrase_record in phrases.items():
        if not isinstance(phrase_name, str) or not phrase_name:
            raise ValueError(f"Arrangement {arrangement_id} has an invalid saved phrase name")
        normalized_name = phrase_name.casefold()
        if normalized_name in profile_by_name:
            raise ValueError(
                f"Arrangement {arrangement_id} has duplicate saved phrase names; difficulty is ambiguous"
            )
        if normalized_name not in maxima_by_name:
            raise ValueError(
                f"Arrangement {arrangement_id} contains saved phrase {phrase_name!r} that does not match its SNG; rescan the archive"
            )
        if not isinstance(phrase_record, dict):
            raise ValueError(f"Arrangement {arrangement_id} has an invalid record for phrase {phrase_name!r}")
        current = phrase_record.get("Diff")
        if (isinstance(current, bool) or not isinstance(current, (int, float))
                or not math.isfinite(float(current))):
            raise ValueError(f"Arrangement {arrangement_id} has no valid saved Diff value for phrase {phrase_name!r}")
        _, maximum = maxima_by_name[normalized_name]
        if not 0.0 <= float(current) <= maximum:
            raise ValueError(
                f"Arrangement {arrangement_id} phrase {phrase_name!r} has stored difficulty outside its SNG maximum; no changes were made"
            )
        profile_by_name[normalized_name] = phrase_name

    missing = [name for name in phrase_maxima if name.casefold() not in profile_by_name]
    if missing:
        shown = ", ".join(repr(name) for name in missing[:4])
        suffix = " and more" if len(missing) > 4 else ""
        raise ValueError(
            f"Arrangement {arrangement_id} is missing saved phrase record(s) {shown}{suffix}; "
            "play the full arrangement in Rocksmith, then refresh the profile and try again"
        )
    return phrases, profile_by_name


def _validated_profile_difficulty_options(profile: dict[str, Any]) -> dict[str, Any]:
    options = profile.get("Options")
    if not isinstance(options, dict):
        raise ValueError("Rocksmith profile has no valid Options object")
    enabled = options.get("DDEnabled")
    override = options.get("DDOverrideMax")
    raw_level = options.get("DDSightreadLevel")
    if type(enabled) is not bool:
        raise ValueError("Rocksmith profile has no valid Options.DDEnabled setting")
    if type(override) is not bool:
        raise ValueError("Rocksmith profile has no valid Options.DDOverrideMax setting")
    if (isinstance(raw_level, bool) or not isinstance(raw_level, (int, float))
            or not math.isfinite(float(raw_level))):
        raise ValueError("Rocksmith profile has no valid Options.DDSightreadLevel setting")
    if float(raw_level) != -1.0 and not 0.0 <= float(raw_level) <= 10.0:
        raise ValueError(f"Unsupported Rocksmith sightreading level: {raw_level}")
    return options


def _target_phrase_difficulty(level: int, maximum: int) -> int:
    """Use the same tenths-of-maximum scale as the global starting level."""

    if maximum == 0:
        return 0
    return math.floor((level * maximum / 10.0) + 0.5)


def _difficulty_summary(
    profile_path: Path,
    original: bytes,
    profile: dict[str, Any],
    arrangements: Sequence[ArrangementDifficultyStatus],
) -> SongDifficultyReport:
    options = _validated_profile_difficulty_options(profile)
    return SongDifficultyReport(
        profile_path=profile_path,
        profile_sha256=hashlib.sha256(original).hexdigest(),
        automatic_leveling_enabled=options["DDEnabled"],
        sightreading_level=float(options["DDSightreadLevel"]),
        arrangements=tuple(arrangements),
    )


def read_song_difficulty(
    profile_path: str | Path,
    arrangements: Mapping[str, Mapping[str, int]],
    arrangement_ids: Sequence[str] | None = None,
) -> SongDifficultyReport:
    """Read phrase levels for selected arrangements using verified SNG metadata.

    `arrangements` maps each exact PersistentID to `{phrase_name: maxDifficulty}`.
    Values come from Rocksmith SNG data; profile phrase names and their stored
    Diff values are checked before returning any report.
    """

    normalized_metadata = _validated_arrangement_maxima(arrangements)
    path, original, profile = _read_profile(profile_path)
    songs = profile.get("Songs")
    if not isinstance(songs, dict):
        raise ValueError("Rocksmith profile has no valid Songs arrangement map")
    if arrangement_ids is None:
        requested_ids = [key for key in songs if isinstance(key, str)]
        if len(requested_ids) != len(songs):
            raise ValueError("Rocksmith profile contains an invalid arrangement id")
    else:
        if (not isinstance(arrangement_ids, Sequence) or isinstance(arrangement_ids, (str, bytes))
                or not arrangement_ids):
            raise ValueError("Select one or more Rocksmith arrangements with saved difficulty")
        requested_ids = list(arrangement_ids)
    if any(not isinstance(value, str) or not value or value != value.strip() for value in requested_ids):
        raise ValueError("Selected arrangement ids must be non-empty strings without surrounding whitespace")
    normalized_requested = [value.casefold() for value in requested_ids]
    if len(normalized_requested) != len(set(normalized_requested)):
        raise ValueError("Selected arrangement ids contain duplicates")

    statuses: list[ArrangementDifficultyStatus] = []
    for arrangement_id in requested_ids:
        metadata = normalized_metadata.get(arrangement_id.casefold())
        if metadata is None:
            raise ValueError(
                f"Arrangement {arrangement_id} has no verified SNG phrase metadata; rescan its archive before reading difficulty"
            )
        metadata_id, maxima = metadata
        phrase_records, profile_names = _profile_phrase_records(profile, metadata_id, maxima)
        current: dict[str, float] = {}
        canonical_maxima: dict[str, int] = {}
        for normalized_name, profile_name in profile_names.items():
            sng_name, maximum = next(
                (name, maximum) for name, maximum in maxima.items()
                if name.casefold() == normalized_name
            )
            current[sng_name] = float(phrase_records[profile_name]["Diff"])
            canonical_maxima[sng_name] = maximum
        statuses.append(ArrangementDifficultyStatus(metadata_id, current, canonical_maxima))
    return _difficulty_summary(path, original, profile, statuses)


def set_song_difficulty(
    profile_path: str | Path,
    arrangements: Mapping[str, Mapping[str, int]],
    arrangement_ids: Sequence[str],
    level: int,
    *,
    expected_profile_sha256: str,
    automatic_leveling_enabled: bool | None = None,
    backup_dir: str | Path = _PROFILE_BACKUPS,
    running_check=None,
) -> SongDifficultyUpdateResult:
    """Set saved phrase levels for selected arrangements in one guarded write.

    Levels use the global starting level's tenths-of-maximum scale. Level 10 maps to its
    SNG `maxDifficulty`. Intermediate values are rounded to the nearest
    available phrase level. Automatic leveling remains as-is unless explicitly
    supplied. Rocksmith must be closed and the confirmed profile hash is required.
    """

    if type(level) is not int or not 1 <= level <= 10:
        raise ValueError("Song difficulty level must be a whole number from 1 to 10")
    if automatic_leveling_enabled is not None and type(automatic_leveling_enabled) is not bool:
        raise ValueError("Automatic leveling must be true, false, or omitted to preserve the current setting")
    expected_hash = _validate_expected_profile_sha256(expected_profile_sha256)
    metadata = _validated_arrangement_maxima(arrangements)
    if (not isinstance(arrangement_ids, Sequence) or isinstance(arrangement_ids, (str, bytes))
            or not arrangement_ids):
        raise ValueError("Select one or more verified Rocksmith arrangements")
    requested_ids = list(arrangement_ids)
    if any(not isinstance(value, str) or not value or value != value.strip() for value in requested_ids):
        raise ValueError("Selected arrangement ids must be non-empty strings without surrounding whitespace")
    requested_normalized = [value.casefold() for value in requested_ids]
    if len(requested_normalized) != len(set(requested_normalized)):
        raise ValueError("Selected arrangement ids contain duplicates")
    selected: list[tuple[str, dict[str, int]]] = []
    for arrangement_id in requested_ids:
        item = metadata.get(arrangement_id.casefold())
        if item is None:
            raise ValueError(
                f"Arrangement {arrangement_id} has no verified SNG phrase metadata; rescan its archive before changing difficulty"
            )
        selected.append(item)

    if running_check is None:
        from song_store import game_is_running

        running_check = game_is_running
    if running_check():
        raise OSError("Close Rocksmith 2014 before changing saved song difficulty")

    path, original, profile = _read_profile(profile_path)
    if hashlib.sha256(original).hexdigest() != expected_hash:
        raise OSError("Rocksmith profile changed after confirmation; no changes were made")
    options = _validated_profile_difficulty_options(profile)
    songs = profile.get("Songs")
    if not isinstance(songs, dict):
        raise ValueError("Rocksmith profile has no valid Songs arrangement map")

    updates: list[tuple[str, dict[str, Any], dict[str, int]]] = []
    unavailable_arrangements: list[str] = []
    for arrangement_id, maxima in selected:
        phrase_records, _ = _profile_phrase_records(
            profile, arrangement_id, maxima, allow_unplayed=True
        )
        if phrase_records is None:
            unavailable_arrangements.append(arrangement_id)
            continue
        updates.append((arrangement_id, phrase_records, maxima))
    if not updates:
        raise ValueError(
            "None of the selected arrangements has saved phrase difficulty yet; play the song once in Rocksmith, "
            "then refresh the profile. Its global Sight Reading level controls the starting level."
        )

    original_dd_enabled = options["DDEnabled"]
    original_override_to_max = options["DDOverrideMax"]
    changed_arrangements: list[str] = []
    changed_phrase_count = 0
    for arrangement_id, phrase_records, maxima in updates:
        maxima_by_name = {name.casefold(): maximum for name, maximum in maxima.items()}
        arrangement_changed = False
        for phrase_name, phrase_record in phrase_records.items():
            target = _target_phrase_difficulty(level, maxima_by_name[phrase_name.casefold()])
            current = phrase_record["Diff"]
            if float(current) == target:
                continue
            phrase_record["Diff"] = float(target) if isinstance(current, float) else target
            arrangement_changed = True
            changed_phrase_count += 1
        if arrangement_changed:
            changed_arrangements.append(arrangement_id)

    if automatic_leveling_enabled is not None:
        options["DDEnabled"] = automatic_leveling_enabled
    options["DDOverrideMax"] = False
    option_changed = (
        original_override_to_max is not False
        or (automatic_leveling_enabled is not None and original_dd_enabled is not automatic_leveling_enabled)
    )
    backup = None
    if changed_phrase_count or option_changed:
        backup = _persist_profile_update(path, original, profile, backup_dir, "song_difficulty")
        updated_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        updated_hash = expected_hash
    return SongDifficultyUpdateResult(
        profile_path=path,
        level=level,
        automatic_leveling_enabled=options["DDEnabled"],
        changed_arrangement_ids=tuple(changed_arrangements),
        changed_phrase_count=changed_phrase_count,
        backup_path=backup,
        profile_sha256=updated_hash,
        unavailable_arrangement_ids=tuple(unavailable_arrangements),
    )


def set_all_song_difficulty(
    profile_path: str | Path,
    arrangements: Mapping[str, Mapping[str, int]],
    active_arrangement_ids: Sequence[str],
    level: int,
    automatic_leveling_enabled: bool,
    *,
    expected_profile_sha256: str,
    backup_dir: str | Path = _PROFILE_BACKUPS,
    running_check=None,
) -> SongDifficultyUpdateResult:
    """Atomically set the global starting level and every saved phrase level.

    Every installed arrangement must have verified SNG phrase metadata. Saved
    phrase levels are changed only for profile arrangements that are currently
    installed; historical records for removed songs are preserved. A newly
    installed arrangement with no saved phrase records receives only the global
    Sight Reading starting level until it has been played once.
    """

    if type(level) is not int or not 1 <= level <= 10:
        raise ValueError("Sight Reading level must be a whole number from 1 to 10")
    if type(automatic_leveling_enabled) is not bool:
        raise ValueError("Automatic leveling must be true or false")
    expected_hash = _validate_expected_profile_sha256(expected_profile_sha256)
    metadata = _validated_arrangement_maxima(arrangements)
    if (not isinstance(active_arrangement_ids, Sequence)
            or isinstance(active_arrangement_ids, (str, bytes))
            or not active_arrangement_ids):
        raise ValueError("The active library has no verified Rocksmith arrangement list")
    active_ids = list(active_arrangement_ids)
    if any(not isinstance(value, str) or not value or value != value.strip() for value in active_ids):
        raise ValueError("The active library contains an invalid arrangement id")
    normalized_active = [value.casefold() for value in active_ids]
    if len(normalized_active) != len(set(normalized_active)):
        raise ValueError("The active library contains duplicate arrangement ids")
    missing_active_metadata = [value for value in active_ids if value.casefold() not in metadata]
    if missing_active_metadata:
        shown = ", ".join(missing_active_metadata[:5])
        suffix = " and more" if len(missing_active_metadata) > 5 else ""
        raise ValueError(
            "Cannot apply difficulty to the full active library: arrangement(s) lack verified SNG phrase metadata: "
            f"{shown}{suffix}. Rescan those archives and retry."
        )
    if running_check is None:
        from song_store import game_is_running

        running_check = game_is_running
    if running_check():
        raise OSError("Close Rocksmith 2014 before applying difficulty to the profile")

    path, original, profile = _read_profile(profile_path)
    if hashlib.sha256(original).hexdigest() != expected_hash:
        raise OSError("Rocksmith profile changed after confirmation; no changes were made")
    options = _validated_profile_difficulty_options(profile)
    songs = profile.get("Songs")
    if not isinstance(songs, dict):
        raise ValueError("Rocksmith profile has no valid Songs arrangement map")

    profile_ids = [key for key in songs if isinstance(key, str) and key]
    if len(profile_ids) != len(songs):
        raise ValueError("Rocksmith profile contains an invalid arrangement id")
    if len({key.casefold() for key in profile_ids}) != len(profile_ids):
        raise ValueError("Rocksmith profile contains duplicate arrangement ids")

    updates: list[tuple[str, dict[str, Any], dict[str, int]]] = []
    active_id_set = set(normalized_active)
    profile_id_set = {profile_id.casefold() for profile_id in profile_ids}
    unavailable_arrangements = [
        arrangement_id for arrangement_id in active_ids
        if arrangement_id.casefold() not in profile_id_set
    ]
    for profile_id in profile_ids:
        if profile_id.casefold() not in active_id_set:
            # Keep uninstalled and removed songs' saved phrase values untouched.
            continue
        if profile_id.casefold() not in metadata:
            raise ValueError(
                f"Active profile arrangement {profile_id} has no verified SNG metadata; rescan its archive"
            )
        canonical_id, maxima = metadata[profile_id.casefold()]
        phrase_records, _ = _profile_phrase_records(
            profile, canonical_id, maxima, allow_unplayed=True
        )
        if phrase_records is None:
            unavailable_arrangements.append(canonical_id)
            continue
        updates.append((canonical_id, phrase_records, maxima))

    original_sightreading_level = float(options["DDSightreadLevel"])
    original_dd_enabled = options["DDEnabled"]
    original_override_to_max = options["DDOverrideMax"]
    changed_arrangements: list[str] = []
    changed_phrase_count = 0
    for arrangement_id, phrase_records, maxima in updates:
        maxima_by_name = {name.casefold(): maximum for name, maximum in maxima.items()}
        arrangement_changed = False
        for phrase_name, phrase_record in phrase_records.items():
            target = _target_phrase_difficulty(level, maxima_by_name[phrase_name.casefold()])
            current = phrase_record["Diff"]
            if float(current) == target:
                continue
            phrase_record["Diff"] = float(target) if isinstance(current, float) else target
            arrangement_changed = True
            changed_phrase_count += 1
        if arrangement_changed:
            changed_arrangements.append(arrangement_id)

    options["DDSightreadLevel"] = float(level)
    options["DDEnabled"] = automatic_leveling_enabled
    options["DDOverrideMax"] = False
    option_changed = (
        original_sightreading_level != float(level)
        or original_dd_enabled is not automatic_leveling_enabled
        or original_override_to_max is not False
    )
    backup = None
    if changed_phrase_count or option_changed:
        backup = _persist_profile_update(path, original, profile, backup_dir, "all_song_difficulty")
        updated_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        updated_hash = expected_hash
    return SongDifficultyUpdateResult(
        profile_path=path,
        level=level,
        automatic_leveling_enabled=automatic_leveling_enabled,
        changed_arrangement_ids=tuple(changed_arrangements),
        changed_phrase_count=changed_phrase_count,
        backup_path=backup,
        profile_sha256=updated_hash,
        unavailable_arrangement_ids=tuple(unavailable_arrangements),
    )


def song_keys_from_metadata(metadata: Any) -> tuple[str, ...]:
    """Return every verified unique song key in one archive's metadata."""

    if not isinstance(metadata, dict):
        raise ValueError("Song metadata is unavailable; read titles before changing favorites")
    songs = metadata.get("songs")
    if (not isinstance(songs, list) or not songs
            or type(metadata.get("song_count")) is not int
            or metadata["song_count"] != len(songs)
            or type(metadata.get("manifest_count")) is not int
            or metadata["manifest_count"] < 1):
        raise ValueError("Archive metadata is incomplete; this archive cannot be favorited safely")
    warnings = metadata.get("warnings", [])
    if not isinstance(warnings, list) or warnings:
        raise ValueError("Archive metadata has warnings; resolve or rescan titles before changing favorites")
    keys: list[str] = []
    for song in songs:
        if not isinstance(song, dict):
            raise ValueError("Archive contains an invalid song metadata entry")
        key = song.get("song_key")
        if not isinstance(key, str) or not key or key != key.strip():
            raise ValueError("Archive contains a song without a valid Rocksmith SongKey")
        keys.append(key)
    normalized = [key.casefold() for key in keys]
    if len(normalized) != len(set(normalized)):
        raise ValueError("Archive metadata contains duplicate SongKeys")
    return tuple(keys)


def set_song_favorites(
    profile_path: str | Path,
    song_keys: list[str] | tuple[str, ...],
    favorited: bool,
    backup_dir: str | Path = _PROFILE_BACKUPS,
    running_check=None,
    expected_profile_sha256: str | None = None,
) -> FavoriteUpdateResult:
    """Add/remove only specified SongKeys in the selected profile's favorite list.

    Rocksmith must be closed. A changed profile receives a verified timestamped
    backup and an atomic EVAS rewrite with read-back verification. Other profile
    fields and favorite entries remain unchanged.
    """

    if type(favorited) is not bool:
        raise ValueError("Favorite status must be true or false")
    if not isinstance(song_keys, (list, tuple)) or not song_keys:
        raise ValueError("Select one or more verified Rocksmith SongKeys")
    if any(not isinstance(key, str) or not key or key != key.strip() for key in song_keys):
        raise ValueError("SongKeys must be non-empty strings without surrounding whitespace")
    if (expected_profile_sha256 is not None
            and (not isinstance(expected_profile_sha256, str)
                 or len(expected_profile_sha256) != 64
                 or any(character not in "0123456789abcdefABCDEF"
                        for character in expected_profile_sha256))):
        raise ValueError("Expected profile SHA-256 must contain exactly 64 hexadecimal characters")
    requested = tuple(song_keys)
    requested_normalized = [key.casefold() for key in requested]
    if len(requested_normalized) != len(set(requested_normalized)):
        raise ValueError("Requested SongKeys contain duplicates")
    if running_check is None:
        from song_store import game_is_running

        running_check = game_is_running
    if running_check():
        raise OSError("Close Rocksmith 2014 before changing song favorites")

    path, original, profile = _read_profile(profile_path)
    if (expected_profile_sha256 is not None
            and hashlib.sha256(original).hexdigest() != expected_profile_sha256.casefold()):
        raise OSError("Rocksmith profile changed after confirmation; no changes were made")
    root = profile.get("FavoritesListRoot")
    if not isinstance(root, dict) or not isinstance(root.get("FavoritesList"), list):
        raise ValueError("Rocksmith profile has no valid FavoritesListRoot.FavoritesList")
    current = root["FavoritesList"]
    if any(not isinstance(key, str) or not key or key != key.strip() for key in current):
        raise ValueError("Rocksmith profile favorite list contains an invalid SongKey")
    current_normalized = [key.casefold() for key in current]
    if len(current_normalized) != len(set(current_normalized)):
        raise ValueError("Rocksmith profile favorite list contains duplicate SongKeys")

    requested_set = set(requested_normalized)
    existing_set = set(current_normalized)
    if favorited:
        changed = tuple(key for key in requested if key.casefold() not in existing_set)
        if not changed:
            return FavoriteUpdateResult(path, True, (), None)
        root["FavoritesList"] = list(current) + list(changed)
    else:
        changed = tuple(key for key in current if key.casefold() in requested_set)
        if not changed:
            return FavoriteUpdateResult(path, False, (), None)
        root["FavoritesList"] = [key for key in current if key.casefold() not in requested_set]

    backup = _persist_profile_update(path, original, profile, backup_dir, "favorite_songs")
    return FavoriteUpdateResult(path, favorited, changed, backup)


def clear_all_song_favorites(
    profile_path: str | Path,
    backup_dir: str | Path = _PROFILE_BACKUPS,
    running_check=None,
    *,
    expected_profile_sha256: str,
) -> FavoriteUpdateResult:
    """Remove every favorite entry from a fresh, confirmed Rocksmith profile snapshot.

    This deliberately does not depend on the current song inventory: favorites
    for missing or archived DLC are removed too. A hash is mandatory because
    this is a profile-wide update. Rocksmith must be closed.
    """

    if (not isinstance(expected_profile_sha256, str)
            or len(expected_profile_sha256) != 64
            or any(character not in "0123456789abcdefABCDEF"
                   for character in expected_profile_sha256)):
        raise ValueError("Expected profile SHA-256 must contain exactly 64 hexadecimal characters")
    if running_check is None:
        from song_store import game_is_running

        running_check = game_is_running
    if running_check():
        raise OSError("Close Rocksmith 2014 before changing song favorites")

    path, original, profile = _read_profile(profile_path)
    if hashlib.sha256(original).hexdigest() != expected_profile_sha256.casefold():
        raise OSError("Rocksmith profile changed after confirmation; no changes were made")
    root = profile.get("FavoritesListRoot")
    if not isinstance(root, dict) or not isinstance(root.get("FavoritesList"), list):
        raise ValueError("Rocksmith profile has no valid FavoritesListRoot.FavoritesList")
    current = root["FavoritesList"]
    if any(not isinstance(key, str) or not key or key != key.strip() for key in current):
        raise ValueError("Rocksmith profile favorite list contains an invalid SongKey")
    normalized = [key.casefold() for key in current]
    if len(normalized) != len(set(normalized)):
        raise ValueError("Rocksmith profile favorite list contains duplicate SongKeys")
    if not current:
        return FavoriteUpdateResult(path, False, (), None)

    changed = tuple(current)
    root["FavoritesList"] = []
    backup = _persist_profile_update(path, original, profile, backup_dir, "unfavorite_all")
    return FavoriteUpdateResult(path, False, changed, backup)


def load_favorites(profile_path: str | Path) -> FavoriteResult:
    """Read one selected PRFLDB; malformed/missing data yields unavailable status."""

    path = Path(profile_path)
    try:
        _, _, profile = _read_profile(path)
        root = profile.get("FavoritesListRoot")
        if not isinstance(root, dict) or not isinstance(root.get("FavoritesList"), list):
            raise ValueError("FavoritesListRoot.FavoritesList is missing or invalid")
        keys = root["FavoritesList"]
        if any(not isinstance(key, str) or not key or key != key.strip() for key in keys):
            raise ValueError("Favorites list contains an invalid song key")
        normalized = [key.casefold() for key in keys]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Favorites list contains duplicate song keys")
        return FavoriteResult(
            available=True,
            favorite_song_keys=frozenset(normalized),
            diagnostics=f"Read {len(keys)} favorite song keys from selected profile.",
            profile_path=path,
        )
    except (OSError, ValueError, UnicodeError, zlib.error, ImportError) as error:
        return FavoriteResult(False, frozenset(), f"Favorites unavailable: {error}", path)


def classify_archive(metadata: dict[str, Any] | None, favorites: FavoriteResult) -> str:
    """Protect whole archive when any contained song is a favorite or unknown."""

    if not favorites.available or not isinstance(metadata, dict):
        return "Unknown"
    songs = metadata.get("songs")
    if not isinstance(songs, list) or not songs:
        return "Unknown"
    if (type(metadata.get("song_count")) is not int
            or metadata["song_count"] != len(songs)
            or type(metadata.get("manifest_count")) is not int
            or metadata["manifest_count"] < 1):
        return "Unknown"
    warnings = metadata.get("warnings", [])
    if not isinstance(warnings, list):
        return "Unknown"
    # Even a missing title may hide another song in this archive.
    if warnings:
        return "Unknown"

    keys: list[str] = []
    has_unknown_key = False
    for song in songs:
        if not isinstance(song, dict):
            return "Unknown"
        key = song.get("song_key")
        if not isinstance(key, str) or not key or key != key.strip():
            has_unknown_key = True
            continue
        keys.append(key.casefold())
    if any(key in favorites.favorite_song_keys for key in keys):
        return "Favorite"
    if has_unknown_key or len(keys) != len(set(keys)):
        # Same SongKey on different metadata rows: cannot prove every song status.
        return "Unknown"
    return "Not favorite"
