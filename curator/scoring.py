"""Stage 6: Compute preservation scores."""

import json
import sqlite3
from . import db


# Scoring weights — adjust these to change what we value
WEIGHTS = {
    # Raises score — evidence of intent to keep
    "favorited": 25,
    "edited": 20,
    "named_album": 15,
    "renamed": 15,
    "has_people": 10,
    "has_pets": 10,
    "significance_4": 8,
    "significance_5": 16,
    # Lowers score — evidence of disposability
    "junk": -30,
    "exact_duplicate": -40,
    "near_duplicate": -20,
    "blurry": -25,
    "blank": -30,
    "low_res": -15,
    "archived": -15,
    "screenshot_no_text": -10,
}


def score_all_items(
    conn: sqlite3.Connection,
    blur_threshold: float = 50.0,
) -> None:
    """Compute scores for all items."""
    db.stage_mark_running(conn, "scoring")

    # Get all items
    items = conn.execute(
        """
        SELECT i.id, i.favorited, i.archived, i.filename_class, i.album,
               i.blur_var, i.exposure_mean, i.exposure_std,
               i.width, i.height, i.is_canonical, i.dup_group, i.near_dup_group,
               v.junk, v.junk_reason, v.has_people, v.contains_text,
               v.significance, v.category, v.tags
        FROM items i
        LEFT JOIN vision v ON i.id = v.item_id
        ORDER BY i.id
        """
    ).fetchall()

    print(f"Scoring {len(items)} items...")

    for item in items:
        breakdown = {}
        score = 50  # Base score

        # Favorited
        if item["favorited"]:
            score += WEIGHTS["favorited"]
            breakdown["favorited"] = WEIGHTS["favorited"]

        # Archived (marked for hiding)
        if item["archived"]:
            score += WEIGHTS["archived"]
            breakdown["archived"] = WEIGHTS["archived"]

        # Filename class
        if item["filename_class"] == "edited":
            score += WEIGHTS["edited"]
            breakdown["edited_filename"] = WEIGHTS["edited"]
        elif item["filename_class"] == "renamed":
            score += WEIGHTS["renamed"]
            breakdown["renamed_filename"] = WEIGHTS["renamed"]

        # Album class
        if item["album"] and not item["album"].startswith("Photos from"):
            score += WEIGHTS["named_album"]
            breakdown["named_album"] = WEIGHTS["named_album"]

        # Vision signals
        if item["junk"]:
            score += WEIGHTS["junk"]
            breakdown["junk"] = WEIGHTS["junk"]
            if item["junk_reason"]:
                breakdown[f"junk_reason_{item['junk_reason']}"] = 0  # For debugging

        if item["has_people"]:
            score += WEIGHTS["has_people"]
            breakdown["has_people"] = WEIGHTS["has_people"]

        # Detect pets via category or tags
        if item["category"] == "pets" or (item["tags"] and "pet" in item["tags"].lower()):
            score += WEIGHTS["has_pets"]
            breakdown["has_pets"] = WEIGHTS["has_pets"]

        # Significance
        if item["significance"]:
            if item["significance"] >= 4:
                delta = WEIGHTS["significance_5"] if item["significance"] == 5 else WEIGHTS["significance_4"]
                score += delta
                breakdown[f"significance_{item['significance']}"] = delta

        # Blur
        if item["blur_var"] is not None and item["blur_var"] < blur_threshold:
            score += WEIGHTS["blurry"]
            breakdown["blurry"] = WEIGHTS["blurry"]

        # Exposure (blank)
        if item["exposure_mean"] is not None and item["exposure_std"] is not None:
            if item["exposure_mean"] < 0.1 or item["exposure_mean"] > 0.95:
                if item["exposure_std"] < 0.05:  # Uniform
                    score += WEIGHTS["blank"]
                    breakdown["blank"] = WEIGHTS["blank"]

        # Resolution
        if item["width"] and item["height"]:
            megapixels = (item["width"] * item["height"]) / 1_000_000
            if megapixels < 0.3:
                score += WEIGHTS["low_res"]
                breakdown["low_res"] = WEIGHTS["low_res"]

        # Duplicates
        if item["dup_group"] and not item["is_canonical"]:
            score += WEIGHTS["exact_duplicate"]
            breakdown["exact_duplicate"] = WEIGHTS["exact_duplicate"]
        elif item["near_dup_group"]:
            score += WEIGHTS["near_duplicate"]
            breakdown["near_duplicate"] = WEIGHTS["near_duplicate"]

        # Screenshot without text
        if item["filename_class"] == "screenshot" and not item["contains_text"]:
            score += WEIGHTS["screenshot_no_text"]
            breakdown["screenshot_no_text"] = WEIGHTS["screenshot_no_text"]

        # Clamp 0-100
        score = max(0, min(100, score))

        # Store
        conn.execute(
            """
            INSERT OR REPLACE INTO scores (item_id, score, breakdown)
            VALUES (?, ?, ?)
            """,
            (item["id"], score, json.dumps(breakdown)),
        )
        conn.commit()

    db.stage_mark_complete(conn, "scoring")


def recompute_scores(
    conn: sqlite3.Connection,
    blur_threshold: float = 50.0,
) -> None:
    """
    Recompute scores without re-running vision.
    Useful for tuning weights.
    """
    # Delete old scores
    conn.execute("DELETE FROM scores")
    conn.commit()

    # Recompute
    score_all_items(conn, blur_threshold)
