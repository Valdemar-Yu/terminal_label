---
name: setup-claude-all
description: Install or update Terminal Label across every profile discovered by the local claude-all launcher. Use only when the user explicitly invokes this skill.
disable-model-invocation: true
allowed-tools: Bash
---

Install Terminal Label across all profiles discovered in
`~/.claude-all/profiles/`.

1. Run this exact command with the Bash tool:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" install-claude-all
   ```

2. Report every discovered profile and config directory, the backup/state path,
   and whether claudish adaptation was applied.
3. Do not print profile files, settings, environment values, API URLs, or status
   line commands while diagnosing failures; they can contain credentials.
4. If any config or profile conflict is reported, do not use force or edit the
   file manually. Preserve the state file and recommend:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" doctor-claude-all
   ```

5. On success, tell the user to restart every open Claude Code or claude-all
   session. The title-disable setting is read at process startup.
