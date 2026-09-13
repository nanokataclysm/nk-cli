"""Link checks shared by bounded local-file readers and scanners."""

from __future__ import annotations

import os
from pathlib import Path
import stat


def is_link_stat(info: os.stat_result) -> bool:
    """Treat every Windows reparse point as a link, including NTFS junctions.

    Python 3.11 does not expose Path.is_junction(). lstat() does expose the
    reparse attribute, even for kinds that are not classified as symlinks.
    Unknown reparse kinds (including cloud placeholders) are skipped too:
    callers must not read their data or traverse redirected directories.
    """
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def is_link_or_reparse(path: Path) -> bool:
    """Inspect the entry itself; let lstat errors reach the caller."""
    return is_link_stat(path.lstat())
