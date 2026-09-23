"""Stage 1: Extract images from zip archives."""

import sqlite3
import zipfile
from pathlib import Path
from typing import Optional

from . import db


def extract_all_zips(
    archive_root: Path,
    extracted_dir: Path,
    only_zip: Optional[str] = None,
) -> Path:
    """
    Extract all zip files from archive_root to extracted_dir.
    Returns path to extracted directory.

    Args:
        archive_root: Root of the media archive
        extracted_dir: Directory to extract zips into
        only_zip: If provided, only extract this specific zip filename

    Returns:
        Path to extracted directory
    """
    extracted_dir.mkdir(parents=True, exist_ok=True)

    zip_files = sorted(archive_root.glob("*.zip"))
    if only_zip:
        zip_files = [z for z in zip_files if z.name == only_zip]

    for zip_path in zip_files:
        extract_single_zip(zip_path, extracted_dir)

    return extracted_dir


def extract_single_zip(zip_path: Path, extracted_dir: Path) -> None:
    """Extract a single zip, preserving internal structure."""
    zip_stem = zip_path.stem  # removes .zip extension
    target_dir = extracted_dir / zip_stem

    # Check marker file to skip re-extraction
    marker = target_dir / ".extraction_complete"
    if marker.exists():
        print(f"  Skipping {zip_path.name} (already extracted)")
        return

    print(f"Extracting {zip_path.name} → {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(target_dir)

    # Write marker
    marker.touch()
    print(f"  Complete: {zip_path.name}")


def get_extracted_dir(archive_root: Path, extracted_dir: Optional[Path] = None) -> Path:
    """Get path to extracted directory."""
    if extracted_dir:
        return extracted_dir
    return archive_root / "_curator" / "extracted"
