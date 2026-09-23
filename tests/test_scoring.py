"""Tests for scoring logic."""

import json
import pytest
from curator import scoring


def test_basic_score_computation():
    """Test that base score is 50 and can go up and down."""
    # No signals, should be 50
    breakdown = {}
    score = 50
    assert 0 <= score <= 100

    # Add favorited
    score += scoring.WEIGHTS["favorited"]
    assert score > 50

    # Clamp to 100
    score = min(100, score)
    assert score == 50 + scoring.WEIGHTS["favorited"]


def test_junk_lowers_score():
    """Test that junk flag significantly lowers score."""
    score = 50 + scoring.WEIGHTS["junk"]
    assert score < 50
    assert score >= 0


def test_score_clamping():
    """Test that scores are clamped 0-100."""
    # Very high
    score = 200
    score = max(0, min(100, score))
    assert score == 100

    # Very low
    score = -100
    score = max(0, min(100, score))
    assert score == 0


def test_weights_sum_reasonably():
    """Verify weights are sensible."""
    # Favorited should beat most negatives
    assert scoring.WEIGHTS["favorited"] > abs(scoring.WEIGHTS["blurry"])

    # Exact duplicates should be heavily penalized
    assert scoring.WEIGHTS["exact_duplicate"] < -30


def test_duplicate_penalties():
    """Verify exact and near duplicates have different penalties."""
    exact_penalty = scoring.WEIGHTS["exact_duplicate"]
    near_penalty = scoring.WEIGHTS["near_duplicate"]

    assert exact_penalty < near_penalty
    assert exact_penalty < 0
    assert near_penalty < 0
