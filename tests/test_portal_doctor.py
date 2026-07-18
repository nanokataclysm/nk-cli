import tempfile
import unittest
from pathlib import Path

from nk_cli.portal_doctor import ManifestError, inspect, load_manifest


class PortalDoctorTests(unittest.TestCase):
    def test_rejects_secret_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hosts.json"
            path.write_text(
                """
                {
                  "version": "nk-portal-hosts/v1",
                  "hosts": [{
                    "id": "bad",
                    "role": "workstation",
                    "provider": "local",
                    "recovery": "rebuild",
                    "token": "nope"
                  }]
                }
                """,
                encoding="utf-8",
            )
            with self.assertRaises(ManifestError):
                load_manifest(path)

    def test_rejects_tailscale_and_ssh_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hosts.json"
            path.write_text(
                """
                {
                  "version": "nk-portal-hosts/v1",
                  "hosts": [{
                    "id": "mesh",
                    "role": "workstation",
                    "provider": "local",
                    "recovery": "rebuild",
                    "tailscale_name": "nk-dev"
                  }]
                }
                """,
                encoding="utf-8",
            )
            with self.assertRaises(ManifestError):
                load_manifest(path)

    def test_rejects_non_localhost_listener(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hosts.json"
            path.write_text(
                """
                {
                  "version": "nk-portal-hosts/v1",
                  "hosts": [{
                    "id": "remote",
                    "role": "worker",
                    "provider": "cloud",
                    "recovery": "rebuild",
                    "listeners": [{"host": "tailnet", "port": 11436, "bind_class": "tailnet"}]
                  }]
                }
                """,
                encoding="utf-8",
            )
            with self.assertRaises(ManifestError):
                load_manifest(path)

    def test_inspect_localhost_listener(self) -> None:
        manifest = {
            "version": "nk-portal-hosts/v1",
            "hosts": [
                {
                    "id": "local-dev",
                    "role": "workstation",
                    "provider": "local",
                    "recovery": "rebuild",
                    "required": True,
                    "listeners": [{"host": "127.0.0.1", "port": 1, "bind_class": "localhost"}],
                }
            ],
        }
        results = inspect(manifest)
        self.assertEqual(1, len(results))
        self.assertEqual("local-dev", results[0].id)
        self.assertTrue(any(c.check == "listener" for c in results[0].checks))


if __name__ == "__main__":
    unittest.main()
