"""Bring Konsole windows that are already open onto the update-in-place loader.

Since 1.4, Konsole preloads a small loader (libkonsole_aware_menu.so) that loads the
real code (libkonsole_aware_module.so) and switches to a new copy whenever an update
replaces that file. Konsole windows opened before that run an older library that can't
do this. `konsole-aware hotload` loads the loader into each of those Konsole processes
with gdb, so they switch over without being closed. It only has to happen once per
running Konsole.

Attaching gdb to a running program needs root on most distributions
(/proc/sys/kernel/yama/ptrace_scope is 1), so this asks for your password once, with
pkexec (a desktop prompt) or sudo in a terminal. Konsole pauses for a moment while gdb
is attached.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

from konsole_aware.install import LOADER_NAME

MODULE_MARK = "konsole-aware/modules/module-"


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "konsole-aware" / "modules"


def konsole_pids() -> List[int]:
    """This user's Konsole processes."""
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            if (entry / "comm").read_text().strip() == "konsole":
                pids.append(int(entry.name))
        except OSError:
            continue
    return sorted(pids)


def has_loader(pid: int) -> bool:
    """True when the Konsole runs a module loaded by the loader (so it updates in place)."""
    try:
        return MODULE_MARK in Path(f"/proc/{pid}/maps").read_text(errors="ignore")
    except OSError:
        return False


def pending() -> List[int]:
    return [pid for pid in konsole_pids() if not has_loader(pid)]


def _loader_file() -> Optional[Path]:
    candidates = [Path.home() / ".local" / "lib" / LOADER_NAME,
                  Path(__file__).resolve().parent.parent / "menu_hook" / LOADER_NAME,
                  Path("/usr/lib/konsole-aware/menu_hook") / LOADER_NAME]
    return next((c for c in candidates if c.exists()), None)


def _ptrace_needs_root() -> bool:
    try:
        scope = int(Path("/proc/sys/kernel/yama/ptrace_scope").read_text().strip())
    except (OSError, ValueError):
        scope = 0
    return scope >= 1 and os.geteuid() != 0


def _gdb_command(gdb: str, pid: int, library: Path) -> List[str]:
    return [gdb, "-p", str(pid), "-batch", "-nx",
            "-iex", "set debuginfod enabled off", "-iex", "set auto-load off",
            "-ex", f'call (void*)dlopen("{library}", 2)', "-ex", "detach"]


def hotload(pids: Optional[List[int]] = None, out: Callable[[str], None] = print, wait: float = 6.0) -> int:
    pids = pending() if pids is None else pids
    if not pids:
        out("Every open Konsole already updates in place.")
        return 0
    gdb = shutil.which("gdb")
    if not gdb:
        out("hotload needs gdb (apt install gdb, or dnf install gdb). Or close and reopen Konsole instead.")
        return 1
    loader = _loader_file()
    if not loader:
        out("The loader library isn't installed yet. Run: konsole-aware install")
        return 1

    # A copy per Konsole under a name it hasn't loaded: dlopen hands back the library it
    # already has when the name matches, and the old 1.2 library has the installed name.
    folder = runtime_dir()
    folder.mkdir(parents=True, exist_ok=True)
    copies = []
    commands = []
    for pid in pids:
        copy = folder / f"loader-{pid}-{int(time.time())}.so"
        shutil.copy2(loader, copy)
        copies.append(copy)
        commands.append(_gdb_command(gdb, pid, copy))

    env = dict(os.environ, DEBUGINFOD_URLS="")
    if _ptrace_needs_root():
        script = " ; ".join(" ".join(_quote(a) for a in cmd) for cmd in commands)
        if sys.stdin.isatty() and shutil.which("sudo"):
            run = ["sudo", "sh", "-c", script]
        elif shutil.which("pkexec"):
            run = ["pkexec", "/bin/sh", "-c", script]
        else:
            out("Attaching to Konsole needs root and neither sudo (in a terminal) nor pkexec is available.")
            return 1
        out(f"Loading the update-in-place loader into {len(pids)} open Konsole "
            f"process{'es' if len(pids) > 1 else ''}. This asks for your password once.")
        result = subprocess.run(run, env=env, capture_output=True, text=True)
    else:
        result = None
        for cmd in commands:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result is not None and result.returncode not in (0,):
        out((result.stderr or result.stdout or "gdb failed").strip().splitlines()[-1])

    # The loader starts the module from Konsole's event loop, right after gdb lets go.
    deadline = time.time() + wait
    while time.time() < deadline and any(not has_loader(p) for p in pids):
        time.sleep(0.3)
    for copy in copies:
        try:
            copy.unlink()  # stays mapped in Konsole
        except OSError:
            pass
    status = 0
    for pid in pids:
        if has_loader(pid):
            out(f"✓ Konsole {pid} now updates in place.")
        else:
            out(f"✗ Konsole {pid} didn't take the loader. Close and reopen it to get the new version.")
            status = 1
    if not pending():
        # Every Konsole runs the 1.4 module now. The 1.2 library still loaded in the ones
        # just brought over paints from this file; without it, it has nothing to paint.
        from konsole_aware.tabs.styles import LEGACY_STYLES_FILE
        try:
            LEGACY_STYLES_FILE.unlink()
        except OSError:
            pass
    return status


def _quote(arg: str) -> str:
    return "'" + arg.replace("'", "'\\''") + "'"
