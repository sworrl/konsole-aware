"""Tests for agent identity detection, project snapping, shell hook ordering, and install merging."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from konsole_aware import cli, install
from konsole_aware.agents import identity
from konsole_aware.state.manager import StateManager
from konsole_aware.tabs.organizer import snap_order
from konsole_aware.tabs.title import TitleComposer
from konsole_aware.terminal.dbus import split_tabs

KEY = "test:/Sessions/1"


class TestIdentity(unittest.TestCase):
    def test_detect_from_argv(self):
        d = identity.detect_agent_from_argv
        self.assertEqual(d(["claude", "--dangerously-skip-permissions"]), "claude")
        self.assertEqual(d(["/home/u/.local/share/claude/versions/2.1.286"]), "claude")
        self.assertEqual(d(["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"]), "claude")
        self.assertEqual(d(["agy", "--dangerously-skip-permissions"]), "agy")
        self.assertEqual(d(["/home/u/.local/bin/agy"]), "agy")
        self.assertEqual(d(["npx", "@google/gemini-cli"]), "gemini")
        self.assertEqual(d(["env", "FOO=1", "codex"]), "codex")
        self.assertIsNone(d(["zsh"]))
        self.assertIsNone(d(["python3", "claude_helper.py"]))
        self.assertIsNone(d(["vim", "claude.md"]))

    def test_modes(self):
        self.assertEqual(identity.mode_from_argv(["agy", "--mode", "plan"]), "plan")
        self.assertEqual(identity.mode_from_argv(["agy", "--dangerously-skip-permissions"]),
                         "dangerously-skip-permissions")
        self.assertEqual(identity.mode_from_argv(["claude", "--permission-mode=acceptEdits"]), "acceptEdits")
        for mode in ("default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions"):
            self.assertIn(mode, identity.MODE_TAGS)

    def test_tags_distinguish_agents_and_user(self):
        tags = {identity.identity_tag(a) for a in ("claude", "agy", identity.USER)}
        self.assertEqual(len(tags), 3)
        self.assertEqual(identity.identity_tag(None), "👤 USER")

    def test_title_project_first(self):
        StateManager().clear_session(KEY)
        c = TitleComposer()
        t = c.compose(session_key=KEY, job_name="proj", task_category="agent", status="thinking",
                      detail="thinking…", agent="claude", mode="plan")
        self.assertTrue(t.startswith("[") and "proj]" in t.split(" ✳️")[0], t)
        self.assertLess(t.index("proj"), t.index("✳️ CLAUDE"))
        self.assertLess(t.index("✳️ CLAUDE"), t.index("📋 PLAN"))
        self.assertNotIn("🟣", t)  # identity replaces the generic agent dot
        u = c.compose(session_key=KEY, job_name="proj", task_category="idle", status="idle", agent=identity.USER)
        self.assertTrue(u.startswith("[") and "👤 USER" in u, u)

    def test_custom_title_order(self):
        StateManager().clear_session(KEY)
        c = TitleComposer()
        c.config["title_order"] = ["identity", "status", "job"]
        t = c.compose(session_key=KEY, job_name="proj", task_category="agent", status="thinking",
                      detail="thinking…", agent="claude", mode="plan")
        self.assertTrue(t.startswith("✳️ CLAUDE 🧠 thinking…"), t)
        self.assertIn("📋 PLAN", t)  # segments left out of a custom order are appended, not dropped

    def test_activity_tag(self):
        StateManager().clear_session(KEY)
        StateManager().set_session(KEY, ultracode=True, background={"workflows": 1, "agents": 2}, teammates=["a"])
        t = TitleComposer().compose(session_key=KEY, job_name="proj", task_category="agent", status="done",
                                    detail="ready", agent="claude")
        self.assertIn("🚀 ULTRA ⧗3 bg 👥1", t)
        StateManager().clear_session(KEY)


def tab(num, idx, project, agent=identity.USER, anchored=False):
    return {"session_num": str(num), "index": idx, "project": project, "agent": agent, "anchored": anchored,
            "panes": [str(num)]}


class TestSessionName(unittest.TestCase):
    def test_claude_user_name_only(self):
        with tempfile.TemporaryDirectory() as d:
            sessions = Path(d) / "sessions"
            sessions.mkdir()
            (sessions / "4242.json").write_text(json.dumps({"name": "MikuOS", "nameSource": "user"}))
            (sessions / "4343.json").write_text(json.dumps({"name": "konsole-aware-3e", "nameSource": "derived"}))
            with mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": d}):
                self.assertEqual(identity.session_name("claude", 4242), "MikuOS")
                self.assertIsNone(identity.session_name("claude", 4343))  # Claude's own folder-based name
                self.assertIsNone(identity.session_name("claude", 9999))
        self.assertIsNone(identity.session_name("claude", None))

    def test_agy_project_flag(self):
        with mock.patch.object(identity, "_read_argv", return_value=["agy", "--project", "MikuOS"]):
            self.assertEqual(identity.session_name("agy", 1), "MikuOS")
        with mock.patch.object(identity, "_read_argv", return_value=["agy", "--project=default-cli-project"]):
            self.assertIsNone(identity.session_name("agy", 1))
        with mock.patch.object(identity, "_read_argv", return_value=["agy"]):
            self.assertIsNone(identity.session_name("agy", 1))

    def test_name_is_the_project_in_the_title(self):
        key = "test-svc:/Sessions/77"
        state = StateManager()
        state.set_session(key, agent="claude", session_name="MikuOS", job_name=None)
        composer = TitleComposer()
        self.assertEqual(composer.get_job_name(session_key=key, cwd="/tmp"), "MikuOS")
        state.set_session(key, job_name="override")  # set-job still wins
        self.assertEqual(composer.get_job_name(session_key=key, cwd="/tmp"), "override")
        state.set_session(key, job_name=None, agent=None)  # agent gone: back to the folder
        self.assertNotEqual(composer.get_job_name(session_key=key, cwd="/tmp"), "MikuOS")
        state.clear_session(key)


class TestSnapOrder(unittest.TestCase):
    def order(self, tabs, **kw):
        return [t["session_num"] for t in snap_order(tabs, **kw)]

    def test_agents_of_same_project_become_adjacent(self):
        # A real-world layout: the "beacon" project has agy at index 3 and claude at index 6.
        tabs = [tab(3, 0, "/p/3d", "agy"), tab(9, 1, "/p/ka", "agy"), tab(13, 2, "/p/ka", "claude"),
                tab(4, 3, "/p/beacon", "agy"), tab(5, 4, "/p/deck", "agy"), tab(6, 5, "/p/media", "agy"),
                tab(12, 6, "/p/beacon", "claude"), tab(8, 7, "/p/ledger", "agy"), tab(10, 8, "/p/ledger", "claude")]
        self.assertEqual(self.order(tabs), ["3", "13", "9", "12", "4", "5", "6", "10", "8"])

    def test_user_sessions_join_cluster_after_agents(self):
        tabs = [tab(1, 0, "/p/a"), tab(2, 1, "/p/b", "agy"), tab(3, 2, "/p/a", "agy"), tab(4, 3, "/p/a", "claude")]
        self.assertEqual(self.order(tabs), ["4", "3", "1", "2"])
        self.assertEqual(self.order(tabs, include_user_sessions=False), ["1", "2", "4", "3"])

    def test_anchored_stay_first(self):
        tabs = [tab(1, 0, "/p/a", "agy"), tab(2, 1, "/p/b", anchored=True), tab(3, 2, "/p/a", "claude")]
        self.assertEqual(self.order(tabs), ["2", "3", "1"])

    def test_no_project_tabs_keep_place(self):
        tabs = [tab(1, 0, ""), tab(2, 1, "/p/a", "agy"), tab(3, 2, ""), tab(4, 3, "/p/a", "claude")]
        self.assertEqual(self.order(tabs), ["1", "4", "2", "3"])

    def test_split_tabs(self):
        self.assertEqual(split_tabs(["2", "9", "13"], ["(2)[2]", "(9)[9]", "(13)[13]"]), [["2"], ["9"], ["13"]])
        self.assertEqual(split_tabs(["5", "7", "8"], ["(5)[5|7]", "(8)[8]"]), [["5", "7"], ["8"]])
        self.assertEqual(split_tabs(["1", "2", "3"], ["(4){1|(6)[2|3]}"]), [["1", "2", "3"]])
        self.assertEqual(split_tabs(["1", "2"], []), [["1"], ["2"]])


class TestShellHookOrdering(unittest.TestCase):
    def setUp(self):
        StateManager().clear_session(KEY)

    def test_late_preexec_is_ignored(self):
        # precmd for command 5 finished before its (backgrounded) preexec ran.
        cli.main(["precmd", "--id", "5", "--shell-pid", "42", "0"])
        cli.main(["preexec", "--id", "5", "--shell-pid", "42", "--", "cargo build"])
        self.assertNotEqual(StateManager().get_session(KEY).get("status"), "busy")
        cli.main(["preexec", "--id", "6", "--shell-pid", "42", "--", "cargo build"])
        self.assertEqual(StateManager().get_session(KEY).get("status"), "busy")
        cli.main(["precmd", "--id", "6", "--shell-pid", "42", "1"])
        self.assertEqual(StateManager().get_session(KEY).get("status"), "error")

    def test_agent_exit_returns_to_user(self):
        cli.main(["preexec", "--id", "1", "--shell-pid", "42", "--", "claude"])
        self.assertEqual(StateManager().get_session(KEY).get("agent"), "claude")
        cli.main(["precmd", "--id", "1", "--shell-pid", "42", "0"])
        self.assertIsNone(StateManager().get_session(KEY).get("agent"))


class TestInstallMerge(unittest.TestCase):
    def test_claude_hooks_merge_and_strip(self):
        user_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-guard.sh"}]}
        settings = {"model": "x", "hooks": {"PreToolUse": [user_hook],
                                            "Stop": [{"hooks": [{"type": "command",
                                                                 "command": "/old/konsole-aware agent claude Stop"}]}]}}
        merged = install.merge_claude_hooks(settings, "/bin/konsole-aware")
        self.assertIn(user_hook, merged["hooks"]["PreToolUse"])
        self.assertEqual(len(merged["hooks"]["Stop"]), 1)  # old entry replaced, not duplicated
        for event in install.CLAUDE_EVENTS:
            self.assertTrue(any("agent claude " + event in h["command"]
                                for g in merged["hooks"][event] for h in g["hooks"]), event)
        again = install.merge_claude_hooks(merged, "/bin/konsole-aware")
        self.assertEqual(again, merged)  # idempotent
        removed = install.remove_claude_hooks(merged)
        self.assertEqual(removed, {"model": "x", "hooks": {"PreToolUse": [user_hook]}})

    def test_install_uninstall_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / "settings.json").write_text(json.dumps(
                {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify.sh"}]}]}}))
            (home / ".gemini" / "config").mkdir(parents=True)
            (home / ".gemini" / "config" / "hooks.json").write_text(json.dumps({"other": {"Stop": []}}))
            repo = Path(__file__).resolve().parent.parent
            out = []
            with mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(install.install(repo, home=home, build_menu=False, out=out.append), 0)
                agy = json.loads((home / ".gemini" / "config" / "hooks.json").read_text())
                self.assertIn("other", agy)
                self.assertNotIn("decision", json.dumps(agy))
                self.assertNotIn("PreToolUse", agy["konsole-aware"])
                self.assertIn("PostToolUse", agy["konsole-aware"])
                install.uninstall(home=home, out=out.append)
            settings = json.loads((home / ".claude" / "settings.json").read_text())
            self.assertEqual(settings, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify.sh"}]}]}})
            self.assertEqual(json.loads((home / ".gemini" / "config" / "hooks.json").read_text()), {"other": {"Stop": []}})


if __name__ == "__main__":
    unittest.main()


class TestQuietCategories(unittest.TestCase):
    def test_editor_exit_does_not_notify(self):
        StateManager().clear_session(KEY)
        with mock.patch("konsole_aware.cli.Notifier") as notifier:
            cli.main(["preexec", "--id", "7", "--shell-pid", "42", "--", "vim notes.md"])
            StateManager().set_session(KEY, start_time=0)  # pretend it ran for ages
            cli.main(["precmd", "--id", "7", "--shell-pid", "42", "0"])
        notifier.assert_not_called()
        self.assertNotEqual(StateManager().get_session(KEY).get("status"), "busy")
