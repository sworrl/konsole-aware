# Contributing to konsole-aware

Bug reports, new agents, other terminals and fixes are all welcome.

## Setup

```bash
git clone https://github.com/sworrl/konsole-aware.git
cd konsole-aware
python3 -m pytest -q        # the tests don't need Konsole and don't touch your setup
make -C menu_hook           # optional: builds the loader and module (needs the Qt 6 headers)
```

The Python side only uses the standard library, plus GLib's `gi` when it's installed. Run it straight from the clone
with `bin/konsole-aware`.

## Ground rules

- The agent hooks only watch, and they have to be quick. They run inside Claude Code and agy while the agent waits.
  Don't print to stdout from a Claude hook (it ends up in the model's context), don't return a permission decision,
  and keep each hook well under a second.
- Tests never write to your real terminal. `tests/conftest.py` points config and state at a temp folder and turns
  D-Bus off; new tests should do the same.
- The installer never overwrites a user's file. It merges its own entries in, and `uninstall` takes only those back
  out.
- Anything in `menu_hook/` that reaches into Konsole's internals is looked up when Konsole starts, and the feature
  that needs it is turned off if it isn't there. It must never crash Konsole.
- Everything the module sets up has to come down in `ka_module_stop()`, because updates swap the module inside running
  Konsole windows. Parent new objects to `root()`, use `root()` as the context of every connection and timer, and
  undo any change to Konsole's own widgets there.
- Don't open windows on the desktop to test the menu library. Run Konsole with `QT_QPA_PLATFORM=offscreen` and use
  `KONSOLE_AWARE_GRAB` to save a picture of it (see the README's environment variables).
- Match the code around your change, and add a test for every change in behavior.

## Adding an agent

1. Add it to `DEFAULT_IDENTITIES` in `konsole_aware/agents/identity.py`: label, icon, color and process names.
2. If the agent has lifecycle hooks, add a handler next to `agents/claude.py` and `agents/agy.py`, built on
   `BaseAgentHandler.render()`, and register its hooks in `install.py`.
3. Add tests for its event mapping and its process detection.

## Pull requests

- One topic per pull request, with a short note on what changes for users.
- `python3 -m pytest -q` has to pass. CI also builds the menu library.
- Update `README.md` and `CHANGELOG.md` when behavior or options change.
