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
- Nothing is sent over the network.
- The Konsole library removes itself from `LD_PRELOAD` when it loads, so the programs you run in your tabs don't load it.
