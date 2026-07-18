import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
