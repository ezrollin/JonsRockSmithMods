"""Choose helper executables matching the installed Toolkit assembly format."""

from __future__ import annotations

from pathlib import Path


def toolkit_library_architecture(toolkit_directory: str | Path) -> str:
    """Return ``x86`` or ``x64`` from the Toolkit library's PE machine/header."""

    library = Path(toolkit_directory) / "RocksmithToolkitLib.dll"
    try:
        with library.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise ValueError("RocksmithToolkitLib.dll has no DOS/PE header")
            stream.seek(0x3C)
            offset_data = stream.read(4)
            if len(offset_data) != 4:
                raise ValueError("RocksmithToolkitLib.dll has a truncated PE header")
            pe_offset = int.from_bytes(offset_data, "little")
            if pe_offset > 16 * 1024 * 1024:
                raise ValueError("RocksmithToolkitLib.dll has an invalid PE header offset")
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\x00\x00":
                raise ValueError("RocksmithToolkitLib.dll has an invalid PE signature")
            machine_data = stream.read(2)
            stream.seek(pe_offset + 24)
            magic_data = stream.read(2)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"RocksmithToolkitLib.dll not found in {toolkit_directory}") from error

    if len(machine_data) != 2 or len(magic_data) != 2:
        raise ValueError("RocksmithToolkitLib.dll has a truncated PE header")
    machine = int.from_bytes(machine_data, "little")
    optional_magic = int.from_bytes(magic_data, "little")
    if machine == 0x8664 and optional_magic == 0x20B:
        return "x64"
    if machine == 0x14C and optional_magic == 0x10B:
        return "x86"
    raise ValueError(
        f"Unsupported RocksmithToolkitLib.dll PE architecture: machine=0x{machine:04X}, "
        f"optional_header=0x{optional_magic:04X}"
    )


def resolve_toolkit_helper(
    default_helper: str | Path,
    toolkit_directory: str | Path,
) -> Path:
    """Select the helper variant built for a ToolkitLib PE architecture.

    New builds contain ``<stem>-x86.exe`` and ``<stem>-x64.exe`` beside the
    historical generic executable. Older single-helper bundles keep working
    through that generic fallback.
    """

    base = Path(default_helper)
    architecture = toolkit_library_architecture(toolkit_directory)
    selected = base.with_name(f"{base.stem}-{architecture}{base.suffix}")
    if selected.is_file():
        return selected

    variants = tuple(base.parent.glob(f"{base.stem}-x*.{base.suffix.lstrip('.')}"))
    if variants:
        raise FileNotFoundError(
            f"No {architecture} helper is bundled for {base.stem}; rebuild the application helpers."
        )
    if base.is_file():
        return base
    raise FileNotFoundError(f"Toolkit helper not found: {selected}")
