import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from nk_cli.paths import is_link_or_reparse, is_link_stat
from nk_cli.reclaim import scan
from nk_cli.repository import read_metadata


class PathTests(unittest.TestCase):
    def test_unknown_reparse_entries_are_links_even_with_regular_file_mode(self):
        for mode in (stat.S_IFDIR, stat.S_IFREG):
            with self.subTest(mode=mode):
                info = SimpleNamespace(st_mode=mode, st_file_attributes=0x400, st_reparse_tag=0)
                self.assertTrue(is_link_stat(info))
        self.assertTrue(is_link_stat(SimpleNamespace(st_mode=stat.S_IFLNK)))
        self.assertFalse(is_link_stat(SimpleNamespace(st_mode=stat.S_IFDIR)))
        self.assertFalse(is_link_stat(SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x20)))

    def test_missing_entries_are_not_silently_classified_as_ordinary_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                is_link_or_reparse(Path(temporary) / "absent")

    def test_metadata_relative_paths_stay_inside_selected_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text("{}", encoding="utf-8")
            self.assertEqual("{}", read_metadata(root, "package.json"))
            for relative in ("../package.json", str(root.resolve() / "package.json")):
                with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, "leaves repository"):
                    read_metadata(root, relative)

    @unittest.skipIf(os.name == "nt", "Windows junction coverage does not need symlink privileges")
    def test_intermediate_metadata_links_are_rejected_even_inside_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            (root / "project").mkdir(parents=True)
            (root / "project/package.json").write_text("{}", encoding="utf-8")
            (root / "linked").symlink_to(root / "project", target_is_directory=True)
            (root / "cycle").symlink_to(root, target_is_directory=True)
            for relative in ("linked/package.json", "cycle/project/package.json"):
                with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, "linked"):
                    read_metadata(root, relative)
            # The selected root itself may be reached through a system/user alias.
            alias = base / "selected-root"
            alias.symlink_to(root, target_is_directory=True)
            self.assertEqual("{}", read_metadata(alias, "project/package.json"))


@unittest.skipUnless(os.name == "nt", "requires Windows NTFS junctions")
class WindowsJunctionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repo with spaces"
        self.root.mkdir()

    def junction(self, name, target):
        link = self.root / name
        # Junction creation needs neither Developer Mode nor symlink privileges.
        # Fixture-generated paths contain no shell metacharacters. cmd.exe is
        # required because mklink is a built-in, not a standalone executable.
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=True, capture_output=True, timeout=10,
        )
        # Remove the junction entry before TemporaryDirectory walks its target;
        # this also makes cleanup safe for the deliberately cyclic fixtures.
        self.addCleanup(os.rmdir, link)
        self.assertTrue(is_link_or_reparse(link))
        return link

    def test_reclaim_skips_within_outside_and_cyclic_junctions(self):
        outside = self.base / "outside"
        (outside / "node_modules").mkdir(parents=True)
        payload = outside / "node_modules/data"
        payload.write_bytes(b"x" * 1000)
        source = self.root / "source"
        source.mkdir()
        (source / "data").write_bytes(b"x" * 100)
        cache = self.root / ".pytest_cache"
        cache.mkdir()
        (cache / "local").write_bytes(b"123")
        self.junction("node_modules", outside)
        self.junction(".mypy_cache", source)
        self.junction("alias", outside)
        self.junction("cycle", self.root)
        self.junction(".pytest_cache/within", source)
        self.junction(".pytest_cache/outside", outside)
        self.junction(".pytest_cache/cycle", cache)
        found = scan(self.root)
        self.assertEqual(1, len(found))
        self.assertEqual(".pytest_cache", found[0].kind)
        self.assertEqual(3, found[0].size_bytes)
        self.assertFalse(found[0].size_complete)
        self.assertEqual(b"x" * 1000, payload.read_bytes())

    def test_explicitly_selected_junction_root_keeps_resolved_root_semantics(self):
        target = self.base / "outside/.pytest_cache"
        target.mkdir(parents=True)
        (target / "local").write_bytes(b"123")
        selected = self.junction("selected", target)
        found = scan(selected)
        self.assertEqual(1, len(found))
        self.assertEqual(str(target.resolve()), found[0].path)
        self.assertEqual(3, found[0].size_bytes)
        self.assertTrue(found[0].size_complete)

    def test_metadata_rejects_within_outside_and_cyclic_junctions(self):
        inside = self.root / "project"
        outside = self.base / "outside"
        for target in (inside, outside):
            target.mkdir()
            (target / "package.json").write_text("{}", encoding="utf-8")
        self.junction("within", inside)
        self.junction("outside", outside)
        self.junction("cycle", self.root)
        for relative in ("within/package.json", "outside/package.json", "cycle/project/package.json"):
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, "linked"):
                read_metadata(self.root, relative)
        selected = self.junction("selected", inside)
        self.assertEqual("{}", read_metadata(selected, "package.json"))


if __name__ == "__main__":
    unittest.main()
