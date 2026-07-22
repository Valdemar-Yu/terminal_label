# Security policy

## Supported versions

Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not publish exploit details in an issue. Use GitHub's private vulnerability
reporting when the repository exposes that option. Otherwise contact the owner
through their GitHub profile to establish a private channel. Include the
affected version, reproduction steps, and expected impact in the private report.

Terminal Label reads Claude Code's status line JSON locally. It does not send
telemetry, make network requests, or persist prompts. Reports should call out
any behavior that violates those boundaries, permits terminal control-sequence
injection, or exposes data from one Claude Code session to another.
