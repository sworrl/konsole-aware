"""Looks for new konsole-aware releases on GitHub and installs them.

The Konsole menu library runs `konsole-aware update check --auto` a little after
Konsole starts and then every hour. That only goes to GitHub when the last check is
older than `update_check_hours`, so the hourly runs are cheap. When a newer release
is out, it shows a notification once for that version and writes
$XDG_CACHE_HOME/konsole-aware/update.json:

    {"checked": 1791600000, "current": "1.2.0", "latest": "1.3.0", "show": true,
     "url": "https://github.com/sworrl/konsole-aware/releases/tag/v1.3.0",
     "assets": {"konsole-aware_1.3.0_amd64.deb": "https://..."}, "notified": "1.3.0",
     "skipped": null, "snooze_until": 0, "method": "deb"}

The library watches that file and shows a tray icon while "show" is true. The tray
menu installs the update (in a Konsole window, since it asks for your password),
opens the release page, skips this version, or snoozes the reminder for a day.

Set "update_check": false in the config to turn all of this off.
"""

import fcntl
import json
import os
import platform
import shutil
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from konsole_aware import __version__
from konsole_aware.config import load_config

REPO = "sworrl/konsole-aware"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "konsole-aware"


UPDATE_FILE = cache_dir() / "update.json"


@contextmanager
def _locked() -> Iterator[None]:
    UPDATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(UPDATE_FILE.with_name(".update.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read() -> Dict[str, Any]:
    try:
        data = json.loads(UPDATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: Dict[str, Any]) -> None:
    tmp = UPDATE_FILE.with_name(f".update.json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(UPDATE_FILE)


def parse_version(text: str) -> Tuple[int, ...]:
    """(1, 3, 0) for "v1.3.0" or "1.3.0". Anything after the numbers is ignored."""
    parts = []
    for piece in str(text or "").strip().lstrip("vV").split("."):
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return bool(parse_version(latest)) and parse_version(latest) > parse_version(current)


def fetch_latest(timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    """The latest release from the GitHub API, or None if GitHub can't be reached."""
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"konsole-aware/{__version__}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("tag_name") or data.get("draft") or data.get("prerelease"):
        return None
    return {
        "latest": str(data["tag_name"]).lstrip("vV"),
        "url": data.get("html_url") or f"{RELEASES_URL}/latest",
        "assets": {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []
                   if a.get("name") and a.get("browser_download_url")},
    }


def install_method() -> str:
    """How this copy was installed: "deb", "rpm", "git" (a checkout), or "unknown"."""
    here = Path(__file__).resolve()
    if str(here).startswith("/usr/"):
        if shutil.which("dpkg") and subprocess.run(["dpkg", "-S", str(here)], capture_output=True).returncode == 0:
            return "deb"
        if shutil.which("rpm") and subprocess.run(["rpm", "-qf", str(here)], capture_output=True).returncode == 0:
            return "rpm"
        return "unknown"
    if (here.parent.parent / ".git").exists():
        return "git"
    return "unknown"


def _settle(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in "current" and "show" from the rest of the state."""
    state["current"] = __version__
    latest = state.get("latest") or ""
    state["show"] = bool(
        is_newer(latest, __version__)
        and state.get("skipped") != latest
        and time.time() >= float(state.get("snooze_until") or 0)
    )
    return state


def status() -> Dict[str, Any]:
    with _locked():
        return _settle(_read())


def check(force: bool = False, notify: bool = True, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Look for a new release. Without `force`, only when the last look is older than
    `update_check_hours` (and never with `update_check` off). Notifies once per version."""
    config = config or load_config()
    with _locked():
        state = _read()
        before = dict(state)
        interval = float(config.get("update_check_hours", 12)) * 3600
        due = force or (config.get("update_check", True)
                        and time.time() - float(state.get("checked") or 0) >= interval)
        if due:
            found = fetch_latest()
            state["checked"] = time.time()
            if found:
                state.update(found)
                state["error"] = None
            else:
                state["error"] = "GitHub could not be reached"
        _settle(state)
        state["method"] = state.get("method") or install_method()
        latest = state.get("latest") or ""
        announce = notify and state["show"] and state.get("notified") != latest
        if announce:
            state["notified"] = latest
        if state != before:
            _write(state)
    if announce:
        _toast(state)
    return state


def _toast(state: Dict[str, Any]) -> None:
    from konsole_aware.notify.notifier import Notifier
    Notifier().notify(
        f"konsole-aware {state['latest']} is out",
        f"You have {state['current']}. Use the tray icon to update, or run: konsole-aware update install",
        icon="system-software-update",
        play_sound=False,
        transient=False,
    )


def skip(version: Optional[str] = None) -> Dict[str, Any]:
    with _locked():
        state = _read()
        state["skipped"] = version or state.get("latest")
        _write(_settle(state))
        return state


def snooze(hours: float = 24) -> Dict[str, Any]:
    with _locked():
        state = _read()
        state["snooze_until"] = time.time() + hours * 3600
        _write(_settle(state))
        return state


def _asset(state: Dict[str, Any], method: str) -> Optional[Tuple[str, str]]:
    arch = platform.machine()
    deb_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(arch, arch)
    for name, url in (state.get("assets") or {}).items():
        if method == "deb" and name.endswith(f"_{deb_arch}.deb"):
            return name, url
        if method == "rpm" and name.endswith(f".{arch}.rpm"):
            return name, url
    return None


def _download(name: str, url: str) -> Path:
    target = cache_dir() / "downloads" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": f"konsole-aware/{__version__}"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(target, "wb") as out:
        shutil.copyfileobj(resp, out)
    return target


def _run(cmd: list) -> int:
    print("$ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.call(cmd)


def install(out=print) -> int:
    """Install the latest release the same way this copy was installed. Runs in a
    terminal: the package manager asks for your password."""
    state = check(force=True, notify=False)
    if not is_newer(state.get("latest") or "", __version__):
        out(f"konsole-aware {__version__} is the latest release.")
        return 0
    method = state.get("method") or install_method()
    out(f"Updating konsole-aware {__version__} to {state['latest']} ({method}).")
    repo_root = Path(__file__).resolve().parent.parent
    if method in ("deb", "rpm"):
        asset = _asset(state, method)
        if not asset:
            out(f"The release has no .{method} for this machine. Download it from {state.get('url')}")
            return 1
        try:
            path = _download(*asset)
        except Exception as e:
            out(f"Download failed: {e}")
            return 1
        if method == "deb":
            cmd = ["sudo", "apt-get", "install", "-y", str(path)]
        elif shutil.which("dnf"):
            cmd = ["sudo", "dnf", "install", "-y", str(path)]
        elif shutil.which("zypper"):
            cmd = ["sudo", "zypper", "--non-interactive", "install", "--allow-unsigned-rpm", str(path)]
        else:
            cmd = ["sudo", "rpm", "-Uvh", str(path)]
        if _run(cmd) != 0:
            out("The package manager didn't finish the install.")
            return 1
        binary = "/usr/bin/konsole-aware"
    elif method == "git":
        if _run(["git", "-C", str(repo_root), "pull", "--ff-only"]) != 0:
            out(f"git pull didn't go through. Update {repo_root} by hand and run: konsole-aware install")
            return 1
        binary = str(repo_root / "bin" / "konsole-aware")
    else:
        out(f"Can't tell how konsole-aware was installed. Get {state['latest']} from {state.get('url')}")
        return 1
    # The new version sets itself up for this user and repaints the open tabs.
    _run([binary, "install"])
    subprocess.call([binary, "refresh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.call([binary, "update", "check", "--auto"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Konsole windows with the loader switch to the new module by themselves. Ones
    # started before 1.4 get the loader now; this terminal can ask for the password.
    from konsole_aware import hotload
    if hotload.pending():
        hotload.hotload(out=out)
    out(f"konsole-aware {state['latest']} is installed. Open Konsole windows switch to it on their own.")
    return 0
