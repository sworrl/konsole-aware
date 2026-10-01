"""Shell command classifier for task recognition and metadata extraction."""

import os
from typing import Any, Dict, List, Optional
from konsole_aware.agents import identity as agent_identity
from konsole_aware.agents.identity import detect_agent_from_argv
from konsole_aware.config import load_config


class CommandClassifier:
    """Classifies shell commands into categories (agents, transfers, builds, etc.)."""

    def __init__(self):
        self.config = load_config()

    def classify(self, cmd_line: str) -> Dict[str, Any]:
        """Parse command line and return metadata dict."""
        cmd_clean = cmd_line.strip()
        if not cmd_clean:
            return {
                "category": "idle",
                "name": "shell",
                "detail": "",
                "icon": self.config.get("emojis", {}).get("idle", "💤"),
                "badge": "IDLE",
                "color": self.config.get("colors", {}).get("idle", "#3b82f6"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": False,
            }

        # Tokenize safely
        try:
            tokens = shlex.split(cmd_clean)
        except Exception:
            tokens = cmd_clean.split()

        if not tokens:
            tokens = [cmd_clean]

        # Strip environment variables and sudo prefixes (e.g. 'sudo rsync', 'VAR=1 cargo build')
        idx = 0
        while idx < len(tokens):
            tok = tokens[idx]
            if "=" in tok and not tok.startswith("-"):
                idx += 1
                continue
            if tok in ("sudo", "doas", "nohup", "time", "nice", "ionice", "strace"):
                idx += 1
                continue
            break

        effective_tokens = tokens[idx:] if idx < len(tokens) else tokens
        base_cmd = os.path.basename(effective_tokens[0]) if effective_tokens else "cmd"
        args = effective_tokens[1:] if len(effective_tokens) > 1 else []

        emojis = self.config.get("emojis", {})
        colors = self.config.get("colors", {})

        task_dots = self.config.get("task_dots", {})

        # 1. Agents (exact process names, launchers like `npx claude-code`, versioned binaries)
        known_agents = self.config.get("known_agents", [])
        agent = detect_agent_from_argv(effective_tokens)
        if agent or base_cmd in known_agents:
            name = agent or base_cmd
            sub = args[0] if args and not args[0].startswith("-") else ""
            detail = f"{name} {sub}".strip()
            ident = agent_identity.identity(name)
            tab_mode = self.config.get("tab_color_mode", "agent")
            color = ident.get("color") if tab_mode in ("agent", "agent_status") else colors.get("busy", "#f59e0b")
            dot = ident.get("dot") or task_dots.get("agent", "🟣")
            icon = ident.get("icon") or ("🤖" if name != "agy" else "🪐")
            return {
                "category": "agent",
                "agent": name,
                "name": name,
                "detail": detail,
                "icon": icon,
                "task_dot": dot,
                "badge": ident.get("label", name.upper())[:4],
                "color": color,
                "is_agent": True,
                "is_transfer": False,
                "is_build": False,
                "is_test": False,
            }

        # 2. File Transfers
        known_transfers = self.config.get("known_transfers", [])
        if base_cmd in known_transfers or base_cmd in ("rsync", "scp", "sftp", "rclone", "bcp"):
            target = args[-1] if args else ""
            preview = f"{base_cmd} -> {os.path.basename(target)}" if target else base_cmd
            return {
                "category": "transfer",
                "name": base_cmd,
                "detail": preview[:30],
                "icon": emojis.get("transfer", "📥"),
                "task_dot": task_dots.get("transfer", "🟠"),
                "badge": "SYNC",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": True,
                "is_build": False,
                "is_test": False,
            }

        # 3. Downloads / Web fetches
        if base_cmd in ("curl", "wget", "aria2c", "axel", "yt-dlp"):
            url_part = next((a for a in args if "://" in a or "." in a), "")
            domain = url_part.split("/")[2] if "://" in url_part else url_part
            return {
                "category": "download",
                "name": base_cmd,
                "detail": f"{base_cmd} {domain}"[:30],
                "icon": "🌐",
                "task_dot": task_dots.get("download", "🟤"),
                "badge": "DOWN",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": True,
                "is_build": False,
                "is_test": False,
            }

        # 4. Tests
        first = args[0] if args else ""
        is_test = (
            base_cmd in ("pytest", "py.test", "jest", "vitest", "ctest", "tox", "nox", "mocha", "phpunit", "rspec")
            or (base_cmd in ("cargo", "npm", "pnpm", "yarn", "bun", "go", "mvn", "gradle", "dotnet", "mix", "deno")
                and first in ("test", "t", "nextest"))
            or (base_cmd in ("npm", "pnpm", "yarn", "bun") and first == "run" and len(args) > 1
                and args[1].split(":")[0] in ("test", "tests", "e2e"))
            or (base_cmd in ("make", "ninja") and first in ("test", "check", "tests"))
            or (base_cmd.startswith("python") and len(args) > 1 and first == "-m"
                and args[1] in ("pytest", "unittest", "tox", "nox"))
        )
        if is_test:
            sub = args[0] if args and not args[0].startswith("-") else ""
            name = f"{base_cmd} {sub}".strip()
            return {
                "category": "test",
                "name": name,
                "detail": name[:30],
                "icon": "🧪",
                "task_dot": task_dots.get("test", "🟢"),
                "badge": "TEST",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": False,
                "is_test": True,
            }

        # 5. Builds & Compilations
        known_builds = self.config.get("known_builds", [])
        if base_cmd in known_builds:
            sub = args[0] if args and not args[0].startswith("-") else ""
            name = f"{base_cmd} {sub}".strip()
            return {
                "category": "build",
                "name": name,
                "detail": name[:30],
                "icon": emojis.get("build", "🔨"),
                "task_dot": task_dots.get("build", "🟡"),
                "badge": "BLD",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": True,
                "is_test": False,
            }

        # 6. VCS (git, hg, svn)
        if base_cmd in ("git", "hg", "svn"):
            sub = args[0] if args else "vcs"
            return {
                "category": "git",
                "name": f"{base_cmd} {sub}",
                "detail": f"{base_cmd} {sub}"[:30],
                "icon": emojis.get("git", "🐙"),
                "task_dot": task_dots.get("git", "🔵"),
                "badge": sub.upper()[:4],
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": False,
                "is_test": False,
            }

        # 7. Containers
        if base_cmd in ("docker", "podman", "kubectl", "helm"):
            sub = args[0] if args else ""
            return {
                "category": "container",
                "name": f"{base_cmd} {sub}".strip(),
                "detail": f"{base_cmd} {sub}"[:30],
                "icon": emojis.get("container", "🐳"),
                "task_dot": task_dots.get("container", "🟦"),
                "badge": "CONT",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": False,
                "is_test": False,
            }

        # 8. Archives
        if base_cmd in ("tar", "zip", "unzip", "gzip", "7z", "zstd"):
            return {
                "category": "archive",
                "name": base_cmd,
                "detail": base_cmd,
                "icon": emojis.get("archive", "📦"),
                "task_dot": task_dots.get("archive", "📦"),
                "badge": "ARCH",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": False,
                "is_test": False,
            }

        # 9. Generic Command
        short_cmd = " ".join([base_cmd] + args[:2])[:25]
        return {
            "category": "command",
            "name": base_cmd,
            "detail": short_cmd,
            "icon": emojis.get("generic", "⏳"),
            "task_dot": task_dots.get("command", "⚪"),
            "badge": "CMD",
            "color": colors.get("busy", "#f59e0b"),
            "is_agent": False,
            "is_transfer": False,
            "is_build": False,
            "is_test": False,
        }
