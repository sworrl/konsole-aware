"""CLI dispatcher and command-line interface for konsole-aware."""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from konsole_aware.config import load_config, save_config, DEFAULT_CONFIG
from konsole_aware.terminal import get_terminal
from konsole_aware.terminal.konsole import KonsoleTerminal
from konsole_aware.state.manager import StateManager
from konsole_aware.tasks.classifier import CommandClassifier
from konsole_aware.notify.notifier import Notifier
from konsole_aware.agents.agy import AgyHandler
from konsole_aware.agents.claude import ClaudeHandler
from konsole_aware.daemon.watcher import TabWatcher


def cmd_agent(args: argparse.Namespace) -> int:
    """Handle AI agent hooks (agy, claude)."""
    # Read payload from stdin if available
    payload: Dict[str, Any] = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read().strip()
            if raw:
                payload = json.loads(raw)
        except Exception:
            payload = {}

    agent_name = args.agent.lower()
    event_name = args.event

    if agent_name in ("agy", "antigravity"):
        handler = AgyHandler()
        res = handler.handle_event(event_name, payload)
        sys.stdout.write(json.dumps(res))
        sys.stdout.flush()
        return 0

    elif agent_name == "claude":
        handler = ClaudeHandler()
        res = handler.handle_event(event_name, payload)
        sys.stdout.write(json.dumps(res))
        sys.stdout.flush()
        return 0

    else:
        sys.stderr.write(f"Unknown agent: {agent_name}\n")
        return 1


def cmd_shell_preexec(args: argparse.Namespace) -> int:
    """Invoked before a shell command executes."""
    cmd_str = args.command
    if not cmd_str or not cmd_str.strip():
        return 0

    classifier = CommandClassifier()
    term = get_terminal()
    state_mgr = StateManager()
    session_id = term.get_session_id()

    classified = classifier.classify(cmd_str)
    if classified["category"] == "idle":
        return 0

    # Save initial state
    orig_title = getattr(term, "get_title", lambda r: "")(0)
    orig_color = getattr(term, "get_tab_color", lambda: "")()

    state_mgr.start_task(
        session_id=session_id,
        task_name=classified["name"],
        task_type=classified["category"],
        detail=classified["detail"],
        original_title=orig_title,
        original_color=orig_color,
    )

    # Update terminal visuals
    icon = classified["icon"]
    name = classified["name"]
    detail = classified["detail"]
    title_str = f"{icon} [{name}] {detail}".strip()

    term.set_title(title_str, role=0)
    term.set_tab_color(classified["color"])
    term.set_badge(classified["badge"], enabled=True)
    return 0


def cmd_shell_precmd(args: argparse.Namespace) -> int:
    """Invoked right before shell prompt redisplay (after command finishes)."""
    exit_code = args.exit_code
    term = get_terminal()
    state_mgr = StateManager()
    config = load_config()
    notifier = Notifier()
    session_id = term.get_session_id()

    sess = state_mgr.get_session(session_id)
    if not sess or sess.get("status") != "busy":
        return 0

    status = "done" if exit_code == 0 else "error"
    finished = state_mgr.finish_task(session_id, exit_code=exit_code, status=status)

    duration = finished.get("duration", 0.0)
    min_dur = config.get("min_duration_seconds", 3.0)
    task_name = sess.get("task_name", "task")
    task_type = sess.get("task_type", "command")

    mins, secs = divmod(int(duration), 60)
    dur_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    colors = config.get("colors", {})

    if exit_code == 0:
        term.set_title(f"✅ [Done {dur_str}] {task_name}", role=0)
        term.set_tab_color(colors.get("done", "#10b981"))
        term.set_badge("DONE", enabled=True)
    else:
        term.set_title(f"❌ [Exit {exit_code} ({dur_str})] {task_name}", role=0)
        term.set_tab_color(colors.get("error", "#ef4444"))
        term.set_badge("ERR", enabled=True)

    # Decide whether to notify
    is_notifiable = duration >= min_dur or task_type in ("agent", "transfer", "build", "download")
    if is_notifiable:
        unfocused_only = config.get("alert_only_unfocused", True)
        if not unfocused_only or not term.is_focused():
            sess_num = getattr(term, "get_session_num", lambda: None)()
            sess_label = f"Tab {sess_num}" if sess_num is not None else "Terminal"
            if exit_code == 0:
                notifier.notify(
                    title=f"Task Finished: {task_name}",
                    message=f"Completed in {dur_str} in {sess_label}",
                    urgency="normal",
                    is_error=False,
                    play_sound=True,
                )
            else:
                notifier.notify(
                    title=f"Task Failed: {task_name}",
                    message=f"Failed with exit code {exit_code} in {dur_str} ({sess_label})",
                    urgency="critical",
                    is_error=True,
                    play_sound=True,
                )

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List all Konsole tabs, sessions, foreground processes, and statuses."""
    watcher = TabWatcher()
    watcher.tick()
    rows = watcher.get_dashboard_data()

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("No active Konsole sessions detected.")
        return 0

    print("\n  🔍 Konsole Aware - Active Tab Status")
    print("  " + "─" * 78)
    fmt = "  {:<4} {:<6} {:<4} {:<24} {:<18} {:<12}"
    print(fmt.format("WIN", "TAB", "ACT", "TITLE", "STATUS", "DURATION"))
    print("  " + "─" * 78)

    for r in rows:
        title_disp = r["title"][:22]
        status_disp = r["status"][:16]
        print(fmt.format(
            r["window"],
            r["session_num"],
            r["active"],
            title_disp,
            status_disp,
            r["duration"],
        ))

    print("  " + "─" * 78 + "\n")
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    """Switch Konsole view to a specific session."""
    sess_target = str(args.session)
    term = KonsoleTerminal(session_path=f"/Sessions/{sess_target}")
    if term.switch_to():
        print(f"Switched to Konsole tab {sess_target}.")
        return 0
    print(f"Failed to switch to session {sess_target}.", file=sys.stderr)
    return 1


def cmd_set_title(args: argparse.Namespace) -> int:
    """Set tab title directly."""
    term = get_terminal()
    term.set_title(args.title, role=0)
    print(f"Title set: {args.title}")
    return 0


def cmd_set_color(args: argparse.Namespace) -> int:
    """Set tab color directly."""
    term = get_terminal()
    term.set_tab_color(args.color)
    print(f"Color set: {args.color}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Reset tab title and color."""
    term = get_terminal()
    term.reset_tab_color()
    term.set_title("", role=0)
    term.set_badge("", enabled=False)
    print("Reset tab appearance.")
    return 0


def cmd_alert(args: argparse.Namespace) -> int:
    """Send test alert and notification."""
    notifier = Notifier()
    notifier.notify(
        title=args.title,
        message=args.message,
        urgency="normal",
        is_error=args.error,
        play_sound=True,
    )
    print("Alert sent.")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Live interactive watcher in the terminal."""
    watcher = TabWatcher(poll_interval=args.interval)
    print("\033[2J\033[H", end="")  # Clear screen
    print("Starting Konsole Aware live monitor. Press Ctrl+C to exit.\n")
    try:
        while True:
            watcher.tick()
            rows = watcher.get_dashboard_data()
            sys.stdout.write("\033[H")  # Move cursor to top
            print("  ⚡ Konsole Aware - Live Monitor (Ctrl+C to stop)    ")
            print("  " + "─" * 78)
            fmt = "  {:<4} {:<6} {:<4} {:<24} {:<18} {:<12}  "
            print(fmt.format("WIN", "TAB", "ACT", "TITLE", "STATUS", "DURATION"))
            print("  " + "─" * 78)
            for r in rows:
                print(fmt.format(
                    r["window"],
                    r["session_num"],
                    r["active"],
                    r["title"][:22],
                    r["status"][:16],
                    r["duration"],
                ))
            print("  " + "─" * 78)
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nExiting monitor.")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run background monitor daemon."""
    watcher = TabWatcher(poll_interval=args.interval)
    watcher.run_daemon()
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Install konsole-aware into user environment, shells, agy, and claude."""
    home = Path.home()
    repo_root = Path(__file__).resolve().parent.parent

    # 1. Symlink binary to ~/.local/bin/konsole-aware
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    target_bin = local_bin / "konsole-aware"
    source_bin = repo_root / "bin" / "konsole-aware"

    if target_bin.is_symlink() or target_bin.exists():
        target_bin.unlink()
    target_bin.symlink_to(source_bin)
    source_bin.chmod(0o755)
    print(f"✓ Installed binary: {target_bin}")

    # 2. Configure Antigravity hook (~/.gemini/config/hooks.json)
    gemini_config_dir = home / ".gemini" / "config"
    gemini_config_dir.mkdir(parents=True, exist_ok=True)
    agy_hooks_file = gemini_config_dir / "hooks.json"

    agy_hook_cmd = f"{target_bin} agent agy"
    agy_hook_def = {
        "konsole-aware": {
            "enabled": True,
            "PreInvocation": [
                {"type": "command", "command": f"{agy_hook_cmd} PreInvocation", "timeout": 5}
            ],
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": f"{agy_hook_cmd} PreToolUse", "timeout": 5}
                    ]
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": f"{agy_hook_cmd} PostToolUse", "timeout": 5}
                    ]
                }
            ],
            "Stop": [
                {"type": "command", "command": f"{agy_hook_cmd} Stop", "timeout": 5}
            ]
        }
    }

    try:
        existing_hooks = {}
        if agy_hooks_file.exists():
            with open(agy_hooks_file, "r", encoding="utf-8") as f:
                existing_hooks = json.load(f)
        existing_hooks.update(agy_hook_def)
        with open(agy_hooks_file, "w", encoding="utf-8") as f:
            json.dump(existing_hooks, f, indent=2)
        print(f"✓ Configured Antigravity lifecycle hooks: {agy_hooks_file}")
    except Exception as e:
        print(f"✗ Failed to configure Antigravity hooks: {e}", file=sys.stderr)

    # 3. Configure Claude Code hooks (~/.claude/settings.json)
    claude_settings_file = home / ".claude" / "settings.json"
    if claude_settings_file.exists():
        try:
            with open(claude_settings_file, "r", encoding="utf-8") as f:
                claude_settings = json.load(f)

            claude_hook_cmd = f"{target_bin} agent claude"
            claude_hooks = claude_settings.get("hooks", {})
            claude_hooks["PreToolUse"] = [
                {
                    "hooks": [
                        {"type": "command", "command": f"{claude_hook_cmd} PreToolUse", "timeout": 5}
                    ]
                }
            ]
            claude_hooks["Stop"] = [
                {
                    "hooks": [
                        {"type": "command", "command": f"{claude_hook_cmd} Stop", "timeout": 5}
                    ]
                }
            ]
            claude_hooks["UserPromptSubmit"] = [
                {
                    "hooks": [
                        {"type": "command", "command": f"{claude_hook_cmd} UserPromptSubmit", "timeout": 5}
                    ]
                }
            ]
            claude_settings["hooks"] = claude_hooks
            with open(claude_settings_file, "w", encoding="utf-8") as f:
                json.dump(claude_settings, f, indent=2)
            print(f"✓ Configured Claude Code hooks: {claude_settings_file}")
        except Exception as e:
            print(f"✗ Failed to configure Claude Code hooks: {e}", file=sys.stderr)

    # 4. Configure Shell Integration (~/.zshrc and ~/.bashrc)
    zsh_plugin = repo_root / "shell" / "konsole-aware.zsh"
    zshrc = home / ".zshrc"
    zsh_line = f'[[ -f "{zsh_plugin}" ]] && source "{zsh_plugin}" # konsole-aware'
    if zshrc.exists():
        content = zshrc.read_text(encoding="utf-8")
        if "# konsole-aware" not in content:
            with open(zshrc, "a", encoding="utf-8") as f:
                f.write(f"\n{zsh_line}\n")
            print(f"✓ Added zsh integration to {zshrc}")
        else:
            print(f"✓ zsh integration already present in {zshrc}")

    bash_plugin = repo_root / "shell" / "konsole-aware.bash"
    bashrc = home / ".bashrc"
    bash_line = f'[[ -f "{bash_plugin}" ]] && source "{bash_plugin}" # konsole-aware'
    if bashrc.exists():
        content = bashrc.read_text(encoding="utf-8")
        if "# konsole-aware" not in content:
            with open(bashrc, "a", encoding="utf-8") as f:
                f.write(f"\n{bash_line}\n")
            print(f"✓ Added bash integration to {bashrc}")
        else:
            print(f"✓ bash integration already present in {bashrc}")

    # 5. Ensure default config exists
    load_config()
    print("\n🎉 Installation completed successfully!")
    print("👉 Reload your shell: 'source ~/.zshrc' (or open a new Konsole tab)")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Cleanly uninstall konsole-aware hooks and integrations."""
    home = Path.home()

    # 1. Remove binary symlink
    target_bin = home / ".local" / "bin" / "konsole-aware"
    if target_bin.is_symlink() or target_bin.exists():
        target_bin.unlink()
        print(f"✓ Removed binary: {target_bin}")

    # 2. Clean Antigravity hooks
    agy_hooks_file = home / ".gemini" / "config" / "hooks.json"
    if agy_hooks_file.exists():
        try:
            with open(agy_hooks_file, "r", encoding="utf-8") as f:
                hooks = json.load(f)
            if "konsole-aware" in hooks:
                del hooks["konsole-aware"]
                with open(agy_hooks_file, "w", encoding="utf-8") as f:
                    json.dump(hooks, f, indent=2)
                print(f"✓ Removed hooks from {agy_hooks_file}")
        except Exception:
            pass

    # 3. Clean Claude Code hooks
    claude_settings_file = home / ".claude" / "settings.json"
    if claude_settings_file.exists():
        try:
            with open(claude_settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
            if "hooks" in settings:
                del settings["hooks"]
                with open(claude_settings_file, "w", encoding="utf-8") as f:
                    json.dump(settings, f, indent=2)
                print(f"✓ Removed hooks from {claude_settings_file}")
        except Exception:
            pass

    # 4. Clean zshrc and bashrc
    for rc_name in (".zshrc", ".bashrc"):
        rc_path = home / rc_name
        if rc_path.exists():
            lines = rc_path.read_text(encoding="utf-8").splitlines()
            new_lines = [l for l in lines if "# konsole-aware" not in l]
            rc_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            print(f"✓ Cleaned {rc_path}")

    print("\n✓ Uninstallation complete.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="konsole-aware",
        description="Terminal & Tab Awareness for Konsole, AI Agents (agy, claude), and long-running tasks.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # agent hook
    p_agent = subparsers.add_parser("agent", help="AI agent lifecycle hook endpoint")
    p_agent.add_argument("agent", choices=["agy", "claude"], help="Agent type")
    p_agent.add_argument("event", help="Event name (PreInvocation, PreToolUse, Stop, etc.)")
    p_agent.set_defaults(func=cmd_agent)

    # shell preexec
    p_preexec = subparsers.add_parser("preexec", help="Shell preexec hook")
    p_preexec.add_argument("command", help="Command string")
    p_preexec.set_defaults(func=cmd_shell_preexec)

    # shell precmd
    p_precmd = subparsers.add_parser("precmd", help="Shell precmd hook")
    p_precmd.add_argument("exit_code", type=int, nargs="?", default=0, help="Exit code of finished command")
    p_precmd.set_defaults(func=cmd_shell_precmd)

    # list / status
    p_list = subparsers.add_parser("list", aliases=["status"], help="List all tabs and running tasks")
    p_list.add_argument("--json", action="store_true", help="Output as JSON")
    p_list.set_defaults(func=cmd_list)

    # switch / focus
    p_switch = subparsers.add_parser("switch", aliases=["focus"], help="Switch focus to a tab/session")
    p_switch.add_argument("session", help="Session number (e.g. 1, 9)")
    p_switch.set_defaults(func=cmd_switch)

    # set-title
    p_title = subparsers.add_parser("set-title", help="Set current tab title")
    p_title.add_argument("title", help="Title string")
    p_title.set_defaults(func=cmd_set_title)

    # set-color
    p_color = subparsers.add_parser("set-color", help="Set current tab color")
    p_color.add_argument("color", help="Hex color string (e.g. #f59e0b)")
    p_color.set_defaults(func=cmd_set_color)

    # reset
    p_reset = subparsers.add_parser("reset", help="Reset tab appearance")
    p_reset.set_defaults(func=cmd_reset)

    # alert
    p_alert = subparsers.add_parser("alert", help="Send test alert notification & sound")
    p_alert.add_argument("title", help="Alert title")
    p_alert.add_argument("message", help="Alert body")
    p_alert.add_argument("--error", action="store_true", help="Mark as error alert")
    p_alert.set_defaults(func=cmd_alert)

    # watch
    p_watch = subparsers.add_parser("watch", help="Live interactive terminal monitor")
    p_watch.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds")
    p_watch.set_defaults(func=cmd_watch)

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Run background monitor daemon")
    p_daemon.add_argument("--interval", type=float, default=1.5, help="Polling interval in seconds")
    p_daemon.set_defaults(func=cmd_daemon)

    # install
    p_install = subparsers.add_parser("install", help="Install hooks and shell integration")
    p_install.set_defaults(func=cmd_install)

    # uninstall
    p_uninstall = subparsers.add_parser("uninstall", help="Uninstall hooks and shell integration")
    p_uninstall.set_defaults(func=cmd_uninstall)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
