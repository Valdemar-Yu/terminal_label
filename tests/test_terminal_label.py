from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "plugins" / "terminal-label" / "lib" / "terminal_label.py"
EXECUTABLE = ROOT / "plugins" / "terminal-label" / "bin" / "terminal-label"
HOOK = ROOT / "plugins" / "terminal-label" / "hooks" / "session-start.py"
SESSION_END_HOOK = ROOT / "plugins" / "terminal-label" / "hooks" / "session-end.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

spec = importlib.util.spec_from_file_location("terminal_label", MODULE_PATH)
assert spec is not None and spec.loader is not None
terminal_label = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terminal_label)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class RenderTests(unittest.TestCase):
    def test_named_session(self) -> None:
        payload = terminal_label.parse_status_payload(fixture("statusline_named.json"))
        self.assertEqual(terminal_label.render_label(payload), "Opus 4.1 · terminal label")

    def test_workspace_basename_fallback(self) -> None:
        payload = terminal_label.parse_status_payload(fixture("statusline_workspace.json"))
        self.assertEqual(
            terminal_label.render_label(payload), "Sonnet 4 · workspace-fallback"
        )

    def test_short_session_id_fallback(self) -> None:
        payload = terminal_label.parse_status_payload(fixture("statusline_session_id.json"))
        self.assertEqual(terminal_label.render_label(payload), "Haiku · deadbeef")

    def test_defaults_when_fields_are_missing(self) -> None:
        self.assertEqual(terminal_label.render_label({}), "Claude · session")

    def test_windows_style_workspace_basename(self) -> None:
        status = {
            "model": {"display_name": "Opus"},
            "session_id": "1234567890",
            "workspace": {"current_dir": r"C:\\work\\project\\"},
        }
        self.assertEqual(terminal_label.render_label(status), "Opus · project")

    def test_control_characters_and_terminal_sequences_are_removed(self) -> None:
        status = {
            "model": {"display_name": "Opus\x1b]0;owned\x07\n4"},
            "session_name": "safe\x1b[31m red\x1b[0m\r\x00‮",
        }
        label = terminal_label.render_label(status)
        self.assertEqual(label, "Opus 4 · safe red")
        sequence = terminal_label.osc_sequence(label)
        self.assertEqual(sequence, b"\x1b]0;Opus 4 \xc2\xb7 safe red\x07")
        self.assertNotIn(b"owned", sequence)

    def test_unterminated_osc_discards_remainder(self) -> None:
        self.assertEqual(terminal_label.sanitize_text("safe\x1b]2;bad"), "safe")

    def test_top_level_cwd_fallback(self) -> None:
        status = {"model": "Opus", "cwd": "/tmp/top-level-cwd"}
        self.assertEqual(terminal_label.render_label(status), "Opus · top-level-cwd")

    def test_title_is_bounded(self) -> None:
        status = {"model": "Opus", "session_name": "x" * 200}
        label = terminal_label.render_label(status)
        self.assertEqual(len(label), terminal_label.MAX_TITLE_LENGTH)
        self.assertTrue(label.endswith("…"))

    def test_invalid_status_payload_is_rejected(self) -> None:
        with self.assertRaises(terminal_label.TerminalLabelError):
            terminal_label.parse_status_payload(b"[]")
        with self.assertRaises(terminal_label.TerminalLabelError):
            terminal_label.parse_status_payload(b"not json")


class TerminalUpdateTests(unittest.TestCase):
    def test_osc_is_written_to_requested_tty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tty"
            target.touch()
            self.assertTrue(terminal_label.write_osc("Opus · demo", str(target)))
            self.assertEqual(target.read_bytes(), terminal_label.osc_sequence("Opus · demo"))

    def test_missing_tty_is_best_effort(self) -> None:
        self.assertFalse(terminal_label.write_osc("demo", "/definitely/missing/tty"))

    @mock.patch.object(terminal_label.shutil, "which", return_value="/usr/bin/tmux")
    @mock.patch.object(terminal_label.subprocess, "run")
    def test_tmux_rename_does_not_use_a_shell(self, run: mock.Mock, which: mock.Mock) -> None:
        del which
        run.return_value = subprocess.CompletedProcess([], 0)
        self.assertTrue(
            terminal_label.rename_tmux_window(
                "safe\x1b]0;bad\x07 title", env={"TMUX": "/tmp/tmux"}
            )
        )
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["tmux", "rename-window", "safe title"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    @mock.patch.object(terminal_label.subprocess, "run")
    def test_tmux_is_skipped_outside_tmux(self, run: mock.Mock) -> None:
        self.assertFalse(terminal_label.rename_tmux_window("title", env={}))
        run.assert_not_called()


class ProxyTests(unittest.TestCase):
    def test_original_command_gets_exact_stdin_and_stdout(self) -> None:
        payload = b'{"model":"Opus","workspace":{"current_dir":"/tmp/demo"}}\n'
        command = "%s -c %s" % (
            terminal_label.shlex.quote(sys.executable),
            terminal_label.shlex.quote(
                "import sys; data=sys.stdin.buffer.read(); "
                "sys.stdout.buffer.write(b'prefix\\x00'+data)"
            ),
        )
        stdout = io.BytesIO()
        fake_stdout = mock.Mock(buffer=stdout)
        with mock.patch.object(terminal_label.sys, "stdout", fake_stdout):
            result = terminal_label.run_statusline_proxy(
                command, payload=payload, apply_title=False
            )
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), b"prefix\x00" + payload)

    def test_original_exit_status_is_preserved(self) -> None:
        command = "%s -c %s" % (
            terminal_label.shlex.quote(sys.executable),
            terminal_label.shlex.quote("raise SystemExit(7)"),
        )
        stdout = io.BytesIO()
        with mock.patch.object(terminal_label.sys, "stdout", mock.Mock(buffer=stdout)):
            result = terminal_label.run_statusline_proxy(
                command, payload=b"{}", apply_title=False
            )
        self.assertEqual(result, 7)
        self.assertEqual(stdout.getvalue(), b"")

    def test_no_original_keeps_status_line_hidden(self) -> None:
        stdout = io.BytesIO()
        with mock.patch.object(terminal_label.sys, "stdout", mock.Mock(buffer=stdout)):
            result = terminal_label.run_statusline_proxy(
                None,
                payload=b'{"model":"Opus","session_name":"demo"}',
                apply_title=False,
            )
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), b"")

    def test_invalid_json_still_reaches_original(self) -> None:
        payload = b"not-json\x00"
        command = "%s -c %s" % (
            terminal_label.shlex.quote(sys.executable),
            terminal_label.shlex.quote("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
        )
        stdout = io.BytesIO()
        with mock.patch.object(terminal_label.sys, "stdout", mock.Mock(buffer=stdout)):
            result = terminal_label.run_statusline_proxy(
                command, payload=payload, apply_title=False
            )
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), payload)


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.settings_path = self.directory / "settings.json"
        self.executable = self.directory / "terminal-label"
        self.executable.write_text("#!/bin/sh\n", encoding="utf-8")
        self.executable.chmod(0o755)

    def write_settings(self, value: object) -> bytes:
        raw = (json.dumps(value, indent=4) + "\n").encode()
        self.settings_path.write_bytes(raw)
        self.settings_path.chmod(0o640)
        return raw

    def read_settings(self) -> object:
        return json.loads(self.settings_path.read_text(encoding="utf-8"))

    def test_install_proxies_existing_command_and_makes_backup(self) -> None:
        original = {
            "theme": "dark",
            "statusLine": {
                "type": "command",
                "command": "python3 '/tmp/my statusline.py' --flag '$HOME'",
                "refreshInterval": 17,
            },
        }
        raw = self.write_settings(original)
        changed, backup = terminal_label.install(self.settings_path, self.executable)
        self.assertTrue(changed)
        assert backup is not None
        self.assertEqual(backup.read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

        installed = self.read_settings()
        self.assertEqual(installed["theme"], "dark")
        self.assertEqual(
            installed["env"][terminal_label.TITLE_ENV_KEY],
            terminal_label.TITLE_ENV_VALUE,
        )
        self.assertEqual(
            installed["statusLine"]["refreshInterval"], terminal_label.REFRESH_INTERVAL
        )
        parsed = terminal_label.parse_proxy_command(installed["statusLine"]["command"])
        self.assertEqual(parsed, (self.executable.resolve(), original["statusLine"]["command"]))
        self.assertEqual(stat.S_IMODE(self.settings_path.stat().st_mode), 0o640)

    def test_install_and_uninstall_are_idempotent(self) -> None:
        original = {
            "statusLine": {
                "type": "command",
                "command": "printf existing",
                "refreshInterval": 30,
            },
            "other": True,
        }
        self.write_settings(original)
        changed, _ = terminal_label.install(self.settings_path, self.executable)
        self.assertTrue(changed)
        installed_raw = self.settings_path.read_bytes()
        backups_after_install = sorted(self.directory.glob("*.terminal-label.bak*"))

        changed, backup = terminal_label.install(self.settings_path, self.executable)
        self.assertFalse(changed)
        self.assertIsNone(backup)
        self.assertEqual(self.settings_path.read_bytes(), installed_raw)
        self.assertEqual(
            sorted(self.directory.glob("*.terminal-label.bak*")), backups_after_install
        )

        changed, backup = terminal_label.uninstall(self.settings_path, self.executable)
        self.assertTrue(changed)
        self.assertIsNotNone(backup)
        self.assertEqual(self.read_settings(), original)

        changed, backup = terminal_label.uninstall(self.settings_path, self.executable)
        self.assertFalse(changed)
        self.assertIsNone(backup)

    def test_install_into_missing_file_then_uninstall(self) -> None:
        changed, backup = terminal_label.install(self.settings_path, self.executable)
        self.assertTrue(changed)
        self.assertIsNone(backup)
        installed = self.read_settings()
        self.assertEqual(installed["statusLine"]["type"], "command")
        self.assertEqual(
            installed["env"][terminal_label.TITLE_ENV_KEY],
            terminal_label.TITLE_ENV_VALUE,
        )

        changed, backup = terminal_label.uninstall(self.settings_path, self.executable)
        self.assertTrue(changed)
        self.assertIsNotNone(backup)
        self.assertEqual(self.read_settings(), {})

    def test_uninstall_restores_complete_statusline_and_title_env(self) -> None:
        original = {
            "env": {
                "OTHER": "kept",
                terminal_label.TITLE_ENV_KEY: "previous",
            },
            "statusLine": {
                "type": "command",
                "command": "printf old",
                "refreshInterval": 9,
                "custom": {"nested": True},
            },
        }
        self.write_settings(original)
        terminal_label.install(self.settings_path, self.executable)
        installed = self.read_settings()
        installed["env"]["ADDED_AFTER_INSTALL"] = "preserved"
        self.settings_path.write_text(json.dumps(installed), encoding="utf-8")

        changed, _ = terminal_label.uninstall(self.settings_path, self.executable)
        self.assertTrue(changed)
        restored = self.read_settings()
        self.assertEqual(restored["statusLine"], original["statusLine"])
        self.assertEqual(
            restored["env"],
            {
                "OTHER": "kept",
                "ADDED_AFTER_INSTALL": "preserved",
                terminal_label.TITLE_ENV_KEY: "previous",
            },
        )

    def test_uninstall_refuses_if_title_env_changed(self) -> None:
        original_raw = self.write_settings({"other": True})
        terminal_label.install(self.settings_path, self.executable)
        installed = self.read_settings()
        installed["env"][terminal_label.TITLE_ENV_KEY] = "manually-changed"
        self.settings_path.write_text(json.dumps(installed), encoding="utf-8")
        changed_raw = self.settings_path.read_bytes()

        with self.assertRaises(terminal_label.ConfigConflict):
            terminal_label.uninstall(self.settings_path, self.executable)
        self.assertEqual(self.settings_path.read_bytes(), changed_raw)
        self.assertNotEqual(self.settings_path.read_bytes(), original_raw)

    def test_uninstall_refuses_if_statusline_metadata_changed(self) -> None:
        self.write_settings(
            {
                "statusLine": {
                    "type": "command",
                    "command": "printf old",
                    "refreshInterval": 9,
                }
            }
        )
        terminal_label.install(self.settings_path, self.executable)
        installed = self.read_settings()
        installed["statusLine"]["refreshInterval"] = 100
        self.settings_path.write_text(json.dumps(installed), encoding="utf-8")
        changed_raw = self.settings_path.read_bytes()

        with self.assertRaises(terminal_label.ConfigConflict):
            terminal_label.uninstall(self.settings_path, self.executable)
        self.assertEqual(self.settings_path.read_bytes(), changed_raw)

    def test_conflict_with_other_installation_is_not_overwritten(self) -> None:
        other = self.directory / "other" / "terminal-label"
        other.parent.mkdir()
        other.write_text("#!/bin/sh\n", encoding="utf-8")
        command = terminal_label.build_proxy_command(other, "old-status")
        raw = self.write_settings(
            {"statusLine": {"type": "command", "command": command}}
        )
        with self.assertRaises(terminal_label.ConfigConflict):
            terminal_label.install(self.settings_path, self.executable)
        self.assertEqual(self.settings_path.read_bytes(), raw)
        self.assertEqual(list(self.directory.glob("*.terminal-label.bak*")), [])

        with self.assertRaises(terminal_label.ConfigConflict):
            terminal_label.uninstall(self.settings_path, self.executable)
        self.assertEqual(self.settings_path.read_bytes(), raw)

    def test_malformed_managed_command_is_a_conflict(self) -> None:
        raw = self.write_settings(
            {
                "statusLine": {
                    "type": "command",
                    "command": "%s statusline %s --original-b64 !!!"
                    % (self.executable, terminal_label.MANAGED_FLAG),
                }
            }
        )
        with self.assertRaises(terminal_label.ConfigConflict):
            terminal_label.install(self.settings_path, self.executable)
        self.assertEqual(self.settings_path.read_bytes(), raw)

    def test_non_command_status_line_requires_force(self) -> None:
        raw = self.write_settings({"statusLine": {"type": "text", "command": "old"}})
        with self.assertRaises(terminal_label.ConfigConflict):
            terminal_label.install(self.settings_path, self.executable)
        self.assertEqual(self.settings_path.read_bytes(), raw)

        changed, _ = terminal_label.install(
            self.settings_path, self.executable, force=True
        )
        self.assertTrue(changed)
        self.assertEqual(self.read_settings()["statusLine"]["type"], "command")

    def test_invalid_settings_is_not_modified(self) -> None:
        raw = b"{not valid json\n"
        self.settings_path.write_bytes(raw)
        with self.assertRaises(terminal_label.TerminalLabelError):
            terminal_label.install(self.settings_path, self.executable)
        self.assertEqual(self.settings_path.read_bytes(), raw)

    def test_symlinked_settings_is_not_modified(self) -> None:
        target = self.directory / "actual.json"
        target.write_text("{}\n", encoding="utf-8")
        self.settings_path.symlink_to(target)
        with self.assertRaises(terminal_label.ConfigConflict):
            terminal_label.install(self.settings_path, self.executable)
        self.assertEqual(target.read_text(encoding="utf-8"), "{}\n")

    def test_doctor_reports_healthy_install(self) -> None:
        self.write_settings({})
        terminal_label.install(self.settings_path, self.executable)
        healthy, messages = terminal_label.doctor(
            self.settings_path, self.executable
        )
        self.assertTrue(healthy)
        self.assertIn("OK statusLine proxy is installed", messages)

    def test_default_settings_honors_claude_config_dir(self) -> None:
        with mock.patch.dict(
            terminal_label.os.environ,
            {"CLAUDE_CONFIG_DIR": str(self.directory)},
            clear=False,
        ):
            self.assertEqual(
                terminal_label.default_settings_path(), self.directory / "settings.json"
            )


class CliTests(unittest.TestCase):
    def test_version_cli(self) -> None:
        completed = subprocess.run(
            [str(EXECUTABLE), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "terminal-label 0.2.0\n")

    def test_render_cli(self) -> None:
        completed = subprocess.run(
            [
                str(EXECUTABLE),
                "render",
                "--json",
                '{"model":{"display_name":"Opus"},"session_name":"demo"}',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Opus · demo\n")

    def test_install_doctor_uninstall_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "statusLine": {
                            "type": "command",
                            "command": "printf original",
                        }
                    }
                ),
                encoding="utf-8",
            )
            install = subprocess.run(
                [str(EXECUTABLE), "install", "--settings", str(settings)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            runtime = terminal_label.runtime_executable(settings)
            self.assertTrue(runtime.is_file())
            installed_settings = json.loads(settings.read_text(encoding="utf-8"))
            owner, original_command = terminal_label.parse_proxy_command(
                installed_settings["statusLine"]["command"]
            )
            self.assertEqual(owner.resolve(), runtime.resolve())
            self.assertEqual(original_command, "printf original")

            doctor = subprocess.run(
                [str(EXECUTABLE), "doctor", "--settings", str(settings)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
            self.assertIn("OK statusLine proxy is installed", doctor.stdout)

            uninstall = subprocess.run(
                [str(EXECUTABLE), "uninstall", "--settings", str(settings)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            restored = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(restored["statusLine"]["command"], "printf original")
            self.assertFalse(runtime.exists())

    def test_claude_all_batch_cli_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            profiles = home / ".claude-all" / "profiles"
            profiles.mkdir(parents=True)
            (profiles / "direct.env").write_text(
                "CLAUDE_ALL_LAUNCH=direct\n", encoding="utf-8"
            )
            env = dict(os.environ, HOME=str(home))

            install = subprocess.run(
                [
                    str(EXECUTABLE),
                    "install-claude-all",
                    "--home",
                    str(home),
                    "--skip-plugin-install",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            self.assertIn("direct", install.stdout)

            runtime = home / ".claude-all" / "terminal-label" / "bin" / "terminal-label"
            self.assertTrue(runtime.is_file())
            self.assertTrue(
                (home / ".claude-all" / "terminal-label" / "lib" / "claude_all.py").is_file()
            )
            doctor = subprocess.run(
                [str(runtime), "doctor-claude-all", "--home", str(home)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
            self.assertIn("OK statusLine proxy is installed", doctor.stdout)

            uninstall = subprocess.run(
                [str(runtime), "uninstall-claude-all", "--home", str(home)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertFalse(
                (home / ".claude-all" / "terminal-label-claude-all.json").exists()
            )

    def test_claude_all_cli_reports_parse_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            profiles = home / ".claude-all" / "profiles"
            profiles.mkdir(parents=True)
            (profiles / "unsafe.env").write_text(
                "CLAUDE_ALL_LAUNCH=direct\nCLAUDE_CONFIG_DIR='$(touch bad)'\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    str(EXECUTABLE),
                    "install-claude-all",
                    "--home",
                    str(home),
                    "--skip-plugin-install",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=dict(os.environ, HOME=str(home)),
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("unsafe shell syntax", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_install_conflict_has_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text(
                json.dumps({"statusLine": {"type": "other"}}), encoding="utf-8"
            )
            completed = subprocess.run(
                [str(EXECUTABLE), "install", "--settings", str(settings)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("not 'command'", completed.stderr)


class HookTests(unittest.TestCase):
    def test_session_start_adapter_uses_cwd_and_session_id(self) -> None:
        hook_spec = importlib.util.spec_from_file_location("session_start", HOOK)
        assert hook_spec is not None and hook_spec.loader is not None
        session_start = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(session_start)
        status = session_start.status_from_hook(
            {"model": "Opus", "cwd": "/tmp/hook-workspace", "session_id": "abcdef123"}
        )
        self.assertEqual(
            terminal_label.render_label(status), "Opus · hook-workspace"
        )

    def test_session_start_prefers_existing_session_title(self) -> None:
        hook_spec = importlib.util.spec_from_file_location("session_start_title", HOOK)
        assert hook_spec is not None and hook_spec.loader is not None
        session_start = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(session_start)
        status = session_start.status_from_hook(
            {
                "model": "Opus",
                "cwd": "/tmp/fallback",
                "session_title": "named task",
            }
        )
        self.assertEqual(terminal_label.render_label(status), "Opus · named task")

    def test_session_end_uses_workspace_basename(self) -> None:
        hook_spec = importlib.util.spec_from_file_location("session_end", SESSION_END_HOOK)
        assert hook_spec is not None and hook_spec.loader is not None
        session_end = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(session_end)
        self.assertEqual(session_end.workspace_title({"cwd": "/tmp/project"}), "project")

    def test_hooks_json_points_to_lifecycle_hooks(self) -> None:
        hooks = json.loads(
            (ROOT / "plugins" / "terminal-label" / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        start_command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        end_command = hooks["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/session-start.py", start_command)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/session-end.py", end_command)


if __name__ == "__main__":
    unittest.main()
