"""Unit tests for TitleComposer and permanent job/project context."""

import os
import unittest
from pathlib import Path

from konsole_aware.tabs.title import TitleComposer, find_project_name


class TestTitleComposer(unittest.TestCase):
    def setUp(self):
        self.composer = TitleComposer()

    def test_find_project_name(self):
        # Current repo should resolve to konsole_aware
        repo_root = Path(__file__).resolve().parent.parent
        self.assertEqual(find_project_name(str(repo_root / "tests")), repo_root.name)

    def test_compose_with_job_and_task_dot(self):
        title = self.composer.compose(
            job_name="MyProject",
            task_category="agent",
            status="thinking",
            detail="agy: thinking...",
        )
        self.assertIn("MyProject", title)
        self.assertIn("🟣", title)
        self.assertIn("🧠", title)
        self.assertIn("agy: thinking...", title)

    def test_compose_transfer(self):
        title = self.composer.compose(
            job_name="BackupJob",
            task_category="transfer",
            status="busy",
            detail="rsync -> remote",
        )
        self.assertIn("BackupJob", title)
        self.assertIn("🟠", title)
        self.assertIn("rsync -> remote", title)

    def test_compose_build_and_test(self):
        title_build = self.composer.compose(
            job_name="CoreApp",
            task_category="build",
            status="done",
            detail="cargo build (42s)",
        )
        self.assertIn("CoreApp", title_build)
        self.assertIn("🟡", title_build)
        self.assertIn("✅", title_build)

        title_test = self.composer.compose(
            job_name="CoreApp",
            task_category="test",
            status="error",
            detail="cargo test (exit 1)",
        )
        self.assertIn("CoreApp", title_test)
        self.assertIn("🟢", title_test)
        self.assertIn("❌", title_test)

    def test_compose_package(self):
        title = self.composer.compose(
            job_name="sys",
            task_category="package",
            status="busy",
            detail="apt-get update",
        )
        self.assertIn("sys", title)
        self.assertIn("🟦", title)
        self.assertIn("apt-get update", title)

    def test_compose_compact_duration(self):
        title = self.composer.compose(
            job_name="myproj",
            task_category="agent",
            status="done",
            detail="ready in 3h 44m",
            agent="agy",
        )
        self.assertIn("ready (3h 44m)", title)
        self.assertNotIn("ready in", title)
        self.assertIn("🪐 AGY", title)

    def test_task_dot_mapping(self):
        self.assertEqual(self.composer.get_task_dot("agent"), "🟣")
        self.assertEqual(self.composer.get_task_dot("transfer"), "🟠")
        self.assertEqual(self.composer.get_task_dot("download"), "🟤")
        self.assertEqual(self.composer.get_task_dot("build"), "🟡")
        self.assertEqual(self.composer.get_task_dot("test"), "🟢")
        self.assertEqual(self.composer.get_task_dot("git"), "🔵")
        self.assertEqual(self.composer.get_task_dot("container"), "🟦")
        self.assertEqual(self.composer.get_task_dot("package"), "🟦")


if __name__ == "__main__":
    unittest.main()

