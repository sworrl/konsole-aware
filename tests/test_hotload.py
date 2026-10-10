"""Tests for update in place: where the installer puts the loader and module, and the
hotload helpers. No Konsole is touched and gdb is never run."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from konsole_aware import hotload, install


def _fake_repo(root: Path) -> Path:
    hook = root / "repo" / "menu_hook"
    hook.mkdir(parents=True)
    (hook / "Makefile").write_text("all:\n")
    (hook / install.LOADER_NAME).write_bytes(b"loader")
    (hook / install.MODULE_NAME).write_bytes(b"module v1")
    return root / "repo"


class TestInstallLayout(unittest.TestCase):
    def run_install(self, system: bool):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo, home = _fake_repo(root), root / "home"
        (home / ".local" / "bin").mkdir(parents=True)
        out = []
        with mock.patch.object(install, "is_system_install", return_value=system), \
                mock.patch.object(install.subprocess, "run"):
            self.assertEqual(install._install_menu_hook(repo, home, home / ".local" / "bin", out.append), 0)
        return repo, home

    def test_source_install_places_loader_and_module(self):
        repo, home = self.run_install(system=False)
        self.assertEqual((home / ".local/lib" / install.LOADER_NAME).read_bytes(), b"loader")
        module = home / ".local/lib/konsole-aware" / install.MODULE_NAME
        self.assertEqual(module.read_bytes(), b"module v1")
        inode = module.stat().st_ino
        # An update replaces the module with a rename, which is what open Konsoles watch for.
        (repo / "menu_hook" / install.MODULE_NAME).write_bytes(b"module v2")
        with mock.patch.object(install, "is_system_install", return_value=False), \
                mock.patch.object(install.subprocess, "run"):
            install._install_menu_hook(repo, home, home / ".local" / "bin", lambda _: None)
        self.assertEqual(module.read_bytes(), b"module v2")
        self.assertNotEqual(module.stat().st_ino, inode)
        self.assertNotIn("KONSOLE_AWARE_MODULE", (home / ".local/bin/konsole").read_text())

    def test_system_install_uses_the_package_module(self):
        _, home = self.run_install(system=True)
        self.assertFalse((home / ".local/lib/konsole-aware" / install.MODULE_NAME).exists())
        self.assertIn(f'KONSOLE_AWARE_MODULE="{install.SYSTEM_MODULE}"', (home / ".local/bin/konsole").read_text())


class TestHotload(unittest.TestCase):
    def test_nothing_to_do(self):
        with mock.patch.object(hotload, "pending", return_value=[]):
            out = []
            self.assertEqual(hotload.hotload(out=out.append), 0)
            self.assertIn("already", out[0])

    def test_pending_reads_maps(self):
        maps = {1: "7f00 r-xp /run/user/1000/konsole-aware/modules/module-1-1.so (deleted)\n",
                2: "7f00 r-xp /home/u/.local/lib/libkonsole_aware_menu.so (deleted)\n"}
        with mock.patch.object(hotload, "konsole_pids", return_value=[1, 2]), \
                mock.patch.object(hotload.Path, "read_text", lambda self, **kw: maps[int(self.parts[2])]):
            self.assertEqual(hotload.pending(), [2])

    def test_gdb_command_loads_with_dlopen(self):
        cmd = hotload._gdb_command("/usr/bin/gdb", 42, Path("/run/x/loader.so"))
        self.assertEqual(cmd[:3], ["/usr/bin/gdb", "-p", "42"])
        self.assertIn('call (void*)dlopen("/run/x/loader.so", 2)', cmd)
        self.assertEqual(cmd[-1], "detach")

    def test_needs_gdb(self):
        with mock.patch.object(hotload.shutil, "which", return_value=None):
            out = []
            self.assertEqual(hotload.hotload([5], out=out.append), 1)
            self.assertIn("gdb", out[0])


if __name__ == "__main__":
    unittest.main()
