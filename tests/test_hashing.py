"""Tests for deduplication logic."""

import pytest
from curator import hashing


def test_canonical_election_named_album():
    """Named albums should beat year buckets."""
    members = [
        {"id": 1, "album": "Photos from 2022", "filename_class": "camera_default", "taken_at": "2022-01-01"},
        {"id": 2, "album": "Dogs", "filename_class": "camera_default", "taken_at": "2022-01-01"},
    ]

    canonical = hashing.elect_canonical(members)
    assert canonical["id"] == 2  # "Dogs" > "Photos from 2022"


def test_canonical_election_renamed():
    """Renamed files should beat camera defaults."""
    members = [
        {"id": 1, "album": None, "filename_class": "camera_default", "taken_at": "2022-01-01"},
        {"id": 2, "album": None, "filename_class": "renamed", "taken_at": "2022-01-01"},
    ]

    canonical = hashing.elect_canonical(members)
    assert canonical["id"] == 2


def test_canonical_election_earliest():
    """Earlier dates should beat later ones (all else equal)."""
    members = [
        {"id": 1, "album": None, "filename_class": "camera_default", "taken_at": "2022-12-31"},
        {"id": 2, "album": None, "filename_class": "camera_default", "taken_at": "2022-01-01"},
    ]

    canonical = hashing.elect_canonical(members)
    assert canonical["id"] == 2  # Earlier date


def test_canonical_election_with_blur():
    """Sharper images should be canonical when preferring sharpness."""
    members = [
        {"id": 1, "album": None, "filename_class": "camera_default", "taken_at": "2022-01-01", "blur_var": 10.0},
        {"id": 2, "album": None, "filename_class": "camera_default", "taken_at": "2022-01-01", "blur_var": 100.0},
    ]

    canonical = hashing.elect_canonical(members, prefer_blur_sharpness=True)
    assert canonical["id"] == 2  # Sharper (higher blur_var)


def test_edit_class_beats_camera_default():
    """Files marked as edited should rank higher."""
    members = [
        {"id": 1, "album": None, "filename_class": "camera_default", "taken_at": "2022-01-01"},
        {"id": 2, "album": None, "filename_class": "edited", "taken_at": "2022-01-01"},
    ]

    canonical = hashing.elect_canonical(members)
    assert canonical["id"] == 2
