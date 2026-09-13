from contextlib import chdir
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from helpers import fixture_repo
from nk_cli.discovery import _executable_names, executable_at_path, executable_on_path, run_metadata_command
from nk_cli.repository import repository_root


class DiscoveryTests(unittest.TestCase):
    def test_stdout_is_returned_without_running_a_shell(self):
        self.assertEqual(b"a;$(b)\n", run_metadata_command([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a;$(b)\\n')"]))

    def test_oversized_output_stops_with_a_controlled_error(self):
        with self.assertRaisesRegex(ValueError, "output exceeds limit"):
            run_metadata_command([sys.executable, "-c", "print('x' * 10000)"], max_bytes=100)

    def test_timeout_stops_the_command(self):
        with self.assertRaisesRegex(ValueError, "timed out"):
            run_metadata_command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)

    def test_failed_command_does_not_echo_output_or_diagnostics(self):
        with self.assertRaisesRegex(ValueError, "^metadata command failed$"):
            run_metadata_command([sys.executable, "-c", "import sys; print('fixture-private'); sys.stderr.write('fixture-private'); sys.exit(1)"])

    def test_repository_shim_cannot_replace_git_even_from_a_nested_directory(self):
        root = fixture_repo(self, {"src/main.py": ""})
        shim = root / "bin" / ("git.exe" if os.name == "nt" else "git")
        shim.parent.mkdir()
        shim.write_text("repository shim must not run")
        shim.chmod(0o755)
        with chdir(root / "src"), patch.dict(os.environ, {"PATH": str(shim.parent) + os.pathsep + os.environ["PATH"]}):
            self.assertNotEqual(str(shim), executable_on_path("git", repo=root / "src"))
            self.assertEqual(root.resolve(), repository_root(Path.cwd()).resolve())

    def test_relative_and_empty_path_entries_are_not_searched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            candidate = root / ("fixture-agent.exe" if os.name == "nt" else "fixture-agent")
            candidate.write_text("not executed")
            candidate.chmod(0o755)
            with chdir(root), patch.dict(os.environ, {"PATH": os.pathsep.join(["", ".", str(root)])}):
                self.assertIsNone(executable_on_path("fixture-agent"))

    def test_windows_pathext_is_bounded_sanitized_and_case_insensitive(self):
        with patch("nk_cli.discovery._WINDOWS", True), patch.dict(os.environ, {
            "PATHEXT": ";.CMD;.exe;.CMD;.JS;../evil;.BAT;.COM; .EXE ;",
        }):
            self.assertEqual(("agent.cmd", "agent.exe", "agent.bat", "agent.com"), _executable_names("agent"))
            self.assertEqual(("agent.ExE",), _executable_names("agent.ExE"))
        with patch("nk_cli.discovery._WINDOWS", True), patch.dict(os.environ, {"PATHEXT": ";.JS;../evil"}):
            self.assertEqual((), _executable_names("agent"))

    def test_windows_cmd_launcher_wins_over_extensionless_shim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ("npm", "npm.cmd"):
                path = root / name
                path.write_text("not executed")
                path.chmod(0o755)
            with patch("nk_cli.discovery._WINDOWS", True), patch.dict(os.environ, {"PATH": str(root), "PATHEXT": ".EXE;.CMD"}):
                self.assertEqual(str(root / "npm.cmd"), executable_on_path("npm"))
                self.assertEqual(str(root / "npm.cmd"), executable_on_path("npm.cmd"))
                self.assertEqual(str(root / "npm.cmd"), executable_at_path(root / "npm"))
            (root / "npm.cmd").unlink()
            with patch("nk_cli.discovery._WINDOWS", True):
                self.assertIsNone(executable_at_path(root / "npm"))

    def test_windows_metadata_probes_reject_batch_files_without_starting_processes(self):
        with patch("nk_cli.discovery._WINDOWS", True), patch("nk_cli.discovery.subprocess.Popen") as popen:
            for launcher in ("tool.CMD", "tool.bat", "tool", "tool.py"):
                with self.subTest(launcher=launcher), self.assertRaisesRegex(ValueError, "native Windows executable"):
                    run_metadata_command([launcher, "list"])
            popen.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows filesystem case handling")
    def test_windows_path_names_and_explicit_paths_ignore_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            candidate = root / "Mixed-AGENT.CmD"
            candidate.write_text("not executed")
            with patch.dict(os.environ, {"PATH": str(root), "PATHEXT": ".EXE;.CMD"}):
                self.assertEqual(candidate.resolve(), Path(executable_on_path("mixed-agent")).resolve())
                self.assertEqual(candidate.resolve(), Path(executable_at_path(root / "mixed-agent.cmd")).resolve())
