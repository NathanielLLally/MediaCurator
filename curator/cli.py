"""Command-line interface for image archive curation."""

import argparse
import sys
from pathlib import Path

from . import db
from . import extract
from . import inventory
from . import hashing
from . import signals
from . import vision
from . import scoring
from . import report
from . import quarantine
from . import organize
from . import config


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze and curate image archives with AI classification.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "archive",
        type=Path,
        nargs="?",
        default=None,
        help="Root directory of the media archive (e.g., /mnt/l0pht/media). "
             "Optional for stages 3-7 (database auto-discovered from ~/.local/_curator/)",
    )

    parser.add_argument(
        "--extract-dir",
        type=Path,
        default=None,
        help="Directory to extract zips into (default: ARCHIVE/_curator/extracted). "
             "Useful if ARCHIVE has limited space",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # run: all stages
    run_parser = subparsers.add_parser("run", help="Run all stages")
    run_parser.add_argument(
        "--all",
        action="store_true",
        help="Run all stages (default is to resume incomplete)",
    )

    # extract
    extract_parser = subparsers.add_parser("extract", help="Stage 1: Extract zips")
    extract_parser.add_argument(
        "--only",
        help="Extract only this zip file",
    )

    # inventory
    subparsers.add_parser("inventory", help="Stage 2: Inventory items")

    # hash
    subparsers.add_parser("hash", help="Stage 3: Hash and dedup")

    # signals
    subparsers.add_parser("signals", help="Stage 4: Compute cheap signals")

    # vision
    vision_parser = subparsers.add_parser("vision", help="Stage 5: Vision classification")
    vision_parser.add_argument(
        "--limit",
        type=int,
        help="Limit vision to N items",
    )

    # scoring
    subparsers.add_parser("scoring", help="Stage 6: Compute scores")

    # report
    subparsers.add_parser("report", help="Stage 7: Generate reports")

    # organize
    subparsers.add_parser("organize", help="Stage 8: Organize by category with tag-based names")

    # quarantine
    quarantine_parser = subparsers.add_parser("quarantine", help="Quarantine low-scoring items")
    quarantine_parser.add_argument(
        "--threshold",
        type=int,
        default=25,
        help="Score threshold (default 25)",
    )
    quarantine_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print moves without executing (default)",
    )
    quarantine_parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually move files",
    )

    # unquarantine
    subparsers.add_parser("unquarantine", help="Restore quarantined items")

    # config
    config_parser = subparsers.add_parser("config", help="Configure extraction directory")
    config_parser.add_argument(
        "--set-extract-dir",
        type=Path,
        help="Set extraction directory in config",
    )
    config_parser.add_argument(
        "--show",
        action="store_true",
        help="Show current configuration",
    )

    args = parser.parse_args()

    # Validate archive argument based on command
    if args.command in ("run", "extract", "inventory", "config"):
        # These commands require archive
        if not args.archive:
            print("Error: archive path required for this command", file=sys.stderr)
            print("Usage: curate /path/to/archive <command>", file=sys.stderr)
            sys.exit(1)
        if not args.archive.exists():
            print(f"Error: archive not found: {args.archive}", file=sys.stderr)
            sys.exit(1)
    # For other commands (hash, signals, vision, scoring, report, quarantine),
    # archive is optional and will be discovered from database

    # Handle config subcommand early (doesn't need DB)
    if args.command == "config":
        if args.set_extract_dir:
            cfg = config.load_config(args.archive)
            cfg["extract_dir"] = str(args.set_extract_dir.resolve())
            config.save_config(args.archive, cfg)
            print(f"✓ Extraction directory set to {args.set_extract_dir}")
            return
        elif args.show:
            cfg = config.load_config(args.archive)
            print("\n" + "=" * 60)
            print("CURRENT CONFIGURATION")
            print("=" * 60)
            print(f"\nArchive root: {args.archive}")
            for key, value in cfg.items():
                if key != "extract_dir":  # Show this separately
                    print(f"  {key}: {value}")
            print(f"  extract_dir: {cfg.get('extract_dir')}")

            # Show space info
            print("\nSpace Analysis:")
            space_info = config.estimate_uncompressed_size(args.archive)
            comp = config.format_bytes(space_info["total_compressed"])
            uncomp = config.format_bytes(space_info["total_uncompressed"])
            ratio = space_info["ratio"]

            print(f"  Compressed (zips):   {comp}")
            print(f"  Uncompressed:        {uncomp}")
            print(f"  Compression ratio:   {ratio:.1f}x")
            print(f"  Zips found:          {space_info['zip_count']}")

            # Check available space
            extract_path = Path(cfg.get("extract_dir"))
            try:
                stat = extract_path.stat()
                available = Path(extract_path).stat().st_size
                # Try to get actual available space
                import shutil
                available = shutil.disk_usage(str(extract_path)).free
                available_fmt = config.format_bytes(available)
                needed = space_info["total_uncompressed"]

                print(f"\nTarget directory: {extract_path}")
                print(f"  Available space: {available_fmt}")
                if available >= needed:
                    print(f"  ✓ Sufficient space for extraction")
                else:
                    needed_fmt = config.format_bytes(needed)
                    print(f"  ✗ WARNING: Only {available_fmt} available, need {needed_fmt}")
            except Exception as e:
                print(f"\nTarget directory: {extract_path}")
                print(f"  (Could not check available space: {e})")

            return
        else:
            # Interactive setup
            cfg = config.interactive_setup(args.archive)
            return

    # Initialize database
    # For stages 1-2 (extract, inventory): need archive to know which DB
    # For stages 3-7 (hash, signals, vision, scoring, report): archive is optional, DB is discovered
    conn = db.init_db(args.archive)

    # Resolve extract directory only for stages 1-2 (extract, inventory)
    # Stages 3-7 only use the database
    extract_dir = None
    if args.command in ("run", "extract", "inventory"):
        if args.extract_dir:
            extract_dir = args.extract_dir.resolve()
        else:
            cfg = config.load_config(args.archive)
            extract_dir = Path(cfg["extract_dir"]).resolve()

    try:
        if args.command == "run":
            run_all_stages(args.archive, conn, extract_dir)
        elif args.command == "extract":
            extract.extract_all_zips(args.archive, extract_dir, args.only)
        elif args.command == "inventory":
            inventory.walk_and_inventory(conn, extract_dir)
        elif args.command == "hash":
            hashing.hash_all_items(args.archive, conn)
        elif args.command == "signals":
            signals.compute_all_signals(conn)
        elif args.command == "vision":
            vision.classify_all_items(conn, limit=args.limit)
        elif args.command == "scoring":
            scoring.score_all_items(conn)
        elif args.command == "report":
            # archive is optional for report (stages 3-7)
            report.generate_reports(args.archive, conn)
        elif args.command == "organize":
            # archive is optional for organize (stages 3-8)
            organize.organize_by_category(conn, extract_dir)
        elif args.command == "quarantine":
            # archive is optional for quarantine (stages 3-7)
            dry_run = args.dry_run and not args.execute
            quarantine.quarantine_items(args.archive, conn, args.threshold, dry_run=dry_run)
        elif args.command == "unquarantine":
            # archive is optional for unquarantine (stages 3-7)
            quarantine.unquarantine_items(args.archive, conn)
        else:
            parser.print_help()

    finally:
        conn.close()


def run_all_stages(archive_root: Path, conn, extract_dir: Path):
    """Run all stages in sequence."""
    print(f"Curating archive: {archive_root}")
    print(f"Extraction dir: {extract_dir}")
    print()

    # Stage 1: Extract
    print("=" * 60)
    print("STAGE 1: EXTRACT")
    print("=" * 60)
    extract.extract_all_zips(archive_root, extract_dir)
    print()

    # Stage 2: Inventory
    print("=" * 60)
    print("STAGE 2: INVENTORY")
    print("=" * 60)
    inventory.walk_and_inventory(conn, extract_dir)
    print()

    # Stage 3: Hash
    print("=" * 60)
    print("STAGE 3: HASH & DEDUP")
    print("=" * 60)
    hashing.hash_all_items(archive_root, conn)
    print()

    # Stage 4: Signals
    print("=" * 60)
    print("STAGE 4: CHEAP SIGNALS")
    print("=" * 60)
    signals.compute_all_signals(conn)
    print()

    # Stage 5: Vision
    print("=" * 60)
    print("STAGE 5: VISION CLASSIFICATION")
    print("=" * 60)
    vision.classify_all_items(conn)
    print()

    # Stage 6: Scoring
    print("=" * 60)
    print("STAGE 6: SCORING")
    print("=" * 60)
    scoring.score_all_items(conn)
    print()

    # Stage 7: Report
    print("=" * 60)
    print("STAGE 7: REPORTS")
    print("=" * 60)
    report.generate_reports(archive_root, conn)
    print()

    print("=" * 60)
    print("COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
