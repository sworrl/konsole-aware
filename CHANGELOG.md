# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

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

[1.1.0]: https://github.com/sworrl/konsole-aware/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/sworrl/konsole-aware/releases/tag/v1.0.0
