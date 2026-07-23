#!/usr/bin/env python3
"""Reversible Warp tab configurations for claude-all profiles."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import unicodedata
from typing import Any, List, Mapping, MutableMapping, Optional, Sequence, Tuple


LIB_DIR = str(Path(__file__).resolve().parent)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import claude_all  # noqa: E402


terminal_label = claude_all.terminal_label
STATE_VERSION = 1
STATE_FILENAME = "warp.json"
TAB_CONFIG_PREFIX = "terminal_label_"
MAX_CONFIG_SIZE = 1024 * 1024

_PROFILE_MODEL_KEYS = (
    "CLAUDE_ALL_MODEL",
    "CLAUDISH_MODEL",
    "CCGP_MODEL",
    "FUGU_MODEL",
    "ANTHROPIC_MODEL",
)
_PLBBL_MODEL_KEYS = ("model", "model_name", "display_name", "displayName")
_ENV_DISPLAY_KEYS = (
    "CLAUDE_ALL_MODEL_DISPLAY_NAME",
    "CLAUDE_MODEL_DISPLAY_NAME",
    "ANTHROPIC_MODEL_DISPLAY_NAME",
    "MODEL_DISPLAY_NAME",
)
_ENV_MODEL_KEYS = (
    "ANTHROPIC_MODEL",
    "CLAUDE_ALL_MODEL",
    "CLAUDISH_MODEL",
    "CCGP_MODEL",
)


def _home_path(home: Path) -> Path:
    return Path(home).expanduser().resolve()


def _state_path(home: Path) -> Path:
    return home / ".config" / "terminal-label" / STATE_FILENAME


def _backup_dir(home: Path) -> Path:
    return home / ".config" / "terminal-label" / "warp-backups"


def _tab_configs_dir(home: Path) -> Path:
    return home / ".warp" / "tab_configs"


def _validate_profile_name(profile: str) -> str:
    if not isinstance(profile, str) or not profile or profile in (".", ".."):
        raise terminal_label.TerminalLabelError("invalid claude-all profile name")
    if any(character in profile for character in ("/", "\\", "\x00", "{", "}")) or any(
        unicodedata.category(character) in ("Cc", "Cf") for character in profile
    ):
        raise terminal_label.TerminalLabelError(
            "unsafe claude-all profile name: %r" % profile
        )
    return profile


def _tab_path(home: Path, profile: str) -> Path:
    safe_profile = _validate_profile_name(profile)
    return _tab_configs_dir(home) / (TAB_CONFIG_PREFIX + safe_profile + ".toml")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_hash(path: Path) -> Optional[str]:
    if path.is_symlink():
        raise terminal_label.ConfigConflict("refusing symlinked file: %s" % path)
    try:
        return _sha256(path.read_bytes())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise terminal_label.TerminalLabelError("could not hash %s: %s" % (path, exc)) from exc


def _atomic_replace(
    path: Path, raw: bytes, expected_hash: Optional[str], mode: int = 0o600
) -> None:
    if path.is_symlink():
        raise terminal_label.ConfigConflict("refusing to replace symlinked file: %s" % path)
    if path.parent.is_symlink():
        raise terminal_label.ConfigConflict(
            "refusing symlinked configuration directory: %s" % path.parent
        )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _file_hash(path) != expected_hash:
        raise terminal_label.ConfigConflict("file changed while preparing update: %s" % path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        if _file_hash(path) != expected_hash:
            raise terminal_label.ConfigConflict("file changed before update: %s" % path)
        os.replace(str(temporary), str(path))
        try:
            directory_descriptor = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_state(
    path: Path, state: Mapping[str, Any], expected_hash: Optional[str]
) -> str:
    raw = (
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_replace(path, raw, expected_hash, mode=0o600)
    path.chmod(0o600)
    path.parent.chmod(0o700)
    return _sha256(raw)


def _write_backup(home: Path, profile: str, raw: bytes) -> Path:
    directory = _backup_dir(home)
    if directory.is_symlink():
        raise terminal_label.ConfigConflict(
            "refusing symlinked Warp backup directory: %s" % directory
        )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    prefix = ".%s%s." % (TAB_CONFIG_PREFIX, _validate_profile_name(profile))
    descriptor, name = tempfile.mkstemp(
        prefix=prefix, suffix=".bak", dir=str(directory)
    )
    path = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    path.chmod(0o600)
    return path


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return terminal_label.sanitize_text(value)


def _model_value(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("display_name", "displayName", "name", "id", "model"):
            candidate = _clean_text(value.get(key))
            if candidate:
                return candidate
        return ""
    return _clean_text(value)


def _read_plbbl_model(path: Path) -> str:
    """Read only model metadata from a PLBBL key/value config."""
    if path.is_symlink():
        raise claude_all.EnvParseError("refusing symlinked cc-gpt-plbbl config: %s" % path)
    try:
        if not path.exists():
            return ""
        if path.stat().st_size > MAX_CONFIG_SIZE:
            raise claude_all.EnvParseError("cc-gpt-plbbl config is too large: %s" % path)
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in line:
                    continue
                key, raw_value = line.split("=", 1)
                key = key.strip()
                if key not in _PLBBL_MODEL_KEYS:
                    # In particular, token and token_cmd are neither parsed nor retained.
                    continue
                value = claude_all._parse_env_value(raw_value, path, line_number)
                candidate = _clean_text(value)
                if candidate:
                    return candidate
    except (OSError, UnicodeDecodeError) as exc:
        raise claude_all.EnvParseError(
            "could not read cc-gpt-plbbl model metadata %s: %s" % (path, exc)
        ) from exc
    return ""


def _plbbl_model(record: Any, home: Path) -> str:
    if not record.info.cc_gpt_plbbl:
        return ""
    value = record.values.get(
        "CCGP_CONFIG_FILE", str(home / ".config" / "claude-all" / "config")
    )
    config_path = claude_all._safe_path(
        value, home, "%s CCGP_CONFIG_FILE" % record.info.path
    )
    return _read_plbbl_model(config_path)


def _read_settings_model(path: Path) -> str:
    """Read model display metadata without copying settings or environment values."""
    if path.is_symlink():
        raise terminal_label.ConfigConflict("refusing symlinked settings file: %s" % path)
    try:
        if not path.exists():
            return ""
        if path.stat().st_size > MAX_CONFIG_SIZE:
            raise terminal_label.TerminalLabelError("settings file is too large: %s" % path)
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise terminal_label.TerminalLabelError(
            "could not read model metadata from settings %s: %s" % (path, exc)
        ) from exc
    if not isinstance(parsed, Mapping):
        raise terminal_label.TerminalLabelError("settings JSON must be an object: %s" % path)

    candidate = _model_value(parsed.get("model"))
    environment = parsed.get("env")
    if candidate and candidate.lower() in {"fable", "opus", "sonnet", "haiku"} and isinstance(
        environment, Mapping
    ):
        family = candidate.upper()
        for suffix in ("MODEL_NAME", "MODEL"):
            resolved = _model_value(
                environment.get("ANTHROPIC_DEFAULT_%s_%s" % (family, suffix))
            )
            if resolved:
                return resolved
    if candidate:
        return candidate
    if isinstance(environment, Mapping):
        for key in _ENV_DISPLAY_KEYS + _ENV_MODEL_KEYS:
            candidate = _model_value(environment.get(key))
            if candidate:
                return candidate
    for key in ("model_display_name", "modelDisplayName", "display_name", "displayName"):
        candidate = _model_value(parsed.get(key))
        if candidate:
            return candidate
    return ""


def _profile_model(record: Any, home: Path) -> str:
    for key in _PROFILE_MODEL_KEYS:
        candidate = _clean_text(record.values.get(key))
        if candidate:
            return candidate
    candidate = _plbbl_model(record, home)
    if candidate:
        return candidate
    candidate = _read_settings_model(record.info.config_dir / "settings.json")
    if candidate:
        return candidate
    return _clean_text(record.values.get("CLAUDE_ALL_LABEL")) or _clean_text(
        record.info.name
    )


def _toml_string(value: str) -> str:
    """Encode a TOML basic string using its JSON-compatible escaped form."""
    return json.dumps(value, ensure_ascii=False)


def _project_default(home: Path, project_dir: Optional[Path]) -> Optional[str]:
    if project_dir is None:
        return None
    value = str(project_dir)
    if value == "~":
        value = str(home)
    elif value.startswith("~/"):
        value = str(home / value[2:])
    if "\x00" in value or any(ord(character) < 32 for character in value):
        raise terminal_label.TerminalLabelError("project_dir contains a control character")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    return str(path)


def _render_config(
    profile: str, model: str, project_dir: Optional[str]
) -> bytes:
    profile = _validate_profile_name(profile)
    clean_model = _clean_text(model)
    if not clean_model:
        clean_model = profile
    # Keep template input out of the shell command. Warp substitutes params as
    # raw text, so passing {{session}} to --name would permit shell expansion.
    command = "WARP_DISABLE_AUTO_TITLE=true exec claude-all %s" % shlex.quote(
        profile
    )
    lines = [
        "name = %s" % _toml_string("Terminal Label · " + profile),
        "title = %s" % _toml_string(clean_model + " · {{session}}"),
        "",
        "[[panes]]",
        'id = "main"',
        'type = "terminal"',
        'directory = "{{project_dir}}"',
        "commands = [%s]" % _toml_string(command),
        "is_focused = true",
        "",
        "[params.project_dir]",
        'type = "repo"',
        'description = "Project directory"',
    ]
    if project_dir is not None:
        lines.append("default = %s" % _toml_string(project_dir))
    lines.extend(
        [
            "",
            "[params.session]",
            'type = "text"',
            'description = "Session name"',
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _load_state(path: Path, home: Path) -> MutableMapping[str, Any]:
    if path.is_symlink():
        raise terminal_label.ConfigConflict("refusing symlinked Warp state: %s" % path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise terminal_label.TerminalLabelError("Warp state does not exist: %s" % path) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise terminal_label.TerminalLabelError(
            "could not read Warp state %s: %s" % (path, exc)
        ) from exc
    if (
        not isinstance(value, MutableMapping)
        or value.get("version") != STATE_VERSION
        or not isinstance(value.get("profiles"), list)
    ):
        raise terminal_label.TerminalLabelError("unsupported Warp state: %s" % path)

    seen = set()
    backup_directory = _backup_dir(home)
    for entry in value["profiles"]:
        if not isinstance(entry, MutableMapping):
            raise terminal_label.TerminalLabelError("invalid Warp profile state: %s" % path)
        profile = entry.get("profile")
        if not isinstance(profile, str):
            raise terminal_label.TerminalLabelError("invalid Warp profile state: %s" % path)
        _validate_profile_name(profile)
        if profile in seen:
            raise terminal_label.TerminalLabelError("duplicate Warp profile state: %s" % profile)
        seen.add(profile)
        expected_path = _tab_path(home, profile)
        if entry.get("path") != str(expected_path):
            raise terminal_label.ConfigConflict("unexpected managed Warp path for %s" % profile)
        invalid_model = not isinstance(entry.get("model"), str)
        invalid_project = entry.get("project_dir") is not None and not isinstance(
            entry.get("project_dir"), str
        )
        if invalid_model or invalid_project:
            raise terminal_label.TerminalLabelError("invalid Warp rendering state: %s" % profile)
        if entry.get("stage") not in ("pending", "updating", "installed", "restoring", "restored"):
            raise terminal_label.TerminalLabelError("invalid Warp profile stage: %s" % profile)
        if not isinstance(entry.get("installed_hash"), str):
            raise terminal_label.TerminalLabelError("missing installed Warp hash: %s" % profile)
        original_hash = entry.get("original_hash")
        if original_hash is not None and not isinstance(original_hash, str):
            raise terminal_label.TerminalLabelError(
                "invalid original Warp hash: %s" % profile
            )
        original_mode = entry.get("original_mode")
        if original_hash is None:
            if original_mode is not None:
                raise terminal_label.TerminalLabelError(
                    "unexpected original Warp mode: %s" % profile
                )
        elif not isinstance(original_mode, int) or not 0 <= original_mode <= 0o7777:
            raise terminal_label.TerminalLabelError(
                "invalid original Warp mode: %s" % profile
            )
        backup = entry.get("backup")
        if original_hash is None:
            if backup is not None:
                raise terminal_label.TerminalLabelError(
                    "unexpected Warp backup: %s" % profile
                )
        elif not isinstance(backup, str) or Path(backup).parent != backup_directory:
            raise terminal_label.ConfigConflict(
                "unexpected Warp backup path for %s" % profile
            )
    return value


def _allowed_current_hashes(entry: Mapping[str, Any]) -> set:
    stage = entry.get("stage")
    installed = entry.get("installed_hash")
    original = entry.get("original_hash")
    if stage == "installed":
        return {installed}
    if stage == "pending":
        return {original, installed}
    if stage == "updating":
        return {entry.get("previous_installed_hash"), installed}
    if stage == "restoring":
        return {installed, original}
    if stage == "restored":
        return {original}
    return set()


def _entry_conflicts(entry: Mapping[str, Any], check_backup: bool = True) -> List[str]:
    conflicts: List[str] = []
    path = Path(str(entry["path"]))
    try:
        current = _file_hash(path)
    except terminal_label.TerminalLabelError as exc:
        conflicts.append(str(exc))
    else:
        if current not in _allowed_current_hashes(entry):
            conflicts.append("managed Warp config hash changed: %s" % path)
    original_hash = entry.get("original_hash")
    backup = entry.get("backup")
    if check_backup and original_hash is not None and entry.get("stage") != "restored":
        if not isinstance(backup, str):
            conflicts.append("missing Warp backup: %s" % path)
        else:
            try:
                backup_hash = _file_hash(Path(backup))
            except terminal_label.TerminalLabelError as exc:
                conflicts.append(str(exc))
            else:
                if backup_hash != original_hash:
                    conflicts.append("Warp backup hash changed: %s" % backup)
    return conflicts


def _preflight(state: Mapping[str, Any]) -> List[str]:
    conflicts: List[str] = []
    for entry in state.get("profiles", []):
        conflicts.extend(_entry_conflicts(entry))
    return conflicts


def _finish_pending(
    state_path: Path,
    state: MutableMapping[str, Any],
    state_hash: str,
) -> Tuple[str, bool]:
    changed = False
    for entry in state["profiles"]:
        if entry.get("stage") not in ("pending", "updating"):
            continue
        path = Path(entry["path"])
        raw = _render_config(entry["profile"], entry["model"], entry.get("project_dir"))
        if _sha256(raw) != entry["installed_hash"]:
            raise terminal_label.ConfigConflict(
                "saved Warp rendering metadata changed: %s" % path
            )
        current_hash = _file_hash(path)
        if current_hash != entry["installed_hash"]:
            _atomic_replace(path, raw, current_hash, mode=0o600)
        entry["stage"] = "installed"
        entry.pop("previous_installed_hash", None)
        state_hash = _write_state(state_path, state, state_hash)
        changed = True
    if not state.get("complete"):
        state["complete"] = True
        state_hash = _write_state(state_path, state, state_hash)
        changed = True
    return state_hash, changed


def _selected_records(
    home: Path, profiles: Sequence[str]
) -> List[Any]:
    records = claude_all._discover_records(home, None)
    by_name = {record.info.name: record for record in records}
    if isinstance(profiles, str):
        requested = [profiles]
    else:
        requested = list(profiles)
    if not requested:
        return records
    unique: List[str] = []
    for name in requested:
        _validate_profile_name(name)
        if name not in by_name:
            raise terminal_label.TerminalLabelError(
                "claude-all profile does not exist: %s" % name
            )
        if name not in unique:
            unique.append(name)
    return [by_name[name] for name in unique]


def configure(
    home: Path,
    profiles: Sequence[str] = (),
    project_dir: Optional[Path] = None,
) -> Tuple[bool, List[str]]:
    """Create or update one Warp tab config for each selected claude-all profile."""
    resolved_home = _home_path(home)
    records = _selected_records(resolved_home, profiles)
    state_path = _state_path(resolved_home)
    project_default = _project_default(resolved_home, project_dir)
    messages: List[str] = []
    changed = False

    if not records and not state_path.exists():
        raise terminal_label.TerminalLabelError("no claude-all profiles found")

    if state_path.exists():
        state = _load_state(state_path, resolved_home)
        conflicts = _preflight(state)
        if conflicts:
            raise terminal_label.ConfigConflict("; ".join(conflicts))
        if any(entry.get("stage") in ("restoring", "restored") for entry in state["profiles"]):
            raise terminal_label.ConfigConflict(
                "Warp restore is in progress; run restore before configuring"
            )
        state_hash_value = _file_hash(state_path)
        if state_hash_value is None:
            raise terminal_label.ConfigConflict("Warp state disappeared: %s" % state_path)
        state_hash, resumed = _finish_pending(
            state_path, state, state_hash_value
        )
        changed = resumed
    else:
        state = {"version": STATE_VERSION, "complete": False, "profiles": []}
        state_hash = _write_state(state_path, state, None)
        changed = True

    entries = {entry["profile"]: entry for entry in state["profiles"]}
    for record in records:
        profile = _validate_profile_name(record.info.name)
        model = _profile_model(record, resolved_home)
        raw = _render_config(profile, model, project_default)
        installed_hash = _sha256(raw)
        path = _tab_path(resolved_home, profile)
        entry = entries.get(profile)

        if entry is not None:
            if entry["installed_hash"] == installed_hash:
                messages.append("OK Warp tab config is current: %s" % profile)
                continue
            previous_hash = entry["installed_hash"]
            entry.update(
                {
                    "model": model,
                    "project_dir": project_default,
                    "installed_hash": installed_hash,
                    "previous_installed_hash": previous_hash,
                    "stage": "updating",
                }
            )
            state["complete"] = False
            state_hash = _write_state(state_path, state, state_hash)
            _atomic_replace(path, raw, previous_hash, mode=0o600)
            entry["stage"] = "installed"
            entry.pop("previous_installed_hash", None)
            state_hash = _write_state(state_path, state, state_hash)
            changed = True
            messages.append("OK updated Warp tab config: %s" % path)
            continue

        if path.is_symlink():
            raise terminal_label.ConfigConflict("refusing symlinked Warp tab config: %s" % path)
        try:
            original = path.read_bytes()
        except FileNotFoundError:
            original = None
        except OSError as exc:
            raise terminal_label.TerminalLabelError(
                "could not read Warp tab config %s: %s" % (path, exc)
            ) from exc
        original_hash = _sha256(original) if original is not None else None
        original_mode = (
            stat.S_IMODE(path.stat().st_mode) if original is not None else None
        )
        backup = (
            _write_backup(resolved_home, profile, original)
            if original is not None
            else None
        )
        entry = {
            "profile": profile,
            "path": str(path),
            "model": model,
            "project_dir": project_default,
            "original_hash": original_hash,
            "original_mode": original_mode,
            "backup": str(backup) if backup is not None else None,
            "installed_hash": installed_hash,
            "stage": "pending",
        }
        state["profiles"].append(entry)
        entries[profile] = entry
        state["complete"] = False
        state_hash = _write_state(state_path, state, state_hash)
        _atomic_replace(path, raw, original_hash, mode=0o600)
        entry["stage"] = "installed"
        state_hash = _write_state(state_path, state, state_hash)
        changed = True
        messages.append("OK created Warp tab config: %s" % path)

    if not state.get("complete"):
        state["complete"] = True
        _write_state(state_path, state, state_hash)
    if not messages:
        messages.append("OK Warp tab configs are already current")
    messages.append("OK reversible Warp state: %s" % state_path)
    return changed, messages


def doctor(home: Path) -> Tuple[bool, List[str]]:
    """Check all managed Warp configs, state, and backups without changing them."""
    resolved_home = _home_path(home)
    state_path = _state_path(resolved_home)
    if not state_path.exists():
        return False, ["FAIL reversible Warp state is missing: %s" % state_path]
    try:
        state = _load_state(state_path, resolved_home)
    except terminal_label.TerminalLabelError as exc:
        return False, ["FAIL %s" % exc]

    healthy = True
    messages: List[str] = []
    if stat.S_IMODE(state_path.stat().st_mode) == 0o600:
        messages.append("OK Warp state mode is 0600: %s" % state_path)
    else:
        healthy = False
        messages.append("FAIL Warp state mode is not 0600: %s" % state_path)
    if not state.get("complete"):
        healthy = False
        messages.append("FAIL Warp configuration is incomplete")

    for entry in state["profiles"]:
        conflicts = _entry_conflicts(entry)
        if conflicts:
            healthy = False
            messages.extend("FAIL %s" % conflict for conflict in conflicts)
            continue
        path = Path(entry["path"])
        if path.exists() and stat.S_IMODE(path.stat().st_mode) != 0o600:
            healthy = False
            messages.append("FAIL Warp tab config mode is not 0600: %s" % path)
        else:
            messages.append("OK Warp tab config: %s" % path)
        backup = entry.get("backup")
        if isinstance(backup, str) and Path(backup).exists():
            if stat.S_IMODE(Path(backup).stat().st_mode) != 0o600:
                healthy = False
                messages.append("FAIL Warp backup mode is not 0600: %s" % backup)
            else:
                messages.append("OK Warp backup mode is 0600: %s" % backup)
    if not state["profiles"]:
        healthy = False
        messages.append("FAIL no Warp tab configs are managed")
    return healthy, messages


def restore(home: Path) -> Tuple[bool, List[str]]:
    """Restore or remove every Warp tab config tracked by :func:`configure`."""
    resolved_home = _home_path(home)
    state_path = _state_path(resolved_home)
    if not state_path.exists():
        return False, ["Warp tab configs are not managed"]
    state = _load_state(state_path, resolved_home)
    conflicts = _preflight(state)
    if conflicts:
        raise terminal_label.ConfigConflict("; ".join(conflicts))
    state_hash_value = _file_hash(state_path)
    if state_hash_value is None:
        raise terminal_label.ConfigConflict("Warp state disappeared: %s" % state_path)
    state_hash, _ = _finish_pending(state_path, state, state_hash_value)

    # Validate every installed file and backup before restoring the first one.
    conflicts = _preflight(state)
    if conflicts:
        raise terminal_label.ConfigConflict("; ".join(conflicts))

    messages: List[str] = []
    for entry in reversed(state["profiles"]):
        path = Path(entry["path"])
        if entry.get("stage") == "restored":
            continue
        entry["stage"] = "restoring"
        state["complete"] = False
        state_hash = _write_state(state_path, state, state_hash)
        current_hash = _file_hash(path)
        original_hash = entry.get("original_hash")
        if original_hash is None:
            if current_hash is not None:
                if current_hash != entry["installed_hash"]:
                    raise terminal_label.ConfigConflict(
                        "managed Warp config changed during restore: %s" % path
                    )
                path.unlink()
        elif current_hash != original_hash:
            backup = Path(entry["backup"])
            raw = backup.read_bytes()
            if _sha256(raw) != original_hash:
                raise terminal_label.ConfigConflict("Warp backup changed: %s" % backup)
            _atomic_replace(
                path, raw, current_hash, mode=int(entry["original_mode"])
            )
        entry["stage"] = "restored"
        state_hash = _write_state(state_path, state, state_hash)
        backup_value = entry.get("backup")
        if isinstance(backup_value, str):
            try:
                Path(backup_value).unlink()
            except FileNotFoundError:
                pass
        messages.append("OK restored Warp tab config: %s" % path)

    # A prior restore may have checkpointed ``restored`` before deleting its
    # backup. Clean every tracked backup before dropping the authoritative state.
    for entry in state["profiles"]:
        backup_value = entry.get("backup")
        if not isinstance(backup_value, str):
            continue
        try:
            Path(backup_value).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise terminal_label.TerminalLabelError(
                "could not remove Warp backup %s: %s" % (backup_value, exc)
            ) from exc

    try:
        state_path.unlink()
    except OSError as exc:
        raise terminal_label.TerminalLabelError(
            "could not remove Warp state %s: %s" % (state_path, exc)
        ) from exc
    try:
        _backup_dir(resolved_home).rmdir()
    except OSError:
        pass
    return True, messages
