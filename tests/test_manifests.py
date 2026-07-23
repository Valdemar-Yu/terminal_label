import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def load(self, relative_path):
        with (ROOT / relative_path).open(encoding="utf-8") as handle:
            return json.load(handle)

    def test_marketplace_points_to_plugin(self):
        marketplace = self.load(".claude-plugin/marketplace.json")
        self.assertEqual(marketplace["name"], "terminal-label")
        self.assertEqual(len(marketplace["plugins"]), 1)
        source = marketplace["plugins"][0]["source"]
        self.assertTrue(source.startswith("./"))
        self.assertTrue((ROOT / source / ".claude-plugin/plugin.json").is_file())

    def test_plugin_identity_and_version(self):
        plugin = self.load("plugins/terminal-label/.claude-plugin/plugin.json")
        self.assertEqual(plugin["name"], "terminal-label")
        self.assertEqual(plugin["version"], "0.2.3")
        self.assertEqual(plugin["license"], "MIT")
        self.assertTrue(
            (ROOT / "plugins/terminal-label/skills/setup-claude-all/SKILL.md").is_file()
        )
        self.assertTrue(
            (ROOT / "plugins/terminal-label/bin/terminal-label-claudish").stat().st_mode
            & 0o111
        )

    def test_hook_commands_use_plugin_root(self):
        hooks = self.load("plugins/terminal-label/hooks/hooks.json")
        session_start = hooks["hooks"]["SessionStart"]
        command = session_start[0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", command)


if __name__ == "__main__":
    unittest.main()
