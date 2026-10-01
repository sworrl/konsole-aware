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
