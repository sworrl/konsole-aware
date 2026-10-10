# Security

konsole-aware runs as hooks inside your coding agents and your shell, and as a library loaded into Konsole. Please
report anything that could make it run a command you didn't ask for, leak data, or change what an agent is allowed
to do.

## Reporting

Please use GitHub's private vulnerability reporting on this repository (Security, then Report a vulnerability) and not
a public issue. Include the steps to reproduce it and the version or commit you're on.

## What it does and doesn't do

- The agent hooks only watch. They never approve, deny or rewrite a tool call.
- Tab state is kept in `$XDG_RUNTIME_DIR/konsole-aware`, which only you can read (mode 0700).
- The only network traffic is the release check: one request to `api.github.com` every 12 hours at most, with a
  `konsole-aware/<version>` user agent and nothing else. `"update_check": false` turns it off. Updates are only
  installed when you pick Install Update… or run `konsole-aware update install`, and the package manager asks for your
  password first.
- The Konsole library removes itself from `LD_PRELOAD` when it loads, so the programs you run in your tabs don't load it.
