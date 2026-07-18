import tempfile
import unittest
from pathlib import Path

from nk_cli.boundaries import forbidden_tracked, run_boundaries, validate_manifest


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

    def test_rejects_non_string_unit_path_without_crashing(self) -> None:
        manifest = {
            "version": "nk-repository-units/v1",
            "root_files": ["README.md"],
            "units": [
                {"path": [], "kind": "test", "lifecycle": "active", "deploy_root": False},
            ],
        }
        errors = validate_manifest(Path("/"), manifest, ["README.md"])
        self.assertIn("invalid unit path: []", errors)

    def test_rejects_invalid_root_files_without_crashing(self) -> None:
        manifest = {
            "version": "nk-repository-units/v1",
            "root_files": [["README.md"]],
            "units": [],
        }
        errors = validate_manifest(Path("/"), manifest, ["README.md"])
        self.assertIn("root_files must be a list of filenames", errors)

    def test_run_rejects_invalid_json_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text("{", encoding="utf-8")
            code, messages = run_boundaries(Path(directory), manifest_path)
        self.assertEqual(2, code)
        self.assertEqual(["manifest is not valid JSON"], messages)


if __name__ == "__main__":
    unittest.main()
