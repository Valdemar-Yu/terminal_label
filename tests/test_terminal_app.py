from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "plugins" / "terminal-label" / "lib"
import sys
sys.path.insert(0, str(LIB))
spec = importlib.util.spec_from_file_location("terminal_app", LIB / "terminal_app.py")
assert spec is not None and spec.loader is not None
terminal_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terminal_app)


class FakeDefaults:
    def __init__(self, preferences):
        self.payload = plistlib.dumps(preferences, fmt=plistlib.FMT_XML)
        self.import_calls = 0
        self.fail_import = False

    def __call__(self, args):
        args = tuple(args)
        if args == ("defaults", "export", terminal_app.DOMAIN, "-"):
            return subprocess.CompletedProcess(args, 0, stdout=self.payload, stderr=b"")
        if args[:3] == ("defaults", "import", terminal_app.DOMAIN):
            self.import_calls += 1
            if self.fail_import:
                return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"failed")
            self.payload = Path(args[3]).read_bytes()
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
        raise AssertionError(args)

    def profile(self, name="Basic"):
        return plistlib.loads(self.payload)[terminal_app.PROFILE_KEY][name]


class TerminalAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.defaults = FakeDefaults(
            {
                "Default Window Settings": "Basic",
                terminal_app.PROFILE_KEY: {
                    "Basic": {"name": "Basic", "Other": True},
                    "Pro": {"name": "Pro"},
                },
            }
        )

    def test_configure_doctor_restore_missing_key(self):
        changed, messages = terminal_app.configure(
            home=self.home, runner=self.defaults
        )
        self.assertTrue(changed)
        self.assertTrue(any("Basic" in message for message in messages))
        self.assertTrue(
            all(
                self.defaults.profile()[key] is False
                for key in terminal_app.TITLE_COMPONENT_KEYS
            )
        )
        state_path = terminal_app._state_path(self.home)
        state = json.loads(state_path.read_text())
        self.assertFalse(
            state["old_values"][terminal_app.CUSTOM_ONLY_KEY]["present"]
        )
        self.assertTrue(state["complete"])
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
        backup = Path(state["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

        healthy, doctor_messages = terminal_app.doctor(
            home=self.home, runner=self.defaults
        )
        self.assertTrue(healthy, doctor_messages)

        changed, _ = terminal_app.restore(home=self.home, runner=self.defaults)
        self.assertTrue(changed)
        self.assertTrue(
            all(
                key not in self.defaults.profile()
                for key in terminal_app.TITLE_COMPONENT_KEYS
            )
        )
        self.assertFalse(state_path.exists())
        self.assertFalse(backup.exists())

    def test_existing_value_is_restored_exactly(self):
        preferences = plistlib.loads(self.defaults.payload)
        preferences[terminal_app.PROFILE_KEY]["Basic"][terminal_app.CUSTOM_ONLY_KEY] = True
        self.defaults.payload = plistlib.dumps(preferences)
        terminal_app.configure(home=self.home, runner=self.defaults)
        terminal_app.restore(home=self.home, runner=self.defaults)
        self.assertIs(self.defaults.profile()[terminal_app.CUSTOM_ONLY_KEY], True)

    def test_configure_is_idempotent(self):
        terminal_app.configure(home=self.home, runner=self.defaults)
        imports = self.defaults.import_calls
        changed, _ = terminal_app.configure(home=self.home, runner=self.defaults)
        self.assertFalse(changed)
        self.assertEqual(self.defaults.import_calls, imports)

    def test_restore_refuses_changed_managed_value(self):
        terminal_app.configure(home=self.home, runner=self.defaults)
        preferences = plistlib.loads(self.defaults.payload)
        preferences[terminal_app.PROFILE_KEY]["Basic"][terminal_app.CUSTOM_ONLY_KEY] = True
        self.defaults.payload = plistlib.dumps(preferences)
        with self.assertRaises(terminal_app.terminal_label.ConfigConflict):
            terminal_app.restore(home=self.home, runner=self.defaults)
        self.assertTrue(terminal_app._state_path(self.home).exists())

    def test_restore_resumes_after_state_checkpoint_failure(self):
        terminal_app.configure(home=self.home, runner=self.defaults)
        original_write = terminal_app._write_json

        def fail_after_import(path, value):
            if value.get("restored"):
                raise OSError("injected state failure")
            return original_write(path, value)

        with mock.patch.object(terminal_app, "_write_json", side_effect=fail_after_import):
            with self.assertRaises(OSError):
                terminal_app.restore(home=self.home, runner=self.defaults)
        self.assertTrue(terminal_app._state_path(self.home).exists())
        changed, _ = terminal_app.restore(home=self.home, runner=self.defaults)
        self.assertTrue(changed)
        self.assertFalse(terminal_app._state_path(self.home).exists())

    def test_restore_refuses_changed_backup(self):
        terminal_app.configure(home=self.home, runner=self.defaults)
        state = json.loads(terminal_app._state_path(self.home).read_text())
        backup = Path(state["backup"])
        backup.write_bytes(backup.read_bytes() + b"changed")
        with self.assertRaises(terminal_app.terminal_label.ConfigConflict):
            terminal_app.restore(home=self.home, runner=self.defaults)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(terminal_app.terminal_label.TerminalLabelError):
            terminal_app.configure(
                home=self.home, profile="Missing", runner=self.defaults
            )

    def test_failed_import_leaves_resumable_state(self):
        self.defaults.fail_import = True
        with self.assertRaises(terminal_app.terminal_label.TerminalLabelError):
            terminal_app.configure(home=self.home, runner=self.defaults)
        state = json.loads(terminal_app._state_path(self.home).read_text())
        self.assertFalse(state["complete"])

        self.defaults.fail_import = False
        changed, _ = terminal_app.configure(home=self.home, runner=self.defaults)
        self.assertTrue(changed)
        state = json.loads(terminal_app._state_path(self.home).read_text())
        self.assertTrue(state["complete"])
        self.assertIs(self.defaults.profile()[terminal_app.CUSTOM_ONLY_KEY], False)


if __name__ == "__main__":
    unittest.main()
