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

2. Report the config file, backup, and installed runtime printed by the command.
   The default title format is `model · session`, refreshed at most five seconds
   after an otherwise idle change.
3. If installation succeeds, tell the user to start a new Claude Code session.
   `CLAUDE_CODE_DISABLE_TERMINAL_TITLE` is read when Claude Code starts, so the
   current session can still overwrite the custom title.
4. If the command detects a conflict, do not edit the settings manually. Report
   the conflict and recommend `/terminal-label:doctor`.
