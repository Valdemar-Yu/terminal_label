# Terminal Label

[![Test](https://github.com/Valdemar-Yu/terminal_label/actions/workflows/test.yml/badge.svg)](https://github.com/Valdemar-Yu/terminal_label/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Keep each Claude Code terminal tab identifiable:

```text
GPT-5.6 Sol · terminal-label README
Opus 4.8 · auth refactor
Sonnet 5 · release checks
```

Terminal Label synchronizes the tab title with the active model and Claude Code
session name. It updates after `/model` and `/rename`, works alongside an
existing custom status line, and does not send telemetry.

[中文文档](README.zh-CN.md)

## Requirements

- Claude Code 2.1.217 or newer
- Python 3.9 or newer
- macOS or Linux, including WSL

## Install

Add this repository as a Claude Code marketplace and install the plugin:

```bash
claude plugin marketplace add Valdemar-Yu/terminal_label
claude plugin install terminal-label@terminal-label
```

Start or reload Claude Code, then run:

```text
/terminal-label:setup
```

Setup makes a reversible change to the active Claude config directory. Start a
new Claude Code session after setup so Claude Code's native animated title no
longer competes with Terminal Label.

Name sessions when launching Claude Code:

```bash
claude --name "terminal-label README"
```

Or rename the current session at any time:

```text
/rename terminal-label README
```

If the session has no custom or generated name yet, Terminal Label uses the
current directory name.

### Alternate Claude config directories

The plugin follows `CLAUDE_CONFIG_DIR`. Install and configure each Claude Code
profile separately:

```bash
CLAUDE_CONFIG_DIR="$HOME/.claude-plbbl" \
  claude plugin marketplace add Valdemar-Yu/terminal_label
CLAUDE_CONFIG_DIR="$HOME/.claude-plbbl" \
  claude plugin install terminal-label@terminal-label
CLAUDE_CONFIG_DIR="$HOME/.claude-plbbl" claude
```

Then run `/terminal-label:setup` inside that session. Terminal Label never scans
for or changes other profiles automatically.

### Try from a clone

```bash
git clone https://github.com/Valdemar-Yu/terminal_label.git
cd terminal_label
claude --plugin-dir ./plugins/terminal-label
```

Run `/terminal-label:setup` in that development session.

## How it works

Claude Code passes live session data to a custom status line process, including
`model.display_name`, `session_name`, and the current workspace. Setup installs
a small dependency-free Python proxy that:

1. formats and sanitizes `model · session`;
2. writes an OSC terminal-title sequence directly to the controlling TTY;
3. passes the original JSON to your previous status line command and returns
   its output unchanged.

The proxy refreshes every five seconds when the previous status line was slower,
so an otherwise idle `/model` or `/rename` change does not leave a stale tab.
Setup also disables Claude Code's built-in animated title for subsequent
sessions. The previous status line and title setting are recorded locally so
uninstall can restore them. Prompts and transcript contents are never read.

## Commands

| Command | Purpose |
| --- | --- |
| `/terminal-label:setup` | Install or update the status line proxy |
| `/terminal-label:doctor` | Check config, runtime, terminal, and tmux detection |
| `/terminal-label:uninstall` | Restore the previous Claude Code settings |

The installed runtime also accepts `render`, `doctor`, `install`, and
`uninstall` subcommands for local development and scripting.

## Terminal support

| Environment | Status | Notes |
| --- | --- | --- |
| macOS Terminal.app | Supported | Uses OSC 0 through `/dev/tty` |
| iTerm2 | Supported | Uses OSC 0 |
| Ghostty | Supported | Uses OSC 0 |
| WezTerm | Supported | Uses OSC 0 |
| kitty | Supported | Uses OSC 0 |
| tmux | Best effort | Updates the active pane/window; use one Claude session per window |
| WSL + Windows Terminal | Best effort | Requires a writable `/dev/tty` |
| Native Windows | Not yet supported | No `/dev/tty` output path |

Some terminal profiles are configured to ignore application-provided titles.
Enable terminal title changes in the emulator's profile if setup succeeds but
the tab text does not change.

## Uninstall

Run:

```text
/terminal-label:uninstall
```

Uninstall restores the exact status line saved during setup and restores the
previous `CLAUDE_CODE_DISABLE_TERMINAL_TITLE` value. It refuses to overwrite the
status line if another tool or a later manual edit replaced Terminal Label's
command. If the plugin was removed first, the stable runtime can still uninstall
itself:

```bash
"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/terminal-label/bin/terminal-label" uninstall
```

To remove the plugin and marketplace afterward:

```bash
claude plugin uninstall terminal-label@terminal-label
claude plugin marketplace remove terminal-label
```

## Privacy and security

- No network requests or telemetry.
- No prompt or transcript parsing.
- Control characters are removed from model and session names before writing
  terminal escape sequences.
- Local state can contain the previous status line command and is written with
  user-only permissions.
- Settings writes are atomic and backed up before the first change.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Development

```bash
python3 -m unittest discover -s tests -v
claude plugin validate --strict .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a pull request.

## License

[MIT](LICENSE)
