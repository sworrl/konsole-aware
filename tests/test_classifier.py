"""Tests for shell command classification."""

import unittest
from konsole_aware.tasks.classifier import CommandClassifier


class TestCommandClassifier(unittest.TestCase):
    def setUp(self):
        self.clf = CommandClassifier()

    def test_classify_agent(self):
        res = self.clf.classify("agy --dangerously-skip-permissions")
        self.assertEqual(res["category"], "agent")
        self.assertEqual(res["name"], "agy")
        self.assertTrue(res["is_agent"])

        res2 = self.clf.classify("claude --agent reviewer")
        self.assertEqual(res2["category"], "agent")
        self.assertEqual(res2["name"], "claude")

    def test_classify_transfer(self):
        res = self.clf.classify("rsync -avz /source/ user@remote:/backup")
        self.assertEqual(res["category"], "transfer")
        self.assertEqual(res["name"], "rsync")
        self.assertTrue(res["is_transfer"])
        self.assertIn("backup", res["detail"])

        res_scp = self.clf.classify("scp -r ./dist user@nas:/var/www")
        self.assertEqual(res_scp["category"], "transfer")
        self.assertEqual(res_scp["name"], "scp")

    def test_classify_download(self):
        res = self.clf.classify("curl -fsSL https://get.docker.com -o get-docker.sh")
        self.assertEqual(res["category"], "download")
        self.assertEqual(res["name"], "curl")
        self.assertIn("get.docker.com", res["detail"])

    def test_classify_build(self):
        res = self.clf.classify("cargo build --release")
        self.assertEqual(res["category"], "build")
        self.assertTrue(res["is_build"])
        self.assertIn("cargo build", res["name"])

        res_make = self.clf.classify("make -j8")
        self.assertEqual(res_make["category"], "build")

    def test_classify_sudo_and_env_prefix(self):
        res = self.clf.classify("sudo rsync -av /data /mnt")
        self.assertEqual(res["category"], "transfer")
        self.assertEqual(res["name"], "rsync")

        res_env = self.clf.classify("RUST_LOG=debug cargo test")
        self.assertEqual(res_env["category"], "build")
        self.assertIn("cargo test", res_env["name"])

    def test_classify_empty(self):
        res = self.clf.classify("")
        self.assertEqual(res["category"], "idle")


if __name__ == "__main__":
    unittest.main()
