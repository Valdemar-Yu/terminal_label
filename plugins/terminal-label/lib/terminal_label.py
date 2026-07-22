#!/usr/bin/env python3
"""Terminal titles derived from Claude Code status-line input."""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple


VERSION = "0.2.1"
DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
MANAGED_FLAG = "--terminal-label-managed"
TITLE_ENV_KEY = "CLAUDE_CODE_DISABLE_TERMINAL_TITLE"
TITLE_ENV_VALUE = "1"
MAX_TITLE_LENGTH = 120
REFRESH_INTERVAL = 5
RUNTIME_DIR_NAME = "terminal-label"
_MISSING = object()
_UNSPECIFIED = object()


class TerminalLabelError(Exception):
    """Base exception for user-facing terminal-label errors."""


class ConfigConflict(TerminalLabelError):
    """Raised when changing settings would overwrite another owner."""


def _skip_osc(text: str, start: int) -> int:
    """Return the first index after an OSC sequence, or len(text)."""
    index = start
    while index < len(text):
        char = text[index]
        if char in ("\x07", "\x9c"):
            return index + 1
        if char == "\x1b" and index + 1 < len(text) and text[index + 1] == "\\":
            return index + 2
        index += 1
    return len(text)


def _strip_terminal_sequences(text: str) -> str:
    """Strip OSC, CSI, and other escape introducers from untrusted text."""
    result: List[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\x1b":
            if index + 1 >= len(text):
                break
            introducer = text[index + 1]
            if introducer == "]":
                index = _skip_osc(text, index + 2)
                continue
            if introducer == "[":
                index += 2
                while index < len(text):
                    codepoint = ord(text[index])
                    index += 1
                    if 0x40 <= codepoint <= 0x7E:
                        break
                continue
            # A two-byte escape sequence. Removing both bytes is safer than
            # leaving a terminal command's final byte in the title.
            index += 2
            continue
        if char == "\x9d":
            index = _skip_osc(text, index + 1)
            continue
        if char == "\x9b":
            index += 1
            while index < len(text):
                codepoint = ord(text[index])
                index += 1
                if 0x40 <= codepoint <= 0x7E:
                    break
            continue
        result.append(char)
        index += 1
    return "".join(result)


def sanitize_text(value: Any) -> str:
    """Make an untrusted value safe for inclusion in an OSC title."""
    if value is None:
        return ""
    text = _strip_terminal_sequences(str(value))
    cleaned: List[str] = []
    for char in text:
        if char in ("\n", "\r", "\t") or char.isspace():
            cleaned.append(" ")
            continue
        # Cc includes C0/C1 terminal controls. Cf includes invisible format
        # controls such as bidi overrides, which are misleading in a title.
        if unicodedata.category(char) in ("Cc", "Cf"):
            continue
        cleaned.append(char)
    return " ".join("".join(cleaned).split())


def _first_clean(values: Sequence[Any]) -> str:
    for value in values:
        cleaned = sanitize_text(value)
        if cleaned:
            return cleaned
    return ""


def model_name(status: Mapping[str, Any]) -> str:
    """Extract the model's display name from Claude status-line JSON."""
    model = status.get("model")
    candidates: List[Any] = []
    if isinstance(model, Mapping):
        candidates.extend(
            model.get(key) for key in ("display_name", "displayName", "name", "id")
        )
    else:
        candidates.append(model)
    candidates.extend((status.get("model_name"), status.get("model_id")))
    return _first_clean(candidates) or "Claude"


def _explicit_session_name(status: Mapping[str, Any]) -> str:
    session = status.get("session")
    candidates: List[Any] = [status.get("session_name"), status.get("session_title")]
    if isinstance(session, Mapping):
        candidates.extend(
            session.get(key) for key in ("display_name", "displayName", "name", "title")
        )
    else:
        candidates.append(session)
    return _first_clean(candidates)


def _workspace_basename(status: Mapping[str, Any]) -> str:
    workspace = status.get("workspace")
    workspace_dir = workspace.get("current_dir") if isinstance(workspace, Mapping) else None
    current_dir = _first_clean((workspace_dir, status.get("cwd")))
    if not current_dir:
        return ""
    # Treat either slash as a separator so input produced on another platform
    # still gets a useful basename.
    normalized = current_dir.rstrip("/\\").replace("\\", "/")
    if not normalized:
        return ""
    return sanitize_text(normalized.rsplit("/", 1)[-1])


def _short_session_id(status: Mapping[str, Any]) -> str:
    session = status.get("session")
    nested_id = session.get("id") if isinstance(session, Mapping) else None
    session_id = _first_clean((status.get("session_id"), nested_id))
    return session_id[:8]


def session_name(status: Mapping[str, Any]) -> str:
    """Extract a session name with workspace and short-ID fallbacks."""
    return (
        _explicit_session_name(status)
        or _workspace_basename(status)
        or _short_session_id(status)
        or "session"
    )


def render_label(status: Mapping[str, Any], max_length: int = MAX_TITLE_LENGTH) -> str:
    """Render a bounded ``model · session`` label from status-line JSON."""
    label = "%s · %s" % (model_name(status), session_name(status))
    if max_length > 0 and len(label) > max_length:
        if max_length == 1:
            return "…"
        return label[: max_length - 1].rstrip() + "…"
    return label


def parse_status_payload(payload: bytes) -> Mapping[str, Any]:
    """Parse one Claude status-line JSON payload."""
    try:
        decoded = payload.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TerminalLabelError("invalid status-line JSON: %s" % exc) from exc
    if not isinstance(value, Mapping):
        raise TerminalLabelError("status-line JSON must be an object")
    return value


def osc_sequence(title: str, code: int = 0) -> bytes:
    """Build one safe terminal-title OSC sequence."""
    if code not in (0, 1, 2):
        raise ValueError("terminal title OSC code must be 0, 1, or 2")
    safe_title = sanitize_text(title)
    return ("\x1b]%d;%s\x07" % (code, safe_title)).encode("utf-8")


def terminal_title_sequence(title: str) -> bytes:
    """Set icon/tab, window, and combined titles across terminal emulators."""
    return b"".join(osc_sequence(title, code) for code in (1, 2, 0))


def _safe_tty_path(path: str) -> Optional[str]:
    allowed_name = path.startswith("/dev/tty") or (
        path.startswith("/dev/pts/") and path.removeprefix("/dev/pts/").isdigit()
    )
    if not allowed_name or any(ord(char) < 32 for char in path):
        return None
    try:
        mode = os.stat(path).st_mode
        if not stat.S_ISCHR(mode):
            return None
        descriptor = os.open(path, os.O_WRONLY | getattr(os, "O_NOCTTY", 0))
        os.close(descriptor)
    except OSError:
        return None
    return path


def _tty_from_pid(pid: str) -> Optional[str]:
    if not pid.isdigit():
        return None
    try:
        completed = subprocess.run(
            ["ps", "-o", "tty=", "-p", pid],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    tty_name = completed.stdout.strip()
    if completed.returncode != 0 or not tty_name or tty_name == "??":
        return None
    return _safe_tty_path("/dev/" + tty_name.removeprefix("/dev/"))


def resolve_tty(
    preferred: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Find the Claude session's real TTY even from detached hook processes."""
    environment = os.environ if env is None else env
    candidates = [preferred, environment.get("TERMINAL_LABEL_TTY")]
    for candidate in candidates:
        if candidate:
            resolved = _safe_tty_path(candidate)
            if resolved:
                return resolved
    claude_pid = environment.get("CLAUDE_PID", "")
    resolved = _tty_from_pid(claude_pid) if claude_pid else None
    if resolved:
        return resolved
    return _safe_tty_path("/dev/tty")


def write_osc(title: str, tty_path: str = "/dev/tty") -> bool:
    """Write compatible title sequences to one known terminal device."""
    payload = terminal_title_sequence(title)
    descriptor: Optional[int] = None
    try:
        flags = os.O_WRONLY | getattr(os, "O_NOCTTY", 0)
        descriptor = os.open(tty_path, flags)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                return False
            view = view[written:]
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def rename_tmux_window(
    title: str,
    env: Optional[Mapping[str, str]] = None,
    timeout: float = 1.0,
) -> bool:
    """Best-effort tmux window rename without invoking a shell."""
    environment = os.environ if env is None else env
    if not environment.get("TMUX") or shutil.which("tmux") is None:
        return False
    try:
        completed = subprocess.run(
            ["tmux", "rename-window", sanitize_text(title)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            env=dict(environment),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def set_terminal_title(
    title: str,
    tty_path: Optional[str] = None,
    update_tmux: bool = True,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Apply a title to the Claude session's tab/window and optional tmux window."""
    resolved_tty = resolve_tty(preferred=tty_path, env=env)
    tty_updated = write_osc(title, tty_path=resolved_tty) if resolved_tty else False
    tmux_updated = rename_tmux_window(title, env=env) if update_tmux else False
    return tty_updated or tmux_updated


def run_statusline_proxy(
    original_command: Optional[str],
    payload: Optional[bytes] = None,
    apply_title: bool = True,
    tty_path: str = "/dev/tty",
    update_tmux: bool = True,
) -> int:
    """Set the title and proxy an existing status-line command byte-for-byte."""
    if payload is None:
        payload = sys.stdin.buffer.read()

    try:
        status = parse_status_payload(payload)
    except TerminalLabelError:
        status = None
    label = render_label(status) if status is not None else None
    if apply_title and label is not None:
        set_terminal_title(label, tty_path=tty_path, update_tmux=update_tmux)

    if original_command is None:
        # An empty stdout keeps Claude Code from allocating a visible status-line
        # row when Terminal Label is the user's only status-line command.
        return 0

    try:
        completed = subprocess.run(
            original_command,
            shell=True,
            input=payload,
            stdout=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        print("terminal-label: could not run original status line: %s" % exc, file=sys.stderr)
        return 127

    try:
        sys.stdout.buffer.write(completed.stdout)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        return completed.returncode
    return completed.returncode


def _encode_bytes(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_bytes(encoded: str, description: str) -> bytes:
    try:
        return base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ConfigConflict("managed statusLine contains an invalid %s" % description) from exc


def _encode_command(command: str) -> str:
    return _encode_bytes(command.encode("utf-8"))


def _decode_command(encoded: str) -> str:
    try:
        return _decode_bytes(encoded, "command").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigConflict("managed statusLine contains an invalid command") from exc


def _encode_statusline(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _encode_bytes(raw)


def _decode_json_state(encoded: str, description: str) -> Any:
    try:
        return json.loads(_decode_bytes(encoded, description).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigConflict(
            "managed statusLine contains an invalid %s" % description
        ) from exc


def _decode_statusline(encoded: str) -> Any:
    return _decode_json_state(encoded, "saved configuration")


def _encode_json_state(value: Any) -> str:
    return _encode_bytes(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def build_proxy_command(
    executable: Path,
    original_command: Optional[str],
    original_statusline: Any = _UNSPECIFIED,
    title_state: Any = _UNSPECIFIED,
) -> str:
    """Build the shell command stored in Claude's statusLine setting."""
    parts = [shlex.quote(str(executable.resolve())), "statusline", MANAGED_FLAG]
    if original_command is None:
        parts.append("--no-original")
    else:
        parts.extend(("--original-b64", shlex.quote(_encode_command(original_command))))
    if original_statusline is _MISSING:
        parts.append("--no-statusline")
    elif original_statusline is not _UNSPECIFIED:
        parts.extend(
            (
                "--statusline-b64",
                shlex.quote(_encode_statusline(original_statusline)),
            )
        )
    if title_state is not _UNSPECIFIED:
        parts.extend(
            ("--title-state-b64", shlex.quote(_encode_json_state(title_state)))
        )
    return " ".join(parts)


def _proxy_parts(command: Any) -> Optional[List[str]]:
    if not isinstance(command, str):
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if len(parts) < 3 or parts[1] != "statusline":
        return None

    value_options = {"--original-b64", "--statusline-b64", "--title-state-b64"}
    flag_options = {MANAGED_FLAG, "--no-original", "--no-statusline"}
    seen = set()
    index = 2
    while index < len(parts):
        option = parts[index]
        if option in seen or option not in value_options | flag_options:
            return None
        seen.add(option)
        if option in value_options:
            index += 1
            if index >= len(parts):
                return None
        index += 1
    if MANAGED_FLAG not in seen:
        return None
    return parts


def parse_proxy_command(command: Any) -> Optional[Tuple[Path, Optional[str]]]:
    """Parse a command generated by :func:`build_proxy_command`."""
    parts = _proxy_parts(command)
    if parts is None:
        return None
    executable = Path(parts[0]).expanduser()
    no_original = "--no-original" in parts
    has_original = "--original-b64" in parts
    if no_original == has_original:
        raise ConfigConflict("managed statusLine has ambiguous original command data")
    if no_original:
        return executable, None
    try:
        index = parts.index("--original-b64")
        encoded = parts[index + 1]
    except IndexError as exc:
        raise ConfigConflict("managed statusLine is missing its original command") from exc
    return executable, _decode_command(encoded)


def _saved_statusline(command: Any) -> Any:
    """Read the original statusLine snapshot from a managed command."""
    parts = _proxy_parts(command)
    if parts is None:
        return _UNSPECIFIED
    no_statusline = "--no-statusline" in parts
    has_statusline = "--statusline-b64" in parts
    if no_statusline and has_statusline:
        raise ConfigConflict("managed statusLine has ambiguous saved configuration")
    if no_statusline:
        return _MISSING
    if not has_statusline:
        # Commands produced by early versions only saved the original command.
        return _UNSPECIFIED
    try:
        index = parts.index("--statusline-b64")
        encoded = parts[index + 1]
    except IndexError as exc:
        raise ConfigConflict(
            "managed statusLine is missing its saved configuration"
        ) from exc
    return _decode_statusline(encoded)


def _expected_managed_statusline(command: str, saved_statusline: Any) -> Any:
    """Reconstruct the statusLine installed around a saved prior value."""
    if saved_statusline is _UNSPECIFIED:
        return _UNSPECIFIED
    if isinstance(saved_statusline, Mapping):
        expected: Dict[str, Any] = copy.deepcopy(dict(saved_statusline))
        expected["type"] = "command"
    else:
        expected = {"type": "command"}
    expected["command"] = command
    previous_refresh = expected.get("refreshInterval")
    if not isinstance(previous_refresh, (int, float)) or previous_refresh > REFRESH_INTERVAL:
        expected["refreshInterval"] = REFRESH_INTERVAL
    return expected


def _check_managed_statusline_unchanged(
    existing: Mapping[str, Any], command: str, saved_statusline: Any
) -> None:
    expected = _expected_managed_statusline(command, saved_statusline)
    if expected is not _UNSPECIFIED and dict(existing) != expected:
        raise ConfigConflict("statusLine changed after terminal-label was installed")


def _saved_title_state(command: Any) -> Any:
    """Read and validate the previous built-in-title setting."""
    parts = _proxy_parts(command)
    if parts is None or "--title-state-b64" not in parts:
        return _UNSPECIFIED
    try:
        index = parts.index("--title-state-b64")
        encoded = parts[index + 1]
    except IndexError as exc:
        raise ConfigConflict("managed statusLine is missing its title state") from exc
    state = _decode_json_state(encoded, "saved title state")
    if not isinstance(state, Mapping):
        raise ConfigConflict("managed statusLine contains an invalid saved title state")
    if not isinstance(state.get("env_present"), bool) or not isinstance(
        state.get("title_present"), bool
    ):
        raise ConfigConflict("managed statusLine contains an invalid saved title state")
    if state["title_present"] and not isinstance(state.get("value"), str):
        raise ConfigConflict("managed statusLine contains an invalid saved title state")
    return dict(state)


def _title_state(settings: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return the saved title state and an env mapping ready for installation."""
    existing_env = settings.get("env", _MISSING)
    if existing_env is not _MISSING and not isinstance(existing_env, Mapping):
        raise ConfigConflict("env exists but is not an object")
    environment = {} if existing_env is _MISSING else copy.deepcopy(dict(existing_env))
    previous = environment.get(TITLE_ENV_KEY, _MISSING)
    if previous is not _MISSING and not isinstance(previous, str):
        raise ConfigConflict("env.%s is not a string" % TITLE_ENV_KEY)
    state: Dict[str, Any] = {
        "env_present": existing_env is not _MISSING,
        "title_present": previous is not _MISSING,
    }
    if previous is not _MISSING:
        state["value"] = previous
    environment[TITLE_ENV_KEY] = TITLE_ENV_VALUE
    return state, environment


def _looks_like_terminal_label_proxy(command: Any) -> bool:
    if not isinstance(command, str):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return "terminal-label" in command and "statusline" in command
    return len(parts) >= 2 and Path(parts[0]).name == "terminal-label" and parts[1] == "statusline"


def default_settings_path() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir).expanduser() / "settings.json"
    return DEFAULT_SETTINGS_PATH


def _read_settings(path: Path) -> Tuple[MutableMapping[str, Any], Optional[bytes]]:
    if path.is_symlink():
        raise ConfigConflict("refusing to replace symlinked settings file: %s" % path)
    if not path.exists():
        return {}, None
    try:
        raw = path.read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TerminalLabelError("could not read settings %s: %s" % (path, exc)) from exc
    if not isinstance(parsed, MutableMapping):
        raise TerminalLabelError("settings JSON must be an object: %s" % path)
    return parsed, raw


def _next_backup_path(path: Path) -> Path:
    candidate = path.with_name(path.name + ".terminal-label.bak")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(path.name + ".terminal-label.bak.%d" % suffix)
        suffix += 1
    return candidate


def _write_backup(path: Path, raw: bytes) -> Path:
    backup = _next_backup_path(path)
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return backup


def _same_file_contents(path: Path, expected: Optional[bytes]) -> bool:
    if expected is None:
        return not path.exists()
    try:
        return path.read_bytes() == expected
    except OSError:
        return False


def _atomic_write_settings(
    path: Path,
    settings: Mapping[str, Any],
    expected_raw: Optional[bytes],
) -> Optional[Path]:
    """Back up and atomically replace settings if they have not changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _same_file_contents(path, expected_raw):
        raise ConfigConflict("settings changed while terminal-label was preparing the update")

    backup: Optional[Path] = None
    if expected_raw is not None:
        backup = _write_backup(path, expected_raw)
        if not _same_file_contents(path, expected_raw):
            try:
                backup.unlink()
            except OSError:
                pass
            raise ConfigConflict("settings changed while terminal-label was creating the backup")

    data = (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, old_mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        if not _same_file_contents(path, expected_raw):
            raise ConfigConflict("settings changed before terminal-label could save the update")
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return backup


def _statusline_mapping(settings: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    statusline = settings.get("statusLine")
    return statusline if isinstance(statusline, Mapping) else None


def install(
    settings_path: Path,
    executable: Path,
    force: bool = False,
) -> Tuple[bool, Optional[Path]]:
    """Install the proxy, preserving any existing status-line command."""
    settings_path = settings_path.expanduser()
    executable = executable.expanduser().resolve()
    settings, raw = _read_settings(settings_path)
    existing = settings.get("statusLine", _MISSING)
    original_statusline = (
        _MISSING if existing is _MISSING else copy.deepcopy(existing)
    )

    if existing is _MISSING:
        original_command = None
        replacement: MutableMapping[str, Any] = {"type": "command"}
    elif not isinstance(existing, Mapping):
        if not force:
            raise ConfigConflict("statusLine exists but is not a command object")
        original_command = None
        replacement = {"type": "command"}
    else:
        current_command = existing.get("command")
        parsed = parse_proxy_command(current_command)
        if parsed is not None:
            owner, _ = parsed
            try:
                same_owner = owner.resolve() == executable
            except OSError:
                same_owner = owner.absolute() == executable
            if same_owner:
                # Validate all saved state and verify settings still have the
                # values installed by terminal-label.
                saved_statusline = _saved_statusline(current_command)
                _check_managed_statusline_unchanged(
                    existing, current_command, saved_statusline
                )
                saved_title = _saved_title_state(current_command)
                environment = settings.get("env")
                if saved_title is not _UNSPECIFIED and (
                    not isinstance(environment, Mapping)
                    or environment.get(TITLE_ENV_KEY) != TITLE_ENV_VALUE
                ):
                    raise ConfigConflict(
                        "env.%s changed after terminal-label was installed"
                        % TITLE_ENV_KEY
                    )
                return False, None
            if not force:
                raise ConfigConflict("statusLine is managed by another terminal-label installation")
            original_command = current_command
        elif _looks_like_terminal_label_proxy(current_command) and not force:
            raise ConfigConflict("statusLine resembles a terminal-label proxy but is not valid")
        else:
            statusline_type = existing.get("type", "command")
            if statusline_type != "command" and not force:
                raise ConfigConflict("statusLine type is %r, not 'command'" % statusline_type)
            if current_command is not None and not isinstance(current_command, str):
                if not force:
                    raise ConfigConflict("statusLine.command is not a string")
                original_command = None
            else:
                original_command = current_command or None
        replacement = copy.deepcopy(dict(existing))
        replacement["type"] = "command"

    title_state, environment = _title_state(settings)
    replacement["command"] = build_proxy_command(
        executable, original_command, original_statusline, title_state
    )
    previous_refresh = replacement.get("refreshInterval")
    if not isinstance(previous_refresh, (int, float)) or previous_refresh > REFRESH_INTERVAL:
        replacement["refreshInterval"] = REFRESH_INTERVAL
    settings["statusLine"] = replacement
    settings["env"] = environment
    try:
        backup = _atomic_write_settings(settings_path, settings, raw)
    except OSError as exc:
        raise TerminalLabelError(
            "could not update settings %s: %s" % (settings_path, exc)
        ) from exc
    return True, backup


def validate_uninstall(settings_path: Path, executable: Path) -> bool:
    """Validate that uninstall owns the current settings without changing files."""
    settings_path = settings_path.expanduser()
    executable = executable.expanduser().resolve()
    settings, _ = _read_settings(settings_path)
    existing = settings.get("statusLine")
    if not isinstance(existing, Mapping):
        return False
    current_command = existing.get("command")
    parsed = parse_proxy_command(current_command)
    if parsed is None:
        if _looks_like_terminal_label_proxy(current_command):
            raise ConfigConflict("statusLine resembles a terminal-label proxy but is not valid")
        return False
    owner, _ = parsed
    try:
        same_owner = owner.resolve() == executable
    except OSError:
        same_owner = owner.absolute() == executable
    if not same_owner:
        raise ConfigConflict("statusLine is managed by another terminal-label installation")
    saved_statusline = _saved_statusline(current_command)
    _check_managed_statusline_unchanged(existing, current_command, saved_statusline)
    saved_title = _saved_title_state(current_command)
    if saved_title is not _UNSPECIFIED:
        environment = settings.get("env")
        if not isinstance(environment, Mapping) or environment.get(
            TITLE_ENV_KEY
        ) != TITLE_ENV_VALUE:
            raise ConfigConflict(
                "env.%s changed after terminal-label was installed" % TITLE_ENV_KEY
            )
    return True


def uninstall(settings_path: Path, executable: Path) -> Tuple[bool, Optional[Path]]:
    """Remove this installation and restore the proxied command."""
    settings_path = settings_path.expanduser()
    executable = executable.expanduser().resolve()
    settings, raw = _read_settings(settings_path)
    existing = settings.get("statusLine")
    if not isinstance(existing, Mapping):
        return False, None

    current_command = existing.get("command")
    parsed = parse_proxy_command(current_command)
    if parsed is None:
        if _looks_like_terminal_label_proxy(current_command):
            raise ConfigConflict("statusLine resembles a terminal-label proxy but is not valid")
        return False, None

    owner, original_command = parsed
    try:
        same_owner = owner.resolve() == executable
    except OSError:
        same_owner = owner.absolute() == executable
    if not same_owner:
        raise ConfigConflict("statusLine is managed by another terminal-label installation")

    saved_statusline = _saved_statusline(current_command)
    _check_managed_statusline_unchanged(existing, current_command, saved_statusline)
    if saved_statusline is _MISSING:
        del settings["statusLine"]
    elif saved_statusline is not _UNSPECIFIED:
        settings["statusLine"] = saved_statusline
    elif original_command is None:
        # Backward-compatible removal for commands created before complete
        # statusLine snapshots were embedded in the managed command.
        del settings["statusLine"]
    else:
        restored = copy.deepcopy(dict(existing))
        restored["command"] = original_command
        settings["statusLine"] = restored

    saved_title = _saved_title_state(current_command)
    if saved_title is not _UNSPECIFIED:
        environment = settings.get("env")
        if not isinstance(environment, Mapping) or environment.get(
            TITLE_ENV_KEY
        ) != TITLE_ENV_VALUE:
            raise ConfigConflict(
                "env.%s changed after terminal-label was installed" % TITLE_ENV_KEY
            )
        restored_environment = copy.deepcopy(dict(environment))
        if saved_title["title_present"]:
            restored_environment[TITLE_ENV_KEY] = saved_title["value"]
        else:
            restored_environment.pop(TITLE_ENV_KEY, None)
        if not saved_title["env_present"] and not restored_environment:
            settings.pop("env", None)
        else:
            settings["env"] = restored_environment
    try:
        backup = _atomic_write_settings(settings_path, settings, raw)
    except OSError as exc:
        raise TerminalLabelError(
            "could not update settings %s: %s" % (settings_path, exc)
        ) from exc
    return True, backup


def doctor(settings_path: Path, executable: Path) -> Tuple[bool, List[str]]:
    """Check runtime and settings without modifying them."""
    messages: List[str] = []
    healthy = True
    if sys.version_info < (3, 9):
        healthy = False
        messages.append("FAIL Python 3.9 or newer is required")
    else:
        messages.append("OK Python %d.%d" % sys.version_info[:2])

    executable = executable.expanduser().resolve()
    if executable.is_file() and os.access(executable, os.X_OK):
        messages.append("OK executable %s" % executable)
    else:
        healthy = False
        messages.append("FAIL executable is missing or not executable: %s" % executable)
    if executable.parent.name == "bin" and executable.parent.parent.name == RUNTIME_DIR_NAME:
        module_path = executable.parent.parent / "lib" / "terminal_label.py"
        if module_path.is_file():
            messages.append("OK runtime module terminal_label.py")
        else:
            healthy = False
            messages.append("FAIL runtime module is missing: %s" % module_path)

    try:
        settings, _ = _read_settings(settings_path.expanduser())
    except TerminalLabelError as exc:
        healthy = False
        messages.append("FAIL %s" % exc)
        return healthy, messages

    statusline = _statusline_mapping(settings)
    if statusline is None:
        healthy = False
        messages.append("FAIL terminal-label is not installed in %s" % settings_path)
    else:
        try:
            parsed = parse_proxy_command(statusline.get("command"))
        except ConfigConflict as exc:
            parsed = None
            healthy = False
            messages.append("FAIL %s" % exc)
        if parsed is None:
            healthy = False
            messages.append("FAIL statusLine is not managed by terminal-label")
        else:
            owner, original = parsed
            try:
                same_owner = owner.resolve() == executable
            except OSError:
                same_owner = owner.absolute() == executable
            if not same_owner:
                healthy = False
                messages.append("FAIL statusLine belongs to %s" % owner)
            else:
                try:
                    _saved_statusline(statusline.get("command"))
                    saved_title = _saved_title_state(statusline.get("command"))
                except ConfigConflict as exc:
                    healthy = False
                    messages.append("FAIL %s" % exc)
                else:
                    messages.append("OK statusLine proxy is installed")
                    if original is not None:
                        messages.append("OK original statusLine command is preserved")
                    if saved_title is _UNSPECIFIED:
                        messages.append("WARN built-in title setting is not managed")
                    else:
                        environment = settings.get("env")
                        if not isinstance(environment, Mapping) or environment.get(
                            TITLE_ENV_KEY
                        ) != TITLE_ENV_VALUE:
                            healthy = False
                            messages.append(
                                "FAIL env.%s is not disabled" % TITLE_ENV_KEY
                            )
                        else:
                            messages.append("OK Claude built-in terminal title is disabled")

    resolved_tty = resolve_tty()
    if resolved_tty:
        messages.append("OK terminal device %s" % resolved_tty)
    else:
        messages.append("WARN no writable Claude terminal device was found")
    if os.environ.get("TMUX"):
        if shutil.which("tmux"):
            messages.append("OK tmux client is available")
        else:
            messages.append("WARN TMUX is set but tmux is not on PATH")
    return healthy, messages


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def runtime_executable(settings_path: Path) -> Path:
    """Return the stable runtime path for one Claude config directory."""
    return settings_path.expanduser().parent / RUNTIME_DIR_NAME / "bin" / "terminal-label"


def _atomic_copy(source: Path, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime_root = destination.parents[1]
    for directory in (runtime_root, destination.parent):
        try:
            directory.chmod(0o700)
        except OSError:
            pass
    payload = source.read_bytes()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % destination.name,
        suffix=".tmp",
        dir=str(destination.parent),
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def install_runtime(settings_path: Path) -> Path:
    """Copy the proxy to a stable path outside Claude's versioned plugin cache."""
    source_root = _source_root()
    destination = runtime_executable(settings_path)
    source_executable = source_root / "bin" / "terminal-label"
    source_claudish = source_root / "bin" / "terminal-label-claudish"
    source_modules = [source_root / "lib" / "terminal_label.py"]
    batch_module = source_root / "lib" / "claude_all.py"
    if batch_module.is_file():
        source_modules.append(batch_module)
    destination_lib = destination.parent.parent / "lib"
    try:
        # The launcher only imports modules, so replace modules first to avoid a
        # new launcher observing an older runtime during an update.
        for source_module in source_modules:
            destination_module = destination_lib / source_module.name
            if source_module.resolve() != destination_module.resolve():
                _atomic_copy(source_module, destination_module, 0o600)
        if source_executable.resolve() != destination.resolve():
            _atomic_copy(source_executable, destination, 0o700)
        destination_claudish = destination.parent / "terminal-label-claudish"
        if source_claudish.is_file() and source_claudish.resolve() != destination_claudish.resolve():
            _atomic_copy(source_claudish, destination_claudish, 0o700)
    except OSError as exc:
        raise TerminalLabelError("could not install runtime: %s" % exc) from exc
    return destination.resolve()


def remove_runtime(settings_path: Path) -> bool:
    """Remove only files and empty directories created by :func:`install_runtime`."""
    executable = runtime_executable(settings_path)
    root = executable.parent.parent
    candidates = [
        executable,
        root / "bin" / "terminal-label-claudish",
        root / "lib" / "terminal_label.py",
        root / "lib" / "claude_all.py",
    ]
    changed = False
    for path in candidates:
        try:
            path.unlink()
            changed = True
        except FileNotFoundError:
            pass
    for cache in (root / "lib" / "__pycache__", root / "bin" / "__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)
            changed = True
    for directory in (root / "lib", root / "bin", root):
        try:
            directory.rmdir()
        except OSError:
            pass
    return changed


def _default_executable() -> Path:
    return _source_root() / "bin" / "terminal-label"


def _read_render_payload(inline_json: Optional[str]) -> bytes:
    if inline_json is not None:
        return inline_json.encode("utf-8")
    return sys.stdin.buffer.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="terminal-label")
    parser.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    render_parser = subparsers.add_parser("render", help="render model · session")
    render_parser.add_argument("--json", dest="inline_json")
    render_parser.add_argument("--apply", action="store_true", help="also update the terminal")
    render_parser.add_argument("--tty", default="/dev/tty", help=argparse.SUPPRESS)
    render_parser.add_argument("--no-tmux", action="store_true")

    proxy_parser = subparsers.add_parser("statusline", help="proxy Claude's status line")
    proxy_parser.add_argument(MANAGED_FLAG, action="store_true", help=argparse.SUPPRESS)
    original_group = proxy_parser.add_mutually_exclusive_group()
    original_group.add_argument("--original-b64", help=argparse.SUPPRESS)
    original_group.add_argument("--no-original", action="store_true", help=argparse.SUPPRESS)
    saved_group = proxy_parser.add_mutually_exclusive_group()
    saved_group.add_argument("--statusline-b64", help=argparse.SUPPRESS)
    saved_group.add_argument("--no-statusline", action="store_true", help=argparse.SUPPRESS)
    proxy_parser.add_argument("--title-state-b64", help=argparse.SUPPRESS)
    proxy_parser.add_argument("--tty", default="/dev/tty", help=argparse.SUPPRESS)
    proxy_parser.add_argument("--no-tmux", action="store_true", help=argparse.SUPPRESS)

    for name in ("install", "uninstall", "doctor"):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--settings", type=Path, default=None)
        command_parser.add_argument("--executable", type=Path, default=None, help=argparse.SUPPRESS)
        if name == "install":
            command_parser.add_argument("--force", action="store_true")

    for name in ("install-claude-all", "doctor-claude-all", "uninstall-claude-all"):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--home", type=Path, default=None, help=argparse.SUPPRESS)
        command_parser.add_argument("--profiles-dir", type=Path, default=None)
        command_parser.add_argument(
            "--config-dir", type=Path, action="append", default=[], dest="config_dirs"
        )
        if name == "install-claude-all":
            command_parser.add_argument("--skip-plugin-install", action="store_true")
        if name == "uninstall-claude-all":
            command_parser.add_argument("--remove-plugin", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "render":
            status = parse_status_payload(_read_render_payload(args.inline_json))
            label = render_label(status)
            if args.apply:
                set_terminal_title(
                    label, tty_path=args.tty, update_tmux=not args.no_tmux
                )
            print(label)
            return 0

        if args.subcommand == "statusline":
            original: Optional[str] = None
            if args.original_b64 is not None:
                try:
                    original = _decode_command(args.original_b64)
                except ConfigConflict as exc:
                    raise TerminalLabelError(str(exc)) from exc
            return run_statusline_proxy(
                original,
                tty_path=args.tty,
                update_tmux=not args.no_tmux,
            )

        if args.subcommand in {
            "install-claude-all",
            "doctor-claude-all",
            "uninstall-claude-all",
        }:
            try:
                from claude_all import doctor_all, install_all, uninstall_all
            except ImportError as exc:
                raise TerminalLabelError(
                    "claude-all support is missing; reinstall or update terminal-label"
                ) from exc
            common = {
                "home": (args.home or Path.home()).expanduser(),
                "profiles_dir": args.profiles_dir,
                "extra_config_dirs": tuple(args.config_dirs),
            }
            if args.subcommand == "install-claude-all":
                messages = install_all(
                    **common,
                    skip_plugin_install=args.skip_plugin_install,
                )
                healthy = True
            elif args.subcommand == "doctor-claude-all":
                healthy, messages = doctor_all(**common)
            else:
                messages = uninstall_all(
                    home=common["home"],
                    remove_plugin=args.remove_plugin,
                )
                healthy = True
            for message in messages:
                print(message)
            return 0 if healthy else 1

        settings_path = args.settings or default_settings_path()
        if args.executable is not None:
            executable = args.executable
        else:
            executable = runtime_executable(settings_path)
        if args.subcommand == "install":
            if args.executable is None:
                executable = install_runtime(settings_path)
            changed, backup = install(settings_path, executable, force=args.force)
            if changed:
                message = "installed terminal-label in %s; runtime: %s" % (
                    settings_path,
                    executable,
                )
                if backup is not None:
                    message += "; backup: %s" % backup
                print(message)
            else:
                print("terminal-label is already installed in %s" % settings_path)
            return 0
        if args.subcommand == "uninstall":
            changed, backup = uninstall(settings_path, executable)
            runtime_removed = remove_runtime(settings_path) if changed and args.executable is None else False
            if changed:
                message = "uninstalled terminal-label from %s" % settings_path
                if backup is not None:
                    message += "; backup: %s" % backup
                message += "; runtime removed: %s" % ("yes" if runtime_removed else "no")
                print(message)
            else:
                print("terminal-label is not installed in %s" % settings_path)
            return 0
        if args.subcommand == "doctor":
            healthy, messages = doctor(settings_path, executable)
            for message in messages:
                print(message)
            return 0 if healthy else 1
    except TerminalLabelError as exc:
        print("terminal-label: %s" % exc, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
