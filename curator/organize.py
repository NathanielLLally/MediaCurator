"""Stage 8: Organize files by category with tag-based naming."""

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from . import db


def organize_by_category(
    conn: sqlite3.Connection,
    extracted_dir: Optional[Path] = None,
) -> None:
    """
    Reorganize extracted files into category directories with tag-based names.

    Files are moved to ~/extracted/category/ and renamed using non-category tags
    in snake_case, preserving the original extension.

    Original zip directories are removed after becoming empty.
    """
    # Determine extracted directory
    if extracted_dir is None:
        db_path = db.get_db_path(None)
        archive_name = db_path.stem
        cfg_path = Path.home() / ".local" / "_curator" / f"{archive_name}.json"
        if cfg_path.exists():
            with open(cfg_path) as f:
                config = json.load(f)
                extracted_dir = Path(config.get("extract_dir", ""))
        else:
            raise ValueError("No extract directory configured")

    extracted_root = Path(extracted_dir).resolve()
    if not extracted_root.exists():
        raise FileNotFoundError(f"Extracted directory not found: {extracted_root}")

    print(f"Organizing {extracted_root}...")

    # Get all items with their vision data
    items = conn.execute(
        """
        SELECT
            i.id,
            i.path,
            v.category,
            v.tags
        FROM items i
        LEFT JOIN vision v ON i.id = v.item_id
        ORDER BY v.category, i.path
        """
    ).fetchall()

    print(f"Processing {len(items)} items...")

    category_dirs = set()
    moved = 0
    skipped = 0
    errors = []

    for item in items:
        item_id = item["id"]
        path_str = item["path"]
        category = item["category"] or "unclassified"
        tags_json = item["tags"] or "[]"

        source = Path(path_str)
        if not source.exists():
            skipped += 1
            continue

        # Parse tags and filter out the category tag
        try:
            tags = json.loads(tags_json)
        except Exception:
            tags = []

        non_category_tags = [t for t in tags if t.lower() != category.lower()]

        # Build new filename: tags in snake_case + original extension
        if non_category_tags:
            name_part = "_".join(
                t.lower().replace(" ", "_") for t in non_category_tags
            )
        else:
            name_part = source.stem

        ext = source.suffix
        new_filename = f"{name_part}{ext}"

        # Create category directory
        cat_dir = extracted_root / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        category_dirs.add(cat_dir)

        # Destination path
        dest = cat_dir / new_filename

        # Handle filename collision
        if dest.exists() and dest != source:
            base = dest.stem
            counter = 1
            while dest.exists():
                dest = cat_dir / f"{base}_{counter}{ext}"
                counter += 1

        # Move file
        try:
            shutil.move(str(source), str(dest))
            # Update database with new path
            conn.execute("UPDATE items SET path = ? WHERE id = ?", (str(dest), item_id))
            moved += 1
        except Exception as e:
            errors.append(f"{source.name}: {e}")
            skipped += 1

    conn.commit()

    print(f"✓ Moved: {moved}")
    if skipped:
        print(f"✗ Skipped: {skipped}")
    if errors:
        print(f"Errors: {len(errors)}")
        for err in errors[:5]:
            print(f"  {err}")

    print(f"Categories created: {len(category_dirs)}")
    for cat_dir in sorted(category_dirs):
        print(f"  {cat_dir.name}/")

    # Remove empty original directories
    print("\nRemoving empty original directories...")
    category_names = {d.name for d in category_dirs}
    removed = 0

    for item in extracted_root.iterdir():
        if item.is_dir() and item.name not in category_names:
            try:
                # Remove all empty subdirs first
                import os

                for root, dirs, files in os.walk(item, topdown=False):
                    for d in dirs:
                        try:
                            os.rmdir(os.path.join(root, d))
                        except OSError:
                            pass
                # Remove top-level dir
                shutil.rmtree(item)
                print(f"  ✓ {item.name}/")
                removed += 1
            except OSError as e:
                print(f"  ✗ {item.name}: {e}")

    print(f"Removed {removed} original directories")
    print("\n✓ Organization complete")
