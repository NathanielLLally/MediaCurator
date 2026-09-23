"""Quarantine management: move/restore low-scoring files."""

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from . import db


def get_quarantine_dir(archive_root: Optional[Path] = None) -> Path:
    """Get quarantine directory in ~/.cache/_curator/quarantine/<archive_name>/"""
    if archive_root is None:
        # Derive archive_name from database
        db_path = db.get_db_path(None)
        archive_name = db_path.stem
    else:
        from curator.db import path_to_dbname
        archive_name = path_to_dbname(archive_root).replace(".db", "")

    quarantine_dir = Path.home() / ".cache" / "_curator" / "quarantine" / archive_name
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    return quarantine_dir


def quarantine_items(
    archive_root: Optional[Path],
    conn: sqlite3.Connection,
    threshold: int = 25,
    dry_run: bool = True,
) -> None:
    """Move items below threshold to quarantine folder."""
    quarantine_dir = get_quarantine_dir(archive_root)
    manifest_path = quarantine_dir / "manifest.json"
    manifest = {}

    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)

    # Get low-scoring items
    items = conn.execute(
        """
        SELECT i.id, i.path, i.size, s.score
        FROM items i
        LEFT JOIN scores s ON i.id = s.item_id
        WHERE s.score < ?
        ORDER BY s.score
        """,
        (threshold,),
    ).fetchall()

    if not items:
        print(f"No items below score {threshold}")
        return

    print(f"Found {len(items)} items below score {threshold}")

    total_bytes = 0
    moves = []

    for item in items:
        source = Path(item["path"])
        if not source.exists():
            continue

        total_bytes += item["size"] or 0

        # Compute destination preserving relative path structure
        # Remove common prefix (archive_root / _curator / extracted / <zip> /)
        if archive_root is None:
            # Stages 3-7 don't require archive_root; just use the filename
            rel_path = source.name
        else:
            try:
                rel_path = source.relative_to(archive_root)
            except ValueError:
                rel_path = source.name

        dest = quarantine_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        moves.append((source, dest, item["id"], item["score"]))

        if not dry_run:
            shutil.move(str(source), str(dest))
            manifest[str(dest)] = {
                "original_path": str(source),
                "score": item["score"],
            }

    print(f"  {len(moves)} files")
    print(f"  {total_bytes / (1024**3):.2f} GB")

    if dry_run:
        print("  (dry run — nothing moved)")
    else:
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"  Wrote manifest to {manifest_path}")


def unquarantine_items(
    archive_root: Optional[Path],
    conn: sqlite3.Connection,
) -> None:
    """Restore quarantined items to original locations."""
    quarantine_dir = get_quarantine_dir(archive_root)
    manifest_path = quarantine_dir / "manifest.json"

    if not manifest_path.exists():
        print("No quarantine manifest found")
        return

    with open(manifest_path) as f:
        manifest = json.load(f)

    print(f"Restoring {len(manifest)} items...")

    for dest_str, info in manifest.items():
        dest = Path(dest_str)
        original = Path(info["original_path"])

        if dest.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(original))
            print(f"  Restored {original.name}")

    # Clear manifest
    manifest_path.unlink()
    print("Quarantine manifest cleared")
