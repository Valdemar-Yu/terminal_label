#!/usr/bin/env python3
"""Batch terminal-label management for claude-all profiles.

The module deliberately parses profile files as data.  It never sources them and
never invokes a shell while discovering Claude configuration directories.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, NamedTuple, Optional, Sequence, Tuple


LIB_DIR = str(Path(__file__).resolve().parent)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import terminal_label  # noqa: E402

STATE_FILENAME = "terminal-label-claude-all.json"
STATE_VERSION = 2
PROFILE_BACKUP_SUFFIX = ".terminal-label-claude-all.bak"
PLUGIN_NAME = "terminal-label@terminal-label"
MARKETPLACE_NAME = "terminal-label"
MARKETPLACE_SOURCE = "Valdemar-Yu/terminal_label"
REFRESH_INTERVAL = 5
MAX_ENV_FILE_SIZE = 1024 * 1024

_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
)
_MANAGED_PROFILE_KEYS = {
    "CLAUDISH_STATUSLINE_COMMAND",
    "CLAUDISH_STATUSLINE_REFRESH",
    "CCGP_STATUSLINE",
    "CLAUDE_ALL_POOL_USAGE_URL",
    "CLAUDE_ALL_POOL_KEYCHAIN_SERVICE",
    "CLAUDE_ALL_POOL_COOKIE_NAME",
}
_POOL_FIELDS = {
    "pool_usage_url": "CLAUDE_ALL_POOL_USAGE_URL",
    "pool_keychain_service": "CLAUDE_ALL_POOL_KEYCHAIN_SERVICE",
    "pool_cookie_name": "CLAUDE_ALL_POOL_COOKIE_NAME",
}

PluginRunner = Callable[[Sequence[str], Mapping[str, str]], Any]


class ProfileInfo(NamedTuple):
    """A profile and the Claude configuration directory it resolves to."""

    name: str
    path: Path
    config_dir: Path
    claudish: bool
    cc_gpt_plbbl: bool


class _ProfileRecord(NamedTuple):
    info: ProfileInfo
    values: Mapping[str, str]
    ccgp_config: Mapping[str, str]


class EnvParseError(terminal_label.TerminalLabelError):
    """Raised when an env file cannot be parsed safely."""


def _home_path(home: Optional[Path]) -> Path:
    if home is not None:
        candidate = Path(home)
    else:
        candidate = Path(os.environ.get("HOME", str(Path.home())))
    return candidate.expanduser().resolve()


def _decode_ansi_c_quoted(value: str, path: Path, line_number: int) -> str:
    """Decode the small ANSI-C quoted subset emitted by bash ``printf %q``."""
    result: List[str] = []
    index = 2
    closed = False
    simple = {
        "a": "\a",
        "b": "\b",
        "e": "\x1b",
        "E": "\x1b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "\\": "\\",
        "'": "'",
        '"': '"',
    }
    while index < len(value):
        char = value[index]
        if char == "'":
            closed = True
            index += 1
            break
        if char != "\\":
            result.append(char)
            index += 1
            continue
        index += 1
        if index >= len(value):
            raise EnvParseError("unterminated escape in %s:%d" % (path, line_number))
        escaped = value[index]
        index += 1
        if escaped in simple:
            result.append(simple[escaped])
        elif escaped == "x":
            digits = value[index : index + 2]
            if not digits or not all(char in "0123456789abcdefABCDEF" for char in digits):
                raise EnvParseError("invalid hexadecimal escape in %s:%d" % (path, line_number))
            index += len(digits)
            result.append(chr(int(digits, 16)))
        elif escaped in "01234567":
            digits = escaped
            while index < len(value) and len(digits) < 3 and value[index] in "01234567":
                digits += value[index]
                index += 1
            result.append(chr(int(digits, 8)))
        elif escaped == "\n":
            continue
        else:
            # Bash preserves a backslash for escapes that have no ANSI-C meaning.
            result.append("\\" + escaped)
    trailing = value[index:].strip()
    if not closed or (trailing and not trailing.startswith("#")):
        raise EnvParseError("invalid ANSI-C quoted value in %s:%d" % (path, line_number))
    decoded = "".join(result)
    if "\x00" in decoded:
        raise EnvParseError("NUL byte in %s:%d" % (path, line_number))
    return decoded


def _strip_env_comment(value: str) -> str:
    quote: Optional[str] = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in ("'", '"'):
            quote = character
        elif character == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value


def _parse_env_value(value: str, path: Path, line_number: int) -> str:
    value = _strip_env_comment(value).strip()
    if value.startswith("$'"):
        return _decode_ansi_c_quoted(value, path, line_number)
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise EnvParseError("invalid quoted value in %s:%d: %s" % (path, line_number, exc)) from exc
    if not tokens:
        return ""
    if len(tokens) != 1:
        # Shell command substitutions can contain whitespace, but evaluating
        # them is precisely what this parser must avoid.  Preserve such input
        # literally; consumers of path fields apply stricter validation.
        literal = value.strip()
        if "\x00" in literal:
            raise EnvParseError("NUL byte in %s:%d" % (path, line_number))
        return literal
    if "\x00" in tokens[0]:
        raise EnvParseError("NUL byte in %s:%d" % (path, line_number))
    return tokens[0]


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse assignment lines without evaluating expansions or commands.

    Blank lines, comments, and non-assignment lines are ignored.  Quoting is
    decoded, but ``$VAR``, command substitutions, redirects, and shell syntax
    are never evaluated.
    """
    path = Path(path)
    if path.is_symlink():
        raise EnvParseError("refusing symlinked env file: %s" % path)
    try:
        if path.stat().st_size > MAX_ENV_FILE_SIZE:
            raise EnvParseError("env file is too large: %s" % path)
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EnvParseError("could not read env file %s: %s" % (path, exc)) from exc
    if "\x00" in text:
        raise EnvParseError("NUL byte in env file: %s" % path)

    values: Dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if match is None:
            continue
        key, raw_value = match.groups()
        values[key] = _parse_env_value(raw_value, path, line_number)
    return values


def _safe_path(value: str, home: Path, description: str) -> Path:
    expanded = value
    if expanded == "~":
        expanded = str(home)
    elif expanded.startswith("~/"):
        expanded = str(home / expanded[2:])
    expanded = expanded.replace("${HOME}", str(home)).replace("$HOME", str(home))
    if any(character in expanded for character in ("$", "`", ";", "|", "&", "<", ">", "(", ")")):
        raise EnvParseError("unsafe shell syntax in %s" % description)
    if any(ord(character) < 32 for character in expanded):
        raise EnvParseError("control character in %s" % description)
    path = Path(expanded)
    if not path.is_absolute():
        raise EnvParseError("%s must be an absolute path" % description)
    return path.resolve()


def _command_basename(command: str) -> str:
    command = command.strip()
    if not command:
        return ""
    if "/" in command and not any(character in command for character in "\"'"):
        return Path(command).name
    try:
        parts = shlex.split(command)
    except ValueError:
        return ""
    return Path(parts[0]).name if parts else ""


def _read_ccgp_config(path: Path) -> Dict[str, str]:
    """Read only non-secret cc-gpt-plbbl routing and pool display fields."""
    try:
        if not path.exists():
            return {}
        if path.stat().st_size > MAX_ENV_FILE_SIZE:
            raise EnvParseError("cc-gpt-plbbl config is too large: %s" % path)
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise EnvParseError("could not read cc-gpt-plbbl config %s: %s" % (path, exc)) from exc
    allowed = set(_POOL_FIELDS) | {"config_dir"}
    result: Dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in allowed:
            continue
        if "\x00" in value or any(ord(character) < 32 for character in value):
            raise EnvParseError("control character in %s:%d" % (path, line_number))
        result[key] = value
    return result


def _profile_record(path: Path, home: Path) -> _ProfileRecord:
    values = parse_env_file(path)
    name = path.stem
    launch = values.get("CLAUDE_ALL_LAUNCH", "direct")
    command_name = _command_basename(values.get("CLAUDE_ALL_CMD", ""))
    ccgp = name == "claude-plbbl" or command_name == "cc-gpt-plbbl"

    ccgp_config: Mapping[str, str] = {}
    if ccgp:
        config_file_value = values.get(
            "CCGP_CONFIG_FILE", str(home / ".config" / "claude-all" / "config")
        )
        config_file = _safe_path(config_file_value, home, "%s CCGP_CONFIG_FILE" % path)
        ccgp_config = _read_ccgp_config(config_file)

    explicit_dir = values.get("CLAUDE_CONFIG_DIR")
    if ccgp:
        config_dir = _safe_path(
            values.get("CCGP_CONFIG_DIR")
            or ccgp_config.get("config_dir")
            or str(home / ".claude-plbbl"),
            home,
            "%s PLBBL config directory" % path,
        )
    elif command_name == "claude-fugu":
        config_dir = _safe_path(
            values.get("FUGU_CONFIG_DIR") or str(home / ".claude-fugu"),
            home,
            "%s FUGU_CONFIG_DIR" % path,
        )
    elif explicit_dir:
        config_dir = _safe_path(explicit_dir, home, "%s CLAUDE_CONFIG_DIR" % path)
    elif launch in ("direct", "claudish"):
        config_dir = (home / ".claude-all").resolve()
    elif launch == "cmd" and command_name == "claude":
        config_dir = (home / ".claude").resolve()
    elif launch == "cmd" and command_name.startswith("claude-"):
        config_dir = (home / ("." + command_name)).resolve()
    elif name == "claude":
        config_dir = (home / ".claude").resolve()
    elif name.startswith("claude-"):
        config_dir = (home / ("." + name)).resolve()
    else:
        config_dir = (home / ".claude-all").resolve()

    label = values.get("CLAUDE_ALL_LABEL", "").lower()
    claudish = (
        launch == "claudish"
        or ccgp
        or "CLAUDISH_STATUSLINE_COMMAND" in values
        or command_name in ("claudish", "claude-fugu")
        or "claudish" in label
    )
    return _ProfileRecord(
        ProfileInfo(name, path.resolve(), config_dir, claudish, ccgp),
        values,
        ccgp_config,
    )


def _discover_records(home: Optional[Path], profiles_dir: Optional[Path]) -> List[_ProfileRecord]:
    resolved_home = _home_path(home)
    directory = (
        Path(profiles_dir).expanduser()
        if profiles_dir is not None
        else resolved_home / ".claude-all" / "profiles"
    )
    if directory.is_symlink():
        raise EnvParseError("refusing symlinked profiles directory: %s" % directory)
    directory = directory.resolve()
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise EnvParseError("profiles path is not a directory: %s" % directory)
    return [_profile_record(path, resolved_home) for path in sorted(directory.glob("*.env"))]


def discover_profiles(
    home: Optional[Path] = None, profiles_dir: Optional[Path] = None
) -> List[ProfileInfo]:
    """Discover every ``*.env`` profile and its effective config directory."""
    return [record.info for record in _discover_records(home, profiles_dir)]


def discover_profile_configs(
    home: Optional[Path] = None, profiles_dir: Optional[Path] = None
) -> Dict[Path, List[ProfileInfo]]:
    """Group profiles by resolved config directory, deduplicating directories."""
    grouped: Dict[Path, List[ProfileInfo]] = {}
    for profile in discover_profiles(home, profiles_dir):
        grouped.setdefault(profile.config_dir, []).append(profile)
    return grouped


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_hash(path: Path) -> Optional[str]:
    try:
        return _sha256(path.read_bytes())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise terminal_label.TerminalLabelError("could not hash %s: %s" % (path, exc)) from exc


def _next_backup_path(path: Path) -> Path:
    candidate = path.with_name(path.name + PROFILE_BACKUP_SUFFIX)
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(path.name + PROFILE_BACKUP_SUFFIX + ".%d" % suffix)
        suffix += 1
    return candidate


def _write_backup(path: Path, raw: bytes) -> Path:
    backup = _next_backup_path(path)
    descriptor = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return backup.resolve()


def _atomic_replace(path: Path, raw: bytes, expected_hash: Optional[str], mode: int = 0o600) -> None:
    if path.is_symlink():
        raise terminal_label.ConfigConflict("refusing to replace symlinked file: %s" % path)
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
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _shell_assignment(key: str, value: str) -> str:
    if "\x00" in value:
        raise EnvParseError("cannot write NUL in %s" % key)
    return "%s='%s'" % (key, value.replace("'", "'\"'\"'"))


def _updated_profile(
    record: _ProfileRecord, executable: Path, home: Path
) -> Tuple[bytes, Dict[str, str]]:
    path = record.info.path
    try:
        original = path.read_bytes()
        text = original.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EnvParseError("could not read profile %s: %s" % (path, exc)) from exc

    current_command = record.values.get("CLAUDISH_STATUSLINE_COMMAND") or None
    managed_command: Optional[str] = None
    if current_command:
        parsed = terminal_label.parse_proxy_command(current_command)
        if parsed is not None:
            owner, _ = parsed
            if owner.expanduser().resolve() != executable.expanduser().resolve():
                raise terminal_label.ConfigConflict(
                    "profile status line belongs to another terminal-label runtime: %s"
                    % path
                )
            managed_command = current_command
        elif terminal_label._looks_like_terminal_label_proxy(current_command):
            raise terminal_label.ConfigConflict(
                "profile contains an invalid terminal-label command: %s" % path
            )
    if managed_command is None:
        original_command = current_command
        if original_command is None:
            shared = home / ".local" / "share" / "claude-all" / "statusline" / "statusline.py"
            if shared.is_file():
                original_command = "python3 %s" % shlex.quote(str(shared))
        managed_command = terminal_label.build_proxy_command(executable, original_command)

    updates: Dict[str, str] = {
        "CLAUDISH_STATUSLINE_COMMAND": managed_command,
        "CLAUDISH_STATUSLINE_REFRESH": str(REFRESH_INTERVAL),
    }
    if record.values.get("CLAUDE_ALL_LAUNCH") == "claudish":
        updates["CLAUDE_ALL_LAUNCH"] = "cmd"
        updates["CLAUDE_ALL_CMD"] = str(
            executable.parent / "terminal-label-claudish"
        )
        updates["CLAUDE_CONFIG_DIR"] = str(record.info.config_dir)
    if record.info.cc_gpt_plbbl:
        updates["CCGP_STATUSLINE"] = "no"
        for config_key, profile_key in _POOL_FIELDS.items():
            if config_key in record.ccgp_config:
                updates[profile_key] = record.ccgp_config[config_key]

    kept_lines: List[str] = []
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        if line.strip() == "# terminal-label managed values":
            continue
        if match is not None and match.group(1) in updates:
            continue
        kept_lines.append(line)
    while kept_lines and not kept_lines[-1].strip():
        kept_lines.pop()
    if kept_lines:
        kept_lines.append("")
    kept_lines.append("# terminal-label managed values")
    kept_lines.extend(_shell_assignment(key, updates[key]) for key in sorted(updates))
    return ("\n".join(kept_lines) + "\n").encode("utf-8"), updates


def _inject_profile(
    record: _ProfileRecord, executable: Path, home: Path
) -> Mapping[str, Any]:
    path = record.info.path
    if path.is_symlink():
        raise terminal_label.ConfigConflict("refusing to replace symlinked profile: %s" % path)
    original = path.read_bytes()
    original_hash = _sha256(original)
    replacement, _ = _updated_profile(record, executable, home)
    backup = _write_backup(path, original)
    try:
        _atomic_replace(path, replacement, original_hash, mode=0o600)
    except Exception:
        try:
            backup.unlink()
        except OSError:
            pass
        raise
    return {
        "path": str(path),
        "backup": str(backup),
        "original_hash": original_hash,
        "installed_hash": _sha256(replacement),
    }


def _state_path(home: Path, state_path: Optional[Path]) -> Path:
    return (
        Path(state_path).resolve()
        if state_path is not None
        else home / ".claude-all" / STATE_FILENAME
    )


def _write_state(path: Path, state: Mapping[str, Any], expected_hash: Optional[str]) -> None:
    raw = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_replace(path, raw, expected_hash, mode=0o600)
    try:
        path.chmod(0o600)
        path.parent.chmod(0o700)
    except OSError:
        pass


def _load_state(path: Path) -> MutableMapping[str, Any]:
    if path.is_symlink():
        raise terminal_label.ConfigConflict("refusing symlinked state file: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise terminal_label.TerminalLabelError("batch state does not exist: %s" % path) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise terminal_label.TerminalLabelError("could not read batch state %s: %s" % (path, exc)) from exc
    if not isinstance(data, MutableMapping) or data.get("version") != STATE_VERSION:
        raise terminal_label.TerminalLabelError("unsupported batch state: %s" % path)
    if not isinstance(data.get("configs"), list) or not isinstance(data.get("profiles"), list):
        raise terminal_label.TerminalLabelError("invalid batch state: %s" % path)
    return data


def _default_plugin_runner(args: Sequence[str], env: Mapping[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["claude"] + list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        env=dict(env),
    )


def _run_plugin(
    runner: PluginRunner, args: Sequence[str], home: Path, config_dir: Path
) -> None:
    allowed = {
        "PATH", "TMPDIR", "TMP", "TEMP", "SHELL", "LANG", "LC_ALL",
        "TERM", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "SSH_AUTH_SOCK",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment["HOME"] = str(home)
    environment["CLAUDE_CONFIG_DIR"] = str(config_dir)
    try:
        result = runner(tuple(args), environment)
    except (OSError, subprocess.SubprocessError) as exc:
        raise terminal_label.TerminalLabelError(
            "plugin command failed for %s: %s" % (config_dir, exc)
        ) from exc
    if isinstance(result, bool):
        returncode = 0 if result else 1
    elif isinstance(result, int):
        returncode = result
    else:
        returncode = int(getattr(result, "returncode", 0))
    if returncode != 0:
        raise terminal_label.TerminalLabelError(
            "plugin command returned %d for %s" % (returncode, config_dir)
        )


def _preflight_state(state: Mapping[str, Any]) -> List[str]:
    """Validate batch-owned profile snapshots without policing unrelated settings."""
    conflicts: List[str] = []
    for entry in state.get("configs", []):
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
            conflicts.append("invalid configs state entry")
            continue
        if not isinstance(entry.get("runtime"), str):
            conflicts.append("invalid runtime state for %s" % entry["path"])

    for entry in state.get("profiles", []):
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
            conflicts.append("invalid profiles state entry")
            continue
        path = Path(entry["path"])
        expected = entry.get("installed_hash")
        current = _file_hash(path)
        allowed_hashes = {expected}
        if entry.get("stage") == "pending" or entry.get("local_cleanup") in {
            "profile_restoring", "profile_restored"
        }:
            allowed_hashes.add(entry.get("original_hash"))
        if not isinstance(expected, str) or current not in allowed_hashes:
            conflicts.append("installed file hash changed: %s" % path)
        backup_value = entry.get("backup")
        original_hash = entry.get("original_hash")
        if not isinstance(backup_value, str):
            conflicts.append("missing profile backup: %s" % path)
        elif entry.get("local_cleanup") != "profile_restored" and (
            not isinstance(original_hash, str)
            or _file_hash(Path(backup_value)) != original_hash
        ):
            conflicts.append("backup file hash changed: %s" % backup_value)
    return conflicts


def batch_install(
    home: Optional[Path] = None,
    profiles_dir: Optional[Path] = None,
    state_path: Optional[Path] = None,
    plugin_runner: Optional[PluginRunner] = None,
    marketplace_source: Optional[str] = None,
    force: bool = False,
    extra_config_dirs: Sequence[Path] = (),
    skip_plugin_install: bool = False,
) -> Mapping[str, Any]:
    """Install or incrementally update all unique claude-all config directories."""
    resolved_home = _home_path(home)
    records = _discover_records(resolved_home, profiles_dir)
    if not records and not extra_config_dirs:
        raise terminal_label.TerminalLabelError("no claude-all profiles found")

    grouped: Dict[Path, List[_ProfileRecord]] = {}
    for record in records:
        grouped.setdefault(record.info.config_dir, []).append(record)
    for extra in extra_config_dirs:
        grouped.setdefault(Path(extra).expanduser().resolve(), [])

    path = _state_path(resolved_home, state_path)
    if path.exists():
        state = _load_state(path)
        conflicts = _preflight_state(state)
        if conflicts:
            raise terminal_label.ConfigConflict("; ".join(conflicts))
        if not state.get("complete"):
            raise terminal_label.TerminalLabelError(
                "previous claude-all installation is incomplete; run uninstall-claude-all before retrying"
            )
        expected_state_hash = _file_hash(path)
        state["terminal_label_version"] = terminal_label.VERSION
        state["complete"] = False
        _write_state(path, state, expected_state_hash)
        state_hash = _file_hash(path)
    else:
        state = {
            "version": STATE_VERSION,
            "terminal_label_version": terminal_label.VERSION,
            "complete": False,
            "configs": [],
            "profiles": [],
        }
        _write_state(path, state, None)
        state_hash = _file_hash(path)

    source = marketplace_source or MARKETPLACE_SOURCE
    runner = plugin_runner or _default_plugin_runner
    configs_by_dir = {
        Path(entry["config_dir"]): entry for entry in state["configs"]
        if isinstance(entry, MutableMapping) and isinstance(entry.get("config_dir"), str)
    }

    for config_dir in sorted(grouped, key=str):
        new_config = config_dir not in configs_by_dir
        settings_path = config_dir / "settings.json"
        executable = terminal_label.runtime_executable(settings_path)
        names = [record.info.name for record in grouped[config_dir]]
        entry = configs_by_dir.get(config_dir)
        if entry is None:
            already_managed = terminal_label.validate_uninstall(
                settings_path, executable
            )
            entry = {
                "path": str(settings_path),
                "config_dir": str(config_dir),
                "profiles": names,
                "runtime": str(executable),
                "stage": "pending",
                "plugin_managed": not skip_plugin_install,
                "settings_owned": not already_managed,
                "runtime_owned": not executable.exists(),
            }
            state["configs"].append(entry)
            configs_by_dir[config_dir] = entry
            _write_state(path, state, state_hash)
            state_hash = _file_hash(path)
        else:
            entry["profiles"] = names

        if new_config and not skip_plugin_install:
            _run_plugin(
                runner,
                ("plugin", "marketplace", "add", str(source)),
                resolved_home,
                config_dir,
            )
            entry["stage"] = "marketplace_added"
            _write_state(path, state, state_hash)
            state_hash = _file_hash(path)
            _run_plugin(
                runner,
                ("plugin", "install", PLUGIN_NAME),
                resolved_home,
                config_dir,
            )
            entry["stage"] = "plugin_installed"
            _write_state(path, state, state_hash)
            state_hash = _file_hash(path)

        executable = terminal_label.install_runtime(settings_path)
        entry["stage"] = "runtime_installed"
        entry["runtime_hash"] = _file_hash(executable)
        _write_state(path, state, state_hash)
        state_hash = _file_hash(path)
        changed, backup = terminal_label.install(settings_path, executable, force=force)
        if new_config:
            entry["settings_owned"] = changed
            entry["backup"] = str(backup.resolve()) if backup is not None else None
        entry.update({
            "stage": "installed",
            "runtime": str(executable),
            "runtime_hash": _file_hash(executable),
            "installed_hash": _file_hash(settings_path),
        })
        _write_state(path, state, state_hash)
        state_hash = _file_hash(path)

    runtime_by_config = {
        Path(entry["config_dir"]): Path(entry["runtime"])
        for entry in state["configs"]
    }
    installed_profiles = {
        Path(entry["path"]) for entry in state["profiles"]
        if isinstance(entry, Mapping) and isinstance(entry.get("path"), str)
    }
    for record in records:
        if not record.info.claudish or record.info.path in installed_profiles:
            continue
        profile_path = record.info.path
        if profile_path.is_symlink():
            raise terminal_label.ConfigConflict(
                "refusing to replace symlinked profile: %s" % profile_path
            )
        original = profile_path.read_bytes()
        original_hash = _sha256(original)
        replacement, _ = _updated_profile(
            record, runtime_by_config[record.info.config_dir], resolved_home
        )
        backup = _write_backup(profile_path, original)
        profile_state = {
            "path": str(profile_path),
            "backup": str(backup),
            "original_hash": original_hash,
            "installed_hash": _sha256(replacement),
            "stage": "pending",
        }
        state["profiles"].append(profile_state)
        _write_state(path, state, state_hash)
        state_hash = _file_hash(path)
        _atomic_replace(profile_path, replacement, original_hash, mode=0o600)
        profile_state["stage"] = "installed"
        installed_profiles.add(profile_path)
        _write_state(path, state, state_hash)
        state_hash = _file_hash(path)

    state["complete"] = True
    _write_state(path, state, state_hash)
    return state


def batch_doctor(
    home: Optional[Path] = None,
    profiles_dir: Optional[Path] = None,
    state_path: Optional[Path] = None,
    extra_config_dirs: Sequence[Path] = (),
) -> Tuple[bool, Dict[str, List[str]]]:
    """Run the core doctor once for every unique discovered config directory."""
    resolved_home = _home_path(home)
    path = _state_path(resolved_home, state_path)
    reports: Dict[str, List[str]] = {}
    healthy = True

    if path.exists():
        state = _load_state(path)
        if not state.get("complete"):
            healthy = False
            reports["state"] = ["FAIL claude-all installation is incomplete"]
        conflicts = _preflight_state(state)
        if conflicts:
            healthy = False
            reports.setdefault("state", []).extend(
                "FAIL %s" % conflict for conflict in conflicts
            )
        entries = state["configs"]
        targets = [
            (Path(entry["path"]), Path(entry["runtime"]))
            for entry in entries
            if isinstance(entry, Mapping)
            and isinstance(entry.get("path"), str)
            and isinstance(entry.get("runtime"), str)
        ]
    else:
        grouped = discover_profile_configs(resolved_home, profiles_dir)
        for extra in extra_config_dirs:
            grouped.setdefault(Path(extra).expanduser().resolve(), [])
        targets = [
            (config_dir / "settings.json", terminal_label.runtime_executable(config_dir / "settings.json"))
            for config_dir in grouped
        ]

    for settings_path, executable in sorted(targets, key=lambda pair: str(pair[0])):
        target_healthy, messages = terminal_label.doctor(settings_path, executable)
        runtime_root = executable.parent.parent
        for relative in (
            Path("lib/claude_all.py"),
            Path("bin/terminal-label-claudish"),
        ):
            candidate = runtime_root / relative
            executable_component = relative.parent.name == "bin"
            if candidate.is_file() and (
                not executable_component or os.access(candidate, os.X_OK)
            ):
                messages.append("OK runtime component %s" % relative)
            else:
                target_healthy = False
                messages.append("FAIL runtime component is missing or unusable: %s" % candidate)
        reports[str(settings_path.parent)] = messages
        healthy = target_healthy and healthy
    if not targets:
        healthy = False
        reports.setdefault("profiles", []).append("FAIL no claude-all profiles found")
    return healthy, reports


def batch_uninstall(
    home: Optional[Path] = None,
    state_path: Optional[Path] = None,
    plugin_runner: Optional[PluginRunner] = None,
    remove_plugin: bool = False,
) -> Mapping[str, Any]:
    """Restore tracked profiles and settings, refusing any hash conflict."""
    resolved_home = _home_path(home)
    path = _state_path(resolved_home, state_path)
    state = _load_state(path)
    state_hash = _file_hash(path)
    conflicts = _preflight_state(state)
    if conflicts:
        raise terminal_label.ConfigConflict("; ".join(conflicts))

    runner = plugin_runner or _default_plugin_runner
    configs = list(state["configs"])

    # Validate every owned settings file before the first destructive change.
    # Entries with a cleanup marker are resumed below instead of re-preflighted.
    for entry in configs:
        cleanup = entry.get("local_cleanup")
        if entry.get("settings_owned") and entry.get("stage") in {
            "runtime_installed", "installed"
        } and cleanup is None:
            owned = terminal_label.validate_uninstall(
                Path(entry["path"]), Path(entry["runtime"])
            )
            if entry.get("stage") == "installed" and not owned:
                raise terminal_label.ConfigConflict(
                    "managed statusLine was replaced after batch install: %s"
                    % entry["path"]
                )

    # Plugin cleanup is outward-facing and can fail. Run it before local restore
    # so the intact state remains retryable on a permission/network error.
    if remove_plugin:
        for entry in configs:
            if not entry.get("plugin_managed"):
                continue
            config_dir = Path(entry["config_dir"])
            stage = entry.get("stage")
            cleanup = entry.get("plugin_cleanup")
            if (
                stage in {"plugin_installed", "runtime_installed", "installed"}
                and cleanup is None
            ):
                _run_plugin(
                    runner,
                    ("plugin", "uninstall", PLUGIN_NAME),
                    resolved_home,
                    config_dir,
                )
                entry["plugin_cleanup"] = "plugin_removed"
                _write_state(path, state, state_hash)
                state_hash = _file_hash(path)
                cleanup = "plugin_removed"
            if (
                stage in {
                    "marketplace_added", "plugin_installed", "runtime_installed", "installed"
                }
                and cleanup != "marketplace_removed"
            ):
                _run_plugin(
                    runner,
                    ("plugin", "marketplace", "remove", MARKETPLACE_NAME),
                    resolved_home,
                    config_dir,
                )
                entry["plugin_cleanup"] = "marketplace_removed"
                _write_state(path, state, state_hash)
                state_hash = _file_hash(path)

    uninstalled_configs: List[Mapping[str, Any]] = []
    for entry in reversed(configs):
        settings_path = Path(entry["path"])
        executable = Path(entry["runtime"])
        changed = False
        backup = None
        cleanup = entry.get("local_cleanup")
        if cleanup not in {"settings_restored", "runtime_removing", "runtime_removed"}:
            entry["local_cleanup"] = "settings_restoring"
            _write_state(path, state, state_hash)
            state_hash = _file_hash(path)
            if entry.get("settings_owned") and terminal_label.validate_uninstall(
                settings_path, executable
            ):
                changed, backup = terminal_label.uninstall(settings_path, executable)
            entry["local_cleanup"] = "settings_restored"
            _write_state(path, state, state_hash)
            state_hash = _file_hash(path)
        runtime_removed = False
        if entry.get("local_cleanup") != "runtime_removed":
            entry["local_cleanup"] = "runtime_removing"
            _write_state(path, state, state_hash)
            state_hash = _file_hash(path)
            runtime_removed = (
                terminal_label.remove_runtime(settings_path)
                if entry.get("runtime_owned")
                else False
            )
            entry["local_cleanup"] = "runtime_removed"
            _write_state(path, state, state_hash)
            state_hash = _file_hash(path)
        uninstalled_configs.append(
            {
                "path": str(settings_path),
                "changed": changed,
                "backup": str(backup) if backup is not None else None,
                "runtime_removed": runtime_removed,
            }
        )

    restored_profiles: List[str] = []
    for entry in reversed(state["profiles"]):
        profile_path = Path(entry["path"])
        backup = Path(entry["backup"])
        cleanup = entry.get("local_cleanup")
        if cleanup != "profile_restored":
            entry["local_cleanup"] = "profile_restoring"
            _write_state(path, state, state_hash)
            state_hash = _file_hash(path)
            replacement = backup.read_bytes()
            current_hash = _file_hash(profile_path)
            if current_hash == entry["installed_hash"]:
                _atomic_replace(
                    profile_path,
                    replacement,
                    entry["installed_hash"],
                    mode=0o600,
                )
                restored_profiles.append(str(profile_path))
            elif current_hash != entry["original_hash"]:
                raise terminal_label.ConfigConflict(
                    "profile changed during batch operation: %s" % profile_path
                )
            entry["local_cleanup"] = "profile_restored"
            _write_state(path, state, state_hash)
            state_hash = _file_hash(path)
        try:
            backup.unlink()
        except FileNotFoundError:
            pass

    try:
        path.unlink()
    except OSError as exc:
        raise terminal_label.TerminalLabelError("could not remove batch state %s: %s" % (path, exc)) from exc

    return {
        "configs": list(reversed(uninstalled_configs)),
        "profiles": list(reversed(restored_profiles)),
    }


def install_all(
    home: Path,
    profiles_dir: Optional[Path] = None,
    extra_config_dirs: Sequence[Path] = (),
    plugin_runner: Optional[PluginRunner] = None,
    skip_plugin_install: bool = False,
) -> List[str]:
    state = batch_install(
        home=home,
        profiles_dir=profiles_dir,
        extra_config_dirs=extra_config_dirs,
        plugin_runner=plugin_runner,
        skip_plugin_install=skip_plugin_install,
    )
    messages = ["OK batch state: %s" % _state_path(_home_path(home), None)]
    for entry in state["configs"]:
        names = ", ".join(entry.get("profiles") or ["explicit"])
        messages.append("OK %s -> %s" % (names, entry["config_dir"]))
    for entry in state["profiles"]:
        messages.append("OK claudish profile: %s" % entry["path"])
    return messages


def doctor_all(
    home: Path,
    profiles_dir: Optional[Path] = None,
    extra_config_dirs: Sequence[Path] = (),
) -> Tuple[bool, List[str]]:
    healthy, reports = batch_doctor(
        home=home,
        profiles_dir=profiles_dir,
        extra_config_dirs=extra_config_dirs,
    )
    messages: List[str] = []
    for target in sorted(reports):
        messages.append("[%s]" % target)
        messages.extend(reports[target])
    return healthy, messages


def uninstall_all(
    home: Path,
    profiles_dir: Optional[Path] = None,
    extra_config_dirs: Sequence[Path] = (),
    plugin_runner: Optional[PluginRunner] = None,
    remove_plugin: bool = False,
) -> List[str]:
    # The saved state is authoritative for uninstall. Accept discovery arguments
    # for a symmetric CLI without trusting a changed profile inventory.
    del profiles_dir, extra_config_dirs
    result = batch_uninstall(
        home=home,
        plugin_runner=plugin_runner,
        remove_plugin=remove_plugin,
    )
    messages = ["OK restored config: %s" % entry["path"] for entry in result["configs"]]
    messages.extend("OK restored profile: %s" % path for path in result["profiles"])
    return messages
