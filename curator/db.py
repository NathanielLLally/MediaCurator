"""SQLite database schema, connection, and migrations."""

import sqlite3
import re
from pathlib import Path
from typing import Optional

DB_SCHEMA_VERSION = 1


def get_curator_home() -> Path:
    """Get ~/.local/_curator directory, create if needed."""
    curator_home = Path.home() / ".local" / "_curator"
    curator_home.mkdir(parents=True, exist_ok=True)
    return curator_home


def path_to_dbname(archive_root: Path) -> str:
    """Convert archive path to snake_case database filename.

    Example: /mnt/l0pht/media → mnt_l0pht_media.db
    """
    # Normalize path
    archive_root = archive_root.resolve()

    # Convert to string and replace path separators with underscores
    path_str = str(archive_root)

    # Replace slashes and special chars with underscores
    dbname = re.sub(r'[^a-zA-Z0-9]', '_', path_str)

    # Clean up multiple underscores
    dbname = re.sub(r'_+', '_', dbname)

    # Remove leading/trailing underscores
    dbname = dbname.strip('_')

    return f"{dbname}.db"


def get_db_path(archive_root: Optional[Path] = None) -> Path:
    """Return path to curator database.

    Args:
        archive_root: Path to archive. If None, uses first .db found or prompts user.

    Returns:
        Path to database file in ~/.local/_curator/
    """
    curator_home = get_curator_home()

    if archive_root:
        # Specific archive requested
        dbname = path_to_dbname(archive_root)
        return curator_home / dbname

    # No archive specified, find or prompt for database
    return discover_or_prompt_db()


def discover_or_prompt_db() -> Path:
    """Find existing database, or prompt user if multiple exist.

    Returns:
        Path to selected database
    """
    curator_home = get_curator_home()
    db_files = sorted(curator_home.glob("*.db"))

    if not db_files:
        raise FileNotFoundError(
            f"No curator databases found in {curator_home}\n"
            f"Run: curate <archive_path> extract  (to initialize a database)"
        )

    if len(db_files) == 1:
        return db_files[0]

    # Multiple databases: prompt user
    print("\nMultiple archives found. Select one:")
    for i, db_path in enumerate(db_files, 1):
        # Extract archive path from db filename
        archive_name = db_path.stem.replace('_', '/')
        print(f"  {i}. {archive_name}")
        print(f"     ({db_path.name})")

    while True:
        try:
            choice = input(f"\nSelect [1-{len(db_files)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(db_files):
                return db_files[idx]
        except (ValueError, IndexError):
            pass
        print(f"Invalid choice. Enter 1-{len(db_files)}")



def init_db(archive_root: Path) -> sqlite3.Connection:
    """Initialize database and return connection."""
    db_path = get_db_path(archive_root)
    # timeout: wait for a contended write lock instead of failing immediately.
    # The long vision pass and a concurrent scoring/report run will overlap.
    conn = sqlite3.connect(str(db_path), timeout=60.0)
    conn.row_factory = sqlite3.Row

    # WAL lets readers (report, scoring queries) run while vision writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")

    # Create tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            zip_source TEXT,
            size INTEGER,
            mtime INTEGER,
            kind TEXT NOT NULL,
            sha256 TEXT,
            phash TEXT,
            width INTEGER,
            height INTEGER,
            taken_at TEXT,
            lat REAL,
            lon REAL,
            album TEXT,
            favorited BOOLEAN DEFAULT 0,
            archived BOOLEAN DEFAULT 0,
            filename_class TEXT,
            blur_var REAL,
            exposure_mean REAL,
            exposure_std REAL,
            dup_group TEXT,
            -- NULL means "not part of any duplicate group", which is not the
            -- same as 0 ("a duplicate, but not the canonical one"). Defaulting
            -- to 0 made vision skip every ungrouped item.
            is_canonical BOOLEAN DEFAULT NULL,
            near_dup_group TEXT
        );

        CREATE TABLE IF NOT EXISTS vision (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            description TEXT,
            category TEXT,
            tags TEXT,
            has_people BOOLEAN,
            contains_text BOOLEAN,
            junk BOOLEAN DEFAULT 0,
            junk_reason TEXT,
            significance INTEGER,
            model TEXT,
            error TEXT,
            attempted_at TEXT,
            FOREIGN KEY (item_id) REFERENCES items(id),
            UNIQUE(item_id)
        );

        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            score REAL,
            breakdown TEXT,
            FOREIGN KEY (item_id) REFERENCES items(id),
            UNIQUE(item_id)
        );

        CREATE TABLE IF NOT EXISTS stage_state (
            stage TEXT PRIMARY KEY,
            status TEXT,
            completed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_items_sha256 ON items(sha256);
        CREATE INDEX IF NOT EXISTS idx_items_phash ON items(phash);
        CREATE INDEX IF NOT EXISTS idx_items_dup_group ON items(dup_group);
        CREATE INDEX IF NOT EXISTS idx_vision_item ON vision(item_id);
        CREATE INDEX IF NOT EXISTS idx_scores_item ON scores(item_id);
    """)

    conn.commit()
    return conn


def stage_mark_running(conn: sqlite3.Connection, stage: str) -> None:
    """Mark a stage as running."""
    conn.execute(
        "INSERT OR REPLACE INTO stage_state (stage, status) VALUES (?, ?)",
        (stage, "running"),
    )
    conn.commit()


def stage_mark_complete(conn: sqlite3.Connection, stage: str) -> None:
    """Mark a stage as complete."""
    from datetime import datetime
    conn.execute(
        "INSERT OR REPLACE INTO stage_state (stage, status, completed_at) VALUES (?, ?, ?)",
        (stage, "complete", datetime.utcnow().isoformat()),
    )
    conn.commit()


def stage_is_complete(conn: sqlite3.Connection, stage: str) -> bool:
    """Check if a stage has completed."""
    row = conn.execute(
        "SELECT status FROM stage_state WHERE stage = ?", (stage,)
    ).fetchone()
    return row is not None and row["status"] == "complete"


def get_incomplete_items(conn: sqlite3.Connection, table: str) -> list[int]:
    """Get item IDs that haven't been processed yet (no vision, score, etc.)."""
    if table == "vision":
        cursor = conn.execute("""
            SELECT i.id FROM items i
            WHERE NOT EXISTS (SELECT 1 FROM vision WHERE item_id = i.id)
            ORDER BY i.id
        """)
    elif table == "scores":
        cursor = conn.execute("""
            SELECT i.id FROM items i
            WHERE NOT EXISTS (SELECT 1 FROM scores WHERE item_id = i.id)
            ORDER BY i.id
        """)
    else:
        raise ValueError(f"Unknown table: {table}")

    return [row[0] for row in cursor.fetchall()]


def count_items(conn: sqlite3.Connection, kind: Optional[str] = None) -> int:
    """Count items in database."""
    if kind:
        cursor = conn.execute("SELECT COUNT(*) FROM items WHERE kind = ?", (kind,))
    else:
        cursor = conn.execute("SELECT COUNT(*) FROM items")
    return cursor.fetchone()[0]
