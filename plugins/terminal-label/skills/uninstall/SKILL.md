---
name: uninstall
description: Uninstall Terminal Label and restore the previous Claude Code status line and terminal-title setting. Use only when the user explicitly invokes this skill.
disable-model-invocation: true
allowed-tools: Bash
---

Uninstall Terminal Label from the Claude Code configuration active in this
session.

1. Run this exact command with the Bash tool:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" uninstall
   ```

2. Report which settings were restored and whether the local runtime was
   removed.
3. If uninstall refuses because the status line changed after setup, do not
   force an overwrite or edit the settings manually. Recommend preserving the
   newer status line and using `/terminal-label:doctor` to locate the conflict.
4. On success, tell the user to start a new Claude Code session before removing
   the plugin itself.
