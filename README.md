# konsole-aware

Tab title, taskbar, color, and alert awareness for KDE Konsole and Linux terminal emulators.

`konsole-aware` tracks AI coding agents (`agy`, `claude`, `aider`, `copilot`), file transfers (`rsync`, `scp`, `curl`, `wget`, `rclone`), compilations (`cargo`, `make`, `npm`), and long-running shell commands. It updates tab titles and taskbar entries in real time, colors tabs based on execution state, displays badges, and dispatches desktop notifications with audio alerts when tasks finish in unfocused tabs.

## Features

- **Agent Identity on Every Tab**: Each tab says who is in it: `✳️ CLAUDE`, `🪐 AGY` (also `♊ GEMINI`, `🧩 CODEX`, `🤝 AIDER`, ...) or `👤 USER` when no agent is running. The identity comes first in the title, so it survives Konsole eliding long tab titles, and is repeated in the top-right badge (`✳️ CLAUDE · THINK`).
- **Agent Lifecycle Integration**: Hooks into Claude Code (`claude`) and Antigravity (`agy`) and shows exact states: thinking, tool use (`Bash(cargo test)`, `edit(main.py)`), waiting for permission, questions, plan ready, subagents, compaction, done, failed, and back to `👤 USER` when the agent exits. The hooks are purely observational: they never approve or block tool calls.
- **Agent Modes**: Claude Code permission modes (`📋 PLAN`, `✏️ EDITS`, `🤖 AUTO`, `🔕 DONT-ASK`, `⚡ BYPASS`; default shows nothing) and agy modes (`--mode plan|accept-edits`, `--dangerously-skip-permissions`) appear next to the identity.
- **Project Snapping**: When `claude` and `agy` run in the same project, their tabs are snapped side by side automatically (and `konsole-aware snap` tidies whole windows: one cluster per project, agents first).
- **Permanent Job / Project Context**: Tab titles permanently display the active project or directory context (`[{job_name}]`) resolved from the Git repository root or directory, overridable with `konsole-aware set-job`.
- **Dual Simultaneous Color-Coding**:
  - Task category is displayed as a colored dot in the tab title (`🟣` Agent, `🟠` Transfer, `🟡` Build, `🟢` Test, `🔵` VCS, `🟤` Download).
  - Execution state is displayed as the physical Konsole tab background color (Amber `#f59e0b` active, Emerald `#10b981` completed, Red `#ef4444` failed, Purple `#8b5cf6` awaiting user input).
- **KDE Konsole DBus Control**:
  - Sets tab title (`Session.setTitle 0`) and window title / taskbar entry (`Session.setTitle 1`).
  - Colors tabs: Amber (`#f59e0b`) while active, Emerald Green (`#10b981`) upon completion, Red (`#ef4444`) on failure, and Purple (`#8b5cf6`) when user input or permission is required.
  - Draws badges (`Session.setBadgeText`, `Session.setBadgeEnabled`).
- **Tab Right-Click Context Menu Integration**: Extends Konsole's native tab context menu with settings for tab colors, pinning, grouping, custom icons, view splits, organizing, and **Send Tab To...** (sending the tab to any other open Konsole window or detaching into a new window).
- **Secondary Terminal Support**: Writes standard ANSI OSC sequences (OSC 0, OSC 1, OSC 2, OSC 30, OSC 777, OSC 9) for foot, kitty, wezterm, alacritty, tmux, and xterm.
- **Smart Focus-Aware Alerts**: Sends desktop notifications via `notify-send` and plays audio via `paplay`, `pw-play`, or `aplay`. If you are already looking at the tab, loud alerts can stay silent while the tab title updates.
- **Task Recognition**: Classifies commands automatically from shell hooks (`zsh` and `bash`) without requiring command wrappers.
- **Live Tab Dashboard**: `konsole-aware list` lists all active tabs across all Konsole windows with foreground processes, active focus, and elapsed runtimes.
- **Tab Switching**: `konsole-aware switch <tab_num>` brings any Konsole tab into immediate focus.
- **Optional Background Daemon**: Runs via systemd or terminal to observe background tabs even when shell hooks are bypassed.

## Installation

Run the installation script:

```bash
./install.sh
```

Or invoke the CLI installer:

```bash
python3 bin/konsole-aware install
```

The installer performs these steps (re-running it is safe):
1. Links `bin/konsole-aware` to `~/.local/bin/konsole-aware`.
2. Registers a `konsole-aware` hook in `~/.gemini/config/hooks.json` for Antigravity (`agy`).
3. Merges hooks for every Claude Code lifecycle event into `~/.claude/settings.json`, keeping your existing hooks (a `.konsole-aware.bak` backup is written first).
4. Sources `shell/konsole-aware.zsh` in `~/.zshrc`.
5. Sources `shell/konsole-aware.bash` in `~/.bashrc`.
6. Builds the tab context-menu hook and a `~/.local/bin/konsole` wrapper that loads it (skip with `install --no-menu`).

Reload your shell or open a new terminal tab:

```bash
source ~/.zshrc
```

## CLI Usage

### View Active Tabs

```bash
konsole-aware list
```

Example output (tabs in visual order, with the agent running in each):
```text
  🔍 Konsole Aware - Tabs by Window (visual order)
  ─────────────────────────────────────────────────────────────────────────────────────────────────────
  WIN  TAB  ACT PIN AGENT              PROJECT              STATUS                       DURATION  GROUP
  ─────────────────────────────────────────────────────────────────────────────────────────────────────
  2    13   ●   -   ✳️ CLAUDE ⚡ BYPASS konsole_aware        tool: Bash(pytest -q)        1m 12s    -
  2    9    ○   -   🪐 AGY ⚡ BYPASS    konsole_aware        done: ready in 41s           41s       -
  2    7    ○   -   👤 USER            konsole_aware        Idle                         -         -
  ─────────────────────────────────────────────────────────────────────────────────────────────────────
```

Output raw JSON:
```bash
konsole-aware list --json
```

### Agent Identity, Modes and States

Every title starts with who is in the tab, then the mode (if any), the project, and the state:

```text
✳️ CLAUDE [🐍 konsole_aware] 🧠 thinking…
✳️ CLAUDE 📋 PLAN [🐍 konsole_aware] ❓ plan ready for review
🪐 AGY ⚡ BYPASS [🐍 konsole_aware] 🛠️ cmd(cargo test)
👤 USER [🐍 konsole_aware] 🟡 ⏳ cargo build
👤 USER [🐍 konsole_aware] 💤
```

Claude Code events handled: `SessionStart` (startup/resume/clear/compact), `UserPromptSubmit`, `PreToolUse`
(incl. `AskUserQuestion` and `ExitPlanMode`), `PermissionRequest`, `PermissionDenied`, `PostToolUse`,
`PostToolUseFailure` (incl. interrupts), `PostModelSwitch`, `Notification` (`permission_prompt`, `idle_prompt`, `elicitation_dialog`),
`Elicitation`/`ElicitationResult`, `SubagentStart`/`SubagentStop`, `PreCompact`/`PostCompact`, `Stop`,
`StopFailure`, and `SessionEnd`. Antigravity events handled: `PreInvocation`, `PreToolUse` (incl. `ask_question`),
`PostToolUse`, and `Stop` (with `terminationReason`).

**Safeguard blocks.** When Claude's safeguards flag a request (often a false positive on legitimate security or
systems work), the tab says so instead of looking finished or generically failed:

- Hard block (`API Error: …'s safeguards flagged this message`): `✳️ CLAUDE [proj] 🛡️ blocked by safeguards [cyber] · rephrase or /model`, pink tab, `BLOCKED` badge, and a desktop alert.
- Automatic model switch after a flag ("Switch models when a message is flagged"): `🛡️ flagged [cyber]: Fable 5.1 → Opus 4.8` while it keeps working, and `ready in 2m 3s · 🛡️ on Opus 4.8` when done.
- Model switches for other reasons (overload, unavailable) show as `switched: A → B` without the shield.

Detection uses the `StopFailure`/`Stop`/`PostModelSwitch` hook payloads plus the current turn's transcript entries
(`stop_reason: "refusal"`, `stop_details.category`), so a reply that merely talks about safeguards is not mistaken for a block.

**Waiting means you can answer now.** `❓ waiting` (purple) only appears while a question, plan approval, or
permission prompt is open. A finished turn stays `✅ ready` even if Claude's last message asked you something.

**Turn ends no hook reports.** Pressing Esc in Claude Code fires no `Stop` hook, and agy has no exit hook. A small
per-tab watcher (started at each turn, exits when it ends) and every `list`/`watch`/`daemon` scan close such turns:
Claude's transcript shows `[Request interrupted by user…]` (→ `interrupted`) or a `turn_duration` record
(→ `ready in …`), and an agent process that exited returns the tab to `👤 USER`. Disable with `"agent_watchdog": false`.

### Package Managers and System Updates

Package managers (`apt`, `apt-get`, `nala`, `pacman`, `dnf`, `zypper`, `snap`, `flatpak`, `brew`) and system updates (including `sudo apt-get update`, `sudo apt full-upgrade`, `pacman -Syu`) are automatically classified with dedicated action icons, badges, and progress tracking:

- **Repository update** (`sudo apt-get update`, `pacman -Sy`): `[proj] 🔄 apt update`, `UPDT` badge, and sky blue tab color.
- **System upgrade** (`sudo apt full-upgrade`, `pacman -Syu`): `[proj] ⬆️ apt full-upgrade`, `UPGR` badge.
- **Package install** (`sudo apt install ripgrep`): `[proj] 📥 apt install ripgrep`, `INST` badge.
- **Removal** (`sudo apt autoremove`): `[proj] 🗑️ apt autoremove`, `RM` badge.

Sudo options (such as `sudo -E`, `sudo -u`, or environment variables) are parsed transparently so the command target and action are always recognized.

### Glanceable Titles with Many Tabs

When 10 or more tabs are open, Konsole shrinks each tab to 12-16 visible characters. To prevent tabs from degrading into identical `... ready in Xs` strings:

- Redundant filler is removed: finished turns display `✅ ready (55s)` or `✅ 55s` instead of repeating `ready in ...`.
- User tabs running active commands omit the redundant `👤 USER` prefix so the project and command remain visible.
- Overlong project names are constrained by `"max_job_length": 16` so they do not crowd out status icons.
- Konsole's tab bar hook sets `Qt::ElideRight` and enables tab scroll buttons so project and agent identities at the front of the tab are never truncated.

Tab colors use `"tab_color_mode": "agent"` by default so each agent's tab is immediately glanceable and distinguishable (Claude terracotta `#d97757` with `🟠`, AGY electric blue `#4285f4` with `🔵`, Gemini purple `#8e75ff` with `🟣`, Codex green `#10a37f` with `🟢`). Urgent attention states override the color (purple for user input/permission, red for failure, orange for usage limit, pink for provider safeguard blocks). Set `"tab_color_mode": "status"` if you prefer uniform state-based colors across all agents, or `"agent_status"` to keep agent colors during execution and transition to emerald green when done.

### Snapping Agent Tabs Together

When an agent starts in the active tab, that tab moves next to the other tabs of the same project in its window
(agents first, in `agent_order`: claude, then agy, ...). To tidy whole windows:

```bash
konsole-aware snap              # every window
konsole-aware snap --window 2   # one window
konsole-aware snap --dry-run    # show the resulting order without moving anything
konsole-aware organize --by project   # same as snap
```

Projects are the Git root (or the nearest project marker below a workspace folder that holds many projects).
Konsole cannot move tabs between windows over D-Bus; `snap` reports projects split across windows, and the tab
menu's **Send Tab To...** moves them. Disable automatic snapping with `"auto_snap_agents": false`.

### Permanent Job and Project Context

Tab titles permanently display the repository or directory context with auto-detected or assigned icons:
`✳️ CLAUDE [🐍 konsole_aware] 🧠 thinking…`
`🪐 AGY [🧪 konsole_aware/tests] 🛠️ cmd(...)`

Override the detected project name with a permanent custom job name and optional icon:

```bash
# Set custom job name for the current tab
konsole-aware set-job "Indexer"

# Set custom job name and icon together
konsole-aware set-job "ETL-Pipeline" --icon "⚡" --tab 4

# Clear custom job name to revert back to auto Git/directory detection
konsole-aware set-job --clear
```

### Folder & Project Icons (Tab Titles and Top-Right Badges)

Icons are automatically picked based on the folder you are in or your project's marker files, and rendered both in the tab title (`[🧪 myproject/tests]`) and next to the top-right Konsole badge (`🧪 THINK`, `🦀 TOOL`, `🐍 DONE`):

#### 1. Auto-Picked Icons

- **Subfolders in Project**:
  - `frontend/`, `client/`, `ui/` -> `🎨`
  - `backend/`, `server/`, `api/` -> `⚙️` or `🔌`
  - `tests/`, `test/`, `spec/` -> `🧪`
  - `docs/`, `doc/` -> `📚`
  - `scripts/`, `bin/`, `tools/` -> `📜` or `🛠️`
  - `infra/`, `terraform/`, `k8s/` -> `☁️` or `☸️`
  - `docker/`, `compose/` -> `🐳`
  - `mobile/`, `ios/`, `android/` -> `📱` or `🍎`
  - `data/`, `models/`, `notebooks/` -> `📊` or `🧠`

- **Project Root Marker Files**:
  - `Cargo.toml` -> `🦀` (Rust)
  - `pyproject.toml`, `requirements.txt`, `setup.py` -> `🐍` (Python)
  - `package.json`, `tsconfig.json` -> `📦` or `🟦` (Node / TypeScript)
  - `go.mod` -> `🐹` (Go)
  - `pom.xml`, `build.gradle` -> `☕` (Java)
  - `Makefile`, `CMakeLists.txt` -> `🛠️` (C / C++)
  - `Dockerfile`, `docker-compose.yml` -> `🐳` (Docker)
  - `.git` -> `🐙` (Git)

#### 2. Top-Right Konsole Badge Integration

Konsole's top-right badge displays the resolved icon directly next to the state text:
- `🐍 THINK` (when agent is thinking)
- `🐍 TOOL` (when agent is executing tools)
- `🧪 TEST` (when test runner is executing)
- `🦀 BLD` (when compilation is in progress)
- `🐍 DONE` (when task finished successfully)
- `🐍 ERR` (when task failed)
- `📌 PIN` (when tab is pinned)

#### 3. Assigning and Customizing Icons

```bash
# Assign custom icon to current tab (or --tab <num>)
konsole-aware set-icon 🚀
konsole-aware set-icon 🦀 --tab 4

# Clear custom icon (reverts to auto folder/project detection)
konsole-aware set-icon --clear

# Inspect which icon and rule was auto-picked for a directory or tab
konsole-aware icon
konsole-aware icon --path ./tests

# Configure custom folder name mappings in user config
konsole-aware set-folder-icon contracts 📜

# List all built-in and user folder and file icon mappings
konsole-aware set-folder-icon --list
```

You can also drop a `.konsole-icon` file (containing an emoji like `🔥`) or a `.konsole-aware.json` file (`{"icon": "🔥"}`) into any project or subfolder to lock its icon.

### Dual Color-Coding: Tasks vs Status

`konsole-aware` displays both task category and execution status simultaneously.

**Task Categories (Tab Title Dot):**

| Category | Dot | Example Commands |
| :--- | :--- | :--- |
| Agent | `🟣` | `agy`, `claude`, `aider` |
| File Transfer | `🟠` | `rsync`, `scp`, `sftp`, `rclone` |
| Download | `🟤` | `curl`, `wget`, `aria2c`, `yt-dlp` |
| Build / Compile | `🟡` | `cargo build`, `make`, `ninja`, `cmake`, `npm run build` |
| Test Runner | `🟢` | `cargo test`, `pytest`, `npm test`, `jest`, `vitest` |
| Version Control | `🔵` | `git commit`, `git push`, `git rebase` |
| Container | `🟦` | `docker`, `podman`, `kubectl` |
| Archive | `📦` | `tar`, `zip`, `unzip`, `gzip` |
| Shell Command | `⚪` | Generic commands |
| Idle / Ready | `⚫` | Prompt awaiting command |

**Execution Status (Konsole Tab Background & Glyph):**

| Status | Color | Tab Background | Glyph | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| Busy / Running | Amber | `#f59e0b` | `⏳` / `🧠` / `🛠️` | Command or agent actively working |
| Done / Success | Emerald | `#10b981` | `✅` | Process exited with code 0 |
| Error / Failed | Red | `#ef4444` | `❌` | Process exited with non-zero code |
| Waiting / Prompt | Purple | `#8b5cf6` | `❓` | Agent waiting for user answer or confirmation |
| Idle | Blue | `#3b82f6` | `💤` | Shell sitting at prompt |

### Tab Grouping

Assign tabs to custom color-coded groups:

```bash
# Assign current tab (or --tab 9) to group 'agents' with purple color and icon
konsole-aware group set agents --tab 9 --color "#8b5cf6" --icon "🤖"

# Assign tab to 'backend' group
konsole-aware group set backend --tab 4 --color "#3b82f6" --icon "⚙️"

# Automatically group all open tabs by their active project directory
konsole-aware group auto

# List all groups and assigned tabs
konsole-aware group list

# Remove tab from group
konsole-aware group remove --tab 9
```

### Tab Anchoring (Pinning)

Anchor critical tabs to lock them at the leftmost position:

```bash
# Pin current tab or tab 9 to the far left
konsole-aware anchor --tab 9

# Unpin tab
konsole-aware unanchor --tab 9
```

Anchored tabs carry a `📌` indicator in titles, display a `PIN` badge, and remain pinned to the front whenever tabs are sorted or organized.

### Tab Organizing and Sorting

Reorder tabs across windows automatically:

```bash
# Organize tabs: Anchored tabs first, then Busy tabs, then Done, then Idle
konsole-aware organize --by status

# Same-project tabs together, agent tabs (claude, agy, ...) side by side
konsole-aware organize --by project

# Group all tabs of the same category or project together
konsole-aware organize --by group

# Sort tabs alphabetically by title or by session ID
konsole-aware organize --by name
konsole-aware organize --by id

# Target a specific window (e.g. Window 2)
konsole-aware organize --by status --window 2
```

### Moving Tabs Manually

Shift tabs within their window:

```bash
konsole-aware move 9 left
konsole-aware move 9 right
konsole-aware move 9 first
konsole-aware move 9 last
konsole-aware move 9 2  # Move directly to index 2
```

### Detaching Tabs to New Windows

Detach any tab into its own independent Konsole window:

```bash
# Detach tab 7 into a new window
konsole-aware detach 7

# Detach currently active tab
konsole-aware detach
```

### Reclipping and Splitting Views

Clip multiple views together side-by-side or stacked inside the same tab:

```bash
# Split view horizontally (side by side)
konsole-aware split left-right

# Split view vertically (top/bottom)
konsole-aware split top-bottom

# Balance all split view sizes equally
konsole-aware equalize

# Toggle maximize on the focused split view
konsole-aware maximize-split
```

### Colored Text and Badges

Apply color tags to titles and tint tabs:

```bash
# Prepend colored Unicode glyph (red, green, blue, yellow, purple, orange)
konsole-aware set-title "Production Logs" --color-tag red

# Use square color tags
konsole-aware set-title "Build Worker" --color-tag emerald --square

# Set tab background color directly (hex or color name)
konsole-aware set-color "#f59e0b"
konsole-aware set-color emerald

# Reset tab appearance back to normal
konsole-aware reset

# Send a desktop alert with audio
konsole-aware alert "Backup Done" "Data synchronization completed."
```

### Tab Right-Click Context Menu

When right-clicking on any tab in Konsole, `konsole-aware` extends the native context menu with direct options:

- **📌 Anchor / Pin Tab** & **📍 Unpin Tab**: Lock tabs to the far left.
- **🎨 Tab Color**: Set tab background directly to Emerald (`#10b981`), Amber (`#f59e0b`), Red (`#ef4444`), Purple (`#8b5cf6`), Blue (`#3b82f6`), Reset Color, or select a Custom Color via color picker.
- **🏷️ Assign Group**: Place tabs into groups (Agents, Frontend, Backend, Data, Infrastructure, Custom, or Remove).
- **📂 Project Context**: Set a permanent custom job name or reset back to the auto-detected Git root.
- **🎯 Tab Icon**: Select from common development icons (🦀, 🐍, 📦, 🐹, ☕, 🐳, 🎨, ⚙️, 🧪, 🚀, 🔥, ⚡, 💎, 📜), pick a custom emoji, or revert to auto.
- **🪟 Send Tab To...**:
  - **➕ New Window (Detach)**: Detach the tab into its own independent window.
  - **🪟 Window: [Title]**: Transfer the tab and its live process directly to any other open Konsole window!
- **✂️ Split View**: Split horizontally (left/right), vertically (top/bottom), or equalize split view sizes.
- **🧲 Snap Project Tabs Together**: Cluster same-project tabs with agent tabs side by side.
- **🧹 Organize Tabs**: Automatically sort tabs in the window by status, group, name, or project.

The context menu hook is built into `~/.local/lib/libkonsole_aware_menu.so` and loaded whenever Konsole is launched from `~/.local/bin/konsole` or the desktop application shortcut (quit and restart Konsole after installing). It acts on the tab you right-clicked, not the active tab, and removes itself from `LD_PRELOAD` so it is not loaded into the shells and programs running in your tabs.

### Run as a Background Daemon (Optional)

To continuously poll tabs and catch long-running tasks regardless of how they were launched:

```bash
# In terminal
konsole-aware daemon

# Or via systemd user service
mkdir -p ~/.config/systemd/user
cp systemd/konsole-aware.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now konsole-aware.service
```

## Configuration

Settings are stored at `~/.config/konsole-aware/config.json`. The file is created automatically on first run with these defaults:

```json
{
  "version": 1,
  "min_duration_seconds": 3.0,
  "alert_sound": true,
  "alert_sound_file": "/usr/share/sounds/freedesktop/stereo/complete.oga",
  "alert_sound_error_file": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
  "alert_only_unfocused": true,
  "enable_tab_colors": true,
  "enable_badges": true,
  "enable_desktop_notifications": true,
  "colors": {
    "busy": "#f59e0b",
    "done": "#10b981",
    "error": "#ef4444",
    "waiting": "#8b5cf6",
    "idle": "#3b82f6",
    "reset": "#000000"
  },
  "show_agent_identity": true,
  "show_agent_mode": true,
  "agent_identities": {},
  "agent_order": ["claude", "agy", "gemini", "codex", "aider", "copilot", "cursor", "opencode"],
  "auto_snap_agents": true,
  "snap_include_user_sessions": true,
  "tab_color_mode": "agent",
  "known_agents": ["agy", "claude", "aider", "copilot", "cursor-agent", "gemini", "codex", "opencode", "openhands"],
  "known_transfers": ["rsync", "scp", "sftp", "rclone", "ftp", "bcp"],
  "known_downloads": ["curl", "wget", "aria2c", "axel", "yt-dlp"],
  "known_builds": ["cargo", "make", "ninja", "cmake", "go", "mvn", "gradle", "npm", "yarn", "pnpm", "rustc", "gcc", "clang", "g++"]
}
```

### Key Options

- `min_duration_seconds`: Commands shorter than this number of seconds will not trigger desktop notifications or audio bells.
- `alert_only_unfocused`: When set to `true`, desktop alerts and sounds only fire if the tab is not the currently active tab in its window.
- `enable_tab_colors`: Enables or disables Konsole tab background highlighting.
- `agent_identities`: Override an agent's tag, e.g. `{"claude": {"icon": "🟠", "label": "CC"}}`, or add a new agent: `{"myagent": {"icon": "🦾", "label": "MINE", "procs": ["myagent"]}}`.
- `agent_order`: Order of agent tabs inside a snapped project cluster.
- `auto_snap_agents`: Snap a tab next to its project's other tabs when an agent starts in it.
- `snap_include_user_sessions`: Also pull plain `👤 USER` shells of the same project into the cluster.

State lives in `$XDG_RUNTIME_DIR/konsole-aware/` (private to your user). Hook errors never reach the agent; they are logged to `errors.log` there.
## Uninstallation

To remove hooks, aliases, and shell sources (only konsole-aware's own hook entries are removed; your other Claude Code and agy hooks are kept):

```bash
./uninstall.sh
```

## Tests

The suite is hermetic (temporary config/state, no D-Bus, no writes to your terminal):

```bash
python3 -m pytest -q
```
