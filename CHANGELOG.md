# Changelog

Every change worth knowing about is listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.4.0] - 2026-10-10

### Added
- Updates without restarting Konsole. Konsole now preloads a small loader that never changes, and the right-click
  menu, tab fills, terminal tint and tray icon live in a module the loader reloads whenever an update replaces it.
  Open Konsole windows switch to the new version within about a second, for source installs, `update install`, and
  package upgrades through apt, dnf or Discover alike. Uninstalling takes konsole-aware out of open windows too.
- `konsole-aware hotload` brings Konsole windows that were opened before 1.4 (or restored by Plasma at login) onto the
  loader without closing them. It loads the loader with gdb and asks for your password once. `--check` lists which
  Konsole processes update in place. `update install` runs it when needed.

### Changed
- The right-click menu fills itself in from Konsole's `aboutToShow` instead of hooking `QMenu::exec`, so it can be
  swapped while Konsole runs.
- Tab fill specs moved to `styles/tab-styles-2.json`. A 1.2 library still loaded in a Konsole reads the old file,
  which `hotload` removes once every Konsole runs the new module, so the old code has nothing left to paint.
- The screenshot hook (`KONSOLE_AWARE_GRAB`) takes requests as `<path>.grab`.

## [1.3.0] - 2026-10-09

### Added
- Update check. konsole-aware looks for new releases on GitHub (at most every 12 hours, `update_check_hours`), sends
  one notification per new version, and shows a tray icon until you update or dismiss it. The tray menu has Install
  Update…, What's New, Remind Me Tomorrow and Skip This Version. `"update_check": false` turns it off.
- `konsole-aware update` (`status`, `check`, `install`, `skip`, `later`). `update install` updates the way you
  installed: the new `.deb` through apt, the new `.rpm` through dnf, zypper or rpm, or `git pull` for a checkout.
- Every release also has `konsole-aware_amd64.deb` and `konsole-aware.x86_64.rpm`, so
  `releases/latest/download/...` links always get the newest package.
- `flag` pattern: the American flag, with stripes and a canton of stars. About one tab in ten gets it at random
  (`flag_chance`, 0 turns it off). A tab keeps it while it's open. Also in `tab-style`, `colors set --pattern` and the
  Tab Pattern menu.

### Changed
- A terminal shows the same pattern as its tab, drawn at the terminal's size. Two-tone, stripes and the flag used to
  turn into a plain left-to-right blend in the terminal. The strip along the top of the terminal is the top edge of
  the same pattern.
- Gradients are mixed in OKLab, so the middle of a blend stays bright instead of going gray or brown.
- Two-tone has a short blend where the colors meet instead of a hard edge.
- New project palette and rainbow: hues evenly spaced around the wheel at matching lightness. Projects on an old
  palette color move to the new color in the same slot. A one-color gradient pairs with a neighboring hue instead of
  one a third of the way around the wheel.
- Tabs have a soft light along their outer edge, unselected tabs a thin edge in their own colors, and titles a
  one-pixel shadow where the pattern has light and dark parts.

## [1.2.0] - 2026-10-09

### Added
- Tab fills painted by the menu library: two-tone, gradient, rainbow, stripes and solid. Titles are drawn in black or
  white, whichever reads better on the fill. New `tab-style` command and a Tab Pattern menu.
- Every project gets its own color, remembered across sessions (`colors`, `colors auto`, `colors set`,
  `colors reset`). By default a tab is two-tone: the project's color, then the agent or state color.
- The selected tab stands out. It's drawn at full strength, reaches down to its terminal and has an accent bar; the
  other tabs are drawn softer.
- Each terminal's background is tinted with its tab's colors, with a 3 px strip of the fill along the top
  (`terminal_tint`, `terminal_band`). Panes of a split tab use the tab's colors, so attached or paired tabs match.
- `colors position on|off` and a Colors Follow Tab Position menu item color tabs by position instead. Off by default.
- A named agent session shows its name as the project: Claude Code `/rename` or `--name`, and agy `--project`. `list`
  shows the same name.
- Ultracode shows in the title, read from the transcript. Background work (workflows, background agents and shells,
  teammates) shows too, and a turn that ends with work still running doesn't show as done.
- One combined title on every pane of a split tab that holds several agents.
- `title_order` setting.
- `KONSOLE_AWARE_GRAB` saves a picture of each Konsole window, so screenshots can be taken with
  `QT_QPA_PLATFORM=offscreen` and nothing showing on the desktop.

### Changed
- The project comes first in tab titles.
- The Claude hook timeout is 10 s. Tool events from inside subagents return early when the tab was redrawn under a
  second ago.
- Right-click menu labels no longer have decorative icons. Color swatches, agent icons and the icon picker keep theirs.
  Project & Job Context is now Project Name.
- Plainer wording in CLI output, notifications, the README and the other docs.

### Fixed
- The installer replaced the menu library in place, which could crash or hang a running Konsole that had it loaded.
  It now writes a new file and renames it.
- Tab fills never showed. The library looked them up under a D-Bus name it had cached before Konsole registered its
  own.
- Dragging or moving a tab gave it a neighbor's colors. Tabs are now painted by their real index, and the library
  notices when tabs change places.
- Konsole could crash when a tab was moved, because the library asked Konsole for its sessions while the move was
  half done. That lookup now waits until the move is finished.
- The main turn's Stop no longer clears workflow subagents that are still running.
- agy PreToolUse is no longer registered, since agy treats an empty reply as "deny".

## [1.1.0] - 2026-10-01

### Packaging
- `.deb` and `.rpm` packages built by the release workflow, and a one-line installer (`get.sh`).

### Added
- Agent name at the start of every tab title (Claude, agy, Gemini, Codex, Aider and others), or USER when no agent runs.
- Model and effort in the title (`Fable 5.1·max`, `Opus 5.5·hi`), kept current across `/model`, `/effort` and automatic switches.
- All Claude Code permission modes and 18 hook events, including subagents, compaction, elicitation, `StopFailure`,
  `PostModelSwitch` and `SessionEnd`.
- Separate states for provider safeguard blocks and usage limits.
- Per-tab watcher that closes turns ended without a hook (Esc interrupts, crashed agents).
- Project snapping (`snap`, `organize --by project`, auto-snap when an agent starts) with agents side by side.
- `attach`, `pair`, `detach-pane`, and an Attach Tab To… menu entry next to Detach Tab.
- Recognition of ssh sessions, dev servers, log follows, monitors, package managers and interactive programs.
- `list --service`, `refresh`, and a dashboard with agent, model, project, state and duration columns.

### Changed
- D-Bus calls go through GLib in-process instead of spawning `qdbus` per call.
- Tab titles elide at the end (context-menu library), keeping the agent identity visible.
- Toasts fade after 4 s, are rate-limited across tabs, and play one sound.
- Title/color/badge updates are deduplicated and tool-by-tool updates throttled.
- State moved to a private `$XDG_RUNTIME_DIR` directory with file locking.

### Fixed
- agy PreToolUse hook auto-approved every tool call; Claude PreToolUse returned an invalid decision.
- Claude hooks read the wrong payload keys and leaked terminal escape codes into the model context.
- `uninstall` deleted all Claude Code hooks; `install` overwrote existing ones.
- Context menu passed the wrong object to Konsole internals and targeted the active tab instead of the clicked one.
- Shell hooks raced (stuck "busy" tabs), bash DEBUG trap fired for prompt internals, and tests drove the live terminal.

## [1.0.0]

- Initial release: tab titles, colors and badges for Konsole; agy and Claude Code hooks; tab grouping, anchoring,
  organizing, detaching and split views; context menu.

[Unreleased]: https://github.com/sworrl/konsole-aware/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/sworrl/konsole-aware/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/sworrl/konsole-aware/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/sworrl/konsole-aware/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/sworrl/konsole-aware/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/sworrl/konsole-aware/releases/tag/v1.0.0
