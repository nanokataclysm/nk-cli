import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nk_cli.reclaim import scan


class ReclaimTests(unittest.TestCase):
    def test_dry_run_finds_node_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nm = root / "proj" / "node_modules"
            nm.mkdir(parents=True)
            (nm / "pkg.js").write_text("x" * 100, encoding="utf-8")
            found = scan(root, max_depth=4)
            kinds = {c.kind for c in found}
            self.assertIn("node_modules", kinds)
            self.assertTrue(all("never deletes" in c.note for c in found))

    def test_blocks_secret_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".nanokat-secrets"
            root.mkdir()
            with self.assertRaises(PermissionError):
                scan(root)

    def test_build_outputs_are_opt_in_and_depth_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ["dist", "build", "src/node_modules"]:
                (root / name).mkdir(parents=True)
                (root / name / "data").write_bytes(b"abc")
            self.assertEqual([], scan(root, max_depth=1))
            self.assertEqual({"node_modules"}, {c.kind for c in scan(root, max_depth=2)})
            self.assertEqual({"dist", "build"}, {c.kind for c in scan(root, max_depth=1, include_names=("dist", "build"))})
            self.assertEqual([], scan(root, max_depth=0))

    def test_symlinked_candidates_and_files_never_count_external_data(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as external:
            root, outside = Path(tmp), Path(external)
            (outside / "data").write_bytes(b"x" * 1000)
            cache = root / ".pytest_cache"
            cache.mkdir()
            (cache / "local").write_bytes(b"123")
            try:
                (root / "node_modules").symlink_to(outside, target_is_directory=True)
                (cache / "external").symlink_to(outside / "data")
            except OSError:
                self.skipTest("symlink creation unavailable")
            results = scan(root)
            self.assertEqual(1, len(results))
            self.assertEqual(3, results[0].size_bytes)
            self.assertFalse(results[0].size_complete)

    def test_sensitive_subtrees_are_pruned_inside_a_matched_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "node_modules"
            (cache / ".aws").mkdir(parents=True)
            (cache / ".aws/data").write_bytes(b"x" * 100)
            (cache / "local").write_bytes(b"123")
            result = scan(root)[0]
            self.assertEqual(3, result.size_bytes)
            self.assertFalse(result.size_complete)

    def test_limit_is_reported_as_partial_and_exclusion_is_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for cache in [root / "node_modules", root / "fixtures/node_modules"]:
                cache.mkdir(parents=True)
                for i in range(3):
                    (cache / str(i)).write_bytes(b"abc")
            result = scan(root, max_files=1, exclude_paths=("fixtures",))
            self.assertEqual(1, len(result))
            self.assertEqual(3, result[0].size_bytes)
            self.assertFalse(result[0].size_complete)

    def test_sensitive_path_matching_uses_components_not_substrings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "my.aws-adapter"
            (root / "node_modules").mkdir(parents=True)
            self.assertEqual(1, len(scan(root)))

    def test_invalid_limits_and_sensitive_cache_overrides_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for options in [{"max_depth": -1}, {"max_files": 0}, {"include_names": ("../outside",)}, {"include_names": (".ssh",)}]:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    scan(Path(tmp), **options)

    def test_unreadable_entries_fail_discovery_but_make_cache_sizes_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "node_modules"
            (cache / "inaccessible").mkdir(parents=True)
            (cache / "local").write_bytes(b"123")
            with patch("nk_cli.reclaim.is_link_or_reparse", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    scan(root)
                found = scan(cache)
            self.assertEqual(1, len(found))
            self.assertEqual(3, found[0].size_bytes)
            self.assertFalse(found[0].size_complete)


if __name__ == "__main__":
    unittest.main()
