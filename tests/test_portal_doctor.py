import tempfile
import unittest
from unittest.mock import Mock
from pathlib import Path

from nk_cli.portal_doctor import ManifestError, inspect, load_manifest, normalize_manifest


class PortalDoctorTests(unittest.TestCase):
    def test_simple_service_needs_no_operator_metadata(self):
        connector = Mock()
        results = inspect({"version": "nk-services/v1", "services": [{"id": "api", "port": 8123}]}, connector=connector)
        self.assertEqual("verified", results[0].status)
        connector.assert_called_once_with(("127.0.0.1", 8123), timeout=0.3)
        connector.return_value.close.assert_called_once()

    def test_loopback_ipv6_and_localhost_are_literal_local_targets(self):
        for host, expected in [("::1", "::1"), ("localhost", "127.0.0.1")]:
            with self.subTest(host=host):
                connector = Mock()
                inspect({"version": "nk-services/v1", "services": [{"id": "api", "port": 8123, "host": host}]}, connector=connector)
                connector.assert_called_once_with((expected, 8123), timeout=0.3)

    def test_direct_call_rejects_remote_or_malformed_input_before_connecting(self):
        for fields in [{"host": "example.invalid"}, {"host": ["127.0.0.1"]}, {"port": True}, {"port": 0}, {"port": 65536}, {"required": "false"}]:
            connector = Mock()
            with self.subTest(fields=fields), self.assertRaises(ManifestError):
                inspect({"version": "nk-services/v1", "services": [{"id": "api", "port": 8123, **fields}]}, connector=connector)
            connector.assert_not_called()

    def test_bad_containers_are_controlled_validation_errors(self):
        for manifest in [
            {"version": [], "hosts": []},
            {"version": "nk-portal-hosts/v1", "hosts": [{"id": "api", "listeners": None}]},
            {"version": "nk-services/v1", "services": {}},
        ]:
            with self.subTest(manifest=manifest), self.assertRaises(ManifestError):
                normalize_manifest(manifest)

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
