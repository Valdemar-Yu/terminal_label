#!/usr/bin/env python3
"""Reversible macOS Terminal profile setup for custom-title-only tabs."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import terminal_label


DOMAIN = "com.apple.Terminal"
PROFILE_KEY = "Window Settings"
CUSTOM_ONLY_KEY = "ShowComponentsWhenTabHasCustomTitle"
TITLE_COMPONENT_KEYS = (
    CUSTOM_ONLY_KEY,
    "ShowActiveProcessInTitle",
    "ShowActiveProcessArgumentsInTitle",
    "ShowCommandKeyInTitle",
    "ShowDimensionsInTitle",
    "ShowRepresentedURLInTitle",
    "ShowRepresentedURLPathInTitle",
    "ShowShellCommandInTitle",
    "ShowTTYNameInTitle",
    "ShowWindowSettingsNameInTitle",
    "ShowActiveProcessInTabTitle",
    "ShowActiveProcessArgumentsInTabTitle",
    "ShowRepresentedURLInTabTitle",
    "ShowRepresentedURLPathInTabTitle",
    "ShowTTYNameInTabTitle",
)
STATE_VERSION = 2
STATE_FILENAME = "terminal-app.json"
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _run(runner: Runner, args: Sequence[str]) -> subprocess.CompletedProcess:
    try:
        result = runner(tuple(args))
    except (OSError, subprocess.SubprocessError) as exc:
        raise terminal_label.TerminalLabelError(
            "Terminal preference command failed: %s" % exc
        ) from exc
    if result.returncode != 0:
        raise terminal_label.TerminalLabelError(
            "Terminal preference command returned %d" % result.returncode
        )
    return result


def _state_path(home: Path) -> Path:
    return home / ".config" / "terminal-label" / STATE_FILENAME


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_preferences(runner: Runner) -> Tuple[MutableMapping[str, Any], bytes]:
    result = _run(runner, ("defaults", "export", DOMAIN, "-"))
    raw = bytes(result.stdout)
    try:
        data = plistlib.loads(raw)
    except (ValueError, TypeError) as exc:
        raise terminal_label.TerminalLabelError(
            "could not parse Terminal preferences"
        ) from exc
    if not isinstance(data, MutableMapping):
        raise terminal_label.TerminalLabelError("Terminal preferences are not a dictionary")
    return data, raw


def _profile_name(
    preferences: Mapping[str, Any], profile: Optional[str]
) -> str:
    profiles = preferences.get(PROFILE_KEY)
    if not isinstance(profiles, Mapping):
        raise terminal_label.TerminalLabelError("Terminal profiles are missing")
    selected = profile or preferences.get("Default Window Settings")
    if not isinstance(selected, str) or not selected:
        raise terminal_label.TerminalLabelError("Terminal default profile is missing")
    if any(ord(char) < 32 for char in selected):
        raise terminal_label.TerminalLabelError("Terminal profile name contains control characters")
    if selected not in profiles or not isinstance(profiles[selected], MutableMapping):
        raise terminal_label.TerminalLabelError(
            "Terminal profile does not exist: %s" % selected
        )
    return selected


def _write_preferences(
    runner: Runner, preferences: Mapping[str, Any], directory: Path
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".terminal-label.", suffix=".plist", dir=str(directory)
    )
    temporary = Path(temporary_name)
    try:
        payload = plistlib.dumps(dict(preferences), fmt=plistlib.FMT_XML)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        _run(runner, ("defaults", "import", DOMAIN, str(temporary)))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_state(path: Path) -> MutableMapping[str, Any]:
    if path.is_symlink():
        raise terminal_label.ConfigConflict("refusing symlinked Terminal state: %s" % path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise terminal_label.TerminalLabelError(
            "could not read Terminal state %s: %s" % (path, exc)
        ) from exc
    if not isinstance(value, MutableMapping) or value.get("version") != STATE_VERSION:
        raise terminal_label.TerminalLabelError("unsupported Terminal state: %s" % path)
    return value


def configure(
    home: Optional[Path] = None,
    profile: Optional[str] = None,
    runner: Optional[Runner] = None,
) -> Tuple[bool, List[str]]:
    """Make one Terminal profile show only its custom title."""
    resolved_home = (home or Path.home()).expanduser().resolve()
    run = runner or _default_runner
    state_path = _state_path(resolved_home)
    preferences, raw = _read_preferences(run)
    selected = _profile_name(preferences, profile)
    profiles = preferences[PROFILE_KEY]
    current = profiles[selected]

    if state_path.exists():
        state = _load_state(state_path)
        if state.get("profile") != selected:
            raise terminal_label.ConfigConflict(
                "another Terminal profile is already managed: %s" % state.get("profile")
            )
        if not state.get("complete"):
            for key in TITLE_COMPONENT_KEYS:
                current[key] = False
            _write_preferences(run, preferences, state_path.parent)
            state["complete"] = True
            _write_json(state_path, state)
            return True, [
                "OK resumed Terminal profile setup: %s" % selected,
                "RESTART Terminal.app to reload profile title components",
            ]
        if any(current.get(key) is not False for key in TITLE_COMPONENT_KEYS):
            raise terminal_label.ConfigConflict(
                "Terminal custom-title setting changed after setup"
            )
        return False, ["OK Terminal profile is already custom-title-only: %s" % selected]

    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path.parent.chmod(0o700)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = state_path.parent / ("com.apple.Terminal.%s.plist" % stamp)
    backup.write_bytes(raw)
    backup.chmod(0o600)
    old_values = {
        key: {"present": key in current, "value": current.get(key)}
        for key in TITLE_COMPONENT_KEYS
    }
    state: Dict[str, Any] = {
        "version": STATE_VERSION,
        "complete": False,
        "profile": selected,
        "old_values": old_values,
        "installed_value": False,
        "backup": str(backup),
        "backup_sha256": _sha256(raw),
    }
    _write_json(state_path, state)
    for key in TITLE_COMPONENT_KEYS:
        current[key] = False
    _write_preferences(run, preferences, state_path.parent)
    state["complete"] = True
    _write_json(state_path, state)
    return True, [
        "OK Terminal profile is custom-title-only: %s" % selected,
        "OK Terminal preferences backup: %s" % backup,
        "RESTART Terminal.app to reload profile title components",
    ]


def doctor(
    home: Optional[Path] = None,
    profile: Optional[str] = None,
    runner: Optional[Runner] = None,
) -> Tuple[bool, List[str]]:
    resolved_home = (home or Path.home()).expanduser().resolve()
    run = runner or _default_runner
    state_path = _state_path(resolved_home)
    preferences, _ = _read_preferences(run)
    selected = _profile_name(preferences, profile)
    profiles = preferences[PROFILE_KEY]
    messages: List[str] = []
    healthy = True
    if all(
        profiles[selected].get(key) is False for key in TITLE_COMPONENT_KEYS
    ):
        messages.append("OK Terminal profile is custom-title-only: %s" % selected)
    else:
        healthy = False
        messages.append("FAIL Terminal profile still appends title components: %s" % selected)
    if state_path.is_file():
        state = _load_state(state_path)
        backup = Path(str(state.get("backup")))
        backup_valid = (
            backup.is_file()
            and _sha256(backup.read_bytes()) == state.get("backup_sha256")
        )
        if state.get("profile") == selected and state.get("complete") and backup_valid:
            messages.append("OK reversible Terminal state: %s" % state_path)
        else:
            healthy = False
            messages.append("FAIL Terminal state or backup is invalid: %s" % selected)
    else:
        healthy = False
        messages.append("FAIL reversible Terminal state is missing: %s" % state_path)
    return healthy, messages


def _matches_saved_values(
    current: Mapping[str, Any], old_values: Mapping[str, Any]
) -> bool:
    for key in TITLE_COMPONENT_KEYS:
        saved = old_values.get(key)
        if not isinstance(saved, Mapping):
            return False
        if saved.get("present"):
            if key not in current or current.get(key) != saved.get("value"):
                return False
        elif key in current:
            return False
    return True


def restore(
    home: Optional[Path] = None,
    profile: Optional[str] = None,
    runner: Optional[Runner] = None,
) -> Tuple[bool, List[str]]:
    resolved_home = (home or Path.home()).expanduser().resolve()
    run = runner or _default_runner
    state_path = _state_path(resolved_home)
    if not state_path.exists():
        return False, ["Terminal profile is not managed"]
    state = _load_state(state_path)
    backup = Path(str(state.get("backup")))
    if not backup.is_file() or _sha256(backup.read_bytes()) != state.get(
        "backup_sha256"
    ):
        raise terminal_label.ConfigConflict("Terminal preferences backup changed")
    if profile is not None and state.get("profile") != profile:
        raise terminal_label.ConfigConflict(
            "Terminal state belongs to profile %s" % state.get("profile")
        )
    preferences, _ = _read_preferences(run)
    selected = _profile_name(preferences, str(state.get("profile")))
    current = preferences[PROFILE_KEY][selected]
    old_values = state.get("old_values")
    if not isinstance(old_values, Mapping):
        raise terminal_label.TerminalLabelError("Terminal state has no saved title values")
    installed = all(current.get(key) is False for key in TITLE_COMPONENT_KEYS)
    already_restored = _matches_saved_values(current, old_values)
    if not state.get("restoring") and not installed:
        raise terminal_label.ConfigConflict(
            "Terminal custom-title setting changed after setup"
        )
    if state.get("restoring") and not (installed or already_restored):
        raise terminal_label.ConfigConflict(
            "Terminal profile changed during restore"
        )
    if not state.get("restoring"):
        state["restoring"] = True
        _write_json(state_path, state)
    if not already_restored:
        for key in TITLE_COMPONENT_KEYS:
            saved = old_values.get(key)
            if not isinstance(saved, Mapping):
                raise terminal_label.TerminalLabelError(
                    "Terminal state is missing saved value: %s" % key
                )
            if saved.get("present"):
                current[key] = saved.get("value")
            else:
                current.pop(key, None)
        _write_preferences(run, preferences, state_path.parent)
    state["restored"] = True
    _write_json(state_path, state)
    try:
        backup.unlink()
    except FileNotFoundError:
        pass
    state_path.unlink()
    return True, [
        "OK restored Terminal profile: %s" % selected,
        "RESTART Terminal.app to reload profile title components",
    ]
