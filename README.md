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
  ──────────────────────────────────────────────────────────────────────────────────────────────
  WIN  TAB   ACT  PIN  GROUP          TITLE                      STATUS               DURATION  
  ──────────────────────────────────────────────────────────────────────────────────────────────
  1    1     ○    -    -              Katamari                   ⚡ agy                14s       
  1    7     ●    -    [transfers]    📥 [rsync] /data           📥 rsync             1m 24s    
  2    9     ●    📌   [agents]       ✅ [Ready] agy             ✅ agy               12s       
  ──────────────────────────────────────────────────────────────────────────────────────────────
```

Output raw JSON:
```bash
konsole-aware list --json
```

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
