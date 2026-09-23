"""Stage 5: Vision classification via Ollama."""

import base64
import json
import logging
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import ollama
from PIL import Image

# Register HEIF/HEIC support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass  # pillow-heif not installed, will fail gracefully on HEIC files

from . import db

# Setup logging to file and stdout
log_file = Path.home() / ".cache" / "_curator" / "vision.log"
log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),  # Also print to stdout
    ],
)
logger = logging.getLogger(__name__)


VISION_MODEL = "qwen2.5vl:7b"

# JSON schema for structured vision output
VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "maxLength": 200},
        "category": {
            "type": "string",
            "enum": ["people", "pets", "landscape", "food", "document", "screenshot", "art", "event", "object", "vehicle", "other"],
        },
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "has_people": {"type": "boolean"},
        "contains_text": {"type": "boolean"},
        "junk": {"type": "boolean"},
        "junk_reason": {
            "type": "string",
            "enum": ["game_screenshot", "accidental", "blank", "ui_capture", "unreadable", "none"],
        },
        "significance": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    "required": [
        "description",
        "category",
        "tags",
        "has_people",
        "contains_text",
        "junk",
        "junk_reason",
        "significance",
    ],
}


def ensure_vision_model() -> None:
    """Pull the vision model if not already present."""
    logger.info(f"Checking for {VISION_MODEL}...")
    models = ollama.list()
    model_names = [m.model for m in models.models]

    if not any(VISION_MODEL in name for name in model_names):
        logger.info(f"Pulling {VISION_MODEL}... (this may take a few minutes)")
        ollama.pull(VISION_MODEL)
        logger.info(f"Complete: {VISION_MODEL}")
    else:
        logger.info(f"{VISION_MODEL} already available")


def classify_all_items(
    conn: sqlite3.Connection,
    limit: Optional[int] = None,
) -> None:
    """Classify all unprocessed items via vision."""
    db.stage_mark_running(conn, "vision")

    ensure_vision_model()

    # Get canonical items (or items without exact duplicates)
    # We don't classify exact duplicates; they inherit the canonical's description
    items = conn.execute(
        """
        SELECT id, path, kind
        FROM items
        WHERE NOT EXISTS (SELECT 1 FROM vision WHERE item_id = items.id)
        AND (is_canonical IS NULL OR is_canonical = 1)
        ORDER BY id
        """
    ).fetchall()

    if limit:
        items = items[:limit]

    logger.info(f"Classifying {len(items)} items...")

    for idx, item in enumerate(items, 1):
        try:
            filename = Path(item['path']).name

            if item["kind"] == "video":
                # Extract middle frame
                frame_path = extract_video_frame(Path(item["path"]))
                if not frame_path:
                    logger.info(f"[{idx}/{len(items)}] {filename} SKIP (no frame)")
                    continue
                result = classify_image(frame_path)
                frame_path.unlink()  # Clean up temp frame
            else:
                result = classify_image(Path(item["path"]))

            if result:
                store_vision(conn, item["id"], result)
                logger.info(f"[{idx}/{len(items)}] {filename} OK")
            else:
                logger.info(f"[{idx}/{len(items)}] {filename} FAIL")
                store_vision_error(conn, item["id"], "No result returned")

        except Exception as e:
            logger.error(f"[{idx}/{len(items)}] {filename} ERROR: {e}")
            # Recording the error must never end the run. A locked database
            # here would otherwise raise straight out of the loop and kill a
            # multi-hour pass; leaving the item unrecorded just means it gets
            # retried on the next invocation.
            try:
                store_vision_error(conn, item["id"], str(e))
            except Exception as store_err:
                logger.error(f"  could not record error for item {item['id']}: {store_err}")

    # Inherit vision from exact duplicates
    inherit_duplicate_descriptions(conn)

    db.stage_mark_complete(conn, "vision")


def classify_image(image_path: Path) -> Optional[dict]:
    """
    Classify a single image via Ollama vision.
    Returns parsed JSON or None on error.
    """
    try:
        # Downscale to 896px for efficiency
        with Image.open(image_path) as img:
            img.thumbnail((896, 896), Image.Resampling.LANCZOS)
            # Convert to JPEG bytes
            import io
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG")
            jpeg_bytes = buf.getvalue()

        # Encode as base64
        b64 = base64.b64encode(jpeg_bytes).decode("utf-8")

        # Query Ollama
        prompt = """Analyze this image and respond with ONLY valid JSON (no markdown, no extra text).

Describe what you see in one sentence. Classify into a category. Identify tags. Note if people/text are present.
Assess if this is junk (blurry/accidental/blank/screenshot/ui).
Rate significance 1-5 (how deliberate and personally meaningful).

Focus on detecting:
- Screenshots of games, apps, or menus (especially if poorly framed)
- Accidental shots (fingers in frame, motion blur)
- Blank/uniform frames (eyes closed, all black, etc.)

Respond ONLY with the JSON object, no extra text:"""

        response = ollama.generate(
            model=VISION_MODEL,
            prompt=prompt,
            images=[b64],
            stream=False,
            format=VISION_SCHEMA,  # Pass dict directly, not JSON string
        )

        # Parse the response
        response_text = response.response.strip()

        # Try to extract JSON (in case there's extra text despite format param)
        if "{" in response_text:
            json_start = response_text.index("{")
            json_end = response_text.rindex("}") + 1
            json_str = response_text[json_start:json_end]
            result = json.loads(json_str)
            return result

        return None

    except Exception as e:
        print(f"    Vision error: {e}")
        return None


def extract_video_frame(video_path: Path) -> Optional[Path]:
    """
    Extract middle frame from video using ffmpeg.
    Returns path to temporary JPEG, or None on error.
    """
    try:
        # Get video duration
        probe = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        duration = float(probe.stdout.strip())
        midpoint = duration / 2

        # Extract frame at midpoint
        temp_frame = video_path.parent / f".frame_{video_path.stem}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-ss", str(midpoint),
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "2",
                str(temp_frame),
            ],
            capture_output=True,
            timeout=30,
        )

        if temp_frame.exists():
            return temp_frame

        return None

    except Exception as e:
        print(f"    Frame extraction error: {e}")
        return None


def store_vision(conn: sqlite3.Connection, item_id: int, result: dict) -> None:
    """Store vision classification result."""
    conn.execute(
        """
        INSERT INTO vision
        (item_id, description, category, tags, has_people, contains_text, junk, junk_reason, significance, model, attempted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            result.get("description"),
            result.get("category"),
            json.dumps(result.get("tags", [])),
            result.get("has_people", False),
            result.get("contains_text", False),
            result.get("junk", False),
            result.get("junk_reason"),
            result.get("significance", 3),
            VISION_MODEL,
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()


def store_vision_error(conn: sqlite3.Connection, item_id: int, error: str) -> None:
    """Store a vision processing error."""
    conn.execute(
        """
        INSERT INTO vision
        (item_id, error, model, attempted_at)
        VALUES (?, ?, ?, ?)
        """,
        (item_id, error, VISION_MODEL, datetime.utcnow().isoformat()),
    )
    conn.commit()


def inherit_duplicate_descriptions(conn: sqlite3.Connection) -> None:
    """Inherit vision from canonical duplicates to non-canonical."""
    # Exact duplicates
    conn.execute(
        """
        INSERT INTO vision (item_id, description, category, tags, has_people, contains_text, junk, junk_reason, significance, model, attempted_at)
        SELECT
            i.id,
            v.description,
            v.category,
            v.tags,
            v.has_people,
            v.contains_text,
            v.junk,
            v.junk_reason,
            v.significance,
            v.model,
            v.attempted_at
        FROM items i
        INNER JOIN items canonical ON i.dup_group = canonical.dup_group AND canonical.is_canonical = 1
        INNER JOIN vision v ON canonical.id = v.item_id
        WHERE i.is_canonical != 1
        AND NOT EXISTS (SELECT 1 FROM vision WHERE item_id = i.id)
        """
    )
    conn.commit()
