"""Install / uninstall konsole-aware hooks without disturbing the user's other hooks."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from konsole_aware.config import load_config

# Claude Code hook events konsole-aware listens to (all observational).
CLAUDE_EVENTS = [
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PermissionDenied",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "Elicitation",
    "ElicitationResult",
    "PostModelSwitch",
]

# Antigravity (agy) hook events. Tool events use the grouped matcher form.
AGY_FLAT_EVENTS = ["PreInvocation", "Stop"]
AGY_TOOL_EVENTS = ["PreToolUse", "PostToolUse"]
AGY_HOOK_NAME = "konsole-aware"

WRAPPER_MARKER = "# konsole-aware konsole wrapper"
SYSTEM_BIN = "/usr/bin/konsole-aware"


def is_system_install(repo_root: Path) -> bool:
    """True when running from a package install (/usr/lib/konsole-aware), not a user's clone."""
    return str(repo_root).startswith(("/usr/", "/opt/")) or not os.access(repo_root, os.W_OK)
DESKTOP_MARKER = "X-KonsoleAware=true"


def _is_ours(hook: Any, agent: str) -> bool:
    return isinstance(hook, dict) and f"konsole-aware agent {agent}" in str(hook.get("command", ""))


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def _write_json(path: Path, data: Dict[str, Any], backup: bool = True) -> None:
    """Atomically rewrite a JSON file, following symlinks (dotfile repos) and keeping its mode.

    The first backup (<name>.konsole-aware.bak) is never overwritten, so the pre-konsole-aware
    file stays recoverable across reinstalls.
    """
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o600
    if path.exists():
        mode = path.stat().st_mode & 0o7777
        bak = path.with_name(path.name + ".konsole-aware.bak")
        if backup and not bak.exists():
            shutil.copy2(path, bak)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, mode)
    tmp.replace(path)


def strip_claude_hooks(hooks: Dict[str, Any]) -> Dict[str, Any]:
    """Remove konsole-aware's entries from a Claude Code `hooks` object, keeping all others."""
    cleaned: Dict[str, Any] = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            cleaned[event] = groups
            continue
        new_groups = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                kept = [h for h in group["hooks"] if not _is_ours(h, "claude")]
                if kept:
                    new_groups.append({**group, "hooks": kept})
            elif not _is_ours(group, "claude"):
                new_groups.append(group)
        if new_groups:
            cleaned[event] = new_groups
    return cleaned


def merge_claude_hooks(settings: Dict[str, Any], hook_bin: str) -> Dict[str, Any]:
    """Return settings with konsole-aware's Claude hooks (re)registered for every event."""
    hooks = strip_claude_hooks(settings.get("hooks") or {})
    for event in CLAUDE_EVENTS:
        hooks.setdefault(event, []).append({
            "hooks": [{"type": "command", "command": f"{hook_bin} agent claude {event}", "timeout": 5}]
        })
    return {**settings, "hooks": hooks}


def remove_claude_hooks(settings: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(settings)
    if "hooks" in out:
        cleaned = strip_claude_hooks(out["hooks"] or {})
        if cleaned:
            out["hooks"] = cleaned
        else:
            del out["hooks"]
    return out


def agy_hook_def(hook_bin: str) -> Dict[str, Any]:
    cmd = f"{hook_bin} agent agy"
    definition: Dict[str, Any] = {"enabled": True}
    for event in AGY_FLAT_EVENTS:
        definition[event] = [{"type": "command", "command": f"{cmd} {event}", "timeout": 5}]
    for event in AGY_TOOL_EVENTS:
        definition[event] = [{
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"{cmd} {event}", "timeout": 5}],
        }]
    return definition


def install(repo_root: Path, home: Optional[Path] = None, build_menu: bool = True,
            out: Callable[[str], None] = print) -> int:
    home = home or Path.home()
    status = 0

    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    source_bin = repo_root / "bin" / "konsole-aware"
    if is_system_install(repo_root):
        # Installed by the .deb/.rpm: the package owns /usr/bin/konsole-aware, and hooks call it.
        target_bin = Path(SYSTEM_BIN) if Path(SYSTEM_BIN).exists() else source_bin
        out(f"✓ Using system install: {target_bin}")
    else:
        target_bin = local_bin / "konsole-aware"
        if target_bin.is_symlink() or target_bin.exists():
            target_bin.unlink()
        target_bin.symlink_to(source_bin)
        try:
            source_bin.chmod(0o755)
        except OSError:
            pass
        out(f"✓ Installed binary: {target_bin}")

    # Antigravity
    agy_hooks_file = home / ".gemini" / "config" / "hooks.json"
    try:
        hooks = _read_json(agy_hooks_file)
        hooks[AGY_HOOK_NAME] = agy_hook_def(str(target_bin))
        _write_json(agy_hooks_file, hooks)
        out(f"✓ Configured Antigravity lifecycle hooks: {agy_hooks_file}")
    except Exception as e:
        status = 1
        out(f"✗ Failed to configure Antigravity hooks ({agy_hooks_file} left unchanged): {e}")

    # Claude Code
    claude_settings_file = home / ".claude" / "settings.json"
    try:
        settings = _read_json(claude_settings_file)
        _write_json(claude_settings_file, merge_claude_hooks(settings, str(target_bin)))
        out(f"✓ Configured Claude Code hooks ({len(CLAUDE_EVENTS)} events): {claude_settings_file}")
    except Exception as e:
        status = 1
        out(f"✗ Failed to configure Claude Code hooks ({claude_settings_file} left unchanged): {e}")

    for rc_name, plugin in ((".zshrc", "konsole-aware.zsh"), (".bashrc", "konsole-aware.bash")):
        rc = home / rc_name
        plugin_path = repo_root / "shell" / plugin
        line = f'[[ -f "{plugin_path}" ]] && source "{plugin_path}" # konsole-aware'
        if rc.exists():
            content = rc.read_text(encoding="utf-8")
            if "# konsole-aware" not in content:
                with open(rc, "a", encoding="utf-8") as f:
                    f.write(f"\n{line}\n")
                out(f"✓ Added shell integration to {rc}")
            else:
                out(f"✓ Shell integration already present in {rc}")

    if build_menu:
        status |= _install_menu_hook(repo_root, home, local_bin, out)

    try:
        load_config()
    except Exception:
        pass
    return status


def _install_menu_hook(repo_root: Path, home: Path, local_bin: Path, out: Callable[[str], None]) -> int:
    menu_hook_dir = repo_root / "menu_hook"
    if not (menu_hook_dir / "Makefile").exists():
        return 0
    prebuilt = menu_hook_dir / "libkonsole_aware_menu.so"
    if is_system_install(repo_root):
        if not prebuilt.exists():
            out("⚠ Skipped tab context-menu hook (the package has no prebuilt library)")
            return 0
    else:
        try:
            subprocess.run(["make", "-C", str(menu_hook_dir)], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as e:
            detail = getattr(e, "stderr", b"") or b""
            out(f"⚠ Skipped tab context-menu hook (build failed: {detail.decode(errors='ignore').strip()[:200] or e})")
            return 0

    local_lib = home / ".local" / "lib"
    local_lib.mkdir(parents=True, exist_ok=True)
    target_so = local_lib / "libkonsole_aware_menu.so"
    shutil.copy2(prebuilt, target_so)
    out(f"✓ Installed context menu hook library: {target_so}")

    real_konsole = shutil.which("konsole", path="/usr/local/bin:/usr/bin:/bin") or "/usr/bin/konsole"
    wrapper = local_bin / "konsole"
    wrapper_text = wrapper.read_text(encoding="utf-8", errors="ignore") if wrapper.exists() else ""
    if wrapper.exists() and WRAPPER_MARKER not in wrapper_text and "libkonsole_aware_menu.so" not in wrapper_text:
        out(f"⚠ {wrapper} exists and is not ours; not replacing it (menu hook not wired up)")
        return 0
    wrapper.write_text(
        "#!/bin/bash\n"
        f"{WRAPPER_MARKER}\n"
        f'HOOK_LIB="{target_so}"\n'
        f'export KONSOLE_AWARE_BIN="{SYSTEM_BIN if is_system_install(repo_root) else local_bin / "konsole-aware"}"\n'
        'if [ -f "$HOOK_LIB" ]; then\n'
        '    export LD_PRELOAD="${HOOK_LIB}${LD_PRELOAD:+:$LD_PRELOAD}"\n'
        "fi\n"
        f'exec {real_konsole} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    out(f"✓ Installed konsole wrapper with menu hook: {wrapper}")

    apps_dir = home / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    sys_desktop = Path("/usr/share/applications/org.kde.konsole.desktop")
    local_desktop = apps_dir / "org.kde.konsole.desktop"
    if local_desktop.exists() and DESKTOP_MARKER not in local_desktop.read_text(encoding="utf-8", errors="ignore") \
            and str(wrapper) not in local_desktop.read_text(encoding="utf-8", errors="ignore"):
        out(f"⚠ {local_desktop} exists and is not ours; not replacing it")
        return 0
    if sys_desktop.exists():
        lines = []
        for line in sys_desktop.read_text(encoding="utf-8").splitlines():
            if line.startswith("Exec=konsole"):
                line = "Exec=" + str(wrapper) + line[len("Exec=konsole"):]
            lines.append(line)
            if line.strip() == "[Desktop Entry]":
                lines.append(DESKTOP_MARKER)
        local_desktop.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            subprocess.run(["update-desktop-database", str(apps_dir)], capture_output=True)
        except OSError:
            pass
        out(f"✓ Installed desktop application entry: {local_desktop}")
        out("  Restart Konsole (quit all windows) for the tab context menu to load.")
    return 0


def uninstall(home: Optional[Path] = None, out: Callable[[str], None] = print) -> int:
    home = home or Path.home()

    target_bin = home / ".local" / "bin" / "konsole-aware"
    if target_bin.is_symlink() or target_bin.exists():
        target_bin.unlink()
        out(f"✓ Removed binary: {target_bin}")

    wrapper = home / ".local" / "bin" / "konsole"
    if wrapper.exists():
        text = wrapper.read_text(encoding="utf-8", errors="ignore")
        if WRAPPER_MARKER in text or "libkonsole_aware_menu.so" in text:
            wrapper.unlink()
            out(f"✓ Removed konsole wrapper: {wrapper}")

    local_so = home / ".local" / "lib" / "libkonsole_aware_menu.so"
    if local_so.exists():
        local_so.unlink()
        out(f"✓ Removed context menu hook: {local_so}")

    local_desktop = home / ".local" / "share" / "applications" / "org.kde.konsole.desktop"
    if local_desktop.exists():
        text = local_desktop.read_text(encoding="utf-8", errors="ignore")
        if DESKTOP_MARKER in text or str(wrapper) in text:
            local_desktop.unlink()
            out(f"✓ Removed desktop override: {local_desktop}")

    agy_hooks_file = home / ".gemini" / "config" / "hooks.json"
    try:
        hooks = _read_json(agy_hooks_file)
        if AGY_HOOK_NAME in hooks:
            del hooks[AGY_HOOK_NAME]
            _write_json(agy_hooks_file, hooks)
            out(f"✓ Removed konsole-aware hooks from {agy_hooks_file}")
    except Exception as e:
        out(f"✗ Could not update {agy_hooks_file}: {e}")

    claude_settings_file = home / ".claude" / "settings.json"
    try:
        settings = _read_json(claude_settings_file)
        cleaned = remove_claude_hooks(settings)
        if cleaned != settings:
            _write_json(claude_settings_file, cleaned)
            out(f"✓ Removed konsole-aware hooks from {claude_settings_file} (other hooks kept)")
    except Exception as e:
        out(f"✗ Could not update {claude_settings_file}: {e}")

    for rc_name in (".zshrc", ".bashrc"):
        rc_path = home / rc_name
        if rc_path.exists():
            lines = rc_path.read_text(encoding="utf-8").splitlines()
            new_lines = [l for l in lines if "# konsole-aware" not in l]
            if new_lines != lines:
                rc_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                out(f"✓ Cleaned {rc_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(install(Path(__file__).resolve().parent.parent))
