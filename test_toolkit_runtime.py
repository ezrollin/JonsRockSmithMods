import tempfile
import unittest
from pathlib import Path

from toolkit_runtime import resolve_toolkit_helper, toolkit_library_architecture


def write_pe_library(folder, machine, magic):
    header = bytearray(256)
    header[:2] = b"MZ"
    header[0x3C:0x40] = (128).to_bytes(4, "little")
    header[128:132] = b"PE\0\0"
    header[132:134] = machine.to_bytes(2, "little")
    header[152:154] = magic.to_bytes(2, "little")
    (folder / "RocksmithToolkitLib.dll").write_bytes(header)


class ToolkitRuntimeTests(unittest.TestCase):
    def test_dispatches_to_matching_x86_or_x64_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "Helper.exe"
            for architecture in ("x86", "x64"):
                base.with_name(f"Helper-{architecture}.exe").write_bytes(b"helper fixture")
            for machine, magic, expected in ((0x14C, 0x10B, "x86"), (0x8664, 0x20B, "x64")):
                write_pe_library(root, machine, magic)
                self.assertEqual(toolkit_library_architecture(root), expected)
                self.assertEqual(resolve_toolkit_helper(base, root), root / f"Helper-{expected}.exe")

    def test_missing_architecture_does_not_launch_wrong_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_pe_library(root, 0x8664, 0x20B)
            (root / "Helper.exe").write_bytes(b"legacy helper")
            (root / "Helper-x86.exe").write_bytes(b"x86 helper")
            with self.assertRaisesRegex(FileNotFoundError, "No x64 helper"):
                resolve_toolkit_helper(root / "Helper.exe", root)

    def test_rejects_corrupt_and_unsupported_pe_libraries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "RocksmithToolkitLib.dll").write_bytes(b"broken")
            with self.assertRaisesRegex(ValueError, "header"):
                toolkit_library_architecture(root)
            write_pe_library(root, 0xAA64, 0x20B)
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                toolkit_library_architecture(root)
