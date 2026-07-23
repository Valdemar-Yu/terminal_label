from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "plugins" / "terminal-label" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
spec = importlib.util.spec_from_file_location("warp", LIB / "warp.py")
assert spec is not None and spec.loader is not None
warp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(warp)


class WarpConfigTests(unittest.TestCase):
    SECRET = "token-must-never-appear-in-warp-output"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.profiles = self.home / ".claude-all" / "profiles"
        self.profiles.mkdir(parents=True)

        self.explicit_name = "odd profile's"
        (self.profiles / (self.explicit_name + ".env")).write_text(
            "CLAUDE_ALL_LAUNCH=claudish\n"
            "CLAUDE_ALL_MODEL='OpenAI \"Prime\"'\n"
            "OPENAI_API_KEY=%s\n" % self.SECRET,
            encoding="utf-8",
        )

        plbbl_config = self.home / ".config" / "claude-all" / "config"
        plbbl_config.parent.mkdir(parents=True)
        plbbl_config.write_text(
            "token=%s\n"
            "token_cmd=security find-generic-password\n"
            "config_dir=$HOME/.claude-plbbl\n"
            "model=GPT-5.6 PLBBL\n" % self.SECRET,
            encoding="utf-8",
        )
        (self.profiles / "claude-plbbl.env").write_text(
            "CLAUDE_ALL_LAUNCH=cmd\n"
            "CLAUDE_ALL_CMD=cc-gpt-plbbl\n",
            encoding="utf-8",
        )

        settings_dir = self.home / ".claude-settings"
        settings_dir.mkdir(parents=True)
        self.settings_path = settings_dir / "settings.json"
        self.settings_path.write_text(
            json.dumps(
                {
                    "model": {"display_name": "Sonnet Settings"},
                    "env": {
                        "MODEL_DISPLAY_NAME": "Ignored Env Display",
                        "ANTHROPIC_AUTH_TOKEN": self.SECRET,
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.profiles / "settings.env").write_text(
            "CLAUDE_ALL_LAUNCH=direct\n"
            "CLAUDE_CONFIG_DIR=%s\n" % settings_dir,
            encoding="utf-8",
        )

        (self.profiles / "label.env").write_text(
            "CLAUDE_ALL_LAUNCH=direct\n"
            "CLAUDE_ALL_LABEL='Readable Label'\n",
            encoding="utf-8",
        )
        (self.profiles / "name-only.env").write_text(
            "CLAUDE_ALL_LAUNCH=direct\n", encoding="utf-8"
        )

        self.existing_path = warp._tab_path(self.home, "label")
        self.existing_path.parent.mkdir(parents=True)
        self.existing_raw = b'name = "user owned"\n'
        self.existing_path.write_bytes(self.existing_raw)
        self.existing_path.chmod(0o640)

    def _config_text(self, profile: str) -> str:
        return warp._tab_path(self.home, profile).read_text(encoding="utf-8")

    @staticmethod
    def _string_value(text: str, key: str) -> str:
        prefix = key + " = "
        line = next(line for line in text.splitlines() if line.startswith(prefix))
        return json.loads(line[len(prefix) :])

    @staticmethod
    def _commands(text: str):
        prefix = "commands = "
        line = next(line for line in text.splitlines() if line.startswith(prefix))
        return json.loads(line[len(prefix) :])

    def test_configure_five_profiles_uses_safe_model_precedence_and_toml(self) -> None:
        project = self.home / 'repo with "quotes" and $dollar'

        changed, messages = warp.configure(home=self.home, project_dir=project)

        self.assertTrue(changed)
        self.assertTrue(any("reversible Warp state" in message for message in messages))
        expected_titles = {
            self.explicit_name: 'OpenAI "Prime" · {{session}}',
            "claude-plbbl": "GPT-5.6 PLBBL · {{session}}",
            "settings": "Sonnet Settings · {{session}}",
            "label": "Readable Label · {{session}}",
            "name-only": "name-only · {{session}}",
        }
        self.assertEqual(
            {path.name for path in warp._tab_configs_dir(self.home).glob("*.toml")},
            {
                warp.TAB_CONFIG_PREFIX + profile + ".toml"
                for profile in expected_titles
            },
        )
        for profile, expected_title in expected_titles.items():
            text = self._config_text(profile)
            self.assertEqual(self._string_value(text, "title"), expected_title)
            self.assertIn('[params.project_dir]\ntype = "repo"', text)
            self.assertIn('[params.session]\ntype = "text"', text)
            self.assertEqual(self._string_value(text, "default"), str(project))
            command = self._commands(text)
            self.assertEqual(len(command), 1)
            self.assertEqual(
                shlex.split(command[0]),
                [
                    "WARP_DISABLE_AUTO_TITLE=true",
                    "exec",
                    "claude-all",
                    profile,
                ],
            )
            self.assertTrue(
                command[0].startswith(
                    "WARP_DISABLE_AUTO_TITLE=true exec claude-all "
                )
            )
            self.assertEqual(
                stat.S_IMODE(warp._tab_path(self.home, profile).stat().st_mode),
                0o600,
            )

        # The PLBBL token and settings auth token are never copied into Warp files
        # or reversible state.
        generated = b"".join(
            path.read_bytes()
            for path in self.home.rglob("*")
            if path.is_file()
            and (
                path.name.startswith(warp.TAB_CONFIG_PREFIX)
                or path == warp._state_path(self.home)
            )
        )
        self.assertNotIn(self.SECRET.encode(), generated)
        self.assertNotIn(b"security find-generic-password", generated)

        state_path = warp._state_path(self.home)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
        self.assertTrue(state["complete"])
        self.assertEqual(len(state["profiles"]), 5)
        label_entry = next(
            entry for entry in state["profiles"] if entry["profile"] == "label"
        )
        backup = Path(label_entry["backup"])
        self.assertEqual(backup.read_bytes(), self.existing_raw)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

        healthy, doctor_messages = warp.doctor(self.home)
        self.assertTrue(healthy, doctor_messages)

        again_changed, _ = warp.configure(home=self.home, project_dir=project)
        self.assertFalse(again_changed)
        self.assertEqual(backup.read_bytes(), self.existing_raw)

    def test_update_model_and_add_new_profile_without_replacing_backup(self) -> None:
        warp.configure(
            home=self.home,
            profiles=("settings", "label"),
            project_dir=self.home / "first project",
        )
        state_path = warp._state_path(self.home)
        first_state = json.loads(state_path.read_text(encoding="utf-8"))
        label_backup = next(
            entry["backup"]
            for entry in first_state["profiles"]
            if entry["profile"] == "label"
        )
        self.settings_path.write_text(
            json.dumps(
                {
                    "env": {
                        "MODEL_DISPLAY_NAME": "Environment Display Name",
                        "ANTHROPIC_AUTH_TOKEN": self.SECRET,
                    }
                }
            ),
            encoding="utf-8",
        )

        changed, _ = warp.configure(
            home=self.home,
            profiles=("settings", "label", "name-only"),
            project_dir=self.home / "second project",
        )

        self.assertTrue(changed)
        settings_text = self._config_text("settings")
        self.assertEqual(
            self._string_value(settings_text, "title"),
            "Environment Display Name · {{session}}",
        )
        self.assertEqual(
            self._string_value(settings_text, "default"),
            str(self.home / "second project"),
        )
        updated_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(updated_state["profiles"]), 3)
        self.assertEqual(
            next(
                entry["backup"]
                for entry in updated_state["profiles"]
                if entry["profile"] == "label"
            ),
            label_backup,
        )
        self.assertNotIn(self.SECRET, state_path.read_text(encoding="utf-8"))
        healthy, messages = warp.doctor(self.home)
        self.assertTrue(healthy, messages)

    def test_hash_conflict_blocks_configure_and_restore(self) -> None:
        warp.configure(home=self.home, profiles=("name-only",))
        path = warp._tab_path(self.home, "name-only")
        path.write_text(path.read_text(encoding="utf-8") + "# manual edit\n")
        changed_raw = path.read_bytes()

        healthy, messages = warp.doctor(self.home)
        self.assertFalse(healthy)
        self.assertTrue(any("hash changed" in message for message in messages))
        with self.assertRaises(warp.terminal_label.ConfigConflict):
            warp.configure(home=self.home, profiles=("name-only",))
        with self.assertRaises(warp.terminal_label.ConfigConflict):
            warp.restore(home=self.home)
        self.assertEqual(path.read_bytes(), changed_raw)
        self.assertTrue(warp._state_path(self.home).exists())

    def test_restore_removes_created_files_and_restores_existing_exactly(self) -> None:
        warp.configure(home=self.home)
        state = json.loads(warp._state_path(self.home).read_text(encoding="utf-8"))
        backups = [
            Path(entry["backup"])
            for entry in state["profiles"]
            if entry["backup"] is not None
        ]

        changed, messages = warp.restore(home=self.home)

        self.assertTrue(changed)
        self.assertTrue(messages)
        self.assertEqual(self.existing_path.read_bytes(), self.existing_raw)
        self.assertEqual(stat.S_IMODE(self.existing_path.stat().st_mode), 0o640)
        for profile in (
            self.explicit_name,
            "claude-plbbl",
            "settings",
            "name-only",
        ):
            self.assertFalse(warp._tab_path(self.home, profile).exists())
        self.assertFalse(warp._state_path(self.home).exists())
        self.assertTrue(all(not backup.exists() for backup in backups))
        again_changed, _ = warp.restore(home=self.home)
        self.assertFalse(again_changed)

    def test_changed_backup_blocks_restore_before_any_config_is_changed(self) -> None:
        warp.configure(home=self.home, profiles=("label", "name-only"))
        state = json.loads(warp._state_path(self.home).read_text(encoding="utf-8"))
        backup = Path(
            next(entry["backup"] for entry in state["profiles"] if entry["backup"])
        )
        backup.write_bytes(backup.read_bytes() + b"changed")
        name_only_before = warp._tab_path(self.home, "name-only").read_bytes()

        with self.assertRaises(warp.terminal_label.ConfigConflict):
            warp.restore(self.home)

        self.assertEqual(
            warp._tab_path(self.home, "name-only").read_bytes(), name_only_before
        )
        self.assertTrue(warp._state_path(self.home).exists())

    def test_unknown_profile_and_symlink_are_rejected(self) -> None:
        with self.assertRaises(warp.terminal_label.TerminalLabelError):
            warp.configure(home=self.home, profiles=("missing",))

        path = warp._tab_path(self.home, "name-only")
        path.parent.mkdir(parents=True, exist_ok=True)
        target = self.home / "outside.toml"
        target.write_text("outside", encoding="utf-8")
        try:
            path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        with self.assertRaises(warp.terminal_label.ConfigConflict):
            warp.configure(home=self.home, profiles=("name-only",))
        self.assertEqual(target.read_text(encoding="utf-8"), "outside")


if __name__ == "__main__":
    unittest.main()
