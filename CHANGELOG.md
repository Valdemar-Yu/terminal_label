# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.3] - 2026-07-23

### Fixed

- Refresh the recorded Terminal Label version when an existing claude-all batch
  installation is upgraded in place.

## [0.2.2] - 2026-07-23

### Fixed

- Checkpoint each local batch-uninstall step so an interrupted multi-profile
  restore can resume without losing state.

## [0.2.1] - 2026-07-22

### Fixed

- Preserve Terminal Label installations that existed before batch setup.
- Make native claudish profiles retain their config directory and managed title.
- Resolve Fugu and PLBBL wrapper-specific config directory overrides.
- Checkpoint batch side effects so interrupted installs remain recoverable.
- Preflight every managed config before batch uninstall and retain retry state.
- Remove provider credentials from plugin subprocess environments.
- Detect missing or non-executable stable runtime components in batch doctor.
- Resolve the Claude process TTY when hooks/status lines have no controlling terminal.
- Update tab, window, and combined title channels with OSC 1, 2, and 0.

## [0.2.0] - 2026-07-22

### Added

- One-command discovery and installation across all local claude-all profiles.
- Claudish status line adaptation for PLBBL, Fugu, and custom claudish profiles.
- Batch doctor and conflict-safe uninstall commands backed by a local state file.
- `/terminal-label:setup-claude-all` for interactive Claude Code setup.

## [0.1.0] - 2026-07-22

### Added

- Claude Code model and session names in terminal tab titles.
- Safe status line proxy that preserves an existing custom status line.
- Stable per-profile runtime that survives plugin cache updates.
- Reversible suppression of Claude Code's built-in animated terminal title.
- Setup, uninstall, diagnosis, and title preview commands.
- Terminal title support for common macOS and Linux terminal emulators, with
  best-effort tmux handling.

[Unreleased]: https://github.com/Valdemar-Yu/terminal_label/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/Valdemar-Yu/terminal_label/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Valdemar-Yu/terminal_label/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Valdemar-Yu/terminal_label/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Valdemar-Yu/terminal_label/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Valdemar-Yu/terminal_label/releases/tag/v0.1.0
