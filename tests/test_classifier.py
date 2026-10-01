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
        self.assertEqual(res["color"], "#4285f4")
        self.assertEqual(res["task_dot"], "🔵")
        self.assertEqual(res["icon"], "🪐")

        res2 = self.clf.classify("claude --agent reviewer")
        self.assertEqual(res2["category"], "agent")
        self.assertEqual(res2["name"], "claude")
        self.assertEqual(res2["color"], "#d97757")
        self.assertEqual(res2["task_dot"], "🟠")
        self.assertEqual(res2["icon"], "✳️")
        self.assertNotEqual(res["color"], res2["color"])

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

        res_env = self.clf.classify("RUST_LOG=debug cargo build --release")
        self.assertEqual(res_env["category"], "build")
        self.assertIn("cargo build", res_env["name"])

        res_test = self.clf.classify("RUST_LOG=debug cargo test")
        self.assertEqual(res_test["category"], "test")
        self.assertIn("cargo test", res_test["name"])

    def test_classify_package_managers(self):
        # sudo apt-get update
        res_update = self.clf.classify("sudo apt-get update")
        self.assertEqual(res_update["category"], "package")
        self.assertEqual(res_update["name"], "apt-get update")
        self.assertEqual(res_update["icon"], "🔄")
        self.assertEqual(res_update["badge"], "UPDT")
        self.assertTrue(res_update["is_package"])

        # sudo -E apt-get update (with sudo flags)
        res_sudo_flag = self.clf.classify("sudo -E apt-get update")
        self.assertEqual(res_sudo_flag["category"], "package")
        self.assertEqual(res_sudo_flag["name"], "apt-get update")

        # sudo apt full-upgrade
        res_upgrade = self.clf.classify("sudo apt full-upgrade")
        self.assertEqual(res_upgrade["category"], "package")
        self.assertEqual(res_upgrade["name"], "apt full-upgrade")
        self.assertEqual(res_upgrade["icon"], "⬆️")
        self.assertEqual(res_upgrade["badge"], "UPGR")

        # sudo apt install nginx
        res_inst = self.clf.classify("sudo apt install nginx")
        self.assertEqual(res_inst["category"], "package")
        self.assertEqual(res_inst["name"], "apt install")
        self.assertIn("nginx", res_inst["detail"])
        self.assertEqual(res_inst["badge"], "INST")

        # pacman -Syu
        res_pacman = self.clf.classify("pacman -Syu")
        self.assertEqual(res_pacman["category"], "package")
        self.assertEqual(res_pacman["badge"], "UPGR")

        # snap refresh
        res_snap = self.clf.classify("snap refresh")
        self.assertEqual(res_snap["category"], "package")
        self.assertEqual(res_snap["badge"], "UPDT")

    def test_classify_empty(self):
        res = self.clf.classify("")
        self.assertEqual(res["category"], "idle")


if __name__ == "__main__":
    unittest.main()



class TestSpecialCategories(unittest.TestCase):
    def setUp(self):
        self.clf = CommandClassifier()

    def test_categories(self):
        cases = {
            "ssh -p 2222 me@nas": ("remote", "ssh nas"),
            "vim README.md": ("interactive", "vim README.md"),
            "npm run dev": ("server", "npm run dev"),
            "python3 -m http.server 9000": ("server", "http.server :9000"),
            "tail -f /var/log/syslog": ("monitor", "tail syslog"),
            "journalctl -fu nginx": ("monitor", "journalctl nginx"),
            "htop": ("monitor", "htop"),
            "python3": ("interactive", "python3"),
        }
        for cmd, (cat, detail) in cases.items():
            r = self.clf.classify(cmd)
            self.assertEqual((r["category"], r["detail"]), (cat, detail), cmd)

    def test_not_misclassified(self):
        self.assertEqual(self.clf.classify("python3 train.py")["category"], "command")
        self.assertEqual(self.clf.classify("cat latest.txt")["category"], "command")
        self.assertEqual(self.clf.classify("tail -n 5 log.txt")["category"], "command")
