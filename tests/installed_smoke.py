"""Exercise the installed console entry point on a disposable local repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", default=shutil.which("nk-cli"))
    args = parser.parse_args()
    if not args.cli:
        raise SystemExit("installed nk-cli entry point is missing")
    cli = str(Path(args.cli).resolve())
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "caf\u00e9 visitor's repository"
        root.mkdir()
        (root / "src").mkdir()
        agent = root / ("custom-agent.cmd" if os.name == "nt" else "custom-agent")
        agent.write_text("@exit /b 77\r\n" if os.name == "nt" else "#!/bin/sh\nexit 77\n", encoding="utf-8")
        agent.chmod(0o755)
        (root / "package.json").write_text('{"scripts":{"test":"exit 77"}}', encoding="utf-8")
        (root / "src/main.py").write_text("print('fixture')\n", encoding="utf-8")
        sources = [agent, root / "package.json", root / "src/main.py"]
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
        for options in [("init", "-q"), ("add", "-A")]:
            subprocess.run(["git", "-c", "init.templateDir=", "-C", str(root), *options],
                           env=env, capture_output=True, check=True, timeout=20)

        def invoke(*options: str, expected: int = 0) -> dict:
            result = subprocess.run([cli, *options, "--json"], cwd=root / "src", env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=45)
            assert result.returncode == expected, (options, result.returncode, result.stdout, result.stderr)
            assert not result.stderr, result.stderr
            return json.loads(result.stdout)

        report = invoke("analyze", "--repo", ".", "--agent", str(agent), "--model", "fixture/model", "--write-config")
        assert Path(report["repo"]).resolve() == root
        assert report["command_shell"] == ("powershell" if os.name == "nt" else "posix")
        assert report["tooling"]["selected_models"] == ["fixture/model"]
        assert next(item for item in report["tooling"]["agents"] if item["selected"])["available"]
        profile = (root / ".nk-cli.json").read_bytes()
        invoke("analyze", "--write-config", expected=2)
        assert (root / ".nk-cli.json").read_bytes() == profile
        assert invoke("boundaries")["mode"] == ".nk-cli.json"
        assert invoke("tools")["selected_agents"] == [str(agent)]
        (root / "node_modules").mkdir()
        (root / "node_modules/fixture.txt").write_bytes(b"fixture")
        assert invoke("reclaim")["candidates"][0]["size_bytes"] == 7
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(2)
            port = listener.getsockname()[1]
            target = invoke("targets", "--target", f"127.0.0.1:{port}", "--probe")["targets"][0]
            assert target["reachability"] == "reachable" and target["push_ready"] is None
            assert invoke("doctor", "--port", str(port))["services"][0]["status"] == "verified"
        assert before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    print("PASS: installed CLI, Unicode/quoted paths, profile, tools, boundaries, cache, and loopback targets/services")


if __name__ == "__main__":
    main()
