"""Stage 7: Generate human-readable reports."""

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from datetime import datetime

from . import db


def get_reports_dir() -> Path:
    """Get reports directory in ~/.cache/_curator/reports/"""
    reports_dir = Path.home() / ".cache" / "_curator" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def generate_reports(
    archive_root: Path,
    conn: sqlite3.Connection,
) -> None:
    """Generate all report files to ~/.cache/_curator/reports/"""
    db.stage_mark_running(conn, "report")

    print("Generating reports...")

    # Get archive identifier for report naming
    if archive_root is None:
        # archive_root is optional for stages 3-7; derive archive_name from database path
        db_path = db.get_db_path(None)
        archive_name = db_path.stem  # filename without .db extension
    else:
        from curator.db import path_to_dbname
        archive_name = path_to_dbname(archive_root).replace(".db", "")

    # Gather all data
    items = conn.execute(
        """
        SELECT i.id, i.path, i.kind, i.size, i.width, i.height, i.taken_at, i.lat, i.lon, i.album,
               i.favorited, i.archived, i.filename_class,
               v.description, v.category, v.tags, v.has_people, v.junk, v.junk_reason,
               s.score, s.breakdown
        FROM items i
        LEFT JOIN vision v ON i.id = v.item_id
        LEFT JOIN scores s ON i.id = s.item_id
        ORDER BY i.album, i.taken_at, i.path
        """
    ).fetchall()

    # Convert sqlite3.Row to dict for easier access with .get()
    items = [dict(item) for item in items]

    # Count totals
    total_items = len(items)
    total_size = sum(item["size"] or 0 for item in items)

    # Generate files (returns paths)
    reports_dir = get_reports_dir()
    md_path = generate_markdown_index(reports_dir, archive_name, items, total_items, total_size)
    csv_path = generate_csv_index(reports_dir, archive_name, items)
    purge_path = generate_purge_candidates(reports_dir, archive_name, items)

    db.stage_mark_complete(conn, "report")
    print(f"\nReports generated in {reports_dir}")
    print(f"  {md_path.name}")
    print(f"  {csv_path.name}")
    print(f"  {purge_path.name}")


def generate_markdown_index(
    reports_dir: Path,
    archive_name: str,
    items: list,
    total_items: int,
    total_size: int,
) -> Path:
    """Generate main Markdown index."""
    index_path = reports_dir / f"{archive_name}_INDEX.md"

    # Categorize items
    categories = defaultdict(lambda: {"count": 0, "size": 0, "items": []})
    albums = defaultdict(lambda: {"count": 0, "size": 0, "items": []})

    for item in items:
        category = item["category"] or "unclassified"
        categories[category]["count"] += 1
        categories[category]["size"] += item["size"] or 0
        categories[category]["items"].append(item)

        album = item["album"] or "Uncategorized"
        albums[album]["count"] += 1
        albums[album]["size"] += item["size"] or 0
        albums[album]["items"].append(item)

    # Write header
    with open(index_path, "w") as f:
        f.write("# Image Archive Index\n\n")
        f.write(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n")
        f.write(f"**{total_items} items** · {total_size / (1024**3):.1f} GB\n\n")

        # Category summary table
        f.write("## Summary by Category\n\n")
        f.write("| Category | Count | Size (MB) | Avg Score |\n")
        f.write("|---|---|---|---|\n")

        for category in sorted(categories.keys()):
            data = categories[category]
            avg_score = sum(item["score"] or 0 for item in data["items"]) / max(1, data["count"])
            f.write(
                f"| {category} | {data['count']} | {data['size'] / (1024**2):.1f} | {avg_score:.0f} |\n"
            )

        f.write("\n")

        # Albums section
        for album in sorted(albums.keys()):
            data = albums[album]
            f.write(f"## {album} ({data['count']} items)\n\n")

            for item in data["items"]:
                write_item_entry(f, item)

            f.write("\n")

    return index_path


def write_item_entry(f, item):
    """Write a single item entry to markdown."""
    filename = Path(item["path"]).name

    # Description
    description = item["description"] or "Unclassified"
    f.write(f"### {filename}\n\n")
    f.write(f"- **{description}**\n")

    # Category and tags
    category = item["category"] or "unknown"
    tags_str = ", ".join(json.loads(item["tags"] or "[]")[:3])
    if tags_str:
        f.write(f"- {category} · {tags_str}\n")
    else:
        f.write(f"- {category}\n")

    # Score
    if item["score"] is not None:
        f.write(f"- Score: **{item['score']:.0f}**/100\n")

    # Dimensions, size, date, location
    details = []
    if item["width"] and item["height"]:
        details.append(f"{item['width']}×{item['height']}")
    if item["size"]:
        details.append(f"{item['size'] / (1024**2):.1f} MB")
    if item["taken_at"]:
        details.append(f"taken {item['taken_at'][:10]}")
    if item["lat"] and item["lon"]:
        details.append(f"{item['lat']:.3f}, {item['lon']:.3f}")

    if details:
        f.write(f"- {' · '.join(details)}\n")

    # Flags
    flags = []
    if item["favorited"]:
        flags.append("⭐ favorited")
    if item["archived"]:
        flags.append("📦 archived")
    if item["junk"]:
        flags.append("🗑️ flagged junk")

    if flags:
        f.write(f"- {" | ".join(flags)}\n")

    f.write("\n")


def generate_csv_index(reports_dir: Path, archive_name: str, items: list) -> Path:
    """Generate flat CSV for sorting/filtering."""
    csv_path = reports_dir / f"{archive_name}_index.csv"

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "filename",
                "size_mb",
                "width",
                "height",
                "taken_at",
                "lat",
                "lon",
                "album",
                "description",
                "category",
                "tags",
                "has_people",
                "score",
                "favorited",
                "archived",
                "junk",
            ],
        )
        writer.writeheader()

        for item in items:
            writer.writerow({
                "filename": Path(item["path"]).name,
                "size_mb": f"{(item['size'] or 0) / (1024**2):.1f}",
                "width": item["width"] or "",
                "height": item["height"] or "",
                "taken_at": item["taken_at"] or "",
                "lat": f"{item['lat']:.4f}" if item["lat"] else "",
                "lon": f"{item['lon']:.4f}" if item["lon"] else "",
                "album": item["album"] or "",
                "description": item["description"] or "",
                "category": item["category"] or "",
                "tags": ",".join(json.loads(item["tags"] or "[]")),
                "has_people": "yes" if item["has_people"] else "no",
                "score": f"{item['score']:.0f}" if item["score"] is not None else "",
                "favorited": "yes" if item["favorited"] else "no",
                "archived": "yes" if item["archived"] else "no",
                "junk": "yes" if item["junk"] else "no",
            })

    return csv_path


def generate_purge_candidates(reports_dir: Path, archive_name: str, items: list) -> Path:
    """Generate list of low-scoring files."""
    purge_path = reports_dir / f"{archive_name}_PURGE-CANDIDATES.md"

    # Filter to low-scoring items
    candidates = [
        item for item in items
        if item["score"] is not None and item["score"] < 25
    ]
    candidates.sort(key=lambda x: x["score"] or 0)

    total_bytes = sum(item["size"] or 0 for item in candidates)

    with open(purge_path, "w") as f:
        f.write("# Purge Candidates (Score < 25)\n\n")
        f.write(f"**{len(candidates)} items** · {total_bytes / (1024**3):.2f} GB reclaimable\n\n")

        # Group by reason
        reasons = defaultdict(list)
        for item in candidates:
            breakdown = json.loads(item.get("breakdown") or "{}")
            # Identify primary reason
            if "junk" in breakdown:
                reason = f"Junk ({item.get('junk_reason') or 'general'})"
            elif "exact_duplicate" in breakdown:
                reason = "Exact duplicate"
            elif "screenshot_no_text" in breakdown:
                reason = "Screenshot (no text)"
            elif "blurry" in breakdown:
                reason = "Blurry"
            elif "blank" in breakdown:
                reason = "Blank/uniform"
            else:
                reason = "Low score"

            reasons[reason].append(item)

        for reason in sorted(reasons.keys()):
            items_in_reason = reasons[reason]
            size_in_reason = sum(item["size"] or 0 for item in items_in_reason)
            f.write(f"## {reason}\n\n")
            f.write(f"**{len(items_in_reason)} items** · {size_in_reason / (1024**2):.1f} MB\n\n")

            for item in items_in_reason:
                f.write(f"- `{Path(item['path']).name}` ")
                f.write(f"({(item['size'] or 0) / (1024**2):.1f} MB) ")
                f.write(f"score {item['score']:.0f}/100\n")

            f.write("\n")

    return purge_path
