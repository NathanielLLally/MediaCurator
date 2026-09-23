"""Configuration management for curator."""

import json
import zipfile
from pathlib import Path
from typing import Optional


def get_config_path(archive_root: Path) -> Path:
    """Get path to curator config file in ~/.local/_curator/"""
    config_home = Path.home() / ".local" / "_curator"
    config_home.mkdir(parents=True, exist_ok=True)

    # Name config by archive path: /mnt/l0pht/media → mnt_l0pht_media.json
    from curator.db import path_to_dbname
    config_name = path_to_dbname(archive_root).replace(".db", ".json")
    return config_home / config_name


def load_config(archive_root: Path) -> dict:
    """Load configuration from curator.json, or return defaults."""
    config_path = get_config_path(archive_root)

    # Default extraction directory: ~/.cache/_curator/extracted/<archive_name>
    from curator.db import path_to_dbname
    archive_name = path_to_dbname(archive_root).replace(".db", "")
    default_extract = Path.home() / ".cache" / "_curator" / "extracted" / archive_name

    defaults = {
        "extract_dir": str(default_extract),
        "blur_threshold": 50.0,
        "score_threshold": 25,
    }

    if config_path.exists():
        try:
            with open(config_path) as f:
                user_config = json.load(f)
                defaults.update(user_config)
        except Exception as e:
            print(f"Warning: could not load config from {config_path}: {e}")

    return defaults


def save_config(archive_root: Path, config: dict) -> None:
    """Save configuration to curator.json."""
    config_path = get_config_path(archive_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Configuration saved to {config_path}")


def format_bytes(num_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def estimate_uncompressed_size(archive_root: Path) -> dict:
    """
    Estimate uncompressed size of all zips in the archive.

    Returns dict with:
      - total_compressed: total bytes of all .zip files
      - total_uncompressed: sum of uncompressed sizes within zips
      - zip_count: number of zip files found
      - ratio: compression ratio (uncompressed / compressed)
    """
    zip_files = sorted(archive_root.glob("*.zip"))

    total_compressed = 0
    total_uncompressed = 0
    zip_count = len(zip_files)

    for zip_path in zip_files:
        # Add compressed size
        total_compressed += zip_path.stat().st_size

        # Add uncompressed size from zip contents
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    total_uncompressed += info.file_size
        except Exception as e:
            print(f"  Warning: could not read {zip_path.name}: {e}")

    ratio = total_uncompressed / total_compressed if total_compressed > 0 else 1.0

    return {
        "total_compressed": total_compressed,
        "total_uncompressed": total_uncompressed,
        "zip_count": zip_count,
        "ratio": ratio,
    }


def interactive_setup(archive_root: Path) -> dict:
    """Interactively prompt user for configuration."""
    print("\n" + "=" * 60)
    print("CURATOR CONFIGURATION SETUP")
    print("=" * 60)

    # Default extraction directory: ~/.cache/_curator/extracted/<archive_name>
    from curator.db import path_to_dbname
    archive_name = path_to_dbname(archive_root).replace(".db", "")
    default_extract = Path.home() / ".cache" / "_curator" / "extracted" / archive_name

    print(f"\nArchive root: {archive_root}")
    print(f"Default extract dir: {default_extract}")
    print(f"Config file: {get_config_path(archive_root)}")

    # Estimate space
    print("\nEstimating uncompressed space requirements...")
    space_info = estimate_uncompressed_size(archive_root)
    comp = format_bytes(space_info["total_compressed"])
    uncomp = format_bytes(space_info["total_uncompressed"])
    ratio = space_info["ratio"]

    print(f"\nSpace Analysis:")
    print(f"  Compressed (zips):   {comp}")
    print(f"  Uncompressed:        {uncomp}")
    print(f"  Compression ratio:   {ratio:.1f}x")
    print(f"  Space needed:        {uncomp}")

    if space_info["zip_count"] == 0:
        print("  Warning: No .zip files found in archive")

    while True:
        extract_input = input(f"\nExtraction directory [{default_extract}]: ").strip()
        if not extract_input:
            extract_dir = default_extract
        else:
            extract_dir = Path(extract_input).resolve()

        # Check if path is writable
        try:
            extract_dir.mkdir(parents=True, exist_ok=True)
            test_file = extract_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
            print(f"✓ Extract directory writable: {extract_dir}")
            break
        except Exception as e:
            print(f"✗ Cannot write to {extract_dir}: {e}")
            print("  Please choose a different path")

    config = {
        "extract_dir": str(extract_dir),
        "blur_threshold": 50.0,
        "score_threshold": 25,
    }

    save_config(archive_root, config)

    return config
