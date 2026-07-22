# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Valdemar-Yu/terminal_label/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Valdemar-Yu/terminal_label/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Valdemar-Yu/terminal_label/releases/tag/v0.1.0
