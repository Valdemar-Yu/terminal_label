---
name: doctor
description: Diagnose Terminal Label configuration and terminal support without changing files. Use when the user invokes this skill or reports stale or missing tab titles.
disable-model-invocation: true
allowed-tools: Bash
---

Run this exact command with the Bash tool:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/terminal-label" doctor
```

Report each check exactly as the command returns it. Do not print Claude Code
settings or environment values as a workaround; those can contain credentials.
If the doctor reports that application-controlled titles are ignored, explain
that the user must enable them in the terminal emulator's profile.
