"""Tests for agy and claude agent hook handlers."""

import unittest
from konsole_aware.agents.agy import AgyHandler
from konsole_aware.agents.claude import ClaudeHandler


class TestAgentHandlers(unittest.TestCase):
    def setUp(self):
        self.agy = AgyHandler()
        self.claude = ClaudeHandler()

    def test_agy_pre_invocation(self):
        payload = {"conversationId": "test-123", "invocationNum": 1}
        res = self.agy.handle_event("PreInvocation", payload)
        self.assertIn("injectSteps", res)

    def test_agy_pre_tool_use_run_command(self):
        payload = {
            "conversationId": "test-123",
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "cargo test --all"}
            }
        }
        res = self.agy.handle_event("PreToolUse", payload)
        self.assertEqual(res.get("decision"), "allow")

    def test_agy_pre_tool_use_edit(self):
        payload = {
            "conversationId": "test-123",
            "toolCall": {
                "name": "replace_file_content",
                "args": {"TargetFile": "/path/to/main.py"}
            }
        }
        detail = self.agy._format_tool_detail("replace_file_content", payload["toolCall"]["args"])
        self.assertEqual(detail, "edit(main.py)")

    def test_agy_stop(self):
        payload = {"terminationReason": "model_stop", "fullyIdle": True}
        res = self.agy.handle_event("Stop", payload)
        self.assertEqual(res, {})

    def test_claude_hooks(self):
        # Prompt submit
        res1 = self.claude.handle_event("UserPromptSubmit", {})
        self.assertEqual(res1, {})

        # Tool use
        res2 = self.claude.handle_event("PreToolUse", {
            "tool": "Bash",
            "input": {"command": "npm run test"}
        })
        self.assertEqual(res2.get("decision"), "allow")

        # Stop
        res3 = self.claude.handle_event("Stop", {})
        self.assertEqual(res3, {})


if __name__ == "__main__":
    unittest.main()
