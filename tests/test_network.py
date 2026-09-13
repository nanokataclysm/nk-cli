import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import Mock, patch

from helpers import fixture_repo
from nk_cli.network import MAX_PROBES, MAX_TARGETS, classify_network, discover_targets, parse_target


class NetworkTests(unittest.TestCase):
    def test_supported_endpoint_forms_discard_userinfo_paths_and_tokens(self):
        cases = [
            ("ssh://user:password@example.org:2222/private/repo", "example.org", 2222, "ssh"),
            ("https://token:secret@example.org/private?access_token=secret#fragment", "example.org", 443, "https"),
            ("git@example.org:private/repo.git", "example.org", 22, "ssh"),
            ("example.org:private/repo.git", "example.org", 22, "ssh"),
            ("example.org:2022", "example.org", 2022, "ssh"),
            ("[2001:4860::1]:2222", "2001:4860::1", 2222, "ssh"),
            ("git@[2001:4860::1]:private/repo.git", "2001:4860::1", 22, "ssh"),
            ("ssh://[2001:4860::1]:2222/private", "2001:4860::1", 2222, "ssh"),
            ("::1", "::1", 22, "ssh"),
            ("2001:db8::1", "2001:db8::1", 22, "ssh"),
            ("LOCALHOST.", "localhost", 22, "ssh"),
        ]
        for value, host, port, transport in cases:
            with self.subTest(value=value):
                self.assertEqual({"host": host, "port": port, "transport": transport}, parse_target(value))

    def test_bad_targets_fail_before_metadata_commands_or_sockets(self):
        for value in ("-bad", "", "x\n", "x\x00", "file:///tmp/repo", "ext::helper", "/local/repo", "https://",
                      "host:0", "host:65536", "ssh://host:0", "[::1]:65536", "0.0.0.0", "224.0.0.1", "fe80::1%eth0", "C:\\private\\repo"):
            with self.subTest(value=value), patch("nk_cli.network._command") as command, patch("nk_cli.network._probe") as probe:
                with self.assertRaises(ValueError):
                    discover_targets(Path("/fixture"), targets=(value,), lan=True, tailscale=True, probe=True)
                command.assert_not_called()
                probe.assert_not_called()

    def test_shared_ip_range_is_not_assumed_to_be_tailscale(self):
        for address, network in (("192.168.1.4", "lan"), ("172.16.1.4", "lan"), ("10.1.2.3", "lan"),
                                 ("fd00::1", "lan"), ("127.0.0.1", "loopback"), ("::1", "loopback"),
                                 ("169.254.1.1", "link-local"), ("100.64.1.1", "shared-address-space"),
                                 ("8.8.8.8", "wan"), ("example.org", "unresolved"), ("192.0.2.1", "special-use")):
            with self.subTest(address=address):
                self.assertEqual(network, classify_network(address))
        self.assertEqual("tailscale", classify_network("100.64.1.1", {"100.64.1.1"}))

    def test_default_is_passive_and_no_repo_is_supported(self):
        with patch("nk_cli.network._command") as command, patch("nk_cli.network._probe") as probe:
            self.assertEqual({"targets": [], "warnings": []}, discover_targets(None))
            report = discover_targets(None, targets=("host.example",))
        command.assert_not_called()
        probe.assert_not_called()
        self.assertEqual("not-probed", report["targets"][0]["reachability"])
        self.assertIsNone(report["targets"][0]["push_ready"])

    def test_git_pushurls_override_fetch_urls_and_keep_multiple_routes(self):
        root = fixture_repo(self, {"README.md": "example"})
        with (root / ".git" / "config").open("a", encoding="utf-8") as config:
            config.write('\n[remote "origin"]\nurl = https://private:token@fetch.example/private?secret=x\n'
                         'pushurl = git@push1.example:team/repository\npushurl = ssh://secret@push2.example:2222/private\n'
                         '[remote "backup"]\nurl = https://token@backup.example/private\n')
        with patch("nk_cli.network._probe") as probe:
            report = discover_targets(root)
        probe.assert_not_called()
        self.assertEqual(["push1.example", "push2.example", "backup.example"], [r["host"] for r in report["targets"]])
        self.assertEqual(["pushurl", "pushurl", "url"], [r["route"] for r in report["targets"]])
        self.assertFalse(report["warnings"])
        serialized = json.dumps(report)
        for private in ("private", "token", "secret", "fetch.example", "team/repository"):
            self.assertNotIn(private, serialized)

    def test_git_repository_without_remotes_is_normal(self):
        root = fixture_repo(self, {"README.md": "example"})
        self.assertEqual({"targets": [], "warnings": []}, discover_targets(root))

    def test_git_ambient_configuration_and_includes_are_disabled(self):
        output = b"remote.origin.url\ngit@example.org:private\0"
        with patch.dict(os.environ, {"GIT_DIR": "/wrong", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "bad"}):
            with patch("nk_cli.network.executable_on_path", return_value="/usr/bin/git"), patch("nk_cli.network.run_metadata_command", return_value=output) as run:
                report = discover_targets(Path("/fixture"))
        argv = run.call_args.args[0]
        self.assertIn("--no-includes", argv)
        self.assertIn("--local", argv)
        self.assertIn("core.fsmonitor=false", argv)
        self.assertIn(f"core.hooksPath={os.devnull}", argv)
        for key in ("GIT_DIR", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
            self.assertNotIn(key, run.call_args.kwargs["env"])
        self.assertEqual("example.org", report["targets"][0]["host"])

    def test_unsupported_git_remotes_are_omitted_without_raw_values(self):
        output = (b"remote.origin.url\nfile:///private/path\0remote.backup.url\next::private-helper\0"
                  b"remote.local.url\nprivate.git\0remote.windows.url\nC:\\private\\repo\0"
                  b"remote.relative.url\n../private\0")
        with patch("nk_cli.network._command", return_value=output):
            report = discover_targets(Path("/fixture"))
        self.assertEqual([], report["targets"])
        self.assertTrue(report["warnings"])
        self.assertNotIn("private", json.dumps(report))

    def test_git_numeric_scp_path_is_not_a_port(self):
        output = b"remote.origin.url\nhost.example:2222\0"
        with patch("nk_cli.network._command", return_value=output):
            target = discover_targets(Path("/fixture"))["targets"][0]
        self.assertEqual("host.example", target["host"])
        self.assertEqual(22, target["port"])

    def test_missing_tools_do_not_discard_explicit_targets(self):
        with patch("nk_cli.network.executable_on_path", return_value=None):
            report = discover_targets(Path("/fixture"), targets=("127.0.0.1:22",), lan=True, tailscale=True)
        self.assertEqual(1, len(report["targets"]))
        self.assertEqual(3, len(report["warnings"]))

    def test_linux_neighbors_are_passive_observations_only(self):
        rows = [{"dst": "192.168.1.2", "state": ["STALE"], "lladdr": "private"},
                {"dst": "fe80::1234", "state": ["REACHABLE"]}, {"dst": "192.168.1.2"},
                {"dst": "224.0.0.1"}, {"dst": "not-an-ip"}]
        with patch("nk_cli.network.platform.system", return_value="Linux"), patch("nk_cli.network._command", return_value=json.dumps(rows).encode()) as command:
            report = discover_targets(None, lan=True)
        command.assert_called_once_with("ip", ["-j", "neigh", "show"], None)
        self.assertEqual(["192.168.1.2", "fe80::1234"], [r["host"] for r in report["targets"]])
        for target in report["targets"]:
            self.assertEqual("cached-neighbor", target["observation"])
            self.assertEqual("not-probed", target["reachability"])
            self.assertEqual("conventional-ssh", target["port_basis"])
        self.assertNotIn("private", json.dumps(report))

    def test_cached_arp_fallback_for_macos_and_windows(self):
        cases = [("Darwin", b"? (192.168.1.3) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"),
                 ("Windows", b"Interface: 192.168.1.1 --- 0x5\n Internet Address Physical Address Type\n 192.168.1.3 aa-bb-cc-dd-ee-ff dynamic\n")]
        for system, output in cases:
            with self.subTest(system=system), patch("nk_cli.network.platform.system", return_value=system), patch("nk_cli.network._command", return_value=output):
                report = discover_targets(None, lan=True)
                self.assertEqual(["192.168.1.3"], [r["host"] for r in report["targets"]])
                self.assertTrue(report["warnings"])

    def test_tailscale_peers_classify_addresses_without_claiming_ssh_access(self):
        status = {"BackendState": "Running", "Self": {"TailscaleIPs": ["100.64.1.1"]},
                  "Peer": {"private-node-id": {"HostName": "private-hostname", "TailscaleIPs": ["100.64.1.2", "fd7a:115c:a1e0::2"], "Online": True},
                           "offline": {"TailscaleIPs": ["100.64.1.3"], "Online": False}}}
        with patch("nk_cli.network._command", return_value=json.dumps(status).encode()) as command:
            report = discover_targets(None, targets=("100.64.1.2:2222",), tailscale=True)
        command.assert_called_once_with("tailscale", ["status", "--json"], None)
        self.assertEqual(4, len(report["targets"]))
        self.assertTrue(all(row["network"] == "tailscale" for row in report["targets"]))
        self.assertFalse(report["targets"][-1]["peer_online"])
        self.assertTrue(all(row["reachability"] == "not-probed" for row in report["targets"]))
        self.assertNotIn("private", json.dumps(report))

    def test_localized_arp_headers_do_not_hide_ascii_neighbor_addresses(self):
        cases = [("Windows", b"Interface: 192.168.1.1 --- 0x5\r\nAdresse r\x82seau\r\n 192.168.1.3 aa-bb-cc-dd-ee-ff dynamique\r\n"),
                 ("Darwin", b"h\xf4te (192.168.1.3) at aa:bb:cc:dd:ee:ff on en0\n")]
        for system, output in cases:
            with self.subTest(system=system), patch("nk_cli.network.platform.system", return_value=system), patch("nk_cli.network._command", return_value=output):
                report = discover_targets(None, lan=True)
                self.assertEqual(["192.168.1.3"], [target["host"] for target in report["targets"]])
                self.assertTrue(all(target["reachability"] == "not-probed" for target in report["targets"]))

    def test_malformed_network_metadata_keeps_other_results(self):
        for value in (b"not json", b"[]", b'{"Peer": []}'):
            with self.subTest(value=value), patch("nk_cli.network._command", return_value=value):
                report = discover_targets(None, targets=("example.org",), tailscale=True)
                self.assertEqual(1, len(report["targets"]))
                self.assertTrue(report["warnings"])
        with patch("nk_cli.network.platform.system", return_value="Linux"), patch("nk_cli.network._command", return_value=b"{}"):
            self.assertTrue(discover_targets(None, lan=True)["warnings"])

    def test_bad_optional_tailscale_fields_do_not_crash_or_authenticate(self):
        state = {"BackendState": {}, "Self": None, "Peer": {"a": {"TailscaleIPs": {}},
                 "b": {"TailscaleIPs": [False, "100.64.1.2"], "Online": "true"}}}
        with patch("nk_cli.network._command", return_value=json.dumps(state).encode()) as command:
            report = discover_targets(None, tailscale=True)
        command.assert_called_once_with("tailscale", ["status", "--json"], None)
        self.assertEqual(1, len(report["targets"]))
        self.assertIsNone(report["targets"][0]["peer_online"])
        self.assertTrue(report["warnings"])

    def test_tcp_success_does_not_claim_push_readiness_and_updates_network(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        # A large fixed monotonic value reproduces floating-point subtraction
        # rounding above 0.6 seconds on Windows and other platforms.
        with patch("nk_cli.network.time.monotonic", return_value=10000.0), patch("nk_cli.network._resolve_host", return_value=[(socket.AF_INET, ("192.168.1.2", 2222))]), patch("nk_cli.network.socket.socket", return_value=connection):
            report = discover_targets(None, targets=("host.example:2222",), probe=True)
        target = report["targets"][0]
        self.assertEqual("reachable", target["reachability"])
        self.assertEqual("lan", target["network"])
        self.assertIsNone(target["push_ready"])
        connection.connect.assert_called_once_with(("192.168.1.2", 2222))
        self.assertLessEqual(connection.settimeout.call_args.args[0], 0.6)

    def test_dns_failure_and_connection_refusal_are_distinct(self):
        with patch("nk_cli.network._resolve_host", return_value=[]):
            target = discover_targets(None, targets=("host.example",), probe=True)["targets"][0]
        self.assertEqual("unresolved", target["reachability"])
        with patch("nk_cli.network.socket.socket", side_effect=OSError):
            target = discover_targets(None, targets=("127.0.0.1:1",), probe=True)["targets"][0]
        self.assertEqual("unreachable", target["reachability"])

    def test_duplicate_endpoints_share_probe_and_probe_count_is_bounded(self):
        values = tuple(f"10.0.0.{index}:22" for index in range(1, MAX_PROBES + 3))
        with patch("nk_cli.network._probe", return_value={"reachability": "unreachable", "resolved_addresses": []}) as probe:
            report = discover_targets(None, targets=(values[0], *values), probe=True)
        self.assertEqual(MAX_PROBES, probe.call_count)
        self.assertEqual(2, sum(row["reachability"] == "skipped-limit" for row in report["targets"]))
        self.assertTrue(report["warnings"])

    def test_explicit_and_discovered_candidate_counts_are_bounded(self):
        with self.assertRaises(ValueError):
            discover_targets(None, targets=("example.org",) * (MAX_TARGETS + 1))
        status = {"Peer": {str(index): {"TailscaleIPs": [f"100.64.{index // 256}.{index % 256}"]}
                           for index in range(1, MAX_TARGETS + 2)}}
        with patch("nk_cli.network._command", return_value=json.dumps(status).encode()):
            report = discover_targets(None, tailscale=True)
        self.assertEqual(MAX_TARGETS, len(report["targets"]))
        self.assertTrue(report["warnings"])


if __name__ == "__main__":
    unittest.main()
