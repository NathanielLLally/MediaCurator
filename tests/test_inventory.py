"""Tests for inventory and metadata extraction."""

import pytest
from curator import inventory


def test_classify_filename_screenshot():
    """Filenames starting with Screenshot should be classified as such."""
    assert inventory.classify_filename("Screenshot_2022-01-01.png") == "screenshot"
    assert inventory.classify_filename("screenshot_ui.jpg") == "screenshot"


def test_classify_filename_edited():
    """Filenames with -edited should be classified as such."""
    assert inventory.classify_filename("photo-edited.jpg") == "edited"
    assert inventory.classify_filename("image_-edited-.png") == "edited"


def test_classify_filename_renamed():
    """Files with _original or -original should be classified as renamed."""
    assert inventory.classify_filename("lake_Original.jpg") == "renamed"
    assert inventory.classify_filename("photo-original.png") == "renamed"


def test_classify_filename_camera_default():
    """IMG or IMG20 patterns should be camera defaults."""
    assert inventory.classify_filename("IMG_1234.jpg") == "camera_default"
    assert inventory.classify_filename("IMG20210815_153021.jpg") == "camera_default"


def test_classify_filename_other():
    """Random names should be classified as other."""
    assert inventory.classify_filename("photo.jpg") == "other"
    assert inventory.classify_filename("DSC0001.jpg") == "other"


def test_parse_date_img_pattern():
    """Should extract dates from IMG20210815_153021 pattern."""
    date = inventory.parse_date_from_filename("IMG20210815_153021")
    assert date == "2021-08-15"


def test_parse_date_underscore_pattern():
    """Should extract dates from 20260213_152435 pattern."""
    date = inventory.parse_date_from_filename("20260213_152435")
    assert date == "2026-02-13"


def test_parse_date_none():
    """Should return None if no date pattern found."""
    date = inventory.parse_date_from_filename("random_photo.jpg")
    assert date is None


def test_parse_date_priority():
    """Should match the first date pattern found."""
    # Photo taken 2021, but filename has 2022
    date = inventory.parse_date_from_filename("DSC_2022_from_2021_trip.jpg")
    assert date == "2022-01-01"  # Should extract 2022 if parseable


def test_extension_inclusion():
    """Verify image and video extensions are recognized."""
    assert ".jpg" in inventory.IMAGE_EXTENSIONS
    assert ".heic" in inventory.IMAGE_EXTENSIONS
    assert ".png" in inventory.IMAGE_EXTENSIONS
    assert ".mp4" in inventory.VIDEO_EXTENSIONS
    assert ".mov" in inventory.VIDEO_EXTENSIONS
