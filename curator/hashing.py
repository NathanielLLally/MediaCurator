"""Stage 3: Hash files and detect duplicates."""

import hashlib
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional

import imagehash
from PIL import Image

from . import db


def hash_all_items(
    archive_root: Path,
    conn: sqlite3.Connection,
    max_workers: int = 16,
) -> None:
    """Compute SHA256 and perceptual hashes for all items."""
    db.stage_mark_running(conn, "hashing")

    # Get all items without hashes yet
    items = conn.execute(
        "SELECT id, path FROM items WHERE sha256 IS NULL ORDER BY id"
    ).fetchall()

    print(f"Computing hashes for {len(items)} items...")

    # Process with thread pool (file I/O bound, can parallelize)
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for item in items:
            future = executor.submit(compute_hashes, Path(item["path"]))
            futures.append((item["id"], future))

        for item_id, future in futures:
            try:
                sha256, phash = future.result()
                conn.execute(
                    "UPDATE items SET sha256 = ?, phash = ? WHERE id = ?",
                    (sha256, phash, item_id),
                )
                conn.commit()
            except Exception as e:
                print(f"  Error hashing item {item_id}: {e}")

    # Group duplicates
    group_exact_duplicates(conn)
    group_perceptual_duplicates(conn)

    db.stage_mark_complete(conn, "hashing")


def compute_hashes(file_path: Path) -> tuple[str, Optional[str]]:
    """Compute SHA256 and perceptual hash for a file."""
    # SHA256
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256_hash.update(chunk)
    sha256 = sha256_hash.hexdigest()

    # Perceptual hash (for images only)
    phash = None
    if file_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff", ".gif", ".bmp"}:
        try:
            with Image.open(file_path) as img:
                # Convert to RGB if necessary
                if img.mode != "RGB":
                    img = img.convert("RGB")
                phash = str(imagehash.phash(img))
        except Exception:
            pass

    return sha256, phash


def group_exact_duplicates(conn: sqlite3.Connection) -> None:
    """Group items by SHA256, elect canonical members."""
    print("Grouping exact duplicates...")

    # Find duplicate groups
    dup_groups = conn.execute(
        """
        SELECT sha256, COUNT(*) as cnt
        FROM items
        WHERE sha256 IS NOT NULL
        GROUP BY sha256
        HAVING cnt > 1
        ORDER BY sha256
        """
    ).fetchall()

    for group in dup_groups:
        sha256 = group["sha256"]

        # Get all items in this group
        members = conn.execute(
            """
            SELECT id, path, album, filename_class, taken_at
            FROM items
            WHERE sha256 = ?
            ORDER BY id
            """,
            (sha256,),
        ).fetchall()

        # Elect canonical: prefer named album > renamed > earliest date > shortest path
        canonical = elect_canonical(members)

        # Mark all as duplicates with the canonical
        dup_group_id = f"exact_{sha256[:8]}"
        for member in members:
            is_canon = member["id"] == canonical["id"]
            conn.execute(
                """
                UPDATE items
                SET dup_group = ?, is_canonical = ?
                WHERE id = ?
                """,
                (dup_group_id, is_canon, member["id"]),
            )

        conn.commit()
        print(f"  Exact dup group {len(members)}: {dup_group_id[:20]}...")


def group_perceptual_duplicates(conn: sqlite3.Connection) -> None:
    """Group items by perceptual similarity (Hamming distance ≤ 5)."""
    print("Grouping perceptual duplicates...")

    # Get all unique phash values
    phashes = conn.execute(
        """
        SELECT DISTINCT phash FROM items
        WHERE phash IS NOT NULL
        ORDER BY phash
        """
    ).fetchall()

    for phash_row in phashes:
        phash_str = phash_row["phash"]
        if not phash_str:
            continue

        # Find all items within Hamming distance 5
        nearby = conn.execute(
            """
            SELECT id, phash FROM items
            WHERE phash IS NOT NULL
            AND dup_group IS NULL
            """
        ).fetchall()

        # Start with items that have this exact phash
        group_members = []
        for item in nearby:
            if item["phash"] == phash_str:
                group_members.append({"id": item["id"], "phash": phash_str})

        if not group_members:
            # No items found with this phash (shouldn't happen)
            continue

        # Add nearby items within Hamming distance
        for item in nearby:
            # Skip if already in group
            if any(m["id"] == item["id"] for m in group_members):
                continue
            if item["phash"]:
                distance = imagehash.hex_to_hash(phash_str) - imagehash.hex_to_hash(item["phash"])
                if distance <= 5:
                    group_members.append({"id": item["id"], "phash": item["phash"]})

        if len(group_members) > 1:
            # Get full info for canonical election
            ids = [m["id"] for m in group_members]
            placeholders = ",".join("?" * len(ids))
            members = conn.execute(
                f"""
                SELECT id, path, album, filename_class, taken_at, width, height, blur_var
                FROM items
                WHERE id IN ({placeholders})
                """,
                ids,
            ).fetchall()

            canonical = elect_canonical(members, prefer_blur_sharpness=True)

            # Mark as near-duplicates
            near_dup_id = f"perceptual_{phash_str[:8]}"
            for member in members:
                is_canon = member["id"] == canonical["id"]
                if not is_canon:
                    conn.execute(
                        "UPDATE items SET near_dup_group = ? WHERE id = ?",
                        (near_dup_id, member["id"]),
                    )

            conn.commit()


def elect_canonical(members, prefer_blur_sharpness: bool = False) -> dict:
    """
    Elect a canonical member from a group.

    Preference order:
    1. In a named album (not year bucket)
    2. Renamed filename
    3. If prefer_blur_sharpness, prefer less blurry
    4. Earliest capture date
    5. Shortest path
    """
    # Score each member
    scores = []
    for member in members:
        # Convert sqlite3.Row to dict if needed (Row doesn't have .get() method)
        if hasattr(member, 'keys'):
            m = dict(member)
        else:
            m = member

        score = 0

        # Named album is better than year bucket
        album = m.get("album", "")
        if album and not album.startswith("Photos from"):
            score += 100

        # Renamed is better than camera default
        if m.get("filename_class") == "renamed":
            score += 50
        elif m.get("filename_class") == "edited":
            score += 40

        # Sharper is better if we care
        if prefer_blur_sharpness and m.get("blur_var"):
            score += m["blur_var"] * 10

        # Earlier is better (negate timestamp for sorting)
        if m.get("taken_at"):
            score -= int(m["taken_at"][:4]) * 1000  # Rough year-based penalty

        # Shorter path is better (ties breaker)
        path_len = len(m.get("path", ""))
        score -= path_len * 0.1

        scores.append((score, member))

    # Return member with highest score (sort by score only; sqlite3.Row
    # doesn't support comparison, so avoid falling through to comparing
    # members when scores tie)
    return max(scores, key=lambda pair: pair[0])[1]
