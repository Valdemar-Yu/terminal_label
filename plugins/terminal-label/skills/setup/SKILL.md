---
name: setup
description: Install or update Terminal Label for the active Claude Code configuration. Use only when the user explicitly invokes this skill.
disable-model-invocation: true
allowed-tools: Bash
---

Install Terminal Label for the Claude Code configuration active in this session.

1. Run this exact command with the Bash tool:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" install
   ```

2. If `TERM_PROGRAM` is `Apple_Terminal`, also run:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" configure-terminal-app
   ```

   This makes tabs with a custom title show only `model · session`, rather than
   appending cwd, process arguments, TTY, and dimensions.
3. Report the config file, backup, installed runtime, and Terminal profile printed
   by the commands. The title refreshes at most five seconds after an idle change.
4. If installation succeeds, tell the user to start a new Claude Code session.
   `CLAUDE_CODE_DISABLE_TERMINAL_TITLE` is read when Claude Code starts, so the
   current session can still overwrite the custom title.
5. If a command detects a conflict, do not edit settings manually. Report
   the conflict and recommend `/terminal-label:doctor`.
