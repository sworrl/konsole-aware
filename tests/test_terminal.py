"""Tests for terminal controller and state manager."""

import time
import unittest
from konsole_aware.terminal.ansi import AnsiTerminal
from konsole_aware.terminal import get_terminal
from konsole_aware.state.manager import StateManager


class TestTerminalAndState(unittest.TestCase):
    def test_ansi_terminal_sequences(self):
        term = AnsiTerminal(tty_path="/dev/null")
        # Should not throw
        self.assertTrue(term.set_title("Test Title"))
        self.assertTrue(term.send_terminal_notification("Alert", "Body"))

    def test_get_terminal(self):
        term = get_terminal()
        self.assertIsNotNone(term)
        sess_id = term.get_session_id()
        self.assertIsInstance(sess_id, str)

    def test_state_manager(self):
        sm = StateManager()
        s_key = "test-session-key-999"
        sm.clear_session(s_key)

        sm.start_task(
            s_key,
            task_name="rsync",
            task_type="transfer",
            detail="backup -> /nas",
            original_title="Original Tab Title",
            original_color="#000000",
        )

        sess = sm.get_session(s_key)
        self.assertEqual(sess["status"], "busy")
        self.assertEqual(sess["task_name"], "rsync")
        self.assertEqual(sess["original_title"], "Original Tab Title")

        time.sleep(0.05)
        finished = sm.finish_task(s_key, exit_code=0, status="done")
        self.assertEqual(finished["status"], "done")
        self.assertGreater(finished["duration"], 0.0)

        sm.clear_session(s_key)
        self.assertEqual(sm.get_session(s_key), {})


if __name__ == "__main__":
    unittest.main()


class TestNotifierTimeout(unittest.TestCase):
    def test_toasts_expire(self):
        from unittest import mock
        from konsole_aware.notify.notifier import Notifier
        n = Notifier()
        n.notify_send_bin = "/usr/bin/notify-send"
        with mock.patch("konsole_aware.notify.notifier.subprocess.Popen") as popen:
            n.notify("t", "m", urgency="critical", play_sound=False)
        cmd = popen.call_args[0][0]
        self.assertIn("-t", cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], "4000")
        self.assertEqual(cmd[cmd.index("-u") + 1], "normal")


class TestTabRefresh(unittest.TestCase):
    def test_refresh_all_tabs_mocked(self):
        from unittest import mock
        import argparse
        from konsole_aware.cli import refresh_all_tabs, cmd_refresh

        fake_sessions = [
            {
                "service": "org.kde.konsole-1234",
                "session_path": "/Sessions/1",
                "session_num": "1",
                "session_key": "org.kde.konsole-1234:/Sessions/1",
                "window": "/Windows/1",
                "is_active_tab": True,
                "tab_index": 0,
                "pane_index": 0,
                "title0": "",
                "title1": "",
                "tab_color": "",
                "shell_pid": 100,
                "foreground_pid": 101,
                "is_idle_shell": False,
                "command": "agy --dangerously-skip-permissions",
                "cwd": "/home/user/project",
            }
        ]

        with mock.patch("konsole_aware.terminal.konsole.KonsoleTerminal.list_all_sessions", return_value=fake_sessions), \
             mock.patch("konsole_aware.terminal.konsole.KonsoleTerminal.is_available", return_value=True), \
             mock.patch("konsole_aware.terminal.konsole.KonsoleTerminal.set_title", return_value=True) as mock_title, \
             mock.patch("konsole_aware.terminal.konsole.KonsoleTerminal.set_tab_color", return_value=True) as mock_color, \
             mock.patch("konsole_aware.terminal.konsole.KonsoleTerminal.set_badge", return_value=True) as mock_badge, \
             mock.patch("konsole_aware.terminal.dbus.call", return_value=""):

            res = refresh_all_tabs(quiet=True)
            self.assertEqual(res, 0)
            mock_title.assert_called_once()
            mock_color.assert_called_once_with("#4285f4")
            mock_badge.assert_called_once()

            # Test cmd_refresh
            args = argparse.Namespace(tab=None, service=None)
            cmd_res = cmd_refresh(args)
            self.assertEqual(cmd_res, 0)


if __name__ == "__main__":
    unittest.main()
