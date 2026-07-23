---
name: configure-warp
description: Generate reversible Warp Tab Configs for every claude-all profile so new tabs are named model plus session without manual Rename. Use only when explicitly invoked.
disable-model-invocation: true
allowed-tools: Bash
---

Generate Warp Tab Configs for all discovered claude-all profiles.

1. Run this exact command with the Bash tool:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" configure-warp
   ```

2. Report each generated config, model label, and the state path. Do not print
   profile contents, provider URLs, environment variables, or tokens.
3. Tell the user how to launch:
   - Open Warp's `+` menu.
   - Select `Terminal Label · <profile>`.
   - Pick the project directory and enter the session name.
   - Warp opens a tab whose custom title is `model · session` and starts the
     matching `claude-all <profile>` command. The Warp title is separate from
     Claude's resume name; use `/rename` inside Claude if that name must also be saved.
4. Explain the current Warp API boundary: `/rename` and `/model` changes inside an
   existing session cannot update the custom tab title until Warp exposes its
   public tab-control CLI. The status icon/badge remains independent.
5. On conflict, do not overwrite files. Run:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" doctor-warp
   ```

   The reversible command is:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" restore-warp
   ```
