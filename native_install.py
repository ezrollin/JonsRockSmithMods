"""Explicit, reversible installation of the pinned Jons RSMods extension."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from song_store import StoreError, game_is_running


# Installed RSMods proxy observed on this machine before development.
SUPPORTED_ORIGINAL_SHA256 = "c884df4a456a1af11e4add21f01148239f6ae1e3b638d75a2cdeb9ca2e5f2647"
# Exact SHA256 of the prior isolated Jons fixed-speed build, retained for guarded upgrades.
KNOWN_PREVIOUS_JONS_SHA256 = "58492a46fa2652859928d303abab113492fe703c880171b6c523ee0559f09a4f"
KNOWN_JONS_RELEASE_SHA256 = frozenset({
    "9b8e059842b672e054d0b31f2d961806e6e5d97a445a8099b89ebdb4f7a73c12",
    "a464eaf61f0ec328f2360a8074eb95e1dccbbc7f029a600d8e72d90bdd6e7ddd",
    "54d35a4eea5f9182f92eafafb0c26522545083fd6ee142dd36724cab85456f4a",
    "dd82ca38d9d6f51a0a5f2e6d837ab1be60fc84e4068206e88d4d60b9b132681e",
    "8c972998f304c5bfd81dbe1a010769d306532e26ad9e0c0447749f9860923586",
})
PATCHED_DLL = Path(__file__).parent / "native" / "artifacts" / "xinput1_3.dll"
STATE_DIR = Path.home() / "Documents" / "JonsRockSmithMods" / "Backups"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def support_status(
    game_root: Path,
    patched_dll: Path = PATCHED_DLL,
    backup_dir: Path = STATE_DIR,
    expected_original_hash: str = SUPPORTED_ORIGINAL_SHA256,
) -> str:
    installed = game_root / "xinput1_3.dll"
    if not installed.is_file():
        return "Jons RSMods extension unavailable"
    if not patched_dll.is_file():
        if sha256(installed) in KNOWN_JONS_RELEASE_SHA256:
            return "Known Jons native extension found; optional installer binary is not bundled"
        return "Optional Jons native installer binary is not bundled; see in-game removal setup"
    installed_hash = sha256(installed)
    if installed_hash == sha256(patched_dll):
        return "Jons RSMods extension installed; restart Rocksmith after changing settings"
    if installed_hash == expected_original_hash:
        return "Compatible RSMods found; Jons extension ready to install"
    if installed_hash == KNOWN_PREVIOUS_JONS_SHA256 or installed_hash in KNOWN_JONS_RELEASE_SHA256:
        return "Previous verified Jons extension found; update ready to install"
    if _managed_install_record(game_root, backup_dir, installed_hash) is not None:
        return "Jons RSMods extension update ready to install"
    return "Unknown RSMods DLL; Jons extension install blocked"


def install(game_root: Path, patched_dll: Path = PATCHED_DLL,
            backup_dir: Path = STATE_DIR, running_check=game_is_running,
            expected_original_hash: str = SUPPORTED_ORIGINAL_SHA256) -> Path | None:
    if running_check():
        raise StoreError("Close Rocksmith 2014 before installing the Jons RSMods extension")
    game_root = game_root.resolve(strict=True)
    installed = game_root / "xinput1_3.dll"
    if not (game_root / "Rocksmith2014.exe").is_file() or not (game_root / "RSMods.ini").is_file():
        raise StoreError("Rocksmith 2014 with RSMods is required")
    if not installed.is_file() or not patched_dll.is_file():
        raise StoreError("Installed RSMods DLL or built Jons extension is missing")
    if installed.is_symlink() or patched_dll.is_symlink():
        raise StoreError("RSMods DLL paths cannot be symbolic links")
    current_hash = sha256(installed)
    patched_hash = sha256(patched_dll)
    if current_hash == patched_hash:
        return None

    managed = _managed_install_record(game_root, backup_dir, current_hash)
    if current_hash == expected_original_hash:
        original_hash = current_hash
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = backup_dir / f"RSMods_xinput1_3_preinstall_{stamp}.dll"
        shutil.copy2(installed, backup)
        if sha256(backup) != original_hash:
            backup.unlink(missing_ok=True)
            raise StoreError("RSMods DLL backup verification failed")
    elif managed is not None:
        original_hash = managed["original_sha256"]
        backup = Path(managed["backup"])
    elif current_hash == KNOWN_PREVIOUS_JONS_SHA256 or current_hash in KNOWN_JONS_RELEASE_SHA256:
        original_hash = current_hash
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = backup_dir / f"Jons_previous_xinput1_3_{stamp}.dll"
        shutil.copy2(installed, backup)
        if sha256(backup) != original_hash:
            backup.unlink(missing_ok=True)
            raise StoreError("Previous Jons DLL backup verification failed")
    else:
        raise StoreError(
            "This RSMods DLL build has not been verified with the Jons extension. "
            f"Installed SHA-256: {current_hash}. Keep it in place until a compatible build is available."
        )

    backup_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".JonsRockSmithMods-", suffix=".dll", dir=game_root)
    os.close(descriptor)
    rollback_descriptor, rollback_temporary = tempfile.mkstemp(
        prefix=".JonsRockSmithMods-rollback-", suffix=".dll", dir=game_root
    )
    os.close(rollback_descriptor)
    try:
        shutil.copy2(installed, rollback_temporary)
        if sha256(Path(rollback_temporary)) != current_hash:
            raise StoreError("Current RSMods DLL rollback copy failed verification")
        shutil.copy2(patched_dll, temporary)
        if sha256(Path(temporary)) != patched_hash:
            raise StoreError("Copied Jons RSMods extension failed verification")
        state = {
            "game_root": str(game_root), "backup": str(backup),
            "original_sha256": original_hash, "patched_sha256": patched_hash,
        }
        if running_check() or sha256(installed) != current_hash:
            raise StoreError("Rocksmith started or the installed DLL changed during installation; retry with the game closed")
        os.replace(temporary, installed)
        try:
            _write_install_record(backup_dir, state)
        except Exception as record_error:
            rollback_path = rollback_temporary
            try:
                os.replace(rollback_path, installed)
            except OSError as rollback_error:
                rollback_temporary = ""
                raise StoreError(
                    f"Install record failed and DLL rollback failed. Preserve recovery file: {rollback_path}"
                ) from rollback_error
            rollback_temporary = ""
            raise
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
        if rollback_temporary and os.path.exists(rollback_temporary):
            os.unlink(rollback_temporary)
    return backup


def restore(game_root: Path, patched_dll: Path = PATCHED_DLL,
            backup_dir: Path = STATE_DIR, running_check=game_is_running) -> Path:
    if running_check():
        raise StoreError("Close Rocksmith 2014 before restoring RSMods")
    state_path = backup_dir / "latest_install.json"
    if state_path.is_symlink() or not state_path.is_file():
        raise StoreError("No Jons install backup record found")
    game_root = game_root.resolve(strict=False)
    installed = game_root / "xinput1_3.dll"
    if not installed.is_file():
        raise StoreError("Installed Jons RSMods DLL is missing; restore blocked")
    verified = _managed_install_record(game_root, backup_dir, sha256(installed))
    if verified is None:
        raise StoreError("Jons RSMods install record or original backup failed verification")
    backup = Path(verified["backup"])
    if not backup.is_file() or sha256(backup) != verified["original_sha256"]:
        raise StoreError("Original RSMods backup missing or changed")
    if not installed.is_file() or sha256(installed) != verified["patched_sha256"]:
        raise StoreError("Installed DLL changed since Jons installation; restore blocked")
    descriptor, temporary = tempfile.mkstemp(prefix=".JonsRockSmithMods-", suffix=".dll", dir=game_root)
    os.close(descriptor)
    try:
        shutil.copy2(backup, temporary)
        os.replace(temporary, installed)
        state_path.unlink()
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup


def _managed_install_record(game_root: Path, backup_dir: Path, installed_hash: str) -> dict | None:
    state_path = backup_dir / "latest_install.json"
    if state_path.is_symlink() or not state_path.is_file():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return None
        if Path(state["game_root"]).resolve(strict=False) != game_root.resolve(strict=False):
            return None
        if state["patched_sha256"] != installed_hash:
            return None
        backup = Path(state["backup"])
        original_hash = state["original_sha256"]
        if not isinstance(original_hash, str) or len(original_hash) != 64:
            return None
        if backup.is_symlink() or backup.resolve(strict=False).parent != backup_dir.resolve(strict=False):
            return None
        if not backup.is_file() or sha256(backup) != original_hash:
            return None
        return state
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _write_install_record(backup_dir: Path, state: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=".latest_install-", suffix=".tmp", dir=backup_dir
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, backup_dir / "latest_install.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
