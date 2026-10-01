"""Unit tests for folder, directory, and project icon resolution."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from konsole_aware.tabs.icons import IconResolver
from konsole_aware.state.manager import StateManager


class TestIconResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = IconResolver()
        self.temp_dir = tempfile.mkdtemp(prefix="konsole_aware_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_resolve_project_file_cargo(self):
        # Create a mock Rust project
        proj_dir = Path(self.temp_dir) / "rust_crate"
        proj_dir.mkdir(parents=True)
        (proj_dir / "Cargo.toml").write_text("[package]\nname = 'test'\n", encoding="utf-8")

        icon, reason = self.resolver.resolve_with_reason(cwd=proj_dir)
        self.assertEqual(icon, "🦀")
        self.assertIn("Cargo.toml", reason)

    def test_resolve_project_file_python(self):
        proj_dir = Path(self.temp_dir) / "py_proj"
        proj_dir.mkdir(parents=True)
        (proj_dir / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")

        icon, reason = self.resolver.resolve_with_reason(cwd=proj_dir)
        self.assertEqual(icon, "🐍")
        self.assertIn("pyproject.toml", reason)

    def test_resolve_project_file_node(self):
        proj_dir = Path(self.temp_dir) / "node_app"
        proj_dir.mkdir(parents=True)
        (proj_dir / "package.json").write_text("{\"name\": \"test\"}", encoding="utf-8")

        icon, reason = self.resolver.resolve_with_reason(cwd=proj_dir)
        self.assertEqual(icon, "📦")
        self.assertIn("package.json", reason)

    def test_resolve_subfolder_tests(self):
        # Create project with git root and tests subfolder
        proj_dir = Path(self.temp_dir) / "repo"
        proj_dir.mkdir(parents=True)
        (proj_dir / ".git").mkdir()
        tests_dir = proj_dir / "tests"
        tests_dir.mkdir()

        icon, reason = self.resolver.resolve_with_reason(cwd=tests_dir)
        self.assertEqual(icon, "🧪")
        self.assertIn("subfolder(tests)", reason)

    def test_resolve_subfolder_frontend(self):
        proj_dir = Path(self.temp_dir) / "repo"
        proj_dir.mkdir(parents=True)
        (proj_dir / ".git").mkdir()
        frontend_dir = proj_dir / "frontend"
        frontend_dir.mkdir()

        icon, reason = self.resolver.resolve_with_reason(cwd=frontend_dir)
        self.assertEqual(icon, "🎨")
        self.assertIn("subfolder(frontend)", reason)

    def test_resolve_subfolder_backend(self):
        proj_dir = Path(self.temp_dir) / "repo"
        proj_dir.mkdir(parents=True)
        (proj_dir / ".git").mkdir()
        backend_dir = proj_dir / "backend"
        backend_dir.mkdir()

        icon, reason = self.resolver.resolve_with_reason(cwd=backend_dir)
        self.assertEqual(icon, "⚙️")
        self.assertIn("subfolder(backend)", reason)

    def test_resolve_subfolder_docs(self):
        proj_dir = Path(self.temp_dir) / "repo"
        proj_dir.mkdir(parents=True)
        (proj_dir / ".git").mkdir()
        docs_dir = proj_dir / "docs"
        docs_dir.mkdir()

        icon, reason = self.resolver.resolve_with_reason(cwd=docs_dir)
        self.assertEqual(icon, "📚")
        self.assertIn("subfolder(docs)", reason)

    def test_resolve_custom_konsole_icon_file(self):
        proj_dir = Path(self.temp_dir) / "custom_proj"
        proj_dir.mkdir(parents=True)
        (proj_dir / ".konsole-icon").write_text("🚀\n", encoding="utf-8")

        icon, reason = self.resolver.resolve_with_reason(cwd=proj_dir)
        self.assertEqual(icon, "🚀")
        self.assertIn("file_override", reason)

    def test_resolve_custom_konsole_aware_json(self):
        proj_dir = Path(self.temp_dir) / "custom_json_proj"
        proj_dir.mkdir(parents=True)
        (proj_dir / ".konsole-aware.json").write_text('{"icon": "⚡", "job": "Speed"}', encoding="utf-8")

        icon, reason = self.resolver.resolve_with_reason(cwd=proj_dir)
        self.assertEqual(icon, "⚡")
        self.assertIn("file_override", reason)

    def test_resolve_explicit_session_assignment(self):
        state_mgr = StateManager()
        s_key = "test_sess_icon_123"
        state_mgr.set_session(s_key, icon="🔥")

        icon, reason = self.resolver.resolve_with_reason(session_key=s_key, cwd=self.temp_dir)
        self.assertEqual(icon, "🔥")
        self.assertEqual(reason, "session")


if __name__ == "__main__":
    unittest.main()
