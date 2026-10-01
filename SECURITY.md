# Security Policy

konsole-aware runs as hooks inside your coding agents and shell, and optionally as a library loaded into Konsole.
Please report anything that could let it run unintended commands, leak data, or change agent permissions.

## Reporting

Use GitHub's **private vulnerability reporting** on this repository (Security → Report a vulnerability). Please do not
open a public issue for security problems. Include steps to reproduce and the affected version or commit.

## Design commitments

- Agent hooks are observational: they never approve, deny or rewrite tool calls.
- State is kept in a private directory (`$XDG_RUNTIME_DIR/konsole-aware`, mode 0700).
- Nothing is sent over the network.
- The Konsole menu library strips itself from `LD_PRELOAD` so child processes do not load it.
