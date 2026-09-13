import json
from pathlib import Path
import tempfile
import unittest

from helpers import fixture_repo
from nk_cli.analyze import analyze_repository, write_profile
from nk_cli.boundaries import boundary_report
from nk_cli.repository import load_profile


class AnalyzeTests(unittest.TestCase):
    def test_mixed_monorepo_inherits_package_manager_and_keeps_task_directories(self):
        root = fixture_repo(self, {
            "package.json": json.dumps({"private": True, "packageManager": "pnpm@10.0.0"}),
            "apps/store front/package.json": json.dumps({"scripts": {"test:unit": "custom-test", "dev": "vite --port 4100"}}),
            "services/api/pyproject.toml": '[project]\nname = "api"\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "tools/parser/Cargo.toml": '[package]\nname = "parser"\nversion = "0.1.0"\n',
            "workers/go.mod": "module example.invalid/worker\n\ngo 1.22\n",
        })
        report = analyze_repository(root / "apps/store front")
        projects = {p["path"]: p for p in report["projects"]}
        self.assertEqual(str(root), report["repo"])
        node = projects["apps/store front"]
        self.assertEqual("pnpm", node["package_manager"])
        self.assertIn(["pnpm", "run", "test:unit"], [t["command"] for t in node["tasks"]])
        self.assertEqual(["python", "-m", "pytest"], projects["services/api"]["tasks"][0]["command"])
        self.assertIn("rust", projects["tools/parser"]["ecosystems"])
        self.assertIn("go", projects["workers"]["ecosystems"])
        self.assertEqual(["target"], report["suggested_config"]["cache_names"])
        self.assertEqual(4100, report["suggested_config"]["services"][0]["port"])
        self.assertFalse((root / ".nk-cli.json").exists())

    def test_analysis_does_not_run_scripts_or_copy_their_contents_to_profile(self):
        root = fixture_repo(self, {
            "package.json": json.dumps({"scripts": {"test": "touch executed-marker; echo fixture-private", "postinstall": "touch installed-marker"}}),
            ".env": "PRIVATE_FIXTURE=do-not-copy\n",
        })
        report = analyze_repository(root)
        self.assertNotIn("fixture-private", json.dumps(report))
        self.assertNotIn("do-not-copy", json.dumps(report))
        self.assertFalse((root / "executed-marker").exists())
        self.assertFalse((root / "installed-marker").exists())
        self.assertEqual(["test"], [t["name"] for t in report["projects"][0]["tasks"]])

    def test_profile_is_portable_accepted_and_never_overwritten(self):
        root = fixture_repo(self, {"src/main.py": "pass\n", "README.md": "hello"})
        report = analyze_repository(root)
        destination = write_profile(report)
        before = destination.read_bytes()
        self.assertNotIn(str(root), destination.read_text())
        self.assertEqual(0, boundary_report(root)[0])
        self.assertEqual(".nk-cli.json", boundary_report(root)[1]["mode"])
        with self.assertRaises(FileExistsError):
            write_profile(report)
        self.assertEqual(before, destination.read_bytes())
        self.assertEqual(["src"], load_profile(root)["units"])

    def test_conflicting_lockfiles_do_not_guess_a_manager(self):
        root = fixture_repo(self, {
            "package.json": '{"scripts":{"test":"echo ok"}}', "yarn.lock": "", "pnpm-lock.yaml": "",
        })
        report = analyze_repository(root)
        self.assertIsNone(report["projects"][0]["package_manager"])
        self.assertEqual([], report["projects"][0]["tasks"])
        self.assertTrue(any("conflicting lockfiles" in w for w in report["warnings"]))

    def test_new_untracked_manifest_is_seen_but_ignored_dependencies_are_not(self):
        root = fixture_repo(self, {".gitignore": "node_modules/\n"})
        (root / "package.json").write_text('{"scripts":{"lint":"lint"}}')
        (root / "node_modules").mkdir()
        (root / "node_modules/package.json").write_text('{"scripts":{"test":"ignored"}}')
        report = analyze_repository(root)
        self.assertEqual(["."], [p["path"] for p in report["projects"]])
        self.assertEqual(["lint"], [t["name"] for t in report["projects"][0]["tasks"]])

    def test_invalid_metadata_is_reported_without_echoing_its_contents(self):
        root = fixture_repo(self, {"package.json": '{"secret": fixture-private', "pyproject.toml": "[broken"})
        report = analyze_repository(root)
        self.assertEqual([], report["projects"])
        self.assertEqual(2, len(report["warnings"]))
        self.assertNotIn("fixture-private", json.dumps(report))

    def test_symlinked_metadata_is_not_read(self):
        root = fixture_repo(self, {"README.md": "hello"})
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "package.json"
            external.write_text('{"scripts":{"test":"outside"}}')
            try:
                (root / "package.json").symlink_to(external)
            except OSError:
                self.skipTest("symlink creation unavailable")
            report = analyze_repository(root)
        self.assertEqual([], report["projects"])
        self.assertTrue(report["warnings"])

    def test_python_does_not_assume_a_test_framework_from_directory_name(self):
        root = fixture_repo(self, {"pyproject.toml": '[project]\nname="thing"\n', "tests/test_thing.py": "pass"})
        report = analyze_repository(root)
        self.assertEqual([], report["projects"][0]["tasks"])
        self.assertTrue(any("no Python task guessed" in w for w in report["warnings"]))

    def test_protected_metadata_is_not_analyzed(self):
        root = fixture_repo(self, {".aws/package.json": '{"scripts":{"test":"private"}}'})
        report = analyze_repository(root)
        self.assertEqual([], report["projects"])
        self.assertTrue(report["warnings"])

    def test_cmake_and_make_tasks_keep_their_project_directory(self):
        root = fixture_repo(self, {"native/CMakeLists.txt": "project(Example)\n", "tools/Makefile": "test:\n\ttrue\n"})
        projects = {p["path"]: p for p in analyze_repository(root)["projects"]}
        self.assertIn(["cmake", "--build", "build"], [t["command"] for t in projects["native"]["tasks"]])
        self.assertEqual(["make", "test"], projects["tools"]["tasks"][0]["command"])
