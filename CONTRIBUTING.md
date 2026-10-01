# Contributing to konsole-aware

Thanks for helping. Bug reports, agent and terminal integrations, and fixes are all welcome.

## Setup

```bash
git clone https://github.com/sworrl/konsole-aware.git
cd konsole-aware
python3 -m pytest -q        # hermetic test suite
make -C menu_hook           # optional: build the Konsole context-menu library (needs Qt 6 dev headers)
```

The package uses only the Python standard library (plus GLib's `gi` when present). Run it straight from the clone
with `bin/konsole-aware`.

## Guidelines

- **Hooks must stay observational and fast.** Agent hooks run synchronously inside Claude Code and agy. Never print
  to stdout from a Claude hook (it reaches the model's context), never return a permission decision, and keep each
  hook well under a second.
- **Never write to the user's terminal from tests.** `tests/conftest.py` redirects config and state and disables
  D-Bus; keep new tests hermetic.
- **No blind overwrites of user files.** Installer changes must merge and be reversible by `uninstall`.
- **Konsole internals** in `menu_hook/` must be resolved at runtime and degrade gracefully when missing.
- Match the surrounding style; add a test for every behavior change.

## Adding an agent

1. Add an entry to `DEFAULT_IDENTITIES` in `konsole_aware/agents/identity.py` (label, icon, color, process names).
2. If the agent has lifecycle hooks, add a handler next to `agents/claude.py` / `agents/agy.py` built on
   `BaseAgentHandler.render()`, and register it in `install.py`.
3. Add tests for its event mapping and for process detection.

## Pull requests

- One topic per PR, with a short description of the user-visible change.
- `python3 -m pytest -q` must pass; CI also builds the menu library.
- Update `README.md` and `CHANGELOG.md` when behavior or options change.
