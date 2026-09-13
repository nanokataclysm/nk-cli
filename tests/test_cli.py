from contextlib import chdir, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from helpers import fixture_repo
from nk_cli.cli import main
from nk_cli.portal_doctor import CheckResult


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_saved_analysis_adapts_existing_boundary_reclaim_and_doctor_commands(self):
        root = fixture_repo(self, {
            "Cargo.toml": '[package]\nname="example"\nversion="0.1.0"\n',
            "package.json": '{"scripts":{"dev":"node server.js --port 4321"}}',
            ".gitignore": "target/\n",
        })
        code, out, err = invoke("analyze", "--repo", str(root), "--write-config", "--json")
        self.assertEqual((0, ""), (code, err))
        self.assertTrue(json.loads(out)["config_written"].endswith(".nk-cli.json"))
        code, out, err = invoke("boundaries", "--repo", str(root), "--json")
        self.assertEqual(0, code)
        self.assertEqual(".nk-cli.json", json.loads(out)["mode"])
        (root / "target").mkdir()
        (root / "target/data").write_bytes(b"abcdef")
        code, out, err = invoke("reclaim", "--root", str(root), "--json")
        self.assertEqual(0, code)
        self.assertEqual("target", json.loads(out)["candidates"][0]["kind"])
        with patch("nk_cli.portal_doctor.listener_check", return_value=CheckResult("listener", "verified", "fixture")) as check:
            code, out, err = invoke("doctor", "--repo", str(root), "--json")
        self.assertEqual(0, code)
        self.assertEqual(("127.0.0.1", 4321), check.call_args.args[:2])

    def test_optional_offline_service_does_not_fail_required_checks(self):
        root = fixture_repo(self, {"services.json": json.dumps({"version": "nk-services/v1", "services": [
            {"id": "optional", "port": 4321, "required": False},
        ]})})
        with patch("nk_cli.portal_doctor.listener_check", return_value=CheckResult("listener", "offline", "fixture")):
            for command in ["doctor", "portal-doctor"]:
                code, out, _ = invoke(command, "--manifest", str(root / "services.json"), "--json")
                self.assertEqual(0, code)
                key = "services" if command == "doctor" else "hosts"
                self.assertEqual("offline", json.loads(out)[key][0]["status"])
                if command == "doctor":
                    self.assertNotIn("role", json.loads(out)[key][0])
                    self.assertNotIn("recovery", json.loads(out)[key][0])
            code, _, _ = invoke("doctor", "--port", "4321", "--json")
            self.assertEqual(1, code)

    def test_missing_listeners_do_not_produce_a_false_success(self):
        root = fixture_repo(self, {"README.md": "hello"})
        code, out, err = invoke("doctor", "--repo", str(root), "--json")
        self.assertEqual(2, code)
        self.assertIn("no local listeners", json.loads(out)["errors"][0])
        self.assertEqual("", err)

    def test_json_errors_remain_json(self):
        root = fixture_repo(self, {"README.md": "hello"})
        for args in [("reclaim", "--root", str(root), "--depth", "-1"),
                     ("doctor", "--port", "99999"),
                     ("boundaries", "--repo", str(root), "--manifest", str(root / "absent.json"))]:
            with self.subTest(args=args):
                code, out, err = invoke(*args, "--json")
                self.assertEqual(2, code)
                self.assertTrue(json.loads(out)["errors"])
                self.assertEqual("", err)

    def test_current_directory_and_relative_repo_paths_survive_safe_git_lookup(self):
        root = fixture_repo(self, {"src/main.py": "", "pyproject.toml": "[tool.pytest.ini_options]\n"})
        with chdir(root / "src"):
            for args in [("analyze",), ("analyze", "--repo", "."), ("analyze", "--repo", "..")]:
                code, out, err = invoke(*args, "--json")
                self.assertEqual((0, ""), (code, err), out)
                report = json.loads(out)
                self.assertEqual(root.resolve(), Path(report["repo"]).resolve())
                self.assertIn("tooling", report)
                self.assertIn("push_targets", report)

    def test_tool_preferences_are_saved_and_reused_without_a_fallback(self):
        root = fixture_repo(self, {"README.md": "hello"})
        unavailable = str(root / "missing-agent")
        code, out, err = invoke("analyze", "--repo", str(root), "--agent", unavailable,
                                "--model", "custom/model", "--write-config", "--json")
        self.assertEqual((0, ""), (code, err), out)
        profile = json.loads((root / ".nk-cli.json").read_text())
        self.assertEqual({"agents": [unavailable], "models": ["custom/model"]}, profile["tooling"])
        self.assertNotIn("push_targets", profile)
        code, out, err = invoke("tools", "--repo", str(root), "--json")
        self.assertEqual((1, ""), (code, err), out)
        self.assertEqual([unavailable], json.loads(out)["selected_agents"])
        self.assertEqual(["custom/model"], json.loads(out)["selected_models"])
        code, out, _ = invoke("tools", "--repo", str(root), "--model", "another/model", "--json")
        self.assertEqual(1, code)
        self.assertEqual(["another/model"], json.loads(out)["selected_models"])
        self.assertEqual(["custom/model"], profile["tooling"]["models"])

    def test_targets_cli_sanitizes_explicit_remote_credentials_without_probing(self):
        root = fixture_repo(self, {"README.md": "hello"})
        with patch("nk_cli.network._probe") as probe:
            code, out, err = invoke("targets", "--repo", str(root), "--target",
                                    "https://fixture-user:fixture-secret@push.example.test/private?token=fixture-token", "--json")
        self.assertEqual((0, ""), (code, err), out)
        self.assertNotIn("fixture-secret", out)
        self.assertNotIn("fixture-token", out)
        self.assertEqual("push.example.test", json.loads(out)["targets"][0]["host"])
        probe.assert_not_called()

    def test_invalid_saved_tooling_fails_without_automatic_fallback(self):
        root = fixture_repo(self, {"README.md": "hello", ".nk-cli.json": json.dumps({
            "version": "nk-repository/v1", "tooling": None,
        })})
        code, out, err = invoke("tools", "--repo", str(root), "--json")
        self.assertEqual((2, ""), (code, err), out)
        self.assertTrue(json.loads(out)["errors"])

    def test_analyze_resolves_a_python3_fallback_without_running_it(self):
        root = fixture_repo(self, {"pyproject.toml": "[tool.pytest.ini_options]\n"})
        with patch("nk_cli.cli.executable_on_path", side_effect=lambda name, **kwargs: "/fixture/python3" if name == "python3" else None):
            code, out, err = invoke("analyze", "--repo", str(root), "--json")
        self.assertEqual((0, ""), (code, err), out)
        task = json.loads(out)["projects"][0]["tasks"][0]
        self.assertEqual(["/fixture/python3", "-m", "pytest"], task["command"])
        self.assertTrue(task["executable_available"])
