"""Shell command classifier for task recognition and metadata extraction."""

import os
import shlex
from typing import Any, Dict, List, Optional
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

        # 1. Agents
        known_agents = self.config.get("known_agents", [])
        if base_cmd in known_agents or any(a in base_cmd for a in ("agy", "claude", "aider")):
            name = base_cmd
            sub = args[0] if args and not args[0].startswith("-") else ""
            detail = f"{name} {sub}".strip()
            return {
                "category": "agent",
                "name": name,
                "detail": detail,
                "icon": "🤖" if name != "agy" else "⚡",
                "badge": name.upper()[:4],
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": True,
                "is_transfer": False,
                "is_build": False,
            }

        # 2. File Transfers
        known_transfers = self.config.get("known_transfers", [])
        if base_cmd in known_transfers or base_cmd in ("rsync", "scp", "sftp", "rclone", "bcp"):
            # Preview destination or args
            target = args[-1] if args else ""
            preview = f"{base_cmd} -> {os.path.basename(target)}" if target else base_cmd
            return {
                "category": "transfer",
                "name": base_cmd,
                "detail": preview[:30],
                "icon": emojis.get("transfer", "📥"),
                "badge": "SYNC",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": True,
                "is_build": False,
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
                "badge": "DOWN",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": True,
                "is_build": False,
            }

        # 4. Builds & Compilations
        known_builds = self.config.get("known_builds", [])
        if base_cmd in known_builds:
            sub = args[0] if args and not args[0].startswith("-") else ""
            name = f"{base_cmd} {sub}".strip()
            return {
                "category": "build",
                "name": name,
                "detail": name[:30],
                "icon": emojis.get("build", "🔨"),
                "badge": "BLD",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": True,
            }

        # 5. VCS (git, hg, svn)
        if base_cmd in ("git", "hg", "svn"):
            sub = args[0] if args else "vcs"
            return {
                "category": "git",
                "name": f"{base_cmd} {sub}",
                "detail": f"{base_cmd} {sub}"[:30],
                "icon": emojis.get("git", "🐙"),
                "badge": sub.upper()[:4],
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": False,
            }

        # 6. Containers
        if base_cmd in ("docker", "podman", "kubectl", "helm"):
            sub = args[0] if args else ""
            return {
                "category": "container",
                "name": f"{base_cmd} {sub}".strip(),
                "detail": f"{base_cmd} {sub}"[:30],
                "icon": emojis.get("container", "🐳"),
                "badge": "CONT",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": False,
            }

        # 7. Archives
        if base_cmd in ("tar", "zip", "unzip", "gzip", "7z", "zstd"):
            return {
                "category": "archive",
                "name": base_cmd,
                "detail": base_cmd,
                "icon": emojis.get("archive", "📦"),
                "badge": "ARCH",
                "color": colors.get("busy", "#f59e0b"),
                "is_agent": False,
                "is_transfer": False,
                "is_build": False,
            }

        # 8. Generic Command
        short_cmd = " ".join([base_cmd] + args[:2])[:25]
        return {
            "category": "command",
            "name": base_cmd,
            "detail": short_cmd,
            "icon": emojis.get("generic", "⏳"),
            "badge": "CMD",
            "color": colors.get("busy", "#f59e0b"),
            "is_agent": False,
            "is_transfer": False,
            "is_build": False,
        }
