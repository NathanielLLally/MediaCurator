"""Stage 2: Walk extracted images and gather metadata."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.ExifTags import TAGS

# Register HEIF/HEIC support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass  # pillow-heif not installed, will fail gracefully on HEIC files

from . import db


# Image extensions we care about
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff", ".gif", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def walk_and_inventory(
    conn: sqlite3.Connection,
    extracted_dir: Path,
) -> None:
    """Walk extracted directory only, recording extracted items to DB."""
    db.stage_mark_running(conn, "inventory")

    # Walk extracted zips only — do not index loose files from archive
    # (they should be extracted first if you want them indexed)
    if extracted_dir.exists():
        for zip_stem_dir in sorted(extracted_dir.iterdir()):
            if zip_stem_dir.is_dir():
                walk_tree(zip_stem_dir, conn, zip_source=zip_stem_dir.name)
    else:
        print(f"Error: extracted directory not found: {extracted_dir}")
        print(f"Run extraction first: curate /path/to/archive extract")
        raise FileNotFoundError(f"Extracted directory not found: {extracted_dir}")

    db.stage_mark_complete(conn, "inventory")


def walk_tree(
    root: Path,
    conn: sqlite3.Connection,
    zip_source: Optional[str] = None,
    skip_dirs: Optional[set] = None,
    skip_extensions: Optional[set] = None,
) -> None:
    """Recursively walk a directory tree and inventory images/videos."""
    skip_dirs = skip_dirs or set()
    skip_extensions = skip_extensions or set()

    for item_path in sorted(root.rglob("*")):
        if not item_path.is_file():
            continue

        # Skip directories in skip list
        if any(part in skip_dirs for part in item_path.relative_to(root).parts):
            continue

        suffix = item_path.suffix.lower()

        # Skip extensions in skip list
        if suffix in skip_extensions:
            continue

        # Only process image/video extensions
        if suffix not in IMAGE_EXTENSIONS and suffix not in VIDEO_EXTENSIONS:
            continue

        kind = "video" if suffix in VIDEO_EXTENSIONS else "image"
        record_item(item_path, conn, kind=kind, zip_source=zip_source)


def record_item(
    file_path: Path,
    conn: sqlite3.Connection,
    kind: str = "image",
    zip_source: Optional[str] = None,
) -> None:
    """Record a single file to the items table."""
    try:
        # Normalize path to string
        path_str = str(file_path)
        size = file_path.stat().st_size
        mtime = int(file_path.stat().st_mtime)

        # Initialize metadata
        width, height = None, None
        taken_at = None
        lat, lon = None, None

        # Try to open as image for EXIF + dimensions
        if kind == "image":
            try:
                with Image.open(file_path) as img:
                    width, height = img.size

                    # Extract EXIF
                    exif_data = extract_exif(img)
                    taken_at = exif_data.get("datetime")
            except Exception as e:
                print(f"  Warning: could not read {file_path.name}: {e}")

        # Try to load Takeout sidecar JSON if it exists
        sidecar_path = file_path.with_suffix(file_path.suffix + ".supplemental-metadata.json")
        album = None
        favorited = False
        archived = False

        if sidecar_path.exists():
            try:
                with open(sidecar_path) as f:
                    sidecar = json.load(f)

                # Extract from sidecar
                if "photoTakenTime" in sidecar and sidecar["photoTakenTime"].get("timestamp"):
                    taken_at = datetime.fromtimestamp(
                        int(sidecar["photoTakenTime"]["timestamp"])
                    ).isoformat()

                if "geoData" in sidecar:
                    geo = sidecar["geoData"]
                    if geo.get("latitude"):
                        lat = geo["latitude"]
                    if geo.get("longitude"):
                        lon = geo["longitude"]

                favorited = sidecar.get("favorited", False)
                archived = sidecar.get("archived", False)

                # Album name from parent dir in Takeout structure
                # e.g., Takeout/Google Photos/Dogs/photo.jpg -> "Dogs"
                if "Takeout/Google Photos/" in str(file_path):
                    parts = file_path.parts
                    for i, part in enumerate(parts):
                        if part == "Google Photos" and i + 1 < len(parts):
                            album = parts[i + 1]
                            break

            except Exception as e:
                print(f"  Warning: could not parse sidecar {sidecar_path.name}: {e}")

        # Fall back to filename-based date parsing
        if not taken_at:
            taken_at = parse_date_from_filename(file_path.stem)

        # Determine filename class
        filename_class = classify_filename(file_path.name)

        # Insert into DB
        conn.execute(
            """
            INSERT OR REPLACE INTO items
            (path, zip_source, size, mtime, kind, width, height, taken_at, lat, lon,
             album, favorited, archived, filename_class)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                path_str,
                zip_source,
                size,
                mtime,
                kind,
                width,
                height,
                taken_at,
                lat,
                lon,
                album,
                favorited,
                archived,
                filename_class,
            ),
        )
        conn.commit()

    except Exception as e:
        print(f"  Error recording {file_path}: {e}")


def extract_exif(img: Image.Image) -> dict:
    """Extract EXIF data from an image."""
    exif_data = {}
    try:
        exif = img.getexif()
        if exif:
            for tag_id, value in exif.items():
                tag_name = TAGS.get(tag_id, tag_id)
                if tag_name == "DateTime":
                    # Format: YYYY:MM:DD HH:MM:SS
                    exif_data["datetime"] = value.replace(":", "-", 2) if value else None
                    break
    except Exception:
        pass
    return exif_data


def parse_date_from_filename(filename: str) -> Optional[str]:
    """Try to extract a date from a filename like IMG20210211174515."""
    import re

    # Match patterns like IMG20210211174515 or 20260213_152435
    match = re.search(r"(\d{4})(\d{2})(\d{2})(?:_(\d{2})(\d{2})(\d{2}))?", filename)
    if match:
        year, month, day = match.group(1), match.group(2), match.group(3)
        return f"{year}-{month}-{day}"

    return None


def classify_filename(filename: str) -> str:
    """Classify a filename to detect camera defaults, screenshots, edits, renames."""
    lower = filename.lower()

    if lower.startswith("screenshot"):
        return "screenshot"
    if "-edited" in lower:
        return "edited"
    if "_original" in lower or "-original" in lower.replace(" ", "_"):
        return "renamed"
    if filename.startswith("IMG") and any(filename.startswith(p) for p in ["IMG_", "IMG20"]):
        return "camera_default"

    return "other"
