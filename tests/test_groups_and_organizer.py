"""Unit tests for tab grouping, anchoring, color utilities, and organizer."""

import unittest
from konsole_aware.terminal.colors import (
    colorize,
    get_color_tag,
    hex_to_rgb,
    pad_ansi,
    resolve_color_hex,
    strip_ansi,
)
from konsole_aware.tabs.groups import TabGroupManager
from konsole_aware.tabs.organizer import TabOrganizer


class TestColors(unittest.TestCase):
    def test_hex_to_rgb(self):
        self.assertEqual(hex_to_rgb("#ff0000"), (255, 0, 0))
        self.assertEqual(hex_to_rgb("00ff00"), (0, 255, 0))
        self.assertEqual(hex_to_rgb("00f"), (0, 0, 255))
        self.assertIsNone(hex_to_rgb("invalid"))

    def test_resolve_color_hex(self):
        self.assertEqual(resolve_color_hex("amber"), "#f59e0b")
        self.assertEqual(resolve_color_hex("emerald"), "#059669")
        self.assertEqual(resolve_color_hex("#123456"), "#123456")

    def test_get_color_tag(self):
        self.assertEqual(get_color_tag("red"), "🔴")
        self.assertEqual(get_color_tag("green"), "🟢")
        self.assertEqual(get_color_tag("blue"), "🔵")
        self.assertEqual(get_color_tag("red", square=True), "🟥")

    def test_colorize_and_strip_ansi(self):
        colored = colorize("TestText", fg="#10b981", bold=True)
        self.assertIn("\033[", colored)
        stripped = strip_ansi(colored)
        self.assertEqual(stripped, "TestText")

    def test_pad_ansi(self):
        colored = colorize("Hi", fg="#ff0000")
        padded = pad_ansi(colored, 6)
        # Visible length should be 6
        self.assertEqual(len(strip_ansi(padded)), 6)


class TestTabGroups(unittest.TestCase):
    def setUp(self):
        self.mgr = TabGroupManager()

    def test_define_and_assign_group(self):
        s_key = "test-service:/Sessions/99"
        self.mgr.define_group("test-group", color="#ec4899", icon="🎨")
        self.mgr.assign_tab(s_key, "test-group")

        grp = self.mgr.get_tab_group(s_key)
        self.assertIsNotNone(grp)
        self.assertEqual(grp["name"], "test-group")
        self.assertEqual(grp["color"], "#ec4899")

        self.mgr.unassign_tab(s_key)
        self.assertIsNone(self.mgr.get_tab_group(s_key))

    def test_anchor_tab(self):
        s_key = "test-service:/Sessions/98"
        self.mgr.unanchor_tab(s_key)
        self.assertFalse(self.mgr.is_anchored(s_key))

        self.mgr.anchor_tab(s_key)
        self.assertTrue(self.mgr.is_anchored(s_key))

        title = self.mgr.format_title(s_key, "Terminal")
        self.assertTrue(title.startswith("📌"))

        self.mgr.unanchor_tab(s_key)
        self.assertFalse(self.mgr.is_anchored(s_key))


class TestTabOrganizer(unittest.TestCase):
    def setUp(self):
        self.org = TabOrganizer()

    def test_organizer_init(self):
        self.assertIsNotNone(self.org)


if __name__ == "__main__":
    unittest.main()
