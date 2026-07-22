#!/usr/bin/env python3
"""Restore a workspace title when a Claude Code session ends."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

from terminal_label import sanitize_text, set_terminal_title  # noqa: E402


def workspace_title(payload: Mapping[str, Any]) -> str:
    cwd = sanitize_text(payload.get("cwd") or os.getcwd())
    normalized = cwd.rstrip("/\\").replace("\\", "/")
    return sanitize_text(normalized.rsplit("/", 1)[-1]) or "Terminal"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, Mapping):
            set_terminal_title(workspace_title(payload))
    except (OSError, ValueError, TypeError):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
