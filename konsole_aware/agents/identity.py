"""Agent identity: which AI agent (if any) runs in a terminal session, and in which mode.

Every tab is labelled with exactly one identity so agent tabs are told apart at a
glance: ``✳️ CLAUDE``, ``🪐 AGY``, ... or ``👤 USER`` when no agent is running.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from konsole_aware.config import load_config

USER = "user"

DEFAULT_IDENTITIES: Dict[str, Dict[str, Any]] = {
    "claude": {"label": "CLAUDE", "icon": "✳️", "color": "#d97757", "dot": "🟠", "procs": ["claude", "claude-code"]},
    "agy": {"label": "AGY", "icon": "🪐", "color": "#4285f4", "dot": "🔵", "procs": ["agy", "antigravity", "antigravity-cli"]},
    "gemini": {"label": "GEMINI", "icon": "♊", "color": "#8e75ff", "dot": "🟣", "procs": ["gemini", "gemini-cli"]},
    "codex": {"label": "CODEX", "icon": "🧩", "color": "#10a37f", "dot": "🟢", "procs": ["codex"]},
    "aider": {"label": "AIDER", "icon": "🤝", "color": "#14b8a6", "dot": "🟢", "procs": ["aider"]},
    "copilot": {"label": "COPILOT", "icon": "🛩️", "color": "#6e40c9", "dot": "🟣", "procs": ["copilot", "github-copilot-cli"]},
    "cursor": {"label": "CURSOR", "icon": "🖱️", "color": "#64748b", "dot": "⚪", "procs": ["cursor-agent", "cursor"]},
    "opencode": {"label": "OPENCODE", "icon": "🔓", "color": "#f97316", "dot": "🟠", "procs": ["opencode"]},
    "openhands": {"label": "OPENHANDS", "icon": "🙌", "color": "#06b6d4", "dot": "🔵", "procs": ["openhands"]},
    USER: {"label": "USER", "icon": "👤", "color": "#6b7280", "dot": "⚫", "procs": []},
}

# Permission / execution modes, as reported by Claude Code (`permission_mode` in every
# hook payload) or parsed from an agent's command line (agy --mode / flags).
MODE_TAGS: Dict[str, str] = {
    "default": "",
    "plan": "📋 PLAN",
    "acceptEdits": "✏️ EDITS",
    "accept-edits": "✏️ EDITS",
    "auto": "🤖 AUTO",
    "dontAsk": "🔕 DONT-ASK",
    "bypassPermissions": "⚡ BYPASS",
    "dangerously-skip-permissions": "⚡ BYPASS",
    "sandbox": "📦 SANDBOX",
}

# Launchers that run the real agent as their first non-option argument.
_INTERPRETERS = {
    "node", "nodejs", "bun", "deno", "npx", "pnpx", "bunx", "env", "uv", "uvx", "pipx",
    "python", "python3", "sudo", "doas", "nice", "ionice", "nohup", "time", "exec",
}
_VERSION_RE = re.compile(r"^\d+(\.\d+)+")


def identities() -> Dict[str, Dict[str, Any]]:
    """Default identities merged with the user's `agent_identities` overrides."""
    merged = {k: dict(v) for k, v in DEFAULT_IDENTITIES.items()}
    for name, override in (load_config().get("agent_identities") or {}).items():
        if isinstance(override, dict):
            merged.setdefault(name, {"label": name.upper(), "icon": "🤖", "color": "#8b5cf6", "procs": [name]})
            merged[name].update(override)
    return merged


def identity(agent: Optional[str]) -> Dict[str, Any]:
    ids = identities()
    return ids.get(agent or USER) or {"label": (agent or USER).upper(), "icon": "🤖", "color": "#8b5cf6", "procs": []}


def identity_tag(agent: Optional[str]) -> str:
    """Return the tab-title identity tag, e.g. '✳️ CLAUDE', '🪐 AGY' or '👤 USER'."""
    ident = identity(agent)
    return f"{ident.get('icon', '')} {ident.get('label', '')}".strip()


def agent_dot(agent: Optional[str]) -> str:
    """Return the tab-title colored dot glyph for the agent, e.g. '🟠' for Claude, '🔵' for AGY."""
    ident = identity(agent)
    return str(ident.get("dot", "🟣"))


def mode_tag(mode: Optional[str]) -> str:
    if not mode:
        return ""
    return MODE_TAGS.get(mode, f"[{mode}]")


def agent_rank(agent: Optional[str]) -> int:
    """Ordering used when snapping tabs: configured agent order first, plain shells last."""
    if not agent or agent == USER:
        return 10_000
    order = load_config().get("agent_order") or []
    return order.index(agent) if agent in order else len(order)


def _proc_names() -> Dict[str, str]:
    names: Dict[str, str] = {}
    for agent, ident in identities().items():
        for proc in ident.get("procs", []):
            names[proc] = agent
    return names


def detect_agent_from_argv(argv: List[str]) -> Optional[str]:
    """Identify an agent from a process argv (handles node/bun launchers and versioned binaries)."""
    names = _proc_names()
    tokens = [t for t in argv if t]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        base = os.path.basename(tok)
        stem = re.sub(r"\.(js|mjs|cjs|py|sh)$", "", base)
        if stem in names:
            return names[stem]
        # Claude Code native installs run ~/.local/share/claude/versions/<x.y.z>
        if _VERSION_RE.match(base):
            parts = Path(tok).parts
            for agent in ("claude", "agy", "gemini", "codex"):
                if agent in parts or f".{agent}" in parts:
                    return names.get(agent, agent)
        # npm installs: node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js
        if "/node_modules/" in tok:
            for pkg, agent in (("claude-code", "claude"), ("gemini-cli", "gemini"), ("codex", "codex")):
                if f"/{pkg}/" in tok or tok.endswith(f"/{pkg}"):
                    return agent
        if base in _INTERPRETERS or "=" in tok or tok.startswith("-"):
            i += 1
            continue
        return None
    return None


def _read_argv(pid: int) -> List[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [a.decode(errors="ignore") for a in f.read().split(b"\x00") if a]
    except OSError:
        return []


def _read_comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        return ""


def _children_map() -> Dict[int, List[int]]:
    children: Dict[int, List[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return children
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "r", encoding="utf-8", errors="ignore") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(entry))
    return children


def detect_agent_pid(pid: int, depth: int = 3) -> Tuple[Optional[str], Optional[int]]:
    """Return (agent, pid) for the first agent process at `pid` or among its descendants."""
    if pid <= 0:
        return None, None
    agent = detect_agent_from_argv(_read_argv(pid)) or _proc_names().get(_read_comm(pid))
    if agent:
        return agent, pid
    if depth <= 0:
        return None, None
    children = _children_map()
    frontier = [pid]
    for _ in range(depth):
        nxt: List[int] = []
        for p in frontier:
            for child in children.get(p, []):
                agent = detect_agent_from_argv(_read_argv(child)) or _proc_names().get(_read_comm(child))
                if agent:
                    return agent, child
                nxt.append(child)
        frontier = nxt
    return None, None


def find_agent_ancestor(expected: Optional[str] = None) -> Tuple[Optional[str], Optional[int]]:
    """Walk up from this process (an agent hook) to the agent process that spawned it."""
    pid = os.getppid()
    for _ in range(12):
        if pid <= 1:
            break
        agent = detect_agent_from_argv(_read_argv(pid)) or _proc_names().get(_read_comm(pid))
        if agent and (expected is None or agent == expected):
            return agent, pid
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return None, None


def ancestor_pids() -> set:
    """PIDs of this process and all its ancestors."""
    pids, pid = set(), os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def pid_alive(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except (OSError, IndexError):
        return False


def mode_from_argv(argv: List[str]) -> Optional[str]:
    """Parse a mode from an agent command line (--mode X, --permission-mode X, skip-permission flags)."""
    mode = None
    for i, tok in enumerate(argv):
        if tok in ("--mode", "--permission-mode") and i + 1 < len(argv):
            mode = argv[i + 1]
        elif tok.startswith("--mode=") or tok.startswith("--permission-mode="):
            mode = tok.split("=", 1)[1]
        elif tok == "--dangerously-skip-permissions" and mode is None:
            mode = "dangerously-skip-permissions"
    return mode


def agent_mode_from_pid(pid: Optional[int]) -> Optional[str]:
    return mode_from_argv(_read_argv(pid)) if pid else None


def resolve_session_agent(
    state: Optional[Dict[str, Any]],
    foreground_pid: int = -1,
    shell_pid: int = -1,
) -> Tuple[str, Optional[int]]:
    """Decide which agent a session is running right now.

    Hook-recorded state wins while its agent process is alive; otherwise the session's
    foreground process tree is inspected. Returns (agent_or_'user', agent_pid).
    """
    state = state or {}
    agent, agent_pid = state.get("agent"), state.get("agent_pid")
    if agent and agent != USER and pid_alive(agent_pid):
        return agent, agent_pid
    if foreground_pid > 0 and foreground_pid != shell_pid:
        found, found_pid = detect_agent_pid(foreground_pid)
        if found:
            return found, found_pid
    if agent and agent != USER and not agent_pid and state.get("status") in ("busy", "waiting"):
        # Hook-recorded agent without a pid (non-Konsole terminal): trust it while active.
        return agent, None
    return USER, None


# ---------------------------------------------------------------- model / effort
EFFORT_SHORT = {"low": "lo", "medium": "med", "high": "hi", "xhigh": "xhi", "max": "max"}
DEFAULT_MODEL_ICONS = {"fable": "💎", "opus": "", "sonnet": "", "haiku": ""}


def short_model(model: Any) -> str:
    """claude-opus-4-8-20260101 -> Opus 4.8; claude-fable-5-1 -> Fable 5.1; other ids pass through."""
    text = str(model or "").strip()
    m = re.match(r"(?:claude-)?(fable|opus|sonnet|haiku|mythos)-(\d+)(?:[-.](\d{1,2}))?(?:-\d{8})?", text, re.I)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
    m = re.match(r"claude-(\d+)(?:-(\d))?-([a-z]+)", text)  # claude-3-5-sonnet-20241022
    if m:
        return f"{m.group(3).capitalize()} {m.group(1)}" + (f".{m.group(2)}" if m.group(2) else "")
    m = re.match(r"gemini-(\d+(?:\.\d+)?)-?([a-z]+)?", text, re.I)  # gemini-3-pro -> Gemini 3 Pro
    if m:
        return f"Gemini {m.group(1)}" + (f" {m.group(2).capitalize()}" if m.group(2) else "")
    text = re.sub(r"\[1m\]$", "", text)
    return text if len(text) <= 18 else text[:17] + "…"


def model_tag(model: Optional[str], effort: Optional[str] = None) -> str:
    """'💎 Fable 5.1·hi', 'Opus 5.5·max', 'Gemini 3 Pro' ... ('' when unknown)."""
    if not model and not effort:
        return ""
    cfg = load_config()
    if not cfg.get("show_agent_model", True):
        return ""
    name = short_model(model) if model else ""
    icons = {**DEFAULT_MODEL_ICONS, **(cfg.get("model_icons") or {})}
    family = name.split(" ")[0].lower() if name else ""
    icon = icons.get(family, "")
    eff = EFFORT_SHORT.get(str(effort or "").lower(), str(effort or "")) if effort else ""
    text = f"{name}·{eff}" if name and eff else (name or f"effort {eff}")
    return f"{icon} {text}".strip()

