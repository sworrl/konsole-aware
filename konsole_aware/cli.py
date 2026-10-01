"""CLI dispatcher and command-line interface for konsole-aware."""

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from konsole_aware import install as installer
from konsole_aware.agents import identity as agent_identity
from konsole_aware.config import load_config, save_config
from konsole_aware.terminal import get_terminal
from konsole_aware.terminal.base import BaseTerminal
from konsole_aware.terminal.konsole import KonsoleTerminal
from konsole_aware.terminal.colors import (
    colorize, get_color_tag, resolve_color_hex, pad_ansi, truncate_display,
)
from konsole_aware.tabs.groups import TabGroupManager
from konsole_aware.tabs.organizer import TabOrganizer
from konsole_aware.tabs.title import TitleComposer
from konsole_aware.tabs.icons import IconResolver
from konsole_aware.state.manager import StateManager, STATE_DIR
from konsole_aware.tasks.classifier import CommandClassifier
from konsole_aware.notify.notifier import Notifier


def _fmt_duration(seconds: float) -> str:
    mins, secs = divmod(int(max(0, seconds)), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m {secs}s" if mins else f"{secs}s"


def _log_error(context: str) -> None:
    """Hooks must never fail loudly; record tracebacks in the state dir instead."""
    try:
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(STATE_DIR / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {context}\n{traceback.format_exc()}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- targeting
def resolve_target(args: argparse.Namespace) -> Tuple[Optional[KonsoleTerminal], Optional[str], Optional[int]]:
    """Resolve the tab a command acts on: --tab/--service if given, else the current tab.

    Returns (konsole_terminal, service, session_num). The menu hook always passes both
    --tab and --service because it runs outside any tab.
    """
    tab = getattr(args, "tab", None)
    service = getattr(args, "service", None)
    if tab is not None:
        kterm = KonsoleTerminal(service=service, session_path=f"/Sessions/{tab}")
        return (kterm if kterm.is_available() else None), kterm.service, int(tab)
    term = get_terminal(service=service)
    if isinstance(term, KonsoleTerminal):
        return term, term.service, term.get_session_num()
    return None, service, None


def _require_tab(args: argparse.Namespace) -> Tuple[Optional[KonsoleTerminal], Optional[str], Optional[int]]:
    kterm, svc, num = resolve_target(args)
    if num is None or kterm is None:
        print("Error: could not find the Konsole tab. Pass --tab <session> (and --service) or run inside Konsole.",
              file=sys.stderr)
        return None, None, None
    return kterm, svc, num


def _session_key(svc: Optional[str], num: int) -> str:
    return f"{svc}:/Sessions/{num}"


def _refresh_title(kterm: KonsoleTerminal) -> None:
    """Re-render a tab's title after a setting (job, icon, group, pin) changed."""
    state = StateManager().get_session(kterm.get_session_id())
    fg_pid, _ = kterm.get_foreground_process()
    agent, _ = agent_identity.resolve_session_agent(state, fg_pid, kterm.get_process_id())
    status = state.get("agent_status") or state.get("status") or "idle"
    detail = state.get("detail") or ""
    if agent == agent_identity.USER and state.get("agent"):
        status, detail = "idle", ""
    title = TitleComposer().compose(
        session_key=kterm.get_session_id(),
        task_category="agent" if agent != agent_identity.USER else (state.get("task_type") or "idle"),
        status=status if status in ("thinking", "tool", "busy", "waiting", "done", "error", "idle",
                                    "compacting", "subagents") else "idle",
        detail=detail,
        cwd=state.get("cwd") or kterm.get_cwd(),
        agent=agent,
    )
    kterm.set_title(title, role=0)


# --------------------------------------------------------------------------- hooks
def cmd_agent(args: argparse.Namespace) -> int:
    """Handle AI agent hooks (agy, claude). Always exits 0 and never blocks the agent."""
    agent_name = getattr(args, "agent", "agy").lower()
    try:
        payload: Dict[str, Any] = {}
        try:
            if not sys.stdin.isatty():
                raw = sys.stdin.read().strip()
                if raw:
                    loaded = json.loads(raw)
                    payload = loaded if isinstance(loaded, dict) else {}
        except Exception:
            payload = {}

        response: Dict[str, Any] = {}
        try:
            if agent_name in ("agy", "antigravity"):
                from konsole_aware.agents.agy import AgyHandler
                response = AgyHandler().handle_event(args.event, payload)
            elif agent_name == "claude":
                from konsole_aware.agents.claude import ClaudeHandler
                ClaudeHandler().handle_event(args.event, payload)
        except Exception:
            _log_error(f"agent {agent_name} {getattr(args, 'event', '')}")

        if agent_name in ("agy", "antigravity"):
            sys.stdout.write(json.dumps(response or {}))
            sys.stdout.flush()
    except Exception:
        if agent_name in ("agy", "antigravity"):
            try:
                sys.stdout.write("{}")
                sys.stdout.flush()
            except Exception:
                pass
    return 0


def _shell_key(args: argparse.Namespace) -> str:
    return str(getattr(args, "shell_pid", None) or "shell")


def cmd_shell_preexec(args: argparse.Namespace) -> int:
    """Invoked (in the background) before a shell command executes."""
    try:
        return _shell_preexec(args)
    except Exception:
        _log_error("preexec")
        return 0


def _shell_preexec(args: argparse.Namespace) -> int:
    cmd_str = " ".join(args.command).strip()
    if not cmd_str:
        return 0

    classified = CommandClassifier().classify(cmd_str)
    if classified["category"] == "idle":
        return 0

    term = get_terminal()
    state_mgr = StateManager()
    session_id = term.get_session_id()
    cmd_id = args.id
    shell = _shell_key(args)
    agent = classified.get("agent")
    mode = agent_identity.mode_from_argv(cmd_str.split()) if agent else None
    skipped = {"value": False}

    def _apply(sess: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        finished = (sess.get("finished_ids") or {}).get(shell)
        if cmd_id is not None and finished is not None and cmd_id <= finished:
            # precmd for this command already ran (fast command): nothing to show.
            skipped["value"] = True
            return None
        updates: Dict[str, Any] = {
            "status": "busy",
            "task_name": classified["name"],
            "task_type": classified["category"],
            "detail": classified["detail"],
            "start_time": time.time(),
            "end_time": None,
            "cmd_id": cmd_id,
            "cmd_shell": shell,
            "hooked": True,
            "cwd": os.getcwd(),
        }
        if agent:
            updates.update({"agent": agent, "agent_pid": None, "agent_mode": mode,
                            "agent_status": "idle", "turn_start": None, "subagents": []})
        return updates

    state_mgr.update_session(session_id, _apply)
    if skipped["value"]:
        return 0

    composer = TitleComposer()
    if agent:
        title = composer.compose(
            session_key=session_id, task_category="agent", status="idle", detail="starting…",
            cwd=os.getcwd(), agent=agent, mode=mode,
        )
    else:
        title = composer.compose(
            session_key=session_id,
            task_category=classified["category"],
            status="busy",
            detail=classified["detail"] or classified["name"],
            cwd=os.getcwd(),
            agent=agent_identity.USER,
        )
    term.set_title(title, role=0)
    term.set_tab_color(classified["color"])
    if agent:
        ident = agent_identity.identity(agent)
        term.set_badge(f"{ident['label']} · START", enabled=True, icon=ident.get("icon", ""))
        if load_config().get("auto_snap_agents", True) and isinstance(term, KonsoleTerminal):
            num = term.get_session_num()
            if num is not None:
                TabOrganizer(service=term.service).snap_session(str(num), require_current=True)
    else:
        term.set_badge(classified["badge"], enabled=True)
    return 0


def cmd_shell_precmd(args: argparse.Namespace) -> int:
    """Invoked (in the background) right before the prompt is redisplayed."""
    try:
        return _shell_precmd(args)
    except Exception:
        _log_error("precmd")
        return 0


def _shell_precmd(args: argparse.Namespace) -> int:
    exit_code = args.exit_code
    term = get_terminal()
    state_mgr = StateManager()
    composer = TitleComposer()
    config = load_config()
    session_id = term.get_session_id()
    cmd_id = args.id
    shell = _shell_key(args)
    cwd = os.getcwd()
    before: Dict[str, Any] = {}

    def _apply(sess: Dict[str, Any]) -> Dict[str, Any]:
        before.update(sess)
        updates: Dict[str, Any] = {"hooked": True, "cwd": cwd}
        if cmd_id is not None:
            finished = dict(sess.get("finished_ids") or {})
            finished[shell] = max(cmd_id, finished.get(shell, -1))
            updates["finished_ids"] = finished
        return updates

    state_mgr.update_session(session_id, _apply)
    sess = before

    def show_user_idle() -> None:
        term.set_title(composer.compose(
            session_key=session_id, task_category="idle", status="idle", detail="", cwd=cwd,
            agent=agent_identity.USER,
        ), role=0)
        if sess.get("status") not in (None, "idle") or sess.get("agent"):
            # Only undo colours/badges konsole-aware itself set; a colour picked with
            # set-color or a group colour survives ordinary prompts.
            state_mgr.set_session(session_id, status="idle")
            grp = TabGroupManager().get_tab_group(session_id)
            if grp and grp.get("color"):
                term.set_tab_color(grp["color"])
            else:
                term.reset_tab_color()
            term.set_badge("", enabled=False)

    same_command = cmd_id is None or (sess.get("cmd_id") == cmd_id and sess.get("cmd_shell", shell) == shell)

    # An agent (claude/agy/...) that ran in this shell has exited: back to a plain user session.
    if sess.get("agent") and (same_command or not agent_identity.pid_alive(sess.get("agent_pid"))):
        state_mgr.update_session(session_id, lambda _s: {
            "agent": None, "agent_pid": None, "agent_mode": None, "agent_status": None,
            "subagents": [], "status": "idle", "task_type": "command", "task_name": "",
        })
        show_user_idle()
        return 0

    if not same_command or sess.get("status") != "busy":
        # Command was not tracked (e.g. `cd`, or its preexec has not run yet): show the
        # idle user prompt, which also refreshes the project after a directory change.
        # A precmd closes every earlier command of this shell, so a lost precmd can
        # never leave the tab stuck "busy".
        if (sess.get("status") == "busy" and cmd_id is not None and sess.get("cmd_shell", shell) == shell
                and isinstance(sess.get("cmd_id"), int) and sess["cmd_id"] < cmd_id):
            state_mgr.finish_task(session_key=session_id, exit_code=0, status="idle")
        show_user_idle()
        return 0

    status = "done" if exit_code == 0 else "error"
    finished = state_mgr.finish_task(session_key=session_id, exit_code=exit_code, status=status)
    duration = finished.get("duration", 0.0)
    min_dur = config.get("min_duration_seconds", 3.0)
    task_name = sess.get("task_name", "task")
    task_type = sess.get("task_type", "command")
    dur_str = _fmt_duration(duration)
    colors = config.get("colors", {})
    notable = duration >= min_dur or task_type in ("transfer", "build", "download", "test")

    if exit_code == 0 and not notable:
        show_user_idle()
        return 0

    if exit_code == 0:
        term.set_title(composer.compose(
            session_key=session_id, task_category=task_type, status="done",
            detail=f"{task_name} ({dur_str})", cwd=cwd, agent=agent_identity.USER,
        ), role=0)
        term.set_tab_color(colors.get("done", "#10b981"))
        term.set_badge("DONE", enabled=True)
    else:
        term.set_title(composer.compose(
            session_key=session_id, task_category=task_type, status="error",
            detail=f"{task_name} (exit {exit_code})", cwd=cwd, agent=agent_identity.USER,
        ), role=0)
        term.set_tab_color(colors.get("error", "#ef4444"))
        term.set_badge("ERR", enabled=True)

    if notable and (not config.get("alert_only_unfocused", True) or not term.is_focused()):
        notifier = Notifier()
        sess_num = getattr(term, "get_session_num", lambda: None)()
        sess_label = f"Tab {sess_num}" if sess_num is not None else "Terminal"
        if exit_code == 0:
            notifier.notify(
                title=f"Task Finished: {task_name}",
                message=f"Completed in {dur_str} in {sess_label}",
                urgency="normal",
            )
        else:
            notifier.notify(
                title=f"Task Failed: {task_name}",
                message=f"Failed with exit code {exit_code} after {dur_str} ({sess_label})",
                urgency="critical",
                is_error=True,
            )
    return 0


# --------------------------------------------------------------------------- dashboard
def _status_display(status_raw: str, width: int) -> str:
    low = status_raw.lower()
    text = truncate_display(status_raw, width)
    if "done" in low or "ready" in low:
        return colorize(text, fg="#10b981")
    if "wait" in low or "permission" in low or "question" in low:
        return colorize(text, fg="#8b5cf6", bold=True)
    if "error" in low or "fail" in low or "block" in low or "flagged" in low or "limit" in low:
        return colorize(text, fg="#ef4444", bold=True)
    if any(k in low for k in ("think", "tool", "busy", "run", "compact", "subagent")):
        return colorize(text, fg="#f59e0b", bold=True)
    return text


def _render_table(rows: List[Dict[str, Any]], heading: str) -> List[str]:
    cols = [("WIN", 4), ("TAB", 4), ("ACT", 3), ("PIN", 3), ("AGENT", 18), ("PROJECT", 20),
            ("STATUS", 28), ("DURATION", 9), ("GROUP", 12)]
    width = sum(w for _, w in cols) + len(cols) + 1
    lines = ["", "  " + heading, "  " + "─" * width,
             "  " + " ".join(pad_ansi(name, w) for name, w in cols), "  " + "─" * width]
    prev_key = None
    for r in rows:
        key = (r["window"], r["project"])
        if prev_key and prev_key[0] != key[0]:
            lines.append("  " + "┄" * width)
        prev_key = key
        act = colorize("●", fg="#10b981", bold=True) if r["active"] == "●" else colorize("○", fg="#6b7280")
        agent_txt = r["agent_tag"] + (" " + agent_identity.mode_tag(r["agent_mode"]) if r.get("agent_mode") else "")
        agent_col = truncate_display(agent_txt, 18)
        if r["agent"] != agent_identity.USER:
            agent_col = colorize(agent_col, fg=agent_identity.identity(r["agent"]).get("color"), bold=True)
        grp = truncate_display(f"[{r['group']}]", 12) if r.get("group") else "-"
        if r.get("group_color"):
            grp = colorize(grp, fg=r["group_color"], bold=True)
        cells = [
            str(r["window"]), str(r["session_num"]), act, "📌" if r.get("is_anchored") else "-",
            agent_col, truncate_display(r["project"] or "-", 20), _status_display(r["status"], 28),
            r["duration"], grp,
        ]
        lines.append("  " + " ".join(pad_ansi(c, w) for c, (_, w) in zip(cells, cols)))
    lines.append("  " + "─" * width)
    return lines


def _dashboard_rows(watcher: Any) -> List[Dict[str, Any]]:
    rows = watcher.get_dashboard_data()
    rows.sort(key=lambda r: (str(r["window"]), r.get("tab_index", 0)))
    return rows


def cmd_list(args: argparse.Namespace) -> int:
    """List all Konsole tabs with the agent (or 👤 USER) running in each."""
    from konsole_aware.daemon.watcher import TabWatcher
    watcher = TabWatcher()
    watcher.tick()
    rows = _dashboard_rows(watcher)

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("No active Konsole sessions detected.")
        return 0
    print("\n".join(_render_table(rows, "🔍 " + colorize("Konsole Aware - Tabs by Window (visual order)", bold=True))))
    print()
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Live interactive watcher in the terminal."""
    from konsole_aware.daemon.watcher import TabWatcher
    watcher = TabWatcher(poll_interval=args.interval)
    sys.stdout.write("\033[2J\033[H")
    try:
        while True:
            watcher.tick()
            lines = _render_table(_dashboard_rows(watcher),
                                  "⚡ " + colorize("Konsole Aware - Live Monitor", bold=True) + " (Ctrl+C to stop)")
            sys.stdout.write("\033[H" + "\n".join(l + "\033[K" for l in lines) + "\n\033[J")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nExiting monitor.")
    return 0


def cmd_watchdog(args: argparse.Namespace) -> int:
    """Internal: per-tab agent turn watcher (started detached by the agent hooks)."""
    from konsole_aware.agents import watchdog
    try:
        return watchdog.run(args.key, args.agent)
    except Exception:
        _log_error("watchdog")
        return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run background monitor daemon."""
    from konsole_aware.daemon.watcher import TabWatcher
    TabWatcher(poll_interval=args.interval).run_daemon()
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    """Switch Konsole view to a specific session."""
    kterm = KonsoleTerminal(service=args.service, session_path=f"/Sessions/{args.session}")
    if kterm.switch_to():
        print(f"✓ Switched to Konsole tab {args.session}.")
        return 0
    print(f"Failed to switch to session {args.session}.", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- snapping
def _organizer_for(args: argparse.Namespace) -> Tuple[TabOrganizer, Optional[str]]:
    """Organizer plus the window to act on (window containing --tab, or --window)."""
    tab = getattr(args, "tab", None)
    svc = getattr(args, "service", None)
    if tab is None and not svc:
        term = get_terminal()
        svc = getattr(term, "service", None)
    organizer = TabOrganizer(service=svc)
    window = None
    if getattr(args, "window", None):
        window = f"/Windows/{args.window}"
    elif tab is not None:
        found = organizer.find_session_window(str(tab))
        window = found[0] if found else None
    return organizer, window


def cmd_snap(args: argparse.Namespace) -> int:
    """Snap same-project tabs together with agent tabs (claude, agy, ...) side by side."""
    organizer, window = _organizer_for(args)
    if getattr(args, "tab", None) is not None and window is None:
        print(f"Error: tab {args.tab} not found.", file=sys.stderr)
        return 1
    results = [organizer.snap_window(window, dry_run=args.dry_run)] if window else organizer.snap_all(dry_run=args.dry_run)
    if not results:
        print("No Konsole windows found.", file=sys.stderr)
        return 1

    split_projects: Dict[str, set] = {}
    for res in results:
        w_idx = res["window"].rsplit("/", 1)[-1]
        verb = "would become" if args.dry_run else ("snapped" if res["changed"] else "already snapped")
        print(f"🧲 Window {w_idx}: {verb}")
        prev_project = None
        for t in res["tabs"]:
            project = Path(t["project"]).name if t["project"] else "-"
            marker = "  ┃" if prev_project == t["project"] and t["project"] else "  ┏" if t["project"] else "   "
            prev_project = t["project"]
            pin = "📌 " if t["anchored"] else ""
            print(f"{marker} tab {t['session_num']:>3}  {pin}{agent_identity.identity_tag(t['agent']):<11} {project}")
            if t["project"]:
                split_projects.setdefault(t["project"], set()).add(res["window"])
    multi = {p: w for p, w in split_projects.items() if len(w) > 1}
    for project, wins in multi.items():
        names = ", ".join(sorted(w.rsplit("/", 1)[-1] for w in wins))
        print(f"ℹ {Path(project).name} has tabs in windows {names}; use the tab menu's “Send Tab To…” to bring them together.")
    return 0


def cmd_organize(args: argparse.Namespace) -> int:
    """Organize/sort tabs in windows."""
    organizer, window = _organizer_for(args)
    sort_by = args.by or "status"
    if window:
        res = organizer.organize_window(window, sort_by=sort_by)
        print(f"✓ Window {window.rsplit('/', 1)[-1]} organized by {sort_by}: {' -> '.join(res)}")
    else:
        for win, sess_list in organizer.organize_all(sort_by=sort_by).items():
            print(f"✓ Window {win.rsplit('/', 1)[-1]} organized by {sort_by}: {' -> '.join(sess_list)}")
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    """Move a tab left, right, first, last, or to an exact index."""
    organizer = TabOrganizer(service=args.service or getattr(get_terminal(), "service", None))
    session_num = str(args.session)
    dest = args.destination.lower()

    found = organizer.find_session_window(session_num)
    if not found:
        print(f"Error: Session {session_num} not found in any window.", file=sys.stderr)
        return 1
    win_path, _ = found
    tabs = organizer.get_window_tabs(win_path)
    cur_idx = next(i for i, t in enumerate(tabs) if session_num in t)

    if dest == "left":
        target_idx = max(0, cur_idx - 1)
    elif dest == "right":
        target_idx = min(len(tabs) - 1, cur_idx + 1)
    elif dest == "first":
        target_idx = 0
    elif dest == "last":
        target_idx = len(tabs) - 1
    elif dest.isdigit():
        target_idx = max(0, min(len(tabs) - 1, int(dest)))
    else:
        print(f"Unknown destination '{dest}'. Use: left, right, first, last, or index.", file=sys.stderr)
        return 1

    organizer.move_session_to_index(win_path, session_num, target_idx)
    print(f"✓ Moved Tab {session_num} to index {target_idx}.")
    return 0


def _window_action(args: argparse.Namespace, action: str, ok_msg: str, fail_msg: str, **kw: Any) -> int:
    kterm, svc, num = _require_tab(args)
    if num is None:
        return 1
    organizer = TabOrganizer(service=svc)
    if getattr(organizer, action)(str(num), **kw):
        print(ok_msg.format(num=num))
        return 0
    print(fail_msg.format(num=num), file=sys.stderr)
    return 1


def cmd_detach(args: argparse.Namespace) -> int:
    """Detach a tab into its own independent Konsole window."""
    if args.session and args.tab is None:
        args.tab = int(args.session)
    return _window_action(args, "detach_tab", "✓ Tab {num} detached into a new window.", "Failed to detach tab {num}.")


def cmd_split(args: argparse.Namespace) -> int:
    """Split view horizontally or vertically."""
    orientation = args.orientation or "left-right"
    return _window_action(args, "split_view", f"✓ View split {orientation} for tab {{num}}.", "Failed to split view.",
                          orientation=orientation)


def cmd_equalize(args: argparse.Namespace) -> int:
    """Balance all split view sizes equally."""
    return _window_action(args, "equalize_splits", "✓ Balanced split views.", "Failed to balance split views.")


def cmd_maximize(args: argparse.Namespace) -> int:
    """Toggle maximize on current split view."""
    return _window_action(args, "toggle_maximize_split", "✓ Toggled maximize on split view.",
                          "Failed to toggle maximize.")


# --------------------------------------------------------------------------- tab settings
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
            print(f"  {pad_ansi(colored_tag, 24)} Tabs: {s_list_str}")
        print("  " + "─" * 70 + "\n")
        return 0

    if action == "auto":
        count = 0
        resolver = IconResolver()
        for s in KonsoleTerminal.list_all_sessions(service=args.service):
            cwd = s.get("cwd")
            if not cwd:
                continue
            root = resolver.find_project_root(cwd)
            if root == Path.home() or root == Path("/"):
                continue
            grp_mgr.assign_tab(s["session_key"], root.name)
            count += 1
        print(f"✓ Auto-grouped {count} tabs by project.")
        return 0

    kterm, svc, num = _require_tab(args)
    if num is None:
        return 1
    s_key = _session_key(svc, num)

    if action == "set":
        grp_name = (args.name or "").strip()
        if not grp_name:
            print("Error: group name required (konsole-aware group set <name>).", file=sys.stderr)
            return 1
        if args.color or args.icon:
            grp_mgr.define_group(grp_name, color=args.color, icon=args.icon)
        grp_mgr.assign_tab(s_key, grp_name)
        grp_info = grp_mgr.get_tab_group(s_key)
        if grp_info and kterm:
            kterm.set_tab_color(grp_info["color"])
            _refresh_title(kterm)
        print(f"✓ Tab {num} assigned to group '{grp_name}'.")
        return 0

    if action == "remove":
        grp_mgr.unassign_tab(s_key)
        if kterm:
            _refresh_title(kterm)
        print(f"✓ Tab {num} removed from groups.")
        return 0
    return 0


def cmd_anchor(args: argparse.Namespace) -> int:
    """Anchor (pin) a tab to the leftmost position."""
    kterm, svc, num = _require_tab(args)
    if num is None:
        return 1
    TabGroupManager().anchor_tab(_session_key(svc, num))
    organizer = TabOrganizer(service=svc)
    found = organizer.find_session_window(str(num))
    if found:
        anchored = set(TabGroupManager().get_all_anchored())
        tabs = organizer.get_window_tabs(found[0])
        # Keep pins in their existing order: go right after the last pinned tab.
        pinned = [i for i, t in enumerate(tabs) if _session_key(svc, int(t[0])) in anchored and str(num) not in t]
        organizer.move_session_to_index(found[0], str(num), (max(pinned) + 1) if pinned else 0)
    if kterm:
        _refresh_title(kterm)
    print(f"📌 Tab {num} anchored (pinned to front).")
    return 0


def cmd_unanchor(args: argparse.Namespace) -> int:
    """Unanchor a tab."""
    kterm, svc, num = _require_tab(args)
    if num is None:
        return 1
    TabGroupManager().unanchor_tab(_session_key(svc, num))
    if kterm:
        _refresh_title(kterm)
    print(f"✓ Tab {num} unanchored.")
    return 0


def cmd_set_job(args: argparse.Namespace) -> int:
    """Set or clear a permanent custom job/project name for a tab."""
    kterm, svc, num = _require_tab(args)
    if num is None or kterm is None:
        return 1
    s_key = kterm.get_session_id()
    updates: Dict[str, Any] = {}
    if args.clear:
        updates["job_name"] = None
        print(f"✓ Cleared custom job name for Tab {num} (reverted to auto project name).")
    elif args.name:
        updates["job_name"] = args.name.strip()
        print(f"✓ Set custom job name for Tab {num}: [{updates['job_name']}]")
    if getattr(args, "icon", None):
        updates["icon"] = args.icon.strip()
        print(f"✓ Assigned icon {updates['icon']} to Tab {num}.")
    if not updates:
        print("Error: give a job name, --icon, or --clear.", file=sys.stderr)
        return 1
    StateManager().set_session(s_key, **updates)
    _refresh_title(kterm)
    return 0


def cmd_set_icon(args: argparse.Namespace) -> int:
    """Assign or clear an explicit icon for a tab."""
    kterm, svc, num = _require_tab(args)
    if num is None or kterm is None:
        return 1
    s_key = kterm.get_session_id()
    if args.clear:
        StateManager().set_session(s_key, icon=None)
        print(f"✓ Cleared custom icon for Tab {num} (reverted to auto folder/project icon).")
    else:
        if not args.icon:
            print("Error: Specify icon (e.g. konsole-aware set-icon 🦀).", file=sys.stderr)
            return 1
        StateManager().set_session(s_key, icon=args.icon.strip())
        print(f"✓ Assigned icon {args.icon.strip()} to Tab {num}.")
    _refresh_title(kterm)
    return 0


def cmd_set_folder_icon(args: argparse.Namespace) -> int:
    """Set a custom folder icon in user configuration or list all mappings."""
    resolver = IconResolver()
    if args.list:
        print("\n  📂 " + colorize("Konsole Aware - Folder and Project File Icons", bold=True))
        print("  " + "─" * 60)
        print(colorize("  Folder Name Mappings:", bold=True))
        folders = resolver.get_folder_icons()
        for f in sorted(folders.keys()):
            print(f"    {folders[f]}  {f}")
        print("\n" + colorize("  Project Marker File Mappings:", bold=True))
        markers = resolver.get_project_file_icons()
        for m in sorted(markers.keys()):
            print(f"    {markers[m]}  {m}")
        print("  " + "─" * 60 + "\n")
        return 0

    if not args.folder or not args.icon:
        print("Error: Specify folder and icon (e.g. konsole-aware set-folder-icon contracts 📜).", file=sys.stderr)
        return 1
    config = load_config()
    folder = args.folder.strip().lower()
    config.setdefault("folder_icons", {})
    if not isinstance(config["folder_icons"], dict):
        config["folder_icons"] = {}
    config["folder_icons"][folder] = args.icon.strip()
    try:
        save_config(config)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"✓ Saved folder icon mapping: {folder} -> {args.icon.strip()}")
    return 0


def cmd_icon(args: argparse.Namespace) -> int:
    """Inspect the auto-picked icon and resolution reason for a directory or tab."""
    resolver = IconResolver()
    kterm, svc, num = resolve_target(args)
    s_key = kterm.get_session_id() if kterm else None
    target_dir = args.path or (kterm.get_cwd() if kterm else os.getcwd())
    icon, reason = resolver.resolve_with_reason(session_key=s_key, cwd=target_dir)
    print(f"Directory:       {target_dir}")
    if num is not None:
        print(f"Tab Session:     {num} ({s_key})")
    print(f"Resolved Icon:   {icon or '(none)'}")
    print(f"Resolution Rule: {reason}")
    return 0


def cmd_set_title(args: argparse.Namespace) -> int:
    """Set tab title directly with optional color tag and group/anchor awareness."""
    kterm, _svc, _num = resolve_target(args)
    term: BaseTerminal = kterm or get_terminal()
    title = args.title
    if args.color_tag:
        title = f"{get_color_tag(args.color_tag, square=args.square)} {title}"
    full_title = TitleComposer().compose(
        session_key=term.get_session_id(),
        status="busy" if args.busy else "idle",
        detail=title,
        cwd=kterm.get_cwd() if kterm else os.getcwd(),
    )
    term.set_title(full_title, role=0)
    print(f"Title set: {full_title}")
    return 0


def cmd_set_color(args: argparse.Namespace) -> int:
    """Set (or reset) a tab's color."""
    kterm, _svc, _num = resolve_target(args)
    term: BaseTerminal = kterm or get_terminal()
    c = args.color.lower().strip()
    if c in ("reset", "default", "none"):
        term.reset_tab_color()
        print("Color reset.")
        return 0
    ids = agent_identity.identities()
    if c in ids:
        hex_color = ids[c]["color"]
    else:
        hex_color = resolve_color_hex(args.color)
    term.set_tab_color(hex_color)
    print(f"Color set: {hex_color}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Reset tab title, color and badge back to Konsole's defaults."""
    kterm, _svc, _num = resolve_target(args)
    if kterm:
        kterm.reset_tab_color()
        kterm.set_badge("", enabled=False)
        kterm.set_tab_title_format(0, "%d : %n")
        kterm.set_tab_title_format(1, "(%u) %H")
    else:
        get_terminal().set_title("", role=0)
    print("Reset tab appearance.")
    return 0


def cmd_alert(args: argparse.Namespace) -> int:
    """Send test alert and notification."""
    Notifier().notify(title=args.title, message=args.message, urgency="normal", is_error=args.error, play_sound=True)
    print("Alert sent.")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Install konsole-aware into user environment, shells, agy, and claude."""
    repo_root = Path(__file__).resolve().parent.parent
    status = installer.install(repo_root, build_menu=not args.no_menu)
    print("\n🎉 Installation completed." if status == 0 else "\n⚠ Installation completed with errors (see above).")
    print("👉 Reload your shell: 'source ~/.zshrc' (or open a new Konsole tab)")
    return status


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Cleanly uninstall konsole-aware hooks and integrations."""
    installer.uninstall()
    print("\n✓ Uninstallation complete.")
    return 0


# --------------------------------------------------------------------------- parser
def _add_target(p: argparse.ArgumentParser) -> None:
    p.add_argument("--tab", type=int, help="Target tab session number (default: current tab)")
    p.add_argument("--service", help="Konsole D-Bus service of --tab (e.g. org.kde.konsole-1234)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="konsole-aware",
        description="Terminal & tab awareness for Konsole: AI agents (claude, agy, ...), tasks, "
                    "project snapping, grouping, anchoring and organization.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    p_agent = subparsers.add_parser("agent", help="AI agent lifecycle hook endpoint")
    p_agent.add_argument("agent", choices=["agy", "claude"], help="Agent type")
    p_agent.add_argument("event", help="Event name (SessionStart, PreToolUse, Stop, PreInvocation, ...)")
    p_agent.set_defaults(func=cmd_agent)

    p_preexec = subparsers.add_parser("preexec", help="Shell preexec hook")
    p_preexec.add_argument("--id", type=int, help="Per-shell command counter")
    p_preexec.add_argument("--shell-pid", type=int, help="PID of the calling shell")
    p_preexec.add_argument("command", nargs=argparse.REMAINDER, help="Command line")
    p_preexec.set_defaults(func=cmd_shell_preexec)

    p_precmd = subparsers.add_parser("precmd", help="Shell precmd hook")
    p_precmd.add_argument("--id", type=int, help="Counter of the command that just finished")
    p_precmd.add_argument("--shell-pid", type=int, help="PID of the calling shell")
    p_precmd.add_argument("exit_code", type=int, nargs="?", default=0, help="Exit code of finished command")
    p_precmd.set_defaults(func=cmd_shell_precmd)

    p_list = subparsers.add_parser("list", aliases=["status"], help="List all tabs with their agent / user identity")
    p_list.add_argument("--json", action="store_true", help="Output as JSON")
    p_list.set_defaults(func=cmd_list)

    p_switch = subparsers.add_parser("switch", aliases=["focus"], help="Switch focus to a tab/session")
    p_switch.add_argument("session", help="Session number (e.g. 1, 9)")
    p_switch.add_argument("--service", help="Konsole D-Bus service")
    p_switch.set_defaults(func=cmd_switch)

    p_snap = subparsers.add_parser(
        "snap", help="Snap tabs of the same project together, agent tabs (claude, agy, ...) side by side")
    _add_target(p_snap)
    p_snap.add_argument("--window", help="Only this window number (default: all windows, or the window of --tab)")
    p_snap.add_argument("--dry-run", action="store_true", help="Show the resulting order without moving tabs")
    p_snap.set_defaults(func=cmd_snap)

    p_group = subparsers.add_parser("group", help="Manage tab groupings and category colors")
    p_group.add_argument("group_action", choices=["set", "remove", "list", "auto"], help="Group action")
    p_group.add_argument("name", nargs="?", default="", help="Group name (e.g. frontend, backend, agents)")
    _add_target(p_group)
    p_group.add_argument("--color", help="Hex color or color name for this group")
    p_group.add_argument("--icon", help="Icon/emoji for this group (e.g. 🎨, 🤖, ⚙️)")
    p_group.set_defaults(func=cmd_group)

    p_anchor = subparsers.add_parser("anchor", aliases=["pin"], help="Anchor (pin) a tab to the leftmost position")
    _add_target(p_anchor)
    p_anchor.set_defaults(func=cmd_anchor)

    p_unanchor = subparsers.add_parser("unanchor", aliases=["unpin"], help="Unanchor a tab")
    _add_target(p_unanchor)
    p_unanchor.set_defaults(func=cmd_unanchor)

    p_org = subparsers.add_parser("organize", help="Organize and sort tabs in Konsole windows")
    p_org.add_argument("--by", choices=["status", "group", "name", "project", "id"], default="status",
                       help="Sort criteria (project = same-project tabs together, agents side by side)")
    p_org.add_argument("--window", help="Specific window number (e.g. 1, 2)")
    _add_target(p_org)
    p_org.set_defaults(func=cmd_organize)

    p_move = subparsers.add_parser("move", help="Move a tab left, right, first, last, or to an index")
    p_move.add_argument("session", help="Session number to move (e.g. 7)")
    p_move.add_argument("destination", help="Target: left, right, first, last, or index number")
    p_move.add_argument("--service", help="Konsole D-Bus service")
    p_move.set_defaults(func=cmd_move)

    p_detach = subparsers.add_parser("detach", help="Detach a tab into its own independent window")
    p_detach.add_argument("session", nargs="?", help="Session number to detach (default: current)")
    _add_target(p_detach)
    p_detach.set_defaults(func=cmd_detach)

    p_split = subparsers.add_parser("split", aliases=["clip"], help="Split view horizontally or vertically in window")
    p_split.add_argument("orientation", nargs="?", choices=["left-right", "top-bottom", "horizontal", "vertical"],
                         default="left-right")
    _add_target(p_split)
    p_split.set_defaults(func=cmd_split)

    p_equalize = subparsers.add_parser("equalize", help="Equalize sizes of all split views")
    _add_target(p_equalize)
    p_equalize.set_defaults(func=cmd_equalize)

    p_max = subparsers.add_parser("maximize-split", help="Toggle maximize on the active split view")
    _add_target(p_max)
    p_max.set_defaults(func=cmd_maximize)

    p_job = subparsers.add_parser("set-job", help="Set or clear a permanent custom job/project name for a tab")
    p_job.add_argument("name", nargs="?", default="", help="Custom job or project name")
    p_job.add_argument("--icon", help="Custom icon to assign alongside the job")
    _add_target(p_job)
    p_job.add_argument("--clear", action="store_true", help="Clear custom job name (revert to auto)")
    p_job.set_defaults(func=cmd_set_job)

    p_icon = subparsers.add_parser("set-icon", help="Assign or clear a custom icon for a tab")
    p_icon.add_argument("icon", nargs="?", default="", help="Icon/emoji (e.g. 🦀, 🐍, ⚡, 🚀)")
    _add_target(p_icon)
    p_icon.add_argument("--clear", action="store_true", help="Clear custom icon (revert to auto)")
    p_icon.set_defaults(func=cmd_set_icon)

    p_ficon = subparsers.add_parser("set-folder-icon", help="Configure folder name to icon mapping in user config")
    p_ficon.add_argument("folder", nargs="?", default="", help="Folder name (e.g. frontend, backend, tests)")
    p_ficon.add_argument("icon", nargs="?", default="", help="Icon/emoji to map to this folder name")
    p_ficon.add_argument("--list", action="store_true", help="List all folder and project file icon mappings")
    p_ficon.set_defaults(func=cmd_set_folder_icon)

    p_inspect_icon = subparsers.add_parser("icon", help="Inspect auto-detected icon and rule for current folder or tab")
    p_inspect_icon.add_argument("--path", help="Directory path to inspect (default: current directory)")
    _add_target(p_inspect_icon)
    p_inspect_icon.set_defaults(func=cmd_icon)

    p_title = subparsers.add_parser("set-title", help="Set current tab title")
    p_title.add_argument("title", help="Title string")
    p_title.add_argument("--color-tag", help="Prepend colored glyph (red, green, blue, yellow, purple, orange, etc.)")
    p_title.add_argument("--square", action="store_true", help="Use square color badge instead of circle")
    p_title.add_argument("--busy", action="store_true", help="Mark title as busy status")
    _add_target(p_title)
    p_title.set_defaults(func=cmd_set_title)

    p_color = subparsers.add_parser("set-color", help="Set (or reset) a tab color")
    p_color.add_argument("color", help="Color name (red, emerald, amber, blue, ...), hex code, or 'reset'")
    _add_target(p_color)
    p_color.set_defaults(func=cmd_set_color)

    p_reset = subparsers.add_parser("reset", help="Reset tab appearance")
    _add_target(p_reset)
    p_reset.set_defaults(func=cmd_reset)

    p_alert = subparsers.add_parser("alert", help="Send test alert notification & sound")
    p_alert.add_argument("title", help="Alert title")
    p_alert.add_argument("message", help="Alert body")
    p_alert.add_argument("--error", action="store_true", help="Mark as error alert")
    p_alert.set_defaults(func=cmd_alert)

    p_watch = subparsers.add_parser("watch", help="Live interactive terminal monitor")
    p_watch.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds")
    p_watch.set_defaults(func=cmd_watch)

    p_daemon = subparsers.add_parser("daemon", help="Run background monitor daemon")
    p_daemon.add_argument("--interval", type=float, default=1.5, help="Polling interval in seconds")
    p_daemon.set_defaults(func=cmd_daemon)

    p_wd = subparsers.add_parser("watchdog", help=argparse.SUPPRESS)
    p_wd.add_argument("--key", required=True)
    p_wd.add_argument("--agent", required=True)
    p_wd.set_defaults(func=cmd_watchdog)

    p_install = subparsers.add_parser("install", help="Install hooks and shell integration")
    p_install.add_argument("--no-menu", action="store_true", help="Skip building the tab context-menu hook")
    p_install.set_defaults(func=cmd_install)

    p_uninstall = subparsers.add_parser("uninstall", help="Uninstall hooks and shell integration")
    p_uninstall.set_defaults(func=cmd_uninstall)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # `group set [options] -- <name>` (used by the menu hook so names may start with '-'):
    # argparse fills both positionals before reading options, so pull the name out here.
    group_name = None
    if argv[:1] == ["group"] and "--" in argv:
        cut = argv.index("--")
        group_name = " ".join(argv[cut + 1:])
        argv = argv[:cut]
    args = parser.parse_args(argv)
    if group_name is not None:
        args.name = group_name
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    if getattr(args, "command", None) and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
