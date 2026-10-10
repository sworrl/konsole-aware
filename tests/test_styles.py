"""Tests for patterned tab backgrounds and automatic project colors."""

import json
import unittest

from konsole_aware.tabs import styles


class TestProjectColors(unittest.TestCase):
    def setUp(self):
        for f in (styles.PROJECT_COLORS_FILE, styles.STYLES_FILE):
            try:
                f.unlink()
            except FileNotFoundError:
                pass

    def test_distinct_and_stable(self):
        a = styles.project_entry("alpha")["color"]
        b = styles.project_entry("beta")["color"]
        self.assertNotEqual(a, b)
        self.assertEqual(styles.project_entry("alpha")["color"], a)  # remembered
        self.assertIn("alpha", json.loads(styles.PROJECT_COLORS_FILE.read_text()))

    def test_more_projects_than_palette(self):
        colors = {styles.project_entry(f"p{i}")["color"] for i in range(len(styles.PROJECT_PALETTE) + 5)}
        self.assertGreaterEqual(len(colors), len(styles.PROJECT_PALETTE))

    def test_default_is_project_status_two_tone(self):
        spec = styles.build_spec("alpha", "#d97757", config={"tab_pattern": "project-status"})
        self.assertEqual(spec["pattern"], "two-tone")
        self.assertEqual(spec["colors"], [styles.project_entry("alpha")["color"], "#d97757"])
        idle = styles.build_spec("alpha", None, config={"tab_pattern": "project-status"})
        self.assertEqual(idle["pattern"], "solid")

    def test_project_pattern_and_tab_override(self):
        styles.set_project_colors("alpha", ["#ff0000", "#0000ff"], "gradient")
        spec = styles.build_spec("alpha", "#d97757", config={})
        self.assertEqual((spec["pattern"], spec["colors"]), ("gradient", ["#ff0000", "#0000ff"]))
        rainbow = styles.build_spec("alpha", None, override={"pattern": "rainbow"}, config={})
        self.assertEqual(rainbow["colors"], styles.RAINBOW)
        stripes = styles.build_spec("beta", "#d97757", override={"pattern": "stripes", "colors": []}, config={})
        self.assertEqual(len(stripes["colors"]), 2)

    def test_none_and_opacity_bounds(self):
        self.assertIsNone(styles.build_spec("alpha", "#d97757", config={"tab_pattern": "none"}))
        spec = styles.build_spec("alpha", None, config={"tab_pattern_opacity": 5})
        self.assertEqual(spec["opacity"], 1.0)

    def test_publish_and_prune(self):
        styles.publish("svc:/Sessions/1", {"pattern": "solid", "colors": ["#123456"], "opacity": 0.6})
        styles.publish("svc:/Sessions/2", {"pattern": "solid", "colors": ["#654321"], "opacity": 0.6})
        styles.prune({"svc:/Sessions/1"})
        data = json.loads(styles.STYLES_FILE.read_text())
        self.assertEqual(sorted(data), ["_settings", "svc:/Sessions/1"])  # settings survive pruning
        styles.publish("svc:/Sessions/1", None)
        self.assertEqual(list(json.loads(styles.STYLES_FILE.read_text())), ["_settings"])

    def test_tint_band_and_position_setting(self):
        spec = styles.build_spec("alpha", None, config={"terminal_tint": 0.9, "terminal_band": False})
        self.assertEqual((spec["tint"], spec["band"]), (0.5, False))
        self.assertEqual(styles.build_spec("alpha", None, config={"terminal_tint": 0})["tint"], 0.0)
        settings = styles.window_settings({"tab_colors_follow_position": True})
        self.assertTrue(settings["follow_position"])
        self.assertFalse(styles.window_settings({})["follow_position"])  # off by default

    def test_flag_pattern(self):
        flag = styles.build_spec("alpha", "#d97757", override={"pattern": "flag"}, config={})
        self.assertEqual((flag["pattern"], flag["colors"]), ("flag", styles.FLAG))
        self.assertEqual(styles.build_spec("alpha", None, config={"tab_pattern": "flag"})["pattern"], "flag")

    def test_flag_is_random_per_tab_and_stable(self):
        seeds = [f"org.kde.konsole-1:/Sessions/{i}" for i in range(400)]
        picked = [s for s in seeds if styles.flag_pick(s, 0.1)]
        self.assertTrue(20 < len(picked) < 60)  # about 1 in 10
        self.assertEqual(picked, [s for s in seeds if styles.flag_pick(s, 0.1)])  # same tab, same answer
        self.assertFalse(any(styles.flag_pick(s, 0) for s in seeds))  # 0 turns it off
        spec = styles.build_spec("alpha", "#d97757", config={"flag_chance": 0.1}, seed=picked[0])
        self.assertEqual(spec["pattern"], "flag")
        # A pattern the user chose wins over the flag.
        chosen = styles.build_spec("alpha", None, override={"pattern": "rainbow"}, config={}, seed=picked[0])
        self.assertEqual(chosen["pattern"], "rainbow")

    def test_old_palette_moves_to_new(self):
        styles.PROJECT_COLORS_FILE.parent.mkdir(parents=True, exist_ok=True)
        styles.PROJECT_COLORS_FILE.write_text(json.dumps({
            "old": {"color": styles.LEGACY_PALETTE[2]}, "mine": {"color": "#123456"},
            "custom": {"color": styles.LEGACY_PALETTE[0], "colors": [styles.LEGACY_PALETTE[0], "#ffffff"]}}))
        self.assertEqual(styles.project_entry("old")["color"], styles.PROJECT_PALETTE[2])
        self.assertEqual(styles.project_entry("mine")["color"], "#123456")
        self.assertEqual(styles.project_colors()["custom"]["color"], styles.LEGACY_PALETTE[0])

    def test_shift_is_a_close_valid_color(self):
        for c in styles.PROJECT_PALETTE + ["#ffffff", "#000000", "#ff0000"]:
            out = styles._shift(c)
            self.assertRegex(out, r"^#[0-9a-f]{6}$")


if __name__ == "__main__":
    unittest.main()
