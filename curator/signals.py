"""Stage 4: Compute cheap deterministic signals (blur, exposure, resolution, etc.)."""

import cv2
import numpy as np
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

# Register HEIF/HEIC support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass  # pillow-heif not installed, will fail gracefully on HEIC files

from . import db


def compute_all_signals(
    conn: sqlite3.Connection,
    max_workers: int = 16,
) -> None:
    """Compute all cheap signals for all images."""
    db.stage_mark_running(conn, "signals")

    # Get all image items without signals yet
    items = conn.execute(
        """
        SELECT id, path, kind FROM items
        WHERE kind = 'image' AND blur_var IS NULL
        ORDER BY id
        """
    ).fetchall()

    print(f"Computing signals for {len(items)} images...")

    # Process with thread pool
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for item in items:
            future = executor.submit(compute_image_signals, Path(item["path"]))
            futures.append((item["id"], future))

        for item_id, future in futures:
            try:
                blur_var, exposure_mean, exposure_std = future.result()
                conn.execute(
                    """
                    UPDATE items
                    SET blur_var = ?, exposure_mean = ?, exposure_std = ?
                    WHERE id = ?
                    """,
                    (blur_var, exposure_mean, exposure_std, item_id),
                )
                conn.commit()
            except Exception as e:
                print(f"  Warning: could not compute signals for {item_id}: {e}")

    db.stage_mark_complete(conn, "signals")


def compute_image_signals(file_path: Path) -> tuple[float, float, float]:
    """
    Compute blur variance, exposure mean, and exposure stddev for an image.

    Returns: (blur_var, exposure_mean, exposure_std)
    """
    # Open with PIL to handle HEIC/orientation
    try:
        pil_img = Image.open(file_path)
        # Apply orientation if present
        if hasattr(pil_img, "_getexif") and pil_img._getexif():
            # Simple handling; full EXIF orientation would be more complex
            pass
        # Convert to RGB if needed
        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")
        pil_img.thumbnail((512, 512), Image.Resampling.LANCZOS)

        # Convert to OpenCV format (BGR)
        img_cv = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception as e:
        # Default values if we can't read
        print(f"  Warning: could not read {file_path} for signals: {e}")
        return 0.0, 128.0, 0.0

    # Blur detection via Laplacian variance
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    blur_var = cv2.Laplacian(gray, cv2.CV_64F).var()

    # Exposure via luminance
    luminance = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    exposure_mean = float(np.mean(luminance))
    exposure_std = float(np.std(luminance))

    return blur_var, exposure_mean, exposure_std


def calibrate_blur_threshold(conn: sqlite3.Connection) -> float:
    """
    Calibrate blur threshold from real data.
    A sample of 100 random images, manually flagged as sharp/blurry,
    would produce a threshold. For now, use a sensible default.

    Typical ranges:
    - Sharp image: > 200
    - Slightly blurry: 50-200
    - Very blurry: < 50
    """
    # Get statistics on current blur values
    stats = conn.execute(
        """
        SELECT
            MIN(blur_var) as min_blur,
            MAX(blur_var) as max_blur,
            AVG(blur_var) as avg_blur
        FROM items
        WHERE blur_var IS NOT NULL
        """
    ).fetchone()

    if stats["avg_blur"]:
        # Return a threshold around lower quartile
        # This is a rough heuristic; ideally calibrate on known samples
        return max(50.0, stats["avg_blur"] * 0.5)

    return 50.0  # Default fallback
