---
name: configure-terminal-app
description: Configure a macOS Terminal profile to show only Terminal Label's custom title, without cwd, process, arguments, TTY, or dimensions. Use only when explicitly invoked.
disable-model-invocation: true
allowed-tools: Bash
---

Configure macOS Terminal.app so tabs with a custom title show only that title.

1. Run this exact command with the Bash tool:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" configure-terminal-app
   ```

2. Report the selected profile and backup path. Do not print the Terminal plist.
3. Tell the user to quit and reopen Terminal.app. Terminal caches profile title
   components for the lifetime of the app; opening only a new window is not
   sufficient.
4. After restart, run:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" doctor-terminal-app
   ```

5. If a conflict is reported, do not force or edit Terminal preferences manually.
   The reversible command is:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" restore-terminal-app
   ```
