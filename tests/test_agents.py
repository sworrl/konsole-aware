"""Tests for agy and claude agent hook handlers (hermetic: NullTerminal, temp state)."""

import io
import time
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from konsole_aware import cli
from konsole_aware.agents.agy import AgyHandler
from konsole_aware.agents.claude import ClaudeHandler
from konsole_aware.state.manager import StateManager
from konsole_aware.terminal import NullTerminal

KEY = "test:/Sessions/1"


class RecordingTerminal(NullTerminal):
    """NullTerminal that also records title/colour/badge calls."""

    def __init__(self):
        super().__init__()
        self.titles, self.colors, self.badges = [], [], []

    def set_title(self, title, role=0):
        self.titles.append(title)
        return True

    def set_tab_color(self, hex_color):
        self.colors.append(hex_color)
        return True

    def reset_tab_color(self):
        self.colors.append(None)
        return True

    def set_badge(self, text, enabled=True, icon=None):
        self.badges.append((text, enabled, icon))
        return True

    def is_focused(self):
        return True  # suppress desktop notifications in tests


class TestAgentHandlers(unittest.TestCase):
    def setUp(self):
        StateManager().clear_session(KEY)
        self.term = RecordingTerminal()
        self.agy = AgyHandler(term=self.term)
        self.claude = ClaudeHandler(term=self.term)

    # ------------------------------------------------------------------ agy
    def test_agy_pre_tool_use_never_returns_a_decision(self):
        payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": "cargo test --all"}},
                   "workspacePaths": ["/tmp/proj"]}
        res = self.agy.handle_event("PreToolUse", payload)
        self.assertNotIn("decision", res)  # "allow" would auto-approve every agy tool call
        self.assertEqual(res, {})
        self.assertIn("🪐 AGY", self.term.titles[-1])
        self.assertIn("cmd(cargo test --all)", self.term.titles[-1])

    def test_agy_edit_detail(self):
        detail = self.agy.format_tool_detail("replace_file_content", {"TargetFile": "/path/to/main.py"})
        self.assertEqual(detail, "edit(main.py)")

    def test_agy_question_waits(self):
        self.agy.handle_event("PreToolUse", {"toolCall": {"name": "ask_question", "args": {}}})
        self.assertEqual(StateManager().get_session(KEY)["status"], "waiting")
        self.assertEqual(self.term.colors[-1], "#8b5cf6")

    def test_agy_stop(self):
        self.agy.handle_event("PreInvocation", {"invocationNum": 1})
        res = self.agy.handle_event("Stop", {"terminationReason": "model_stop", "fullyIdle": True})
        self.assertEqual(res, {})
        sess = StateManager().get_session(KEY)
        self.assertEqual(sess["status"], "done")
        self.assertIn("ready", self.term.titles[-1])

    def test_agy_stop_error(self):
        self.agy.handle_event("Stop", {"terminationReason": "max_steps_exceeded"})
        self.assertEqual(StateManager().get_session(KEY)["status"], "error")

    # --------------------------------------------------------------- claude
    def test_claude_reads_real_payload_keys(self):
        self.claude.handle_event("PreToolUse", {
            "tool_name": "Bash", "tool_input": {"command": "npm run test"},
            "permission_mode": "default", "cwd": "/tmp",
        })
        self.assertIn("Bash(npm run test)", self.term.titles[-1])
        self.assertIn("✳️ CLAUDE", self.term.titles[-1])

    def test_claude_permission_modes_are_shown(self):
        for mode, tag in (("plan", "📋 PLAN"), ("acceptEdits", "✏️ EDITS"), ("auto", "🤖 AUTO"),
                          ("dontAsk", "🔕 DONT-ASK"), ("bypassPermissions", "⚡ BYPASS")):
            self.claude.handle_event("UserPromptSubmit", {"permission_mode": mode, "cwd": "/tmp"})
            self.assertIn(tag, self.term.titles[-1], mode)
        self.claude.handle_event("UserPromptSubmit", {"permission_mode": "default", "cwd": "/tmp"})
        self.assertNotIn("PLAN", self.term.titles[-1])

    def test_claude_full_lifecycle(self):
        base = {"cwd": "/tmp", "permission_mode": "default"}
        steps = [
            ("SessionStart", {"source": "startup"}, "idle"),
            ("UserPromptSubmit", {"prompt": "hi"}, "thinking"),
            ("PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "/a/b.py"}}, "tool"),
            ("PostToolUse", {"tool_name": "Read"}, "thinking"),
            ("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "rm x"}}, "waiting"),
            ("Notification", {"notification_type": "permission_prompt", "message": "m"}, "waiting"),
            ("SubagentStart", {"agent_id": "a1", "agent_type": "Explore"}, "subagents"),
            ("SubagentStop", {"agent_id": "a1", "agent_type": "Explore"}, "thinking"),
            ("PreCompact", {"trigger": "auto"}, "compacting"),
            ("PostCompact", {"trigger": "auto"}, "thinking"),
            ("PostToolUseFailure", {"tool_name": "Bash", "error": "x"}, "thinking"),
            ("Stop", {}, "done"),
            ("Notification", {"notification_type": "idle_prompt"}, "done"),
            ("UserPromptSubmit", {}, "thinking"),
            ("StopFailure", {"error": "overloaded"}, "error"),
        ]
        for event, extra, expected in steps:
            res = self.claude.handle_event(event, {**base, **extra})
            self.assertEqual(res, {}, event)
            self.assertEqual(StateManager().get_session(KEY).get("agent_status"), expected, event)
        self.assertIn("failed: overloaded", self.term.titles[-1])

    def test_claude_session_end_reverts_to_user(self):
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        self.claude.handle_event("SessionEnd", {"reason": "prompt_input_exit", "cwd": "/tmp"})
        sess = StateManager().get_session(KEY)
        self.assertIsNone(sess.get("agent"))
        self.assertIn("👤 USER", self.term.titles[-1])

    def test_claude_ask_user_question_and_plan(self):
        self.claude.handle_event("PreToolUse", {"tool_name": "AskUserQuestion", "tool_input": {}, "cwd": "/tmp"})
        self.assertIn("question", self.term.titles[-1])
        self.claude.handle_event("PreToolUse", {"tool_name": "ExitPlanMode", "tool_input": {}, "cwd": "/tmp",
                                                "permission_mode": "plan"})
        self.assertIn("plan ready", self.term.titles[-1])
        self.assertIn("📋 PLAN", self.term.titles[-1])

    def test_tool_detail_formats(self):
        f = self.claude.format_tool_detail
        self.assertEqual(f("Edit", {"file_path": "/x/y/main.rs"}), "Edit(main.rs)")
        self.assertEqual(f("Grep", {"pattern": "TODO"}), "Grep(TODO)")
        self.assertEqual(f("WebFetch", {"url": "https://example.com/a"}), "WebFetch(example.com)")
        self.assertEqual(f("mcp__github__create_issue", {}), "mcp:github.create_issue")
        self.assertTrue(f("Agent", {"subagent_type": "Explore", "description": "find"}).startswith("Agent(Explore"))

    def test_turn_duration_spans_tools(self):
        with mock.patch("konsole_aware.agents.base.time.time", return_value=1000.0):
            self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        with mock.patch("konsole_aware.agents.base.time.time", return_value=1030.0):
            self.claude.handle_event("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/tmp"})
        with mock.patch("konsole_aware.agents.base.time.time", return_value=1065.0):
            self.assertEqual(self.claude.turn_duration(), "1m 5s")


class TestAgentCli(unittest.TestCase):
    def _run(self, argv, stdin):
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_claude_hook_prints_nothing(self):
        # UserPromptSubmit stdout is injected into the model context: must stay empty.
        code, out = self._run(["agent", "claude", "UserPromptSubmit"], json.dumps({"cwd": "/tmp", "prompt": "x"}))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_agy_hook_prints_empty_json(self):
        code, out = self._run(["agent", "agy", "PreToolUse"], json.dumps({"toolCall": {"name": "view_file"}}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {})

    def test_hook_survives_garbage(self):
        code, out = self._run(["agent", "claude", "PreToolUse"], "not json [")
        self.assertEqual((code, out), (0, ""))
        code, out = self._run(["agent", "agy", "Stop"], "[1,2,3]")
        self.assertEqual((code, json.loads(out)), (0, {}))


if __name__ == "__main__":
    unittest.main()


class TestSafeguardBlocks(unittest.TestCase):
    """Claude's safeguards false-positive blocks get their own 🛡️ BLOCKED state."""

    BLOCK = ("API Error: Opus 4.8's safeguards flagged this message. Our intentionally broad safeguards allow us "
             "to deliver more capabilities faster, but can sometimes flag legitimate cybersecurity work. Apply to "
             "the Cyber Verification Program to reduce these interruptions.\n\nDetails: `[cyber]`")

    def setUp(self):
        StateManager().clear_session(KEY)
        self.term = RecordingTerminal()
        self.claude = ClaudeHandler(term=self.term)

    def _transcript(self, entries):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write("\n".join(json.dumps(e) for e in entries))
        f.close()
        return f.name

    def test_stop_failure_block(self):
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        self.claude.handle_event("StopFailure", {"cwd": "/tmp", "error": "invalid_request",
                                                 "last_assistant_message": self.BLOCK})
        sess = StateManager().get_session(KEY)
        self.assertEqual(sess["status"], "blocked")
        self.assertEqual(sess["safeguard_block"], "cyber")
        self.assertIn("🛡️", self.term.titles[-1])
        self.assertIn("[cyber]", self.term.titles[-1])
        self.assertEqual(self.term.colors[-1], "#db2777")
        self.assertIn("BLOCKED", self.term.badges[-1][0])

    def test_stop_failure_other_error_is_not_block(self):
        self.claude.handle_event("StopFailure", {"cwd": "/tmp", "error": "server_error"})
        self.assertEqual(StateManager().get_session(KEY)["status"], "error")

    def test_stop_with_block_message(self):
        self.claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message": self.BLOCK})
        self.assertEqual(StateManager().get_session(KEY)["status"], "blocked")

    def test_reply_discussing_safeguards_is_not_a_block(self):
        self.claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message":
                                          "Done. The detector now recognises when safeguards flagged this message."})
        self.assertEqual(StateManager().get_session(KEY)["status"], "done")

    def test_transcript_refusal_detected(self):
        path = self._transcript([
            {"type": "user", "message": {"role": "user", "content": "do the thing"}},
            {"type": "assistant", "isApiErrorMessage": True, "error": "invalid_request",
             "message": {"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber"},
                         "content": [{"type": "text", "text": self.BLOCK}]}},
        ])
        self.claude.handle_event("StopFailure", {"cwd": "/tmp", "error": "invalid_request", "transcript_path": path})
        self.assertEqual(StateManager().get_session(KEY)["safeguard_block"], "cyber")

    def test_old_turn_block_is_ignored(self):
        path = self._transcript([
            {"type": "assistant", "message": {"stop_reason": "refusal", "content": [{"type": "text", "text": self.BLOCK}]}},
            {"type": "user", "message": {"role": "user", "content": "try again"}},
            {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}},
        ])
        self.claude.handle_event("Stop", {"cwd": "/tmp", "transcript_path": path, "last_assistant_message": "ok"})
        self.assertEqual(StateManager().get_session(KEY)["status"], "done")

    def test_auto_model_switch_is_flagged_but_keeps_working(self):
        path = self._transcript([
            {"type": "user", "message": {"role": "user", "content": "scan my router"}},
            {"type": "assistant", "message": {"stop_reason": "refusal",
                                              "stop_details": {"type": "refusal", "category": "cyber"},
                                              "content": [{"type": "fallback", "from": {"model": "claude-fable-5-1"},
                                                           "to": {"model": "claude-opus-4-8"}}]}},
        ])
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        self.claude.handle_event("PostModelSwitch", {"cwd": "/tmp", "source": "auto", "transcript_path": path,
                                                     "from_model": "claude-fable-5-1", "to_model": "claude-opus-4-8"})
        self.assertEqual(StateManager().get_session(KEY)["agent_status"], "thinking")
        self.assertIn("flagged [cyber]: Fable 5.1 → Opus 4.8", self.term.titles[-1])
        self.claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message": "finished"})
        self.assertIn("🛡️ on Opus 4.8", self.term.titles[-1])
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        self.claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message": "finished"})
        self.assertNotIn("🛡️", self.term.titles[-1])

    def test_overload_switch_is_not_flagged(self):
        self.claude.handle_event("PostModelSwitch", {"cwd": "/tmp", "source": "auto",
                                                     "from_model": "claude-opus-5-5", "to_model": "claude-sonnet-5-5"})
        self.assertFalse(StateManager().get_session(KEY).get("safeguard_switch"))
        self.assertIn("switched: Opus 5.5 → Sonnet 5.5", self.term.titles[-1])

    def test_manual_model_switch_is_not_flagged(self):
        self.claude.handle_event("PostModelSwitch", {"cwd": "/tmp", "source": "command",
                                                     "from_model": "claude-opus-5-5", "to_model": "claude-fable-5-1"})
        self.assertFalse(StateManager().get_session(KEY).get("safeguard_switch"))


class TestWatchdogAndLimits(unittest.TestCase):
    def setUp(self):
        StateManager().clear_session(KEY)
        self.term = RecordingTerminal()
        self.claude = ClaudeHandler(term=self.term)

    def _transcript(self, entries):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write("\n".join(json.dumps(e) for e in entries))
        f.close()
        return f.name

    def test_usage_limit_stop(self):
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        self.claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message":
                                          "You've hit your weekly limit · resets Oct 1, 2am (America/New_York)"})
        sess = StateManager().get_session(KEY)
        self.assertEqual(sess["status"], "limited")
        self.assertIn("weekly limit · resets Oct 1, 2am", self.term.titles[-1])
        self.assertEqual(self.term.colors[-1], "#f97316")

    def test_model_limit_stop(self):
        self.claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message":
                                          "You've reached your Fable limit. Run /usage-credits to continue."})
        self.assertEqual(StateManager().get_session(KEY)["status"], "limited")

    def test_watchdog_detects_interrupt(self):
        from konsole_aware.agents import watchdog
        now = time.time()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now + 1))
        path = self._transcript([
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "user", "timestamp": stamp,
             "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
        ])
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp", "transcript_path": path})
        self.claude.handle_event("PreToolUse", {"cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "x"},
                                                "transcript_path": path})
        self.assertFalse(watchdog.check_once(KEY, "claude", StateManager(), term=self.term))
        self.assertEqual(StateManager().get_session(KEY)["status"], "idle")
        self.assertIn("interrupted", self.term.titles[-1])

    def test_watchdog_ignores_old_interrupt(self):
        from konsole_aware.agents import watchdog
        path = self._transcript([
            {"type": "user", "timestamp": "2020-01-01T00:00:00.000Z",
             "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
        ])
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp", "transcript_path": path})
        self.assertTrue(watchdog.check_once(KEY, "claude", StateManager(), term=self.term))
        self.assertEqual(StateManager().get_session(KEY)["agent_status"], "thinking")

    def test_watchdog_agent_exit(self):
        from konsole_aware.agents import watchdog
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        StateManager().set_session(KEY, agent_pid=999999999)
        self.assertFalse(watchdog.check_once(KEY, "claude", StateManager(), term=self.term))
        self.assertIsNone(StateManager().get_session(KEY).get("agent"))
        self.assertIn("👤 USER", self.term.titles[-1])


class TestWatchdogTurnEnd(unittest.TestCase):
    def test_turn_duration_marks_done(self):
        from konsole_aware.agents import watchdog
        import tempfile
        StateManager().clear_session(KEY)
        term = RecordingTerminal()
        claude = ClaudeHandler(term=term)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 1))
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write("\n".join(json.dumps(e) for e in [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Want me to continue?"}]}},
            {"type": "system", "subtype": "turn_duration", "timestamp": stamp},
        ]))
        f.close()
        claude.handle_event("UserPromptSubmit", {"cwd": "/tmp", "transcript_path": f.name})
        self.assertFalse(watchdog.check_once(KEY, "claude", StateManager(), term=term))
        self.assertEqual(StateManager().get_session(KEY)["status"], "done")


class TestDistinctAgentColors(unittest.TestCase):
    """Verify that Claude, AGY, and other agents receive distinct colors and dots."""

    def setUp(self):
        StateManager().clear_session(KEY)
        self.term = RecordingTerminal()

    def test_claude_and_agy_have_different_tab_colors(self):
        agy = AgyHandler(term=self.term)
        agy.handle_event("PreInvocation", {"workspacePaths": ["/tmp/proj"]})
        agy_color = self.term.colors[-1]
        self.assertEqual(agy_color, "#4285f4")  # AGY electric blue

        StateManager().clear_session(KEY)
        term_claude = RecordingTerminal()
        claude = ClaudeHandler(term=term_claude)
        claude.handle_event("UserPromptSubmit", {"cwd": "/tmp/proj"})
        claude_color = term_claude.colors[-1]
        self.assertEqual(claude_color, "#d97757")  # Claude terracotta

        # Ensure they are completely distinct
        self.assertNotEqual(agy_color, claude_color)

    def test_claude_and_agy_have_distinct_dots(self):
        agy = AgyHandler(term=self.term)
        agy.handle_event("PreInvocation", {"workspacePaths": ["/tmp/proj"]})
        self.assertIn("🔵", self.term.titles[-1])
        self.assertIn("🪐 AGY", self.term.titles[-1])

        StateManager().clear_session(KEY)
        term_claude = RecordingTerminal()
        claude = ClaudeHandler(term=term_claude)
        claude.handle_event("UserPromptSubmit", {"cwd": "/tmp/proj"})
        self.assertIn("🟠", term_claude.titles[-1])
        self.assertIn("✳️ CLAUDE", term_claude.titles[-1])



class TestModelEffort(unittest.TestCase):
    def setUp(self):
        StateManager().clear_session(KEY)
        self.term = RecordingTerminal()
        self.claude = ClaudeHandler(term=self.term)

    def test_session_start_model_and_env_effort(self):
        with mock.patch.dict("os.environ", {"CLAUDE_EFFORT": "high"}):
            self.claude.handle_event("SessionStart", {"cwd": "/tmp", "source": "startup", "model": "claude-fable-5-1"})
        self.assertIn("💎 Fable 5.1·hi", self.term.titles[-1])
        self.assertIn("Fable 5.1", self.term.badges[-1][0])

    def test_model_switch_updates_tag(self):
        self.claude.handle_event("SessionStart", {"cwd": "/tmp", "model": "claude-fable-5-1"})
        self.claude.handle_event("PostModelSwitch", {"cwd": "/tmp", "source": "command",
                                                     "from_model": "claude-fable-5-1", "to_model": "claude-opus-5-5"})
        self.assertIn("Opus 5.5", self.term.titles[-1])
        self.assertNotIn("Fable", self.term.titles[-1])

    def test_model_from_transcript(self):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write(json.dumps({"type": "assistant", "effort": "max",
                            "message": {"model": "claude-opus-5-5", "content": []}}) + "\n")
        f.close()
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CLAUDE_EFFORT", None)
            self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp", "transcript_path": f.name})
        self.assertIn("Opus 5.5·max", self.term.titles[-1])


class TestUiThrottle(unittest.TestCase):
    def test_tool_churn_is_throttled_and_state_changes_are_not(self):
        StateManager().clear_session(KEY)
        term = RecordingTerminal()
        claude = ClaudeHandler(term=term)
        claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        n = len(term.titles)
        for i in range(5):
            claude.handle_event("PreToolUse", {"cwd": "/tmp", "tool_name": "Read", "tool_input": {"file_path": f"/f{i}"}})
        self.assertEqual(len(term.titles), n + 1)  # first tool title only; the rest within 1 s are skipped
        claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message": "done"})
        self.assertIn("ready", term.titles[-1])
        colors = len(term.colors)
        claude.handle_event("Notification", {"cwd": "/tmp", "notification_type": "idle_prompt"})
        self.assertEqual(len(term.colors), colors)  # nothing re-sent when nothing changed


class TestUltracodeAndBackground(unittest.TestCase):
    def setUp(self):
        StateManager().clear_session(KEY)
        self.term = RecordingTerminal()
        self.claude = ClaudeHandler(term=self.term)

    def _transcript(self, entries):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write("\n".join(json.dumps(e) for e in entries))
        f.close()
        return f.name

    def _ts(self, offset=0):
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + offset))

    def test_ultracode_on_off(self):
        on = self._transcript([
            {"type": "user", "timestamp": self._ts(), "message": {"role": "user", "content":
             "<command-name>/effort</command-name>\n<command-message>effort</command-message>\n<command-args>ultracode</command-args>"}},
            {"type": "user", "timestamp": self._ts(), "message": {"role": "user", "content":
             "<local-command-stdout>Ultracode on (this session only): dynamic workflows on every task.</local-command-stdout>"}},
        ])
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp", "transcript_path": on})
        self.assertTrue(StateManager().get_session(KEY).get("ultracode"))
        self.assertIn("🚀 ULTRA", self.term.titles[-1])
        off = self._transcript([{"type": "attachment", "timestamp": self._ts(1), "attachment": {"type": "ultra_effort_exit"}}])
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp", "transcript_path": off})
        self.assertFalse(StateManager().get_session(KEY).get("ultracode"))
        self.assertNotIn("ULTRA", self.term.titles[-1])

    def test_typed_word_is_not_ultracode(self):
        path = self._transcript([{"type": "user", "message": {"role": "user", "content": "we're not aware of ultracode"}},
                                 {"type": "attachment", "attachment": {"type": "workflow_keyword_request"}}])
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp", "transcript_path": path})
        self.assertFalse(StateManager().get_session(KEY).get("ultracode"))

    def test_stale_ultracode_ignored_after_resume(self):
        path = self._transcript([{"type": "attachment", "timestamp": "2020-01-01T00:00:00.000Z",
                                  "attachment": {"type": "ultra_effort_enter", "reminderType": "full"}}])
        self.claude.handle_event("SessionStart", {"cwd": "/tmp", "source": "resume", "transcript_path": path})
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp", "transcript_path": path})
        self.assertFalse(StateManager().get_session(KEY).get("ultracode"))

    def test_turn_with_background_work_is_not_done(self):
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        self.claude.handle_event("PostToolUse", {"cwd": "/tmp", "tool_name": "Workflow",
                                                 "tool_response": {"status": "async_launched", "taskId": "w123",
                                                                   "taskType": "local_workflow", "workflowName": "audit"}})
        self.assertEqual(StateManager().get_session(KEY)["background"], {"workflow": 1})
        self.claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message": "launched",
                                          "background_tasks": [
                                              {"id": "w123", "type": "workflow", "status": "running", "name": "audit"},
                                              {"id": "bq1", "type": "shell", "status": "running", "command": "make"}],
                                          "session_crons": []})
        sess = StateManager().get_session(KEY)
        self.assertEqual(sess["agent_status"], "background")
        self.assertIn("1 workflow, 1 shell running", self.term.titles[-1])
        # completion notification arrives as a new prompt, then the turn ends with nothing in flight
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp", "prompt":
                                 "<task-notification><task-id>w123</task-id><status>completed</status></task-notification>"})
        self.assertEqual(StateManager().get_session(KEY)["background"], {"shell": 1})
        self.claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message": "all done",
                                          "background_tasks": [], "session_crons": []})
        self.assertEqual(StateManager().get_session(KEY)["agent_status"], "done")

    def test_workflow_agents_survive_main_stop(self):
        self.claude.handle_event("SubagentStart", {"cwd": "/tmp", "agent_id": "a1", "agent_type": "workflow-subagent"})
        self.claude.handle_event("Stop", {"cwd": "/tmp", "last_assistant_message": "x",
                                          "background_tasks": [{"id": "w9", "type": "workflow", "status": "running"}]})
        self.assertEqual(StateManager().get_session(KEY).get("subagents"), ["a1"])

    def test_subagent_tool_chatter_is_cheap(self):
        self.claude.handle_event("UserPromptSubmit", {"cwd": "/tmp"})
        n = len(self.term.titles)
        for i in range(20):
            self.claude.handle_event("PreToolUse", {"cwd": "/tmp", "agent_id": "a1", "agent_type": "workflow-subagent",
                                                    "tool_name": "Read", "tool_input": {"file_path": f"/f{i}"}})
        self.assertLessEqual(len(self.term.titles) - n, 1)
