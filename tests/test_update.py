"""Tests for the release check. GitHub and notifications are never really called."""

import json
import time
import unittest
from unittest import mock

from konsole_aware import __version__, update

RELEASE = {"latest": "99.0.0", "url": "https://github.com/sworrl/konsole-aware/releases/tag/v99.0.0",
           "assets": {"konsole-aware_99.0.0_amd64.deb": "https://example.invalid/a.deb",
                      "konsole-aware-99.0.0-1.fc44.x86_64.rpm": "https://example.invalid/a.rpm"}}


class TestUpdate(unittest.TestCase):
    def setUp(self):
        try:
            update.UPDATE_FILE.unlink()
        except FileNotFoundError:
            pass
        self.toasts = []
        patcher = mock.patch.object(update, "_toast", side_effect=lambda s: self.toasts.append(s["latest"]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_versions(self):
        self.assertEqual(update.parse_version("v1.3.0"), (1, 3, 0))
        self.assertEqual(update.parse_version("1.10.2-rc1"), (1, 10, 2))
        self.assertTrue(update.is_newer("1.10.0", "1.9.9"))
        self.assertFalse(update.is_newer("1.2.0", "1.2.0"))
        self.assertFalse(update.is_newer("", "1.2.0"))

    def test_new_release_shows_and_notifies_once(self):
        with mock.patch.object(update, "fetch_latest", return_value=dict(RELEASE)) as fetch:
            state = update.check(config={})
            self.assertTrue(state["show"])
            self.assertEqual(state["current"], __version__)
            update.check(config={})  # not due again yet: no second look, no second toast
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.toasts, ["99.0.0"])
        self.assertTrue(json.loads(update.UPDATE_FILE.read_text())["show"])

    def test_up_to_date_and_disabled(self):
        with mock.patch.object(update, "fetch_latest", return_value=dict(RELEASE, latest=__version__)):
            self.assertFalse(update.check(config={})["show"])
        with mock.patch.object(update, "fetch_latest") as fetch:
            update.check(config={"update_check": False})
            fetch.assert_not_called()
        self.assertEqual(self.toasts, [])

    def test_offline_keeps_quiet(self):
        with mock.patch.object(update, "fetch_latest", return_value=None):
            state = update.check(force=True, config={})
        self.assertFalse(state["show"])
        self.assertTrue(state["error"])

    def test_skip_and_snooze_hide_the_notice(self):
        with mock.patch.object(update, "fetch_latest", return_value=dict(RELEASE)):
            update.check(config={})
        self.assertFalse(update.skip()["show"])
        self.assertEqual(update.status()["skipped"], "99.0.0")
        update.skip("0.0.1")
        self.assertTrue(update.status()["show"])
        update.snooze(1)
        self.assertFalse(update.status()["show"])
        self.assertGreater(update.status()["snooze_until"], time.time())

    def test_asset_for_this_machine(self):
        with mock.patch.object(update.platform, "machine", return_value="x86_64"):
            self.assertEqual(update._asset(RELEASE, "deb")[0], "konsole-aware_99.0.0_amd64.deb")
            self.assertTrue(update._asset(RELEASE, "rpm")[0].endswith(".x86_64.rpm"))
        with mock.patch.object(update.platform, "machine", return_value="riscv64"):
            self.assertIsNone(update._asset(RELEASE, "deb"))


if __name__ == "__main__":
    unittest.main()
