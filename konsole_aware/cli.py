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
from konsole_aware.terminal.colors import colorize, get_color_tag, resolve_color_hex, pad_ansi, strip_ansi
from konsole_aware.tabs.groups import TabGroupManager
from konsole_aware.tabs.organizer import TabOrganizer
from konsole_aware.tabs.title import TitleComposer
from konsole_aware.state.manager import StateManager
from konsole_aware.tasks.classifier import CommandClassifier
from konsole_aware.notify.notifier import Notifier
from konsole_aware.agents.agy import AgyHandler
from konsole_aware.agents.claude import ClaudeHandler
from konsole_aware.daemon.watcher import TabWatcher


def cmd_agent(args: argparse.Namespace) -> int:
    """Handle AI agent hooks (agy, claude)."""
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
    composer = TitleComposer()
    session_id = term.get_session_id()

    classified = classifier.classify(cmd_str)
    if classified["category"] == "idle":
        return 0

    orig_title = getattr(term, "get_title", lambda r: "")(0)
    orig_color = getattr(term, "get_tab_color", lambda: "")()

    state_mgr.start_task(
        session_key=session_id,
        task_name=classified["name"],
        task_type=classified["category"],
        detail=classified["detail"],
        original_title=orig_title,
        original_color=orig_color,
    )

    name = classified["name"]
    detail = classified["detail"]
    full_title = composer.compose(
        session_key=session_id,
        task_category=classified["category"],
        status="busy",
        detail=f"{name} {detail}".strip(),
        cwd=os.getcwd(),
    )

    term.set_title(full_title, role=0)
    term.set_tab_color(classified["color"])
    term.set_badge(classified["badge"], enabled=True)
    return 0


def cmd_shell_precmd(args: argparse.Namespace) -> int:
    """Invoked right before shell prompt redisplay (after command finishes)."""
    exit_code = args.exit_code
    term = get_terminal()
    state_mgr = StateManager()
    composer = TitleComposer()
    config = load_config()
    notifier = Notifier()
    session_id = term.get_session_id()

    sess = state_mgr.get_session(session_id)
    if not sess or sess.get("status") != "busy":
        return 0

    status = "done" if exit_code == 0 else "error"
    finished = state_mgr.finish_task(session_key=session_id, exit_code=exit_code, status=status)

    duration = finished.get("duration", 0.0)
    min_dur = config.get("min_duration_seconds", 3.0)
    task_name = sess.get("task_name", "task")
    task_type = sess.get("task_type", "command")

    mins, secs = divmod(int(duration), 60)
    dur_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    colors = config.get("colors", {})

    if exit_code == 0:
        full_title = composer.compose(
            session_key=session_id,
            task_category=task_type,
            status="done",
            detail=f"{task_name} ({dur_str})",
            cwd=os.getcwd(),
        )
        term.set_title(full_title, role=0)
        term.set_tab_color(colors.get("done", "#10b981"))
        term.set_badge("DONE", enabled=True)
    else:
        full_title = composer.compose(
            session_key=session_id,
            task_category=task_type,
            status="error",
            detail=f"{task_name} (exit {exit_code})",
            cwd=os.getcwd(),
        )
        term.set_title(full_title, role=0)
        term.set_tab_color(colors.get("error", "#ef4444"))
        term.set_badge("ERR", enabled=True)

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
    """List all Konsole tabs, sessions, foreground processes, groups, and statuses."""
    watcher = TabWatcher()
    watcher.tick()
    rows = watcher.get_dashboard_data()

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("No active Konsole sessions detected.")
        return 0

    print("\n  🔍 " + colorize("Konsole Aware - Active Tab Status", bold=True))
    print("  " + "─" * 94)
    fmt_header = "  {:<4} {:<5} {:<4} {:<4} {:<14} {:<26} {:<20} {:<10}"
    print(fmt_header.format("WIN", "TAB", "ACT", "PIN", "GROUP", "TITLE", "STATUS", "DURATION"))
    print("  " + "─" * 94)

    for r in rows:
        act_icon = colorize("●", fg="#10b981", bold=True) if r["active"] == "●" else colorize("○", fg="#6b7280")
        pin_icon = "📌" if r.get("is_anchored") else "-"
        grp_str = f"[{r['group']}]" if r.get("group") else "-"
        if r.get("group_color"):
            grp_str = colorize(grp_str, fg=r["group_color"], bold=True)

        status_raw = r["status"]
        if "done" in status_raw.lower() or "ready" in status_raw.lower():
            status_disp = colorize(status_raw[:18], fg="#10b981")
        elif "think" in status_raw.lower() or "run" in status_raw.lower() or "busy" in status_raw.lower():
            status_disp = colorize(status_raw[:18], fg="#f59e0b", bold=True)
        elif "error" in status_raw.lower() or "fail" in status_raw.lower():
            status_disp = colorize(status_raw[:18], fg="#ef4444", bold=True)
        else:
            status_disp = status_raw[:18]

        col_win = pad_ansi(str(r["window"]), 4)
        col_tab = pad_ansi(str(r["session_num"]), 5)
        col_act = pad_ansi(act_icon, 4)
        col_pin = pad_ansi(pin_icon, 4)
        col_grp = pad_ansi(grp_str, 14)
        col_title = pad_ansi(r["title"][:25], 26)
        col_status = pad_ansi(status_disp, 20)
        col_dur = pad_ansi(r["duration"], 10)

        print(f"  {col_win} {col_tab} {col_act} {col_pin} {col_grp} {col_title} {col_status} {col_dur}")

    print("  " + "─" * 94 + "\n")
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    """Switch Konsole view to a specific session."""
    sess_target = str(args.session)
    term = KonsoleTerminal(session_path=f"/Sessions/{sess_target}")
    if term.switch_to():
        print(f"✓ Switched to Konsole tab {sess_target}.")
        return 0
    print(f"Failed to switch to session {sess_target}.", file=sys.stderr)
    return 1


def cmd_group(args: argparse.Namespace) -> int:
    """Manage tab grouping."""
    grp_mgr = TabGroupManager()
    action = args.group_action

    if action == "list":
        grps = grp_mgr.list_groups()
        print("\n  🏷️  " + colorize("Konsole Aware - Tab Groups", bold=True))
        print("  " + "─" * 70)
        for g_name, g_info in grps.items():
            icon = g_info.get("icon", "📁")
            color = g_info.get("color", "#3b82f6")
            colored_tag = colorize(f" {icon} {g_name.upper()} ", fg="#ffffff", bg=color, bold=True)
            sessions = g_info.get("sessions", [])
            s_list_str = ", ".join(s.split("/")[-1] for s in sessions) if sessions else "none"
            print(f"  {colored_tag:<32} Tabs: {s_list_str}")
        print("  " + "─" * 70 + "\n")
        return 0

    term = get_terminal()
    session_num = args.tab or (getattr(term, "get_session_num", lambda: None)() if hasattr(term, "get_session_num") else None)

    if action == "auto":
        sessions = KonsoleTerminal.list_all_sessions()
        count = 0
        for s in sessions:
            fg_pid = s["foreground_pid"]
            cwd_name = ""
            if fg_pid > 0:
                try:
                    cwd = os.readlink(f"/proc/{fg_pid}/cwd")
                    cwd_name = Path(cwd).name
                except Exception:
                    pass
            if cwd_name and cwd_name not in ("reaver", "home", "root"):
                s_key_item = f"{s['service']}:{s['session_path']}"
                grp_mgr.define_group(cwd_name)
                grp_mgr.assign_tab(s_key_item, cwd_name)
                count += 1
        print(f"✓ Auto-grouped {count} tabs by active project directory.")
        return 0

    if not session_num:
        print("Error: Specify target tab with --tab <num> or run inside a Konsole tab.", file=sys.stderr)
        return 1

    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    s_key = f"{svc}:/Sessions/{session_num}"

    if action == "set":
        grp_name = args.name
        color = args.color
        icon = args.icon
        if color or icon:
            grp_mgr.define_group(grp_name, color=color, icon=icon)
        grp_mgr.assign_tab(s_key, grp_name)
        grp_info = grp_mgr.get_tab_group(s_key)

        # Set tab color to group color
        if grp_info and hasattr(term, "set_tab_color"):
            kterm = KonsoleTerminal(service=svc, session_path=f"/Sessions/{session_num}")
            kterm.set_tab_color(grp_info["color"])

        print(f"✓ Tab {session_num} assigned to group '{grp_name}'.")
        return 0

    elif action == "remove":
        grp_mgr.unassign_tab(s_key)
        print(f"✓ Tab {session_num} removed from groups.")
        return 0

    return 0


def cmd_anchor(args: argparse.Namespace) -> int:
    """Anchor (pin) a tab to the leftmost position."""
    term = get_terminal()
    grp_mgr = TabGroupManager()
    session_num = args.tab or (getattr(term, "get_session_num", lambda: None)() if hasattr(term, "get_session_num") else None)
    if not session_num:
        print("Error: Specify tab with --tab <num> or run inside a tab.", file=sys.stderr)
        return 1

    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    s_key = f"{svc}:/Sessions/{session_num}"
    grp_mgr.anchor_tab(s_key)

    organizer = TabOrganizer(service=svc)
    win_info = organizer.find_session_window(str(session_num))
    if win_info:
        organizer.move_session_to_index(win_info[0], str(session_num), 0)

    kterm = KonsoleTerminal(service=svc, session_path=f"/Sessions/{session_num}")
    kterm.set_badge("PIN", enabled=True)

    print(f"📌 Tab {session_num} anchored (pinned to front).")
    return 0


def cmd_unanchor(args: argparse.Namespace) -> int:
    """Unanchor a tab."""
    term = get_terminal()
    grp_mgr = TabGroupManager()
    session_num = args.tab or (getattr(term, "get_session_num", lambda: None)() if hasattr(term, "get_session_num") else None)
    if not session_num:
        print("Error: Specify tab with --tab <num> or run inside a tab.", file=sys.stderr)
        return 1

    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    s_key = f"{svc}:/Sessions/{session_num}"
    grp_mgr.unanchor_tab(s_key)

    kterm = KonsoleTerminal(service=svc, session_path=f"/Sessions/{session_num}")
    kterm.set_badge("", enabled=False)

    print(f"✓ Tab {session_num} unanchored.")
    return 0


def cmd_organize(args: argparse.Namespace) -> int:
    """Organize/sort tabs in windows."""
    term = get_terminal()
    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    organizer = TabOrganizer(service=svc)
    sort_by = args.by or "status"

    if args.window:
        win_path = f"/Windows/{args.window}"
        res = organizer.organize_window(win_path, sort_by=sort_by)
        print(f"✓ Window {args.window} organized by {sort_by}: {' -> '.join(res)}")
    else:
        results = organizer.organize_all(sort_by=sort_by)
        for win, sess_list in results.items():
            w_idx = win.split("/")[-1]
            print(f"✓ Window {w_idx} organized by {sort_by}: {' -> '.join(sess_list)}")
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    """Move a tab left, right, first, last, or to an exact index."""
    term = get_terminal()
    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    organizer = TabOrganizer(service=svc)
    session_num = str(args.session)
    dest = args.destination.lower()

    win_info = organizer.find_session_window(session_num)
    if not win_info:
        print(f"Error: Session {session_num} not found in any window.", file=sys.stderr)
        return 1

    win_path, _ = win_info
    sessions = organizer.get_window_sessions(win_path)
    cur_idx = sessions.index(session_num)

    if dest == "left":
        target_idx = max(0, cur_idx - 1)
    elif dest == "right":
        target_idx = min(len(sessions) - 1, cur_idx + 1)
    elif dest == "first":
        target_idx = 0
    elif dest == "last":
        target_idx = len(sessions) - 1
    elif dest.isdigit():
        target_idx = max(0, min(len(sessions) - 1, int(dest)))
    else:
        print(f"Unknown destination '{dest}'. Use: left, right, first, last, or index.", file=sys.stderr)
        return 1

    organizer.move_session_to_index(win_path, session_num, target_idx)
    print(f"✓ Moved Tab {session_num} to index {target_idx}.")
    return 0


def cmd_detach(args: argparse.Namespace) -> int:
    """Detach a tab into its own independent Konsole window."""
    term = get_terminal()
    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    organizer = TabOrganizer(service=svc)
    session_num = args.session or (getattr(term, "get_session_num", lambda: None)() if hasattr(term, "get_session_num") else None)
    if not session_num:
        print("Error: Specify session number to detach with --session <num> or run inside a tab.", file=sys.stderr)
        return 1

    if organizer.detach_tab(str(session_num)):
        print(f"✓ Tab {session_num} detached into a new window.")
        return 0
    print(f"Failed to detach tab {session_num}.", file=sys.stderr)
    return 1


def cmd_split(args: argparse.Namespace) -> int:
    """Split view horizontally or vertically to reclip views together."""
    term = get_terminal()
    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    organizer = TabOrganizer(service=svc)
    session_num = args.tab or (getattr(term, "get_session_num", lambda: None)() if hasattr(term, "get_session_num") else None)
    if not session_num:
        print("Error: Specify tab with --tab <num> or run inside a tab.", file=sys.stderr)
        return 1

    orientation = args.orientation or "left-right"
    if organizer.split_view(str(session_num), orientation=orientation):
        print(f"✓ View split {orientation} for tab {session_num}.")
        return 0
    print("Failed to split view.", file=sys.stderr)
    return 1


def cmd_equalize(args: argparse.Namespace) -> int:
    """Balance all split view sizes equally."""
    term = get_terminal()
    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    organizer = TabOrganizer(service=svc)
    session_num = args.tab or (getattr(term, "get_session_num", lambda: None)() if hasattr(term, "get_session_num") else None)
    if not session_num:
        print("Error: Specify tab with --tab <num> or run inside a tab.", file=sys.stderr)
        return 1

    if organizer.equalize_splits(str(session_num)):
        print("✓ Balanced split views.")
        return 0
    return 1


def cmd_maximize(args: argparse.Namespace) -> int:
    """Toggle maximize on current split view."""
    term = get_terminal()
    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    organizer = TabOrganizer(service=svc)
    session_num = args.tab or (getattr(term, "get_session_num", lambda: None)() if hasattr(term, "get_session_num") else None)
    if not session_num:
        print("Error: Specify tab with --tab <num> or run inside a tab.", file=sys.stderr)
        return 1

    if organizer.toggle_maximize_split(str(session_num)):
        print("✓ Toggled maximize on split view.")
        return 0
    return 1


def cmd_set_job(args: argparse.Namespace) -> int:
    """Set or clear a permanent custom job/project name for a tab."""
    term = get_terminal()
    state_mgr = StateManager()
    composer = TitleComposer()
    session_num = args.tab or (getattr(term, "get_session_num", lambda: None)() if hasattr(term, "get_session_num") else None)
    if not session_num:
        print("Error: Specify tab with --tab <num> or run inside a tab.", file=sys.stderr)
        return 1

    svc = getattr(term, "service", None) or os.environ.get("KONSOLE_DBUS_SERVICE", "")
    s_key = f"{svc}:/Sessions/{session_num}"

    if args.clear:
        state_mgr.set_session(s_key, job_name=None)
        print(f"✓ Cleared custom job name for Tab {session_num} (reverted to auto project name).")
    else:
        if not args.name:
            print("Error: Specify job name (e.g. konsole-aware set-job MyProject).", file=sys.stderr)
            return 1
        job = args.name.strip()
        state_mgr.set_session(s_key, job_name=job)
        print(f"✓ Set custom job name for Tab {session_num}: [{job}]")

    kterm = KonsoleTerminal(service=svc, session_path=f"/Sessions/{session_num}")
    new_title = composer.compose(session_key=s_key, status="idle", detail="ready", cwd=os.getcwd())
    kterm.set_title(new_title, role=0)
    return 0


def cmd_set_title(args: argparse.Namespace) -> int:
    """Set tab title directly with optional color tag and group/anchor awareness."""
    term = get_terminal()
    composer = TitleComposer()
    session_id = term.get_session_id()

    title = args.title
    if args.color_tag:
        tag_glyph = get_color_tag(args.color_tag, square=args.square)
        title = f"{tag_glyph} {title}"

    full_title = composer.compose(
        session_key=session_id,
        status="busy" if args.busy else "idle",
        detail=title,
        cwd=os.getcwd(),
    )
    term.set_title(full_title, role=0)
    print(f"Title set: {full_title}")
    return 0


def cmd_set_color(args: argparse.Namespace) -> int:
    """Set tab color directly."""
    term = get_terminal()
    hex_color = resolve_color_hex(args.color)
    term.set_tab_color(hex_color)
    print(f"Color set: {hex_color}")
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
            print("  ⚡ " + colorize("Konsole Aware - Live Monitor", bold=True) + " (Ctrl+C to stop)        ")
            print("  " + "─" * 94)
            fmt = "  {:<4} {:<5} {:<4} {:<4} {:<12} {:<26} {:<20} {:<10}  "
            print(fmt.format("WIN", "TAB", "ACT", "PIN", "GROUP", "TITLE", "STATUS", "DURATION"))
            print("  " + "─" * 94)
            for r in rows:
                act_icon = colorize("●", fg="#10b981", bold=True) if r["active"] == "●" else colorize("○", fg="#6b7280")
                pin_icon = "📌" if r.get("is_anchored") else "-"
                grp_str = f"[{r['group']}]" if r.get("group") else "-"
                if r.get("group_color"):
                    grp_str = colorize(grp_str, fg=r["group_color"], bold=True)

                status_raw = r["status"]
                if "done" in status_raw.lower() or "ready" in status_raw.lower():
                    status_disp = colorize(status_raw[:18], fg="#10b981")
                elif "think" in status_raw.lower() or "run" in status_raw.lower() or "busy" in status_raw.lower():
                    status_disp = colorize(status_raw[:18], fg="#f59e0b", bold=True)
                elif "error" in status_raw.lower() or "fail" in status_raw.lower():
                    status_disp = colorize(status_raw[:18], fg="#ef4444", bold=True)
                else:
                    status_disp = status_raw[:18]

                title_disp = r["title"][:24]

                print(fmt.format(
                    r["window"],
                    r["session_num"],
                    act_icon,
                    pin_icon,
                    grp_str[:12],
                    title_disp,
                    status_disp,
                    r["duration"],
                ))
            print("  " + "─" * 94)
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

    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    target_bin = local_bin / "konsole-aware"
    source_bin = repo_root / "bin" / "konsole-aware"

    if target_bin.is_symlink() or target_bin.exists():
        target_bin.unlink()
    target_bin.symlink_to(source_bin)
    source_bin.chmod(0o755)
    print(f"✓ Installed binary: {target_bin}")

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

    load_config()
    print("\n🎉 Installation completed successfully!")
    print("👉 Reload your shell: 'source ~/.zshrc' (or open a new Konsole tab)")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Cleanly uninstall konsole-aware hooks and integrations."""
    home = Path.home()

    target_bin = home / ".local" / "bin" / "konsole-aware"
    if target_bin.is_symlink() or target_bin.exists():
        target_bin.unlink()
        print(f"✓ Removed binary: {target_bin}")

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
        description="Terminal & Tab Awareness for Konsole, AI Agents (agy, claude), Grouping, Anchoring & Organization.",
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
    p_list = subparsers.add_parser("list", aliases=["status"], help="List all tabs, groups, and running tasks")
    p_list.add_argument("--json", action="store_true", help="Output as JSON")
    p_list.set_defaults(func=cmd_list)

    # switch / focus
    p_switch = subparsers.add_parser("switch", aliases=["focus"], help="Switch focus to a tab/session")
    p_switch.add_argument("session", help="Session number (e.g. 1, 9)")
    p_switch.set_defaults(func=cmd_switch)

    # group
    p_group = subparsers.add_parser("group", help="Manage tab groupings and category colors")
    p_group.add_argument("group_action", choices=["set", "remove", "list", "auto"], help="Group action")
    p_group.add_argument("name", nargs="?", default="", help="Group name (e.g. frontend, backend, agents)")
    p_group.add_argument("--tab", type=int, help="Target tab session number")
    p_group.add_argument("--color", help="Hex color or color name for this group")
    p_group.add_argument("--icon", help="Icon/emoji for this group (e.g. 🎨, 🤖, ⚙️)")
    p_group.set_defaults(func=cmd_group)

    # anchor / pin
    p_anchor = subparsers.add_parser("anchor", aliases=["pin"], help="Anchor (pin) a tab to the leftmost position")
    p_anchor.add_argument("--tab", type=int, help="Target tab session number")
    p_anchor.set_defaults(func=cmd_anchor)

    p_unanchor = subparsers.add_parser("unanchor", aliases=["unpin"], help="Unanchor a tab")
    p_unanchor.add_argument("--tab", type=int, help="Target tab session number")
    p_unanchor.set_defaults(func=cmd_unanchor)

    # organize
    p_org = subparsers.add_parser("organize", help="Organize and sort tabs in Konsole windows")
    p_org.add_argument("--by", choices=["status", "group", "name", "id"], default="status", help="Sort criteria")
    p_org.add_argument("--window", help="Specific window number (e.g. 1, 2)")
    p_org.set_defaults(func=cmd_organize)

    # move
    p_move = subparsers.add_parser("move", help="Move a tab left, right, first, last, or to an index")
    p_move.add_argument("session", help="Session number to move (e.g. 7)")
    p_move.add_argument("destination", help="Target: left, right, first, last, or index number")
    p_move.set_defaults(func=cmd_move)

    # detach
    p_detach = subparsers.add_parser("detach", help="Detach a tab into its own independent window")
    p_detach.add_argument("session", nargs="?", help="Session number to detach (default: current)")
    p_detach.set_defaults(func=cmd_detach)

    # split / clip
    p_split = subparsers.add_parser("split", aliases=["clip"], help="Split view horizontally or vertically in window")
    p_split.add_argument("orientation", nargs="?", choices=["left-right", "top-bottom", "horizontal", "vertical"], default="left-right")
    p_split.add_argument("--tab", type=int, help="Target tab session number")
    p_split.set_defaults(func=cmd_split)

    p_equalize = subparsers.add_parser("equalize", help="Equalize sizes of all split views")
    p_equalize.add_argument("--tab", type=int, help="Target tab session number")
    p_equalize.set_defaults(func=cmd_equalize)

    p_max = subparsers.add_parser("maximize-split", help="Toggle maximize on the active split view")
    p_max.add_argument("--tab", type=int, help="Target tab session number")
    p_max.set_defaults(func=cmd_maximize)

    # set-job
    p_job = subparsers.add_parser("set-job", help="Set or clear a permanent custom job/project name for a tab")
    p_job.add_argument("name", nargs="?", default="", help="Custom job or project name")
    p_job.add_argument("--tab", type=int, help="Target tab session number")
    p_job.add_argument("--clear", action="store_true", help="Clear custom job name (revert to auto)")
    p_job.set_defaults(func=cmd_set_job)

    # set-title
    p_title = subparsers.add_parser("set-title", help="Set current tab title")
    p_title.add_argument("title", help="Title string")
    p_title.add_argument("--color-tag", help="Prepend colored glyph (red, green, blue, yellow, purple, orange, etc.)")
    p_title.add_argument("--square", action="store_true", help="Use square color badge instead of circle")
    p_title.add_argument("--busy", action="store_true", help="Mark title as busy status")
    p_title.set_defaults(func=cmd_set_title)

    # set-color
    p_color = subparsers.add_parser("set-color", help="Set current tab color")
    p_color.add_argument("color", help="Color name (red, emerald, amber, blue, etc.) or hex code")
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
