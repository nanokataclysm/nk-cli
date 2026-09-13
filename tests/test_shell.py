import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from nk_cli.shell import command_shell, format_command


class ShellTests(unittest.TestCase):
    def test_default_shell_matches_platform(self):
        self.assertEqual("powershell" if os.name == "nt" else "posix", command_shell())

    def test_posix_roundtrip_preserves_literal_arguments(self):
        argv = ["/path with spaces/python", "person's project", "$value;$(cmd)", ""]
        self.assertEqual(argv, shlex.split(format_command(argv, shell="posix")))

    def test_powershell_uses_call_operator_and_literal_quoting(self):
        self.assertEqual("& 'C:\\Program Files\\agent.exe' 'person''s project' '$value;`cmd'",
                         format_command([r"C:\Program Files\agent.exe", "person's project", "$value;`cmd"], shell="powershell"))

    def test_powershell_does_not_suggest_arguments_legacy_binding_would_change(self):
        for argv in [["tool.exe", ""], ["tool.exe", 'embedded"quote'], ["tool.cmd", "a&b"], ["tool.exe", "line\nbreak"]]:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                format_command(argv, shell="powershell")

    @unittest.skipUnless(os.name == "nt", "requires native Windows PowerShell")
    def test_powershell_suggestion_executes_with_literal_paths_and_arguments(self):
        # Fictional fixture only: verify the rendered command with the real shell.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "a visitor's project"
            root.mkdir()
            script = root / "show args.py"
            script.write_text("import json, sys; print(json.dumps(sys.argv[1:]))", encoding="utf-8")
            args = ["space here", "apostrophe's", "$never;`execute&", "plain"]
            command = format_command([sys.executable, str(script), *args], shell="powershell")
            process = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                                     capture_output=True, text=True, timeout=15, check=True)
            self.assertEqual(args, json.loads(process.stdout))

    @unittest.skipUnless(os.name == "nt", "requires native Windows batch launchers")
    def test_powershell_suggestion_supports_npm_style_cmd_launchers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "a visitor's project"
            root.mkdir()
            (root / "show args.py").write_text("import json, sys; print(json.dumps(sys.argv[1:]))", encoding="utf-8")
            launcher = root / "package-manager.cmd"
            launcher.write_text(f'@"{sys.executable}" "%~dp0show args.py" %*\n', encoding="utf-8")
            args = ["run", "test:unit", "--", "path with spaces", "person's project"]
            command = format_command([str(launcher), *args], shell="powershell")
            process = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                                     capture_output=True, text=True, timeout=15, check=True)
            self.assertEqual(args, json.loads(process.stdout))
