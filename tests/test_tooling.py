import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nk_cli.tooling import discover_tools, validate_tooling_profile


class ToolingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.environment = patch.dict(os.environ, {"PATH": str(self.bin)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def executable(self, name, directory=None):
        path = (directory or self.bin) / name
        path.write_text("#!/bin/sh\nexit 77\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_known_and_pattern_tools_are_detected_without_execution(self):
        self.executable("codex")
        self.executable("custom-agent")
        self.executable("unrelated")
        self.executable("ssh-agent")
        with patch("nk_cli.tooling.run_metadata_command") as runner:
            report = discover_tools(self.repo)
        runner.assert_not_called()
        self.assertEqual(["codex", "custom-agent"], [entry["id"] for entry in report["agents"]])
        self.assertEqual(["known-tool", "name-match-unverified"], [entry["evidence"] for entry in report["agents"]])
        self.assertEqual([], report["selected_agents"])
        self.assertEqual([], report["models"])
        self.assertEqual("not-requested", report["model_catalog"]["status"])

    def test_explicit_missing_tool_does_not_select_available_fallback(self):
        self.executable("codex")
        report = discover_tools(self.repo, agents=("missing-agent",), profile={"agents": ["codex"]})
        entries = {entry["id"]: entry for entry in report["agents"]}
        self.assertEqual(["missing-agent"], report["selected_agents"])
        self.assertFalse(entries["missing-agent"]["available"])
        self.assertTrue(entries["missing-agent"]["selected"])
        self.assertFalse(entries["codex"]["selected"])
        self.assertTrue(report["warnings"])

    def test_saved_preferences_are_used_and_explicit_model_ids_replace_them(self):
        self.executable("custom-agent")
        report = discover_tools(self.repo, models=("vendor/custom-model:v2",), profile={"agents": ["custom-agent"], "models": ["old-model"]})
        self.assertEqual(["custom-agent"], report["selected_agents"])
        self.assertEqual(["vendor/custom-model:v2"], report["selected_models"])
        self.assertEqual({"agents": "profile", "models": "specified"}, report["selection_source"])
        self.assertEqual("specified-unverified", report["models"][0]["status"])
        self.assertFalse(report["models"][0]["verified"])

    def test_custom_explicit_path_can_select_repo_executable(self):
        local_bin = self.repo / "my scripts"
        local_bin.mkdir()
        path = self.executable("assistant", local_bin)
        report = discover_tools(self.repo, agents=("./my scripts/assistant",))
        self.assertEqual(str(path), report["agents"][0]["executable"])
        self.assertTrue(report["agents"][0]["available"])

    def test_repo_and_relative_path_entries_are_not_automatically_used(self):
        local_bin = self.repo / "bin"
        local_bin.mkdir()
        self.executable("codex", local_bin)
        self.executable("unsafe-agent", local_bin)
        with patch.dict(os.environ, {"PATH": os.pathsep.join([str(local_bin), ".", "bin"])}):
            report = discover_tools(self.repo)
        self.assertEqual([], report["agents"])

    def test_model_listing_is_local_opt_in_and_does_not_verify_inference(self):
        executable = self.executable("ollama")
        catalog = b"NAME                     ID              SIZE      MODIFIED\nexample/model:small      0123456789ab    1.2 GB    2 hours ago\n"
        with patch.dict(os.environ, {"OLLAMA_HOST": "https://private.invalid", "HTTPS_PROXY": "https://proxy.invalid"}):
            with patch("nk_cli.tooling.run_metadata_command", return_value=catalog) as runner:
                report = discover_tools(self.repo, list_models=True, models=("example/model:small",))
        self.assertEqual([str(executable), "list"], runner.call_args.args[0])
        env = runner.call_args.kwargs["env"]
        self.assertEqual("http://127.0.0.1:11434", env["OLLAMA_HOST"])
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertEqual("listed-local", report["model_catalog"]["status"])
        self.assertEqual("specified", report["models"][0]["source"])
        self.assertEqual("listed-local", report["models"][0]["catalog_status"])
        self.assertFalse(report["models"][0]["verified"])
        self.assertEqual(["example/model:small"], report["selected_models"])

    def test_other_tool_catalog_is_not_guessed_or_executed(self):
        self.executable("llm")
        with patch("nk_cli.tooling.run_metadata_command") as runner:
            report = discover_tools(self.repo, list_models=True, models=("my-future-model",))
        runner.assert_not_called()
        self.assertEqual("unavailable", report["model_catalog"]["status"])
        self.assertEqual("my-future-model", report["models"][0]["id"])
        self.assertFalse(report["models"][0]["verified"])

    def test_invalid_catalog_output_is_not_echoed(self):
        self.executable("ollama")
        for output in (b"fixture-secret", b"NAME ID SIZE MODIFIED\nfixture-secret\n", b"\xff"):
            with self.subTest(output=output), patch("nk_cli.tooling.run_metadata_command", return_value=output):
                report = discover_tools(self.repo, list_models=True)
            self.assertEqual("unavailable", report["model_catalog"]["status"])
            self.assertNotIn("fixture-secret", str(report))
            self.assertEqual([], report["models"])

    def test_catalog_errors_are_reported_without_details(self):
        self.executable("ollama")
        with patch("nk_cli.tooling.run_metadata_command", side_effect=ValueError("fixture-secret")):
            report = discover_tools(self.repo, list_models=True)
        self.assertEqual("unavailable", report["model_catalog"]["status"])
        self.assertNotIn("fixture-secret", str(report))

    def test_empty_catalog_is_valid_and_installed_models_are_not_selected(self):
        self.executable("ollama")
        with patch("nk_cli.tooling.run_metadata_command", return_value=b"NAME ID SIZE MODIFIED\n"):
            report = discover_tools(self.repo, list_models=True)
        self.assertEqual("listed-local", report["model_catalog"]["status"])
        self.assertEqual([], report["models"])
        with patch("nk_cli.tooling.run_metadata_command", return_value=b"NAME ID SIZE MODIFIED\nexample:latest abcdef123456 12 MB yesterday\n"):
            report = discover_tools(self.repo, list_models=True)
        self.assertEqual([], report["selected_models"])
        self.assertFalse(report["models"][0]["selected"])

    def test_profile_validation_is_strict_bounded_and_deduplicates(self):
        self.assertEqual({"agents": ["custom-agent"], "models": []}, validate_tooling_profile({"agents": ["custom-agent", "custom-agent"]}))
        for profile in ([], {"command": "run"}, {"agents": "codex"}, {"agents": ("codex",)}, {"models": [True]}, {"models": [""]}, {"models": ["x\nsecret"]}, {"models": [" x"]}, {"models": ["x" * 257]}, {"agents": ["x"] * 65}):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                validate_tooling_profile(profile)

    def test_explicit_selector_validation_rejects_strings_controls_and_limits(self):
        for values in ("codex", ("agent\x1b[1m",), ("x" * 513,), (None,), ("x",) * 65):
            with self.subTest(values=values), self.assertRaises(ValueError):
                discover_tools(self.repo, agents=values)
