from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "plugins" / "terminal-label" / "lib" / "claude_all.py"

spec = importlib.util.spec_from_file_location("claude_all", MODULE_PATH)
assert spec is not None and spec.loader is not None
claude_all = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claude_all)


class RecordingPluginRunner:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, args, env):
        self.calls.append((tuple(args), dict(env)))
        return 0


class EnvParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()

    def test_parser_decodes_quotes_without_executing_shell(self) -> None:
        marker = self.home / "must-not-exist"
        profile = self.home / "profile.env"
        profile.write_text(
            "# comment\n"
            "export PLAIN=value\n"
            "SINGLE='a b # literal'\n"
            'DOUBLE="quoted value" # comment\n'
            "MULTILINE=$'one\\ntwo'\n"
            "ATTACK=$(touch %s)\n"
            "NOT_AN_ASSIGNMENT; touch %s\n" % (marker, marker),
            encoding="utf-8",
        )

        parsed = claude_all.parse_env_file(profile)

        self.assertEqual(parsed["PLAIN"], "value")
        self.assertEqual(parsed["SINGLE"], "a b # literal")
        self.assertEqual(parsed["DOUBLE"], "quoted value")
        self.assertEqual(parsed["MULTILINE"], "one\ntwo")
        self.assertEqual(parsed["ATTACK"], "$(touch %s)" % marker)
        self.assertFalse(marker.exists())

    def test_unsafe_config_directory_is_rejected_without_execution(self) -> None:
        profiles = self.home.resolve() / ".claude-all" / "profiles"
        profiles.mkdir(parents=True)
        marker = self.home / "must-not-exist"
        (profiles / "evil.env").write_text(
            "CLAUDE_ALL_LAUNCH=direct\n"
            "CLAUDE_CONFIG_DIR='$(touch %s)'\n" % marker,
            encoding="utf-8",
        )

        with self.assertRaises(claude_all.EnvParseError):
            claude_all.discover_profiles(home=self.home)
        self.assertFalse(marker.exists())

    def test_fugu_profile_gets_visible_shared_statusline_fallback(self) -> None:
        profiles = self.home / ".claude-all" / "profiles"
        profiles.mkdir(parents=True)
        profile = profiles / "claude-fugu.env"
        profile.write_text(
            "CLAUDE_ALL_LAUNCH=cmd\nCLAUDE_ALL_CMD=claude-fugu\n",
            encoding="utf-8",
        )
        shared = self.home / ".local" / "share" / "claude-all" / "statusline" / "statusline.py"
        shared.parent.mkdir(parents=True)
        shared.write_text("print('shared')\n", encoding="utf-8")
        record = claude_all._discover_records(self.home, None)[0]
        executable = self.home / ".claude-fugu" / "terminal-label" / "bin" / "terminal-label"

        replacement, values = claude_all._updated_profile(record, executable, self.home)

        parsed = claude_all.terminal_label.parse_proxy_command(
            values["CLAUDISH_STATUSLINE_COMMAND"]
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[1], "python3 %s" % shared)
        self.assertIn(b"CLAUDISH_STATUSLINE_REFRESH='5'", replacement)

    def test_discovery_maps_and_deduplicates_config_directories(self) -> None:
        profiles = self.home.resolve() / ".claude-all" / "profiles"
        profiles.mkdir(parents=True)
        (profiles / "direct.env").write_text(
            "CLAUDE_ALL_LAUNCH=direct\n", encoding="utf-8"
        )
        (profiles / "claudish.env").write_text(
            "CLAUDE_ALL_LAUNCH=claudish\n", encoding="utf-8"
        )
        (profiles / "official.env").write_text(
            'CLAUDE_ALL_LAUNCH=cmd\nCLAUDE_ALL_CMD="claude"\n',
            encoding="utf-8",
        )
        (profiles / "glm.env").write_text(
            'CLAUDE_ALL_LAUNCH=cmd\nCLAUDE_ALL_CMD="claude-glm"\n',
            encoding="utf-8",
        )
        (profiles / "claude-fugu.env").write_text(
            'CLAUDE_ALL_LAUNCH=cmd\nCLAUDE_ALL_CMD="claude-fugu"\n'
            'CLAUDE_ALL_LABEL="Sakana Fugu (claudish)"\n',
            encoding="utf-8",
        )
        custom = self.home / "custom-config"
        (profiles / "custom.env").write_text(
            "CLAUDE_ALL_LAUNCH=direct\nCLAUDE_CONFIG_DIR='$HOME/custom-config'\n",
            encoding="utf-8",
        )

        grouped = claude_all.discover_profile_configs(home=self.home)

        self.assertEqual(
            set(grouped),
            {
                self.home.resolve() / ".claude-all",
                self.home / ".claude",
                self.home / ".claude-glm",
                self.home / ".claude-fugu",
                custom,
            },
        )
        self.assertEqual(
            {profile.name for profile in grouped[self.home.resolve() / ".claude-all"]},
            {"direct", "claudish"},
        )
        self.assertTrue(grouped[self.home / ".claude-fugu"][0].claudish)

    def test_wrapper_specific_config_directories_take_precedence(self) -> None:
        profiles = self.home / ".claude-all" / "profiles"
        profiles.mkdir(parents=True)
        (profiles / "claude-fugu.env").write_text(
            "CLAUDE_ALL_LAUNCH=cmd\nCLAUDE_ALL_CMD=claude-fugu\n"
            "FUGU_CONFIG_DIR=$HOME/custom-fugu\nCLAUDE_CONFIG_DIR=$HOME/wrong\n",
            encoding="utf-8",
        )
        (profiles / "claude-plbbl.env").write_text(
            "CLAUDE_ALL_LAUNCH=cmd\nCLAUDE_ALL_CMD=cc-gpt-plbbl\n"
            "CCGP_CONFIG_DIR=$HOME/custom-plbbl\nCLAUDE_CONFIG_DIR=$HOME/wrong\n",
            encoding="utf-8",
        )
        records = {item.name: item for item in claude_all.discover_profiles(self.home)}
        self.assertEqual(records["claude-fugu"].config_dir, self.home / "custom-fugu")
        self.assertEqual(records["claude-plbbl"].config_dir, self.home / "custom-plbbl")

    def test_native_claudish_profile_is_rewritten_to_wrapper_command(self) -> None:
        profiles = self.home / ".claude-all" / "profiles"
        profiles.mkdir(parents=True)
        profile = profiles / "gateway.env"
        profile.write_text(
            "CLAUDE_ALL_LAUNCH=claudish\nCLAUDE_ALL_MODEL=oai@test\n",
            encoding="utf-8",
        )
        record = claude_all._discover_records(self.home, None)[0]
        executable = self.home / ".claude-all/terminal-label/bin/terminal-label"
        _, values = claude_all._updated_profile(record, executable, self.home)
        self.assertEqual(values["CLAUDE_ALL_LAUNCH"], "cmd")
        self.assertEqual(values["CLAUDE_CONFIG_DIR"], str(self.home / ".claude-all"))
        self.assertEqual(
            values["CLAUDE_ALL_CMD"],
            str(executable.parent / "terminal-label-claudish"),
        )


class BatchLifecycleTests(unittest.TestCase):
    SECRET = "credential-do-not-copy-this-value"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.profiles = self.home.resolve() / ".claude-all" / "profiles"
        self.profiles.mkdir(parents=True)
        self.config_file = self.home / ".config" / "claude-all" / "config"
        self.config_file.parent.mkdir(parents=True)
        self.config_file.write_text(
            "token=%s\n"
            "token_cmd=security find-generic-password\n"
            "config_dir=$HOME/.claude-plbbl\n"
            "pool_usage_url=https://pool.example/api/quota\n"
            "pool_keychain_service=pool.example statusline\n"
            "pool_cookie_name=quota_cookie\n" % self.SECRET,
            encoding="utf-8",
        )
        self.direct_profile = self.profiles / "direct.env"
        self.direct_profile.write_text(
            "CLAUDE_ALL_LAUNCH=direct\n"
            "OPENAI_API_KEY=%s\n" % self.SECRET,
            encoding="utf-8",
        )
        self.claudish_profile = self.profiles / "gateway.env"
        self.claudish_profile.write_text(
            "CLAUDE_ALL_LAUNCH=claudish\n"
            "OPENAI_API_KEY=%s\n"
            "CLAUDISH_STATUSLINE_COMMAND='printf old-status'\n"
            "CLAUDISH_STATUSLINE_REFRESH=60\n" % self.SECRET,
            encoding="utf-8",
        )
        self.ccgp_profile = self.profiles / "claude-plbbl.env"
        self.ccgp_profile.write_text(
            "CLAUDE_ALL_LAUNCH=cmd\n"
            "CLAUDE_ALL_CMD=cc-gpt-plbbl\n"
            "UNRELATED=kept\n",
            encoding="utf-8",
        )
        self.original_profiles = {
            path: path.read_bytes()
            for path in (self.direct_profile, self.claudish_profile, self.ccgp_profile)
        }

        self.shared_settings = self.home.resolve() / ".claude-all" / "settings.json"
        self.plbbl_settings = self.home / ".claude-plbbl" / "settings.json"
        self.shared_settings.parent.mkdir(parents=True, exist_ok=True)
        self.plbbl_settings.parent.mkdir(parents=True, exist_ok=True)
        self.shared_settings.write_text(
            json.dumps(
                {
                    "shared": True,
                    "statusLine": {
                        "type": "command",
                        "command": "printf shared",
                        "refreshInterval": 30,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.plbbl_settings.write_text(
            json.dumps({"plbbl": True}), encoding="utf-8"
        )
        self.original_settings = {
            self.shared_settings: json.loads(self.shared_settings.read_text()),
            self.plbbl_settings: json.loads(self.plbbl_settings.read_text()),
        }
        self.runner = RecordingPluginRunner()

    def install(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False):
            return claude_all.batch_install(
                home=self.home,
                plugin_runner=self.runner,
                marketplace_source=ROOT,
            )

    def test_full_install_doctor_uninstall_in_temporary_home(self) -> None:
        state = self.install()

        state_path = self.home.resolve() / ".claude-all" / claude_all.STATE_FILENAME
        self.assertTrue(state_path.is_file())
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
        state_text = state_path.read_text(encoding="utf-8")
        self.assertNotIn(self.SECRET, state_text)
        self.assertNotIn("security find-generic-password", state_text)
        self.assertTrue(state["complete"])
        self.assertEqual(len(state["configs"]), 2)
        self.assertEqual(len(state["profiles"]), 2)

        install_calls = self.runner.calls
        self.assertEqual(len(install_calls), 4)
        config_dirs = {
            call[1]["CLAUDE_CONFIG_DIR"] for call in install_calls
        }
        self.assertEqual(
            config_dirs,
            {str(self.home.resolve() / ".claude-all"), str(self.home / ".claude-plbbl")},
        )
        for config_dir in config_dirs:
            calls = [call[0] for call in install_calls if call[1]["CLAUDE_CONFIG_DIR"] == config_dir]
            self.assertEqual(
                calls,
                [
                    ("plugin", "marketplace", "add", str(ROOT)),
                    ("plugin", "install", claude_all.PLUGIN_NAME),
                ],
            )
            selected = [
                call[1]
                for call in install_calls
                if call[1]["CLAUDE_CONFIG_DIR"] == config_dir
            ]
            self.assertEqual({env["HOME"] for env in selected}, {str(self.home)})
            self.assertTrue(
                all(
                    not any(
                        key.startswith(("ANTHROPIC_", "OPENAI_", "LITELLM_", "SAKANA_"))
                        or key == "CCGP_TOKEN"
                        for key in env
                    )
                    for env in selected
                )
            )

        # Direct and claudish profiles share one config directory and one runtime.
        shared_runtime = claude_all.terminal_label.runtime_executable(self.shared_settings)
        plbbl_runtime = claude_all.terminal_label.runtime_executable(self.plbbl_settings)
        self.assertTrue(shared_runtime.is_file())
        self.assertTrue(plbbl_runtime.is_file())
        shared_installed = json.loads(self.shared_settings.read_text())
        self.assertEqual(shared_installed["statusLine"]["refreshInterval"], 5)
        self.assertEqual(
            claude_all.terminal_label.parse_proxy_command(
                shared_installed["statusLine"]["command"]
            )[0].resolve(),
            shared_runtime.resolve(),
        )

        # Only claudish-compatible profiles are changed and backed up atomically.
        self.assertEqual(self.direct_profile.read_bytes(), self.original_profiles[self.direct_profile])
        claudish_values = claude_all.parse_env_file(self.claudish_profile)
        self.assertEqual(claudish_values["CLAUDISH_STATUSLINE_REFRESH"], "5")
        parsed_command = claude_all.terminal_label.parse_proxy_command(
            claudish_values["CLAUDISH_STATUSLINE_COMMAND"]
        )
        self.assertIsNotNone(parsed_command)
        self.assertEqual(parsed_command[0].resolve(), shared_runtime.resolve())
        self.assertEqual(parsed_command[1], "printf old-status")

        ccgp_values = claude_all.parse_env_file(self.ccgp_profile)
        self.assertEqual(ccgp_values["CCGP_STATUSLINE"], "no")
        self.assertEqual(
            ccgp_values["CLAUDE_ALL_POOL_USAGE_URL"],
            "https://pool.example/api/quota",
        )
        self.assertEqual(
            ccgp_values["CLAUDE_ALL_POOL_KEYCHAIN_SERVICE"],
            "pool.example statusline",
        )
        self.assertEqual(
            ccgp_values["CLAUDE_ALL_POOL_COOKIE_NAME"], "quota_cookie"
        )
        self.assertNotIn("token", ccgp_values)
        for entry in state["profiles"]:
            backup = Path(entry["backup"])
            self.assertTrue(backup.is_file())
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

        healthy, reports = claude_all.batch_doctor(home=self.home)
        self.assertTrue(healthy, reports)
        self.assertEqual(
            set(reports),
            {str(self.home.resolve() / ".claude-all"), str(self.home / ".claude-plbbl")},
        )
        self.assertTrue(
            all(
                "OK statusLine proxy is installed" in messages
                for messages in reports.values()
            )
        )

        calls_before_uninstall = len(self.runner.calls)
        result = claude_all.batch_uninstall(
            home=self.home, plugin_runner=self.runner
        )
        self.assertEqual(len(result["configs"]), 2)
        uninstall_calls = self.runner.calls[calls_before_uninstall:]
        self.assertEqual(uninstall_calls, [])
        self.assertFalse(state_path.exists())
        for profile, original in self.original_profiles.items():
            self.assertEqual(profile.read_bytes(), original)
        for entry in state["profiles"]:
            self.assertFalse(Path(entry["backup"]).exists())
        for settings, original in self.original_settings.items():
            self.assertEqual(json.loads(settings.read_text()), original)
        self.assertFalse(shared_runtime.exists())
        self.assertFalse(plbbl_runtime.exists())

    def test_install_is_idempotent_while_state_is_valid(self) -> None:
        first = self.install()
        call_count = len(self.runner.calls)
        second = self.install()
        self.assertEqual(first, second)
        self.assertEqual(len(self.runner.calls), call_count)

    def test_preexisting_single_profile_install_is_adopted_not_removed(self) -> None:
        runtime = claude_all.terminal_label.install_runtime(self.shared_settings)
        claude_all.terminal_label.install(self.shared_settings, runtime)
        managed_before = self.shared_settings.read_bytes()

        state = self.install()
        entry = next(
            item for item in state["configs"]
            if item["config_dir"] == str(self.home / ".claude-all")
        )
        self.assertFalse(entry["settings_owned"])
        self.assertFalse(entry["runtime_owned"])

        claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)
        self.assertEqual(self.shared_settings.read_bytes(), managed_before)
        self.assertTrue(runtime.is_file())
        claude_all.terminal_label.uninstall(self.shared_settings, runtime)
        claude_all.terminal_label.remove_runtime(self.shared_settings)

    def test_rerun_adds_new_profile_without_reinstalling_existing_configs(self) -> None:
        state = self.install()
        call_count = len(self.runner.calls)
        new_config = self.home / ".claude-new"
        new_profile = self.profiles / "claude-new.env"
        new_profile.write_text(
            'CLAUDE_ALL_LAUNCH=cmd\nCLAUDE_ALL_CMD="claude-new"\n',
            encoding="utf-8",
        )

        updated = self.install()

        self.assertEqual(len(updated["configs"]), len(state["configs"]) + 1)
        new_calls = self.runner.calls[call_count:]
        self.assertEqual(len(new_calls), 2)
        self.assertEqual(
            {call[1]["CLAUDE_CONFIG_DIR"] for call in new_calls}, {str(new_config)}
        )
        self.assertTrue(
            claude_all.terminal_label.runtime_executable(
                new_config / "settings.json"
            ).is_file()
        )
        claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)

    def test_uninstall_allows_unrelated_settings_changes(self) -> None:
        self.install()
        installed = json.loads(self.shared_settings.read_text(encoding="utf-8"))
        installed["unrelated_after_install"] = True
        self.shared_settings.write_text(json.dumps(installed), encoding="utf-8")

        claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)

        restored = json.loads(self.shared_settings.read_text(encoding="utf-8"))
        self.assertTrue(restored["unrelated_after_install"])
        self.assertEqual(restored["statusLine"], self.original_settings[self.shared_settings]["statusLine"])

    def test_all_settings_are_preflighted_before_uninstall(self) -> None:
        self.install()
        shared = json.loads(self.shared_settings.read_text(encoding="utf-8"))
        shared["statusLine"]["refreshInterval"] = 99
        self.shared_settings.write_text(json.dumps(shared), encoding="utf-8")
        plbbl_before = self.plbbl_settings.read_bytes()
        plbbl_runtime = claude_all.terminal_label.runtime_executable(self.plbbl_settings)

        with self.assertRaises(claude_all.terminal_label.ConfigConflict):
            claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)

        self.assertEqual(self.plbbl_settings.read_bytes(), plbbl_before)
        self.assertTrue(plbbl_runtime.is_file())

    def test_uninstall_refuses_profile_hash_conflict_before_plugin_commands(self) -> None:
        self.install()
        call_count = len(self.runner.calls)
        self.claudish_profile.write_text(
            self.claudish_profile.read_text(encoding="utf-8") + "MANUAL=yes\n",
            encoding="utf-8",
        )
        changed = self.claudish_profile.read_bytes()

        with self.assertRaises(claude_all.terminal_label.ConfigConflict):
            claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)

        self.assertEqual(len(self.runner.calls), call_count)
        self.assertEqual(self.claudish_profile.read_bytes(), changed)
        self.assertTrue(
            (self.home.resolve() / ".claude-all" / claude_all.STATE_FILENAME).exists()
        )

    def test_uninstall_refuses_profile_backup_hash_conflict(self) -> None:
        state = self.install()
        call_count = len(self.runner.calls)
        backup = Path(state["profiles"][0]["backup"])
        backup.write_bytes(backup.read_bytes() + b"changed")

        with self.assertRaises(claude_all.terminal_label.ConfigConflict):
            claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)
        self.assertEqual(len(self.runner.calls), call_count)

    def test_doctor_detects_missing_batch_runtime_component(self) -> None:
        state = self.install()
        runtime = Path(state["configs"][0]["runtime"])
        (runtime.parent.parent / "lib" / "claude_all.py").unlink()
        healthy, reports = claude_all.batch_doctor(home=self.home)
        self.assertFalse(healthy)
        self.assertTrue(
            any(
                "claude_all.py" in message and message.startswith("FAIL")
                for messages in reports.values()
                for message in messages
            )
        )

    def test_doctor_detects_non_executable_claudish_wrapper(self) -> None:
        state = self.install()
        runtime = Path(state["configs"][0]["runtime"])
        wrapper = runtime.parent / "terminal-label-claudish"
        wrapper.chmod(0o600)
        healthy, reports = claude_all.batch_doctor(home=self.home)
        self.assertFalse(healthy)
        self.assertTrue(
            any(
                "terminal-label-claudish" in message and message.startswith("FAIL")
                for messages in reports.values()
                for message in messages
            )
        )

    def test_old_state_schema_is_rejected(self) -> None:
        state_path = self.home / ".claude-all" / claude_all.STATE_FILENAME
        state_path.write_text(
            json.dumps({"version": 1, "complete": True, "configs": [], "profiles": []}),
            encoding="utf-8",
        )
        with self.assertRaises(claude_all.terminal_label.TerminalLabelError):
            claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)
        self.assertTrue(state_path.exists())

    def test_plugin_removal_is_explicit(self) -> None:
        self.install()
        call_count = len(self.runner.calls)

        claude_all.batch_uninstall(
            home=self.home,
            plugin_runner=self.runner,
            remove_plugin=True,
        )

        calls = [call[0] for call in self.runner.calls[call_count:]]
        self.assertEqual(
            calls,
            [
                ("plugin", "uninstall", claude_all.PLUGIN_NAME),
                ("plugin", "marketplace", "remove", claude_all.MARKETPLACE_NAME),
                ("plugin", "uninstall", claude_all.PLUGIN_NAME),
                ("plugin", "marketplace", "remove", claude_all.MARKETPLACE_NAME),
            ],
        )

    def test_settings_checkpoint_failure_remains_uninstallable(self) -> None:
        original_write = claude_all._write_state
        calls = {"count": 0}

        def fail_after_settings(path, state, expected_hash):
            calls["count"] += 1
            if calls["count"] == 4:
                raise OSError("injected state failure")
            return original_write(path, state, expected_hash)

        with mock.patch.object(claude_all, "_write_state", side_effect=fail_after_settings):
            with self.assertRaises(OSError):
                claude_all.batch_install(
                    home=self.home,
                    plugin_runner=self.runner,
                    skip_plugin_install=True,
                )
        claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)
        self.assertEqual(
            json.loads(self.shared_settings.read_text()),
            self.original_settings[self.shared_settings],
        )

    def test_profile_checkpoint_failure_remains_uninstallable(self) -> None:
        original_write = claude_all._write_state

        def fail_after_profile_replace(path, state, expected_hash):
            if state["profiles"] and state["profiles"][-1].get("stage") == "installed":
                raise OSError("injected profile state failure")
            return original_write(path, state, expected_hash)

        with mock.patch.object(
            claude_all, "_write_state", side_effect=fail_after_profile_replace
        ):
            with self.assertRaises(OSError):
                claude_all.batch_install(
                    home=self.home,
                    plugin_runner=self.runner,
                    skip_plugin_install=True,
                )
        claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)
        self.assertEqual(
            self.claudish_profile.read_bytes(),
            self.original_profiles[self.claudish_profile],
        )

    def test_failed_explicit_plugin_removal_keeps_state_and_local_install(self) -> None:
        self.install()
        state_path = self.home / ".claude-all" / claude_all.STATE_FILENAME
        settings_before = self.shared_settings.read_bytes()

        class FailingRunner:
            def __call__(self, args, env):
                del args, env
                return 1

        with self.assertRaises(claude_all.terminal_label.TerminalLabelError):
            claude_all.batch_uninstall(
                home=self.home,
                plugin_runner=FailingRunner(),
                remove_plugin=True,
            )
        self.assertTrue(state_path.is_file())
        self.assertEqual(self.shared_settings.read_bytes(), settings_before)

    def test_plugin_cleanup_resume_skips_completed_uninstall_step(self) -> None:
        self.install()

        class FailSecondCall:
            def __init__(self):
                self.calls = []

            def __call__(self, args, env):
                del env
                self.calls.append(tuple(args))
                return 1 if len(self.calls) == 2 else 0

        failing = FailSecondCall()
        with self.assertRaises(claude_all.terminal_label.TerminalLabelError):
            claude_all.batch_uninstall(
                home=self.home, plugin_runner=failing, remove_plugin=True
            )
        state = json.loads(
            (self.home / ".claude-all" / claude_all.STATE_FILENAME).read_text()
        )
        self.assertEqual(state["configs"][0]["plugin_cleanup"], "plugin_removed")

        retry = RecordingPluginRunner()
        claude_all.batch_uninstall(
            home=self.home, plugin_runner=retry, remove_plugin=True
        )
        self.assertEqual(
            retry.calls[0][0],
            ("plugin", "marketplace", "remove", claude_all.MARKETPLACE_NAME),
        )

    def test_failed_plugin_install_leaves_explicit_incomplete_state(self) -> None:
        class FailingRunner:
            def __init__(self):
                self.calls = 0

            def __call__(self, args, env):
                del args, env
                self.calls += 1
                return 0 if self.calls == 1 else 1

        with self.assertRaises(claude_all.terminal_label.TerminalLabelError):
            claude_all.batch_install(
                home=self.home,
                plugin_runner=FailingRunner(),
            )
        state_path = self.home / ".claude-all" / claude_all.STATE_FILENAME
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertFalse(state["complete"])
        self.assertTrue(state["configs"])
        self.assertEqual(state["configs"][0]["stage"], "marketplace_added")
        with self.assertRaises(claude_all.terminal_label.TerminalLabelError):
            claude_all.batch_install(home=self.home, plugin_runner=self.runner)
        claude_all.batch_uninstall(home=self.home, plugin_runner=self.runner)
        self.assertFalse(state_path.exists())

    def test_existing_managed_profile_command_is_not_nested(self) -> None:
        state = self.install()
        runtime = Path(
            next(
                entry["runtime"]
                for entry in state["configs"]
                if entry["config_dir"] == str(self.home / ".claude-all")
            )
        )
        before = claude_all.parse_env_file(self.claudish_profile)[
            "CLAUDISH_STATUSLINE_COMMAND"
        ]
        record = next(
            record
            for record in claude_all._discover_records(self.home, None)
            if record.info.path == self.claudish_profile.resolve()
        )

        _, values = claude_all._updated_profile(record, runtime, self.home)

        self.assertEqual(values["CLAUDISH_STATUSLINE_COMMAND"], before)

    def test_explicit_config_can_install_without_plugin_commands(self) -> None:
        extra = self.home / "extra-config"
        empty_profiles = self.home / "empty-profiles"
        empty_profiles.mkdir()
        runner = RecordingPluginRunner()

        state = claude_all.batch_install(
            home=self.home,
            profiles_dir=empty_profiles,
            extra_config_dirs=(extra,),
            plugin_runner=runner,
            skip_plugin_install=True,
        )

        self.assertEqual(runner.calls, [])
        self.assertEqual([entry["config_dir"] for entry in state["configs"]], [str(extra)])
        claude_all.batch_uninstall(home=self.home, plugin_runner=runner)


if __name__ == "__main__":
    unittest.main()
