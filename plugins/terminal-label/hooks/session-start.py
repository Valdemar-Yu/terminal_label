#!/usr/bin/env python3
"""Best-effort SessionStart hook for the initial terminal title."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
from typing import Any, Dict, Mapping


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

from terminal_label import render_label, resolve_tty, set_terminal_title  # noqa: E402


def status_from_hook(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Adapt SessionStart hook input to the status-line input shape."""
    model = payload.get("model") or payload.get("model_name")
    if model is None:
        model = os.environ.get("ANTHROPIC_MODEL")

    workspace = payload.get("workspace")
    if not isinstance(workspace, Mapping):
        workspace = {"current_dir": payload.get("cwd") or os.getcwd()}

    return {
        "model": model,
        "session": payload.get("session"),
        "session_name": payload.get("session_name"),
        "session_title": payload.get("session_title"),
        "session_id": payload.get("session_id"),
        "workspace": dict(workspace),
        "cwd": payload.get("cwd"),
    }


def persist_tty(environment: Mapping[str, str]) -> str:
    """Resolve and persist the session TTY for detached status-line processes."""
    tty_path = resolve_tty(env=environment)
    env_file = environment.get("CLAUDE_ENV_FILE")
    if tty_path and env_file:
        with open(env_file, "a", encoding="utf-8") as handle:
            handle.write("export TERMINAL_LABEL_TTY=%s\n" % shlex.quote(tty_path))
    return tty_path or ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, Mapping):
            return 0
        tty_path = persist_tty(os.environ)
        set_terminal_title(
            render_label(status_from_hook(payload)), tty_path=tty_path or None
        )
    except (OSError, ValueError, TypeError):
        # A hook must never block session startup because no controlling TTY is
        # available or a future Claude version changes the input shape.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
