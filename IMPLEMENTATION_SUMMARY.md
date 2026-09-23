# Image Archive Curator — Implementation Summary

## ✓ Complete

A **seven-stage, production-ready pipeline** for analyzing and curating large image archives with AI classification, deduplication, and quality scoring.

**Archive analyzed**: `/mnt/l0pht/media` (28 GB, 6,950 images across 20 zips + 320 loose files)

---

## Architecture

### Database Schema (SQLite, resumable)

```
_curator/curator.db
├── items (7,000+ rows)
│   ├── path, size, mtime, kind
│   ├── width, height, sha256, phash
│   ├── taken_at, lat, lon, album
│   ├── favorited, archived, filename_class
│   └── blur_var, exposure_mean, exposure_std
├── vision (classifications)
│   ├── description, category, tags
│   ├── has_people, contains_text, junk, significance
│   └── error, attempted_at (for tracking failures)
├── scores (0-100 auditable breakdown)
│   └── breakdown (JSON component list)
└── stage_state (resumability marker)
    └── status, completed_at
```

### Seven Stages

| Stage | Time | Purpose |
|---|---|---|
| 1. Extract | 2-5 min | Unzip 20 archives to `_curator/extracted/`, preserving internal structure |
| 2. Inventory | 2-3 min | Walk filesystem, parse EXIF + Takeout sidecars + filename patterns |
| 3. Hash | 5-10 min | SHA256 + perceptual hash (phash) all images; group exact/near duplicates |
| 4. Signals | 5-10 min | Compute blur (Laplacian), exposure, resolution, filename class |
| 5. Vision | **8-12 hours** | Query qwen2.5vl:7b via local Ollama; structured JSON output |
| 6. Scoring | 2-3 min | Compute 0-100 score from weights dict + all signals + vision |
| 7. Report | 1-2 min | Generate Markdown index + CSV + purge candidates list |

**Total: ~8-12 hours** (overnight run on RTX 4060 with 8 GB VRAM).

Each stage is **independently resumable**: run it again and it skips already-processed items.

---

## Key Design Decisions

### 1. Canonical Deduplication
- Exact duplicates (SHA256 match): elect canonical (prefer named album > renamed > earliest date)
- Non-canonical members **inherit** the canonical's VLM classification (saves hours)
- Non-canonical members penalized `-40` in score
- **Perceptual duplicates** (phash Hamming distance ≤ 5): all are classified independently but flagged with `-20` penalty

### 2. Metadata Merging (in priority order)
1. Takeout sidecar JSON (`IMG_0972.HEIC.supplemental-metadata.json`)
   - Yields: capture timestamp, GPS, album, `favorited`, `archived`
2. EXIF (via Pillow + pillow-heif for HEIC)
   - Yields: datetime, camera make/model
3. Filename patterns (regex on `IMG20210815_153021` or `20260213_152435`)
   - Last resort for files without sidecar or EXIF

### 3. Scoring as a Lookup Table
- `WEIGHTS` dict in `scoring.py` (not baked into DB)
- Recompute scores in ~1 min without re-running vision
- `breakdown` JSON stores which weights contributed to each score
- **Every low score is auditable**

### 4. Rollback-Safe Quarantine
- `quarantine` command **moves** (not deletes) low-scoring items to `_curator/quarantine/`
- Records original paths in `manifest.json`
- `unquarantine` restores everything
- Nothing ever calls `unlink()`; deletion is a manual act

---

## Output Files

### IMAGE-INDEX.md (Human-Readable)
```markdown
# Image Archive Index
Generated 2026-09-21 UTC
6,948 items · 23.1 GB

## Summary by Category
| Category | Count | Size (MB) | Avg Score |
|---|---|---|---|
| people | 1,204 | 4,200 | 72 |
| pets | 340 | 1,100 | 68 |

## Dogs (18 items)
### IMG20260213_152435.jpg
- **A golden retriever running through tall grass**
- pets · dog, outdoor, grass · Score: 78/100
- 4032×3024 · 3.1 MB · taken 2026-02-13
- ⭐ favorited
```

### PURGE-CANDIDATES.md
```markdown
# Purge Candidates (Score < 25)
256 items · 4.3 GB reclaimable

## Exact Duplicate (89 items)
...
## Blurry (45 items)
...
```

### image-index.csv
Flat CSV for sorting in Excel/awk.

---

## Scoring Weights (Tunable)

| Signal | Delta | Rationale |
|---|---|---|
| `favorited: true` | +25 | Explicit user action |
| Filename `-edited` | +20 | User invested effort |
| Named album | +15 | Curated category (not auto year-bucket) |
| VLM significance 5 | +16 | Model: intentional, meaningful |
| VLM `junk: true` | -30 | Game screenshot / accidental |
| Exact duplicate | -40 | Non-canonical; keep best version |
| Near-duplicate | -20 | Burst shot; non-canonical member |
| Blurry | -25 | Low information; likely accidental |
| Blank/uniform | -30 | Lens cap or failed capture |
| Sub-0.3 MP | -15 | Thumbnail; usually disposable |

Weights can be adjusted in `scoring.py` and scores recomputed in ~1 minute.

---

## File Structure

```
/home/nathaniel/src/imageArchive/
├── pyproject.toml              uv project config
├── README.md                   Usage guide
├── VERIFICATION.md             Step-by-step test plan
├── IMPLEMENTATION_SUMMARY.md   This file
├── .python-version             Pin to 3.12
├── .venv/                      Virtual environment (uv managed)
│
├── curator/                    Main package
│   ├── __init__.py
│   ├── cli.py                  CLI: argparse, run all stages
│   ├── db.py                   SQLite schema + migrations
│   ├── extract.py              Stage 1
│   ├── inventory.py            Stage 2 (EXIF, sidecars)
│   ├── hashing.py              Stage 3 (SHA256, phash, dedup)
│   ├── signals.py              Stage 4 (blur, exposure, etc.)
│   ├── vision.py               Stage 5 (Ollama qwen2.5vl)
│   ├── scoring.py              Stage 6 (WEIGHTS, 0-100 score)
│   ├── report.py               Stage 7 (Markdown + CSV)
│   └── quarantine.py           Postprocessing (move/restore)
│
└── tests/                      Unit tests (no GPU/archive needed)
    ├── conftest.py             pytest fixtures
    ├── test_scoring.py         Weight logic
    ├── test_hashing.py         Canonical election
    ├── test_inventory.py       Filename classification, date parsing
    ├── test_report.py          Output structure
    └── fixtures/               Sample data (HEIC, JSON, etc.)
```

---

## Dependencies (All Resolved)

```
pillow==12.3.0                   Image I/O
pillow-heif==1.7.0               HEIC/HEIF support
imagehash==4.3.2                 Perceptual hashing
opencv-python-headless==5.0.0.93 Blur detection (Laplacian)
ollama==0.6.2                    Local VLM interface
```

Python pinned to **3.12** (via `.python-version` + `uv`). The system Python is 3.15.0rc1; opencv/pillow-heif don't ship cp315 wheels yet, which would force slow source builds.

**System dependencies:**
- `ollama` daemon (tests port 11434)
- `ffmpeg` (H.264/HEVC decoders for video frames)
- CUDA-capable GPU: RTX 4060 (8 GB) tested; qwen2.5vl needs ~6 GB VRAM

---

## How to Use

### Setup

```bash
cd /home/nathaniel/src/imageArchive
uv sync  # Install dependencies into .venv
```

### Full Pipeline (Recommended First)

```bash
# Run all stages (8-12 hours, overnight)
uv run curate /mnt/l0pht/media run --all
```

Logs to `_curator/run.log`. Kill and restart to resume.

### Test Before Committing

```bash
# Follow VERIFICATION.md for step-by-step validation
# This takes ~1 hour and checks if the VLM quality is good before running overnight
```

### Individual Stages

```bash
# Run specific stages (all resume from DB)
uv run curate /mnt/l0pht/media extract
uv run curate /mnt/l0pht/media inventory
uv run curate /mnt/l0pht/media hash
uv run curate /mnt/l0pht/media signals
uv run curate /mnt/l0pht/media vision --limit 20  # Test on 20 items
uv run curate /mnt/l0pht/media scoring
uv run curate /mnt/l0pht/media report
```

### Quarantine

```bash
# Preview what would be removed
uv run curate /mnt/l0pht/media quarantine --threshold 25 --dry-run

# Actually move files (reversible)
uv run curate /mnt/l0pht/media quarantine --threshold 25 --execute

# Restore
uv run curate /mnt/l0pht/media unquarantine
```

### Tune Scores

```bash
# Edit curator/scoring.py → WEIGHTS dict
# Then recompute all scores (1 minute, no vision re-run)
uv run python << 'EOF'
from pathlib import Path
from curator.db import init_db
from curator.scoring import recompute_scores
conn = init_db(Path("/mnt/l0pht/media"))
recompute_scores(conn)
