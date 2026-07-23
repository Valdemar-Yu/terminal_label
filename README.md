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

### Install across claude-all

If you launch several providers through
[claude-all](https://github.com/Valdemar-Yu/claude-all), install the plugin once
in the current profile, then run:

```text
/terminal-label:setup-claude-all
```

The equivalent command from a source checkout is:

```bash
./plugins/terminal-label/bin/terminal-label install-claude-all
```

It discovers `~/.claude-all/profiles/*.env`, installs Terminal Label into each
unique Claude config directory, preserves the existing unified status line, and
adapts claudish profiles such as PLBBL and Fugu. It never sources profile files
or records token values in its state file.

Check every profile without changing files:

```bash
./plugins/terminal-label/bin/terminal-label doctor-claude-all
```

Undo the batch installation:

```bash
./plugins/terminal-label/bin/terminal-label uninstall-claude-all
```

Profile edits are restored only if their SHA-256 still matches the installed
version. If a profile was edited afterward, uninstall reports a conflict rather
than overwriting it. Re-run `install-claude-all` after adding a new claude-all
profile.

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

### Show only the label in macOS Terminal

Terminal.app can append the working directory, active process, full arguments,
TTY, and dimensions after a custom title. Configure the active/default profile
so a tab with a Terminal Label title shows only `model · session`:

```text
/terminal-label:configure-terminal-app
```

From a source checkout or installed runtime:

```bash
./plugins/terminal-label/bin/terminal-label configure-terminal-app
```

Terminal.app caches profile title components. Quit and reopen Terminal.app after
configuration; opening only a new window in the same app process is insufficient.
Check or reverse the setting with:

```bash
./plugins/terminal-label/bin/terminal-label doctor-terminal-app
./plugins/terminal-label/bin/terminal-label restore-terminal-app
```

Terminal Label disables the profile's window/tab title components, including
cwd, process name and arguments, TTY, settings name, and dimensions. This applies
to every tab using that Terminal profile; use a dedicated profile if ordinary
shell tabs should retain those components. The complete Terminal plist is backed
up locally with mode `0600`, and restore refuses to overwrite a later manual
change.

### Stable names in Warp's left tabs

Warp currently has no public API to rename the active sidebar tab dynamically.
OSC titles can be replaced by Warp's CLI-agent/conversation title. Terminal Label
therefore uses Warp's supported **Tab Config** custom title, which has stable
precedence over generated agent text.

Generate one config per claude-all profile:

```text
/terminal-label:configure-warp
```

Or from a checkout/runtime:

```bash
./plugins/terminal-label/bin/terminal-label configure-warp
```

Then open Warp's `+` menu, select `Terminal Label · <profile>`, choose the repo,
and enter a session name. Warp creates a tab titled `model · session` and runs:

```text
claude-all <profile>
```

The Warp label is intentionally not interpolated into a shell command. If the
Claude resume name should match too, run `/rename <session>` after startup.

Generated files live in `~/.warp/tab_configs/`; reversible ownership state and
backups live under `~/.config/terminal-label/`. Check or remove them with:

```bash
./plugins/terminal-label/bin/terminal-label doctor-warp
./plugins/terminal-label/bin/terminal-label restore-warp
```

Current limitation: `/rename` and `/model` inside an already-running session do
not update Warp's custom tab title. Warp has not yet released its public Local
Control/warpctrl tab-rename API. The separate agent status icon/badge still works.

## How it works

Claude Code passes live session data to a custom status line process, including
`model.display_name`, `session_name`, and the current workspace. Setup installs
a small dependency-free Python proxy that:

1. formats and sanitizes `model · session`;
2. resolves the Claude process TTY even when the hook/status-line process has no
   controlling terminal;
3. writes OSC 1, 2, and 0 sequences for tab, window, and combined titles;
4. passes the original JSON to your previous status line command and returns
   its output unchanged.

The proxy refreshes every five seconds when the previous status line was slower,
so an otherwise idle `/model` or `/rename` change does not leave a stale tab.
Setup also disables Claude Code's built-in animated title for subsequent
sessions. The previous status line and title setting are recorded locally so
uninstall can restore them. Prompts and transcript contents are never read.

## Commands

| Command | Purpose |
| --- | --- |
| `/terminal-label:setup` | Install or update one profile's status line proxy |
| `/terminal-label:setup-claude-all` | Discover and install every claude-all profile |
| `/terminal-label:configure-terminal-app` | Hide extra Terminal.app title components |
| `/terminal-label:configure-warp` | Generate stable Warp sidebar Tab Configs |
| `/terminal-label:doctor` | Check config, runtime, terminal, and tmux detection |
| `/terminal-label:uninstall` | Restore the previous Claude Code settings |

The installed runtime also accepts `render`, `doctor`, `install`, `uninstall`,
`install-claude-all`, `doctor-claude-all`, and `uninstall-claude-all`
subcommands for local development and scripting.

## Terminal support

| Environment | Status | Notes |
| --- | --- | --- |
| macOS Terminal.app | Supported | Verified with OSC 0/2 on the selected tab |
| iTerm2 | Supported | Uses OSC 1/2/0 |
| Ghostty | Supported | Uses OSC 1/2/0 |
| WezTerm | Supported | Uses OSC 1/2/0 |
| kitty | Supported | Uses OSC 1/2/0 |
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

- No runtime network requests or telemetry. Batch setup uses Claude's plugin CLI
  only to fetch the public GitHub marketplace.
- No prompt or transcript parsing.
- Control characters are removed from model and session names before writing
  terminal escape sequences.
- Local state can contain the previous status line command and is written with
  user-only permissions. Batch state records paths and SHA-256 hashes, not token
  values; adjacent profile backups remain local with mode `0600`.
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
