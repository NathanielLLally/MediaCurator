# Image Archive Curator

A local, privacy-first pipeline for analyzing, categorizing, and curating large photo archives. Extracts from compressed zips, classifies images with a local vision model, scores by significance, and reorganizes files by category with AI-generated tag-based names.

## Description

The curator processes a media archive through eight resumable stages:
1. **Extract** — Unzip all archives, preserving structure
2. **Inventory** — Walk filesystem, parse EXIF and metadata sidecars
3. **Hash** — SHA256 and perceptual hashing for deduplication
4. **Signals** — Compute blur, exposure, resolution, filename class
5. **Vision** — Classify via local [qwen2.5vl:7b](https://ollama.ai/library/qwen2.5-vl) model
6. **Scoring** — Rank items 0–100 based on signals and VLM output
7. **Report** — Generate Markdown index, purge candidates list, CSV export
8. **Organize** — Reorganize by category with tag-based snake_case names

All state is stored in a single SQLite database, making runs fully resumable. No data is ever deleted by the tool — quarantine operations move files, not destroy them. The archive directory itself remains completely untouched; all working files go to `~/.local/_curator/` and `~/.cache/_curator/`.

## Usage

### Prerequisites

- Python 3.12+
- `uv` package manager
- `ollama` daemon running (install from [ollama.ai](https://ollama.ai))
- ~25 GB free space (for extraction, database, reports)
- CUDA GPU recommended for vision stage (tested on RTX 4060 8GB)

### Installation

```bash
git clone git@github.com:NathanielLLally/MediaCurator.git
cd MediaCurator
uv sync  # Install dependencies
```

### Quick Start

```bash
# Full pipeline on archive at /mnt/l0pht/media
uv run curate /mnt/l0pht/media run --all

# Or run stages individually
uv run curate /mnt/l0pht/media extract
uv run curate /mnt/l0pht/media inventory
uv run curate hash
uv run curate signals
uv run curate vision
uv run curate scoring
uv run curate report
uv run curate organize

# Quarantine low-scoring items (dry-run by default)
uv run curate quarantine --threshold 25 --dry-run
uv run curate quarantine --threshold 25 --execute  # Commit the move

# Restore quarantined items
uv run curate unquarantine
```

### Configuration

```bash
# Interactive setup (extracts to configured directory)
uv run curate /mnt/l0pht/media config

# Show current config
uv run curate /mnt/l0pht/media config --show

# Set extraction directory
uv run curate /mnt/l0pht/media config --set-extract-dir /mnt/fast-ssd/extracted
```

### Run in Background

```bash
nohup uv run curate /mnt/l0pht/media run --all > curator.log 2>&1 &
tail -f curator.log
```

## Dependencies

| Library | Version | Purpose | Link |
|---|---|---|---|
| Python | 3.12 | Runtime | [python.org](https://www.python.org/) |
| Pillow | 12.3.0 | Image I/O | [pillow.palletsprojects.com](https://pillow.palletsprojects.com/) |
| pillow-heif | 1.7.0 | HEIC support | [github.com/pillow-heif](https://github.com/pillow/pillow-heif) |
| imagehash | 4.3.2 | Perceptual hashing | [pypi.org/imagehash](https://pypi.org/project/ImageHash/) |
| opencv-python-headless | 5.0.0.93 | Blur detection | [opencv.org](https://opencv.org/) |
| ollama | 0.6.2 | Vision model client | [ollama.ai](https://ollama.ai/) |

### System Tools

- `ffmpeg` with h264/hevc support (for video frame extraction)
- `exiftool` (for metadata parsing)

## Files

### Project Structure

```
/home/nathaniel/src/imageArchive/
├── README.md                          # This file
├── pyproject.toml                     # Project config, dependencies
├── .python-version                    # Pin Python 3.12
├── .gitignore
└── curator/
    ├── __init__.py
    ├── cli.py                         # Entry point, subcommand routing
    ├── db.py                          # SQLite schema, migrations, connection
    ├── config.py                      # Config file I/O (extract dir, etc.)
    ├── extract.py                     # Stage 1: Unzip archives
    ├── inventory.py                   # Stage 2: Walk and index
    ├── hashing.py                     # Stage 3: SHA256 + perceptual hashing
    ├── signals.py                     # Stage 4: Blur, exposure, resolution
    ├── vision.py                      # Stage 5: Ollama classification
    ├── scoring.py                     # Stage 6: 0–100 score computation
    ├── report.py                      # Stage 7: Markdown/CSV reports
    └── organize.py                    # Stage 8: Category-based reorganization
```

### Data Locations

**Persistent state** (`~/.local/_curator/`):
- `<archive_name>.db` — SQLite database (all pipeline state, resumability)
- `<archive_name>.db-wal`, `.db-shm` — WAL and shared memory (concurrent access)
- `<archive_name>.json` — Configuration (extraction directory, thresholds)

**Working files** (`~/.cache/_curator/`):
- `extracted/<archive_name>/` — Unzipped images (recreatable, ~20 GB)
- `reports/<archive_name>_INDEX.md` — Full album index with scores
- `reports/<archive_name>_PURGE-CANDIDATES.md` — Low-scoring items
- `reports/<archive_name>_index.csv` — Flat export
- `quarantine/<archive_name>/` — Quarantined items + manifest
- `vision.log` — Classification log

**Archive directory** (e.g., `/mnt/l0pht/media/`):
- Remains completely untouched by the curator

## Scoring Weights

Scores range 0–100 and are built from weighted signals:

**Positive signals** (raise score):
- `favorited: true` → +25
- Edited filename → +20
- Named album → +15
- VLM significance 4–5 → +8–16
- Contains people or pets → +10 each

**Negative signals** (lower score):
- VLM junk flag → −30
- Exact duplicate (non-canonical) → −40
- Near-duplicate → −20
- Blurry image → −25
- Blank/uniform exposure → −30
- Resolution < 0.3 MP → −15
- Archived flag → −15
- Screenshot without text → −10

Weights are stored in `curator/scoring.py` and can be tuned without re-running vision — just call `uv run curate scoring` to recompute all scores.

## Author

**Nathaniel Lally** — Original vision, data curation, and requirements.

**Claude Code** — Implementation, debugging, and ongoing maintenance ([Anthropic](https://www.anthropic.com/)).

