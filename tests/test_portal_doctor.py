import unittest
from pathlib import Path
import tempfile

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

        def no_tailscale(args, timeout=5.0):  # noqa: ARG001
            class R:
                returncode = 1
                stdout = ""
                stderr = "not running"

            return R()

        results = inspect(manifest, runner=no_tailscale)
        self.assertEqual(1, len(results))
        self.assertEqual("local-dev", results[0].id)
        self.assertTrue(any(c.check == "listener" for c in results[0].checks))


if __name__ == "__main__":
    unittest.main()
