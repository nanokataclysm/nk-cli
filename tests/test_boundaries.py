import tempfile
import unittest
import os
import subprocess
from unittest.mock import patch
from pathlib import Path

from helpers import fixture_repo
from nk_cli.boundaries import boundary_report, forbidden_tracked, run_boundaries, validate_manifest


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
        self.assertIn("unclassified tracked path: mystery/file.txt", errors)

    def test_no_manifest_works_for_a_single_project_from_a_subdirectory(self):
        root = fixture_repo(self, {"pyproject.toml": "", "src/hello.py": "pass", "README.md": "hello"})
        code, report = boundary_report(root / "src")
        self.assertEqual(0, code)
        self.assertEqual(str(root), report["repo"])
        self.assertEqual(3, report["tracked_files"])
        self.assertEqual(["src"], report["units"])

    def test_nested_unit_does_not_authorize_its_sibling(self):
        root = fixture_repo(self, {"apps/api/main.py": "", "apps/web/index.js": ""})
        manifest = {"version": "nk-repository-units/v1", "root_files": [], "units": ["apps/api"]}
        errors = validate_manifest(root, manifest, ["apps/api/main.py", "apps/web/index.js"])
        self.assertEqual(["unclassified tracked path: apps/web/index.js"], errors)

    def test_path_validation_rejects_absolute_traversal_and_windows_drive_paths(self):
        root = fixture_repo(self, {"README.md": ""})
        for path in ["../outside", "/tmp/outside", "C:/outside", "C:\\outside", ".", "src//code"]:
            with self.subTest(path=path):
                manifest = {"version": "nk-repository-units/v1", "root_files": ["README.md"], "units": [path]}
                self.assertTrue(any("invalid unit path" in e for e in validate_manifest(root, manifest, ["README.md"])))

    def test_intentional_artifacts_can_be_allowed_without_disabling_other_findings(self):
        root = fixture_repo(self, {"fixtures/node_modules/example.js": "", "src/__pycache__/bad.pyc": ""})
        code, report = boundary_report(root, allow_patterns=("fixtures/*",))
        self.assertEqual(1, code)
        self.assertEqual(["forbidden tracked runtime/generated path: src/__pycache__/bad.pyc"], report["errors"])
        self.assertEqual([], forbidden_tracked([".venv.example", "docs/.venv-guide.md"]))

    def test_invalid_saved_profile_cannot_silently_fall_back_to_automatic(self):
        root = fixture_repo(self, {"README.md": "", ".nk-cli.json": "{"})
        code, report = boundary_report(root)
        self.assertEqual(2, code)
        self.assertTrue(report["errors"])

    def test_manifest_version_container_is_rejected_without_type_error(self):
        manifest = {"version": [], "root_files": [], "units": []}
        self.assertIn("unsupported manifest version", validate_manifest(Path("."), manifest, []))

    def test_shell_git_overrides_cannot_redirect_an_explicit_repository(self):
        first = fixture_repo(self, {"first.py": ""})
        second = fixture_repo(self, {"second.py": ""})
        with patch.dict(os.environ, {"GIT_DIR": str(second / ".git"), "GIT_WORK_TREE": str(second)}):
            code, report = boundary_report(first)
        self.assertEqual(0, code)
        self.assertEqual(["first.py"], report["root_files"])

    def test_linked_worktree_is_detected_from_a_nested_directory(self):
        root = fixture_repo(self, {"src/hello.py": ""})
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], check=True, env=env)
        with tempfile.TemporaryDirectory() as temp:
            worktree = Path(temp) / "linked"
            subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", "-q", str(worktree)], check=True, env=env)
            code, report = boundary_report(worktree / "src")
            self.assertEqual(0, code)
            self.assertEqual(str(worktree), report["repo"])
            self.assertEqual(1, report["tracked_files"])

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
