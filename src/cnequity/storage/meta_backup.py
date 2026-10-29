"""Daily copy of the metadata the curated lake cannot rebuild.

The manifest DB, incremental state, revision and source receipts, quality
findings and the operational acceptance evidence. Curated Parquet, revision
generations (``meta/revisions/data``), caches and runtime locks stay out: they
are large or reproducible, and portable snapshots (`cne snapshot`) cover
curated data.

This used to be ``scripts/scheduler/backup_meta.sh``, which only a repository
checkout had, so a lake scheduled from a pip install — `cne init --schedule`,
the dashboard, `cne schedule install` — never had its manifest backed up. It
also needed the ``sqlite3`` CLI for a consistent copy and fell back to a plain
file copy, torn under a concurrent write; the standard library's backup API
needs neither.
"""

from __future__ import annotations

import sqlite3
import sys
import tarfile
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cnequity.storage.atomic import replace_with_retry

#: Subtrees of ``meta/`` worth keeping. ``revisions/`` is kept for its receipts
#: (``{dataset}/*.json``), which name the generation a dataset points at.
INCLUDED = ("state", "quality", "revisions", "source_snapshots", "source_health", "stability")
EXCLUDED = ("revisions/data",)
PREFIX = "meta-"
SUFFIX = ".tar.gz"


@dataclass
class BackupResult:
    archive: Path
    size_bytes: int
    kept: int
    removed: list[Path] = field(default_factory=list)


def _excluded(name: str) -> bool:
    return any(name == item or name.startswith(item + "/") for item in EXCLUDED)


def _archive_path(backup_dir: Path, now: datetime) -> Path:
    stamp = now.strftime("%Y%m%d-%H%M%S")
    path = backup_dir / f"{PREFIX}{stamp}{SUFFIX}"
    number = 2
    while path.exists():
        path = backup_dir / f"{PREFIX}{stamp}-{number}{SUFFIX}"
        number += 1
    return path


def rotate(backup_dir: Path, *, retention_days: int, retention_count: int) -> list[Path]:
    """Drop archives older than *retention_days*, then all but the newest *retention_count*.

    Age alone assumed one archive a day; a caller that runs more often keeps
    every copy until the window expires, so the count cap removes the extras.
    Zero disables either bound.
    """
    archives = sorted(
        (p for p in backup_dir.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    cutoff = time.time() - retention_days * 86400
    for index, path in enumerate(archives):
        too_old = retention_days > 0 and path.stat().st_mtime < cutoff
        too_many = retention_count > 0 and index >= retention_count
        if too_old or too_many:
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def backup_meta(
    meta_root: Path,
    backup_dir: Path,
    *,
    retention_days: int = 14,
    retention_count: int = 30,
    now: datetime | None = None,
) -> BackupResult:
    """Write one ``meta-<stamp>.tar.gz`` under *backup_dir* and rotate the old ones.

    Raises:
        FileNotFoundError: *meta_root* does not exist.
        ValueError: there is nothing under it to back up.
    """
    meta_root = Path(meta_root)
    if not meta_root.is_dir():
        raise FileNotFoundError(f"meta directory not found: {meta_root}")
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = meta_root / "manifest.db"
    members = [sub for sub in INCLUDED if (meta_root / sub).exists()]
    if not manifest.is_file() and not members:
        raise ValueError(f"nothing to back up under {meta_root}")

    archive = _archive_path(backup_dir, now or datetime.now())
    with tempfile.TemporaryDirectory(prefix="cne-meta-backup-") as scratch:
        partial = backup_dir / f".{archive.name}.partial"
        try:
            with tarfile.open(partial, "w:gz") as tar:
                if manifest.is_file():
                    copy = Path(scratch) / "manifest.db"
                    # The backup API copies a consistent snapshot even while a
                    # run is writing; a file copy can catch a torn page.
                    with (
                        closing(sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)) as src,
                        closing(sqlite3.connect(copy)) as dst,
                    ):
                        src.backup(dst)
                    tar.add(copy, arcname="manifest.db")
                for sub in members:
                    tar.add(
                        meta_root / sub,
                        arcname=sub,
                        filter=lambda info: None if _excluded(info.name) else info,
                    )
            replace_with_retry(partial, archive)
        finally:
            partial.unlink(missing_ok=True)
    removed = rotate(backup_dir, retention_days=retention_days, retention_count=retention_count)
    kept = sum(1 for p in backup_dir.glob(f"{PREFIX}*{SUFFIX}") if p.is_file())
    return BackupResult(
        archive=archive, size_bytes=archive.stat().st_size, kept=kept, removed=removed
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m cnequity.storage.meta_backup DATA_ROOT [BACKUP_DIR] [DAYS] [COUNT]``.

    The calling convention of the old ``scripts/scheduler/backup_meta.sh``,
    which now delegates here.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Back up a lake's non-rebuildable metadata.")
    parser.add_argument("data_root", type=Path)
    parser.add_argument("backup_dir", type=Path, nargs="?")
    parser.add_argument("retention_days", type=int, nargs="?", default=14)
    parser.add_argument("retention_count", type=int, nargs="?", default=30)
    args = parser.parse_args(argv)
    try:
        result = backup_meta(
            args.data_root / "meta",
            args.backup_dir or args.data_root / "backups",
            retention_days=args.retention_days,
            retention_count=args.retention_count,
        )
    except (OSError, ValueError) as exc:
        print(f"backup_meta: {exc}", file=sys.stderr)
        return 1
    print(
        f"backup_meta: wrote {result.archive} ({result.size_bytes} bytes); retention "
        f"{args.retention_days}d/{args.retention_count} archives; {result.kept} kept"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
