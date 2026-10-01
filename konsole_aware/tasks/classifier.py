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

        # Strip environment variables and sudo prefixes (e.g. 'sudo rsync', 'VAR=1 cargo build', 'sudo -E apt update')
        idx = 0
        while idx < len(tokens):
            tok = tokens[idx]
            if "=" in tok and not tok.startswith("-"):
                idx += 1
                continue
            if tok in ("sudo", "doas", "nohup", "time", "nice", "ionice", "strace"):
                idx += 1
                while idx < len(tokens) and tokens[idx].startswith("-"):
                    flag = tokens[idx]
                    idx += 1
                    if flag in ("-u", "-g", "-p", "-h", "-C") and idx < len(tokens):
                        idx += 1
                continue
            break

        effective_tokens = tokens[idx:] if idx < len(tokens) else tokens
        base_cmd = os.path.basename(effective_tokens[0]) if effective_tokens else "cmd"
        args = effective_tokens[1:] if len(effective_tokens) > 1 else []

        emojis = self.config.get("emojis", {})
        colors = self.config.get("colors", {})
        task_colors = self.config.get("task_colors", {})
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

        # 2. Package Managers & System Updates (apt, apt-get, nala, pacman, dnf, zypper, snap, etc.)
        pkg_managers = set(self.config.get("known_packages", [])) | {
            "apt", "apt-get", "dpkg", "nala", "aptitude", "pkcon",
            "pacman", "yay", "paru", "pamac",
            "dnf", "yum", "rpm",
            "zypper", "apk",
            "snap", "flatpak", "brew",
            "do-release-upgrade", "neon-upgrade"
        }
        if base_cmd in pkg_managers:
            action = ""
            targets = []
            for a in args:
                if a.startswith("-"):
                    if base_cmd in ("pacman", "yay", "paru", "pamac") and not action:
                        action = a
                    continue
                if not action:
                    action = a
                else:
                    targets.append(a)

            action_lower = action.lower()
            if action_lower in ("update", "check-update", "refresh", "-sy", "-syy"):
                pkg_action = "update"
                icon = "🔄"
                badge = "UPDT"
                detail = f"{base_cmd} update"
            elif action_lower in ("upgrade", "full-upgrade", "dist-upgrade", "-syu", "-syyu"):
                pkg_action = action_lower
                icon = "⬆️"
                badge = "UPGR"
                detail = f"{base_cmd} {action_lower}"
            elif action_lower in ("install", "add", "i", "-s"):
                pkg_action = "install"
                icon = "📥"
                badge = "INST"
                target_str = f" {', '.join(targets[:2])}" if targets else ""
                detail = f"{base_cmd} install{target_str}"
            elif action_lower in ("remove", "purge", "autoremove", "auto-remove", "uninstall", "-r", "-rns"):
                pkg_action = action_lower
                icon = "🗑️"
                badge = "RM"
                target_str = f" {', '.join(targets[:2])}" if targets else ""
                detail = f"{base_cmd} {action_lower}{target_str}"
            elif action_lower in ("clean", "autoclean", "-sc"):
                pkg_action = "clean"
                icon = "🧹"
                badge = "CLN"
                detail = f"{base_cmd} clean"
            else:
                pkg_action = action or "manage"
                icon = emojis.get("package", "📦")
                badge = "PKGS"
                target_str = f" {', '.join(targets[:2])}" if targets else ""
                detail = f"{base_cmd} {pkg_action}{target_str}"

            return {
                "category": "package",
                "name": f"{base_cmd} {pkg_action}",
                "detail": f"{icon} {detail}"[:30],
                "icon": icon,
                "task_dot": task_dots.get("package", "🟦"),
                "badge": badge,
                "color": task_colors.get("package", colors.get("busy", "#0284c7")),
                "is_agent": False,
                "is_transfer": False,
                "is_build": False,
                "is_test": False,
                "is_package": True,
            }

        # 2b. Remote shells, dev servers, log follows, and interactive programs
        special = self._classify_special(base_cmd, args, emojis, colors, task_dots)
        if special:
            return special

        # 3. File Transfers
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

    @staticmethod
    def _result(category: str, name: str, detail: str, icon: str, dot: str, badge: str, color: str) -> Dict[str, Any]:
        return {
            "category": category, "name": name, "detail": detail[:30], "icon": icon, "task_dot": dot,
            "badge": badge, "color": color, "is_agent": False, "is_transfer": False, "is_build": False,
            "is_test": False,
        }

    def _classify_special(self, base: str, args: List[str], emojis: Dict[str, Any], colors: Dict[str, Any],
                          dots: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        busy = colors.get("busy", "#f59e0b")
        positional = [a for a in args if not a.startswith("-")]

        # Remote shells: show the host ("ssh -p 2222 me@nas" -> "ssh nas")
        if base in ("ssh", "mosh", "et", "autossh"):
            host = ""
            skip_next = False
            for a in args:
                if skip_next:
                    skip_next = False
                    continue
                if a in ("-p", "-i", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-W", "-b", "-c", "-E", "-e", "-m", "-S", "-w"):
                    skip_next = True
                    continue
                if not a.startswith("-"):
                    host = a.split("@")[-1]
                    break
            return self._result("remote", base, f"{base} {host}".strip(), "🔐", dots.get("remote", "🟪"), "SSH",
                                colors.get("remote", busy))

        # Long-running dev servers
        script = positional[1] if len(positional) > 1 and positional[0] == "run" else (positional[0] if positional else "")
        is_server = (
            base in ("vite", "next", "nuxt", "uvicorn", "gunicorn", "hypercorn", "flask", "rails", "hugo", "jekyll",
                     "live-server", "http-server", "serve", "caddy", "nginx")
            or (base in ("npm", "pnpm", "yarn", "bun") and script.split(":")[0] in ("dev", "start", "serve", "preview"))
            or (base in ("python", "python3") and "http.server" in args)
            or (base in ("cargo",) and positional[:1] == ["watch"])
            or (base in ("php", "manage.py") and "runserver" in args)
            or (base == "docker" and positional[:2] == ["compose", "up"] and "-d" not in args)
        )
        if is_server:
            port = next((a.split("=", 1)[1] if "=" in a else "" for a in args if a.startswith("--port")), "")
            if not port and "--port" in args and args.index("--port") + 1 < len(args):
                port = args[args.index("--port") + 1]
            if not port and "http.server" in args:
                port = next((a for a in positional if a.isdigit()), "8000")
            if base in ("npm", "pnpm", "yarn", "bun"):
                label = " ".join([base] + positional[:2])
            elif "http.server" in args:
                label = "http.server"
            else:
                label = base
            return self._result("server", label, label + (f" :{port}" if port else ""), "🌐",
                                dots.get("server", "🟩"), "SRV", colors.get("server", busy))

        # Following logs / live monitors
        follows = (base in ("tail", "journalctl", "kubectl", "docker", "podman") and
                   any(a in ("-F", "--follow") or (a.startswith("-") and not a.startswith("--") and "f" in a[1:])
                       for a in args))
        if follows or base in ("htop", "btop", "top", "atop", "nvtop", "watch", "glances", "iotop", "bmon", "nethogs"):
            target = positional[-1] if positional and base != "watch" else (" ".join(positional[:2]) if positional else "")
            return self._result("monitor", base, f"{base} {os.path.basename(target)}".strip(), "👀",
                                dots.get("monitor", "⚪"), "LIVE", colors.get("monitor", "#64748b"))

        # Interactive programs: editors, pagers, REPLs. Quitting them is not a "task finished".
        if base in INTERACTIVE or (base in ("python", "python3", "node", "bun", "deno", "irb", "ghci", "lua")
                                   and not positional):
            target = os.path.basename(positional[0]) if positional else ""
            return self._result("interactive", base, f"{base} {target}".strip(), "✏️",
                                dots.get("interactive", "⚪"), "EDIT", colors.get("interactive", "#64748b"))
        return None


INTERACTIVE = {
    "vim", "nvim", "vi", "nano", "emacs", "hx", "helix", "micro", "kak", "joe", "mcedit", "ne",
    "less", "more", "most", "man", "info", "bat",
    "mc", "ranger", "nnn", "lf", "yazi", "vifm", "tig", "lazygit", "gitui", "lazydocker", "k9s",
    "ipython", "bpython", "ptpython", "psql", "mysql", "mariadb", "sqlite3", "redis-cli", "mongosh",
    "tmux", "screen", "zellij", "fzf", "sudoedit", "crontab", "visudo",
}

