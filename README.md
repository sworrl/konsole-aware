# konsole-aware

Tab title, taskbar, color, and alert awareness for KDE Konsole and Linux terminal emulators.

`konsole-aware` tracks AI coding agents (`agy`, `claude`, `aider`, `copilot`), file transfers (`rsync`, `scp`, `curl`, `wget`, `rclone`), compilations (`cargo`, `make`, `npm`), and long-running shell commands. It updates tab titles and taskbar entries in real time, colors tabs based on execution state, displays badges, and dispatches desktop notifications with audio alerts when tasks finish in unfocused tabs.

## Features

- **Agent Lifecycle Integration**: Hooks directly into Antigravity (`agy`) and Claude Code (`claude`). Displays exact states such as `🧠 agy: thinking...`, `🛠️ agy: cmd(cargo test)`, `📝 agy: edit(main.py)`, `❓ agy: question`, and `✅ [Ready] agy`.
- **KDE Konsole DBus Control**:
  - Sets tab title (`Session.setTitle 0`) and window title / taskbar entry (`Session.setTitle 1`).
  - Colors tabs: Amber (`#f59e0b`) while active, Emerald Green (`#10b981`) upon completion, Red (`#ef4444`) on failure, and Purple (`#8b5cf6`) when user input or permission is required.
  - Draws badges (`Session.setBadgeText`, `Session.setBadgeEnabled`).
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

The installer performs five steps:
1. Links `bin/konsole-aware` to `~/.local/bin/konsole-aware`.
2. Registers lifecycle hooks in `~/.gemini/config/hooks.json` for Antigravity (`agy`).
3. Registers hooks in `~/.claude/settings.json` for Claude Code (`claude`).
4. Sources `shell/konsole-aware.zsh` in `~/.zshrc`.
5. Sources `shell/konsole-aware.bash` in `~/.bashrc`.

Reload your shell or open a new terminal tab:

```bash
source ~/.zshrc
```

## CLI Usage

### View Active Tabs

```bash
konsole-aware list
```

Example output:
```text
  🔍 Konsole Aware - Active Tab Status
  ──────────────────────────────────────────────────────────────────────────────
  WIN  TAB    ACT  TITLE                    STATUS             DURATION    
  ──────────────────────────────────────────────────────────────────────────────
  1    1      ○    Katamari                 ⚡ agy              14s         
  1    7      ●    📥 [rsync] /data         📥 rsync           1m 24s      
  2    9      ●    ✅ [Ready] agy            ✅ agy             12s         
  ──────────────────────────────────────────────────────────────────────────────
```

Output raw JSON:
```bash
konsole-aware list --json
```

### Live Interactive Monitor

Monitor all tabs in a persistent terminal view:

```bash
konsole-aware watch
```

### Focus a Tab

Switch the active view in Konsole directly to tab 7:

```bash
konsole-aware switch 7
```

### Manual Visual Controls

Scripts or one-off jobs can set tab properties directly:

```bash
# Set tab title
konsole-aware set-title "📥 [Syncing 45%]"

# Set tab color (hex)
konsole-aware set-color "#f59e0b"

# Reset tab appearance back to normal
konsole-aware reset

# Send a desktop alert with audio
konsole-aware alert "Backup Done" "Data synchronization completed."
```

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
  "known_agents": ["agy", "claude", "aider", "copilot", "cursor", "gemini", "openhands"],
  "known_transfers": ["rsync", "scp", "sftp", "rclone", "ftp", "bcp"],
  "known_downloads": ["curl", "wget", "aria2c", "axel", "yt-dlp"],
  "known_builds": ["cargo", "make", "ninja", "cmake", "go", "mvn", "gradle", "npm", "yarn", "pnpm", "rustc", "gcc", "clang", "g++"]
}
```

### Key Options

- `min_duration_seconds`: Commands shorter than this number of seconds will not trigger desktop notifications or audio bells.
- `alert_only_unfocused`: When set to `true`, desktop alerts and sounds only fire if the tab is not the currently active tab in its window.
- `enable_tab_colors`: Enables or disables Konsole tab background highlighting.

## Uninstallation

To remove hooks, aliases, and shell sources:

```bash
./uninstall.sh
```

## Tests

Run the test suite with Python's built-in test runner:

```bash
python3 -m unittest discover tests
```
