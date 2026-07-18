import unittest
from pathlib import Path

from nk_cli.boundaries import forbidden_tracked, validate_manifest


class BoundariesTests(unittest.TestCase):
    def test_forbidden_runtime_paths(self) -> None:
        paths = [
            "app/page.tsx",
            "app/.next/build.json",
            "x/node_modules/pkg/index.js",
            "memory.chroma/chroma.sqlite3",
            "client/tsconfig.tsbuildinfo",
        ]
        self.assertEqual(4, len(forbidden_tracked(paths)))

    def test_rejects_unclassified_root_and_duplicate_unit(self) -> None:
        manifest = {
            "version": "nk-repository-units/v1",
            "root_files": ["README.md"],
            "units": [
                {"path": "docs", "kind": "documentation", "lifecycle": "active", "deploy_root": False},
                {"path": "docs", "kind": "documentation", "lifecycle": "active", "deploy_root": False},
            ],
        }
        errors = validate_manifest(Path("/"), manifest, ["README.md", "mystery/file.txt"])
        self.assertIn("unit paths must be unique", errors)
        self.assertIn("unclassified root path: mystery", errors)

    def test_accepts_legacy_nanokat_version_string(self) -> None:
        manifest = {
            "version": "nanokat-repository-units/v1",
            "root_files": ["README.md"],
            "units": [
                {"path": "docs", "kind": "documentation", "lifecycle": "active", "deploy_root": False},
            ],
        }
        # Path may not exist on this host — expect missing path error only, not version
        errors = validate_manifest(Path("/tmp"), manifest, ["README.md"])
        self.assertTrue(any("does not exist" in e for e in errors) or errors == [])
        self.assertFalse(any("unsupported manifest version" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
