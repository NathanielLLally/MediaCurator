# Verification Steps

Follow this checklist before committing to the full 8-12 hour run. Each step should complete in minutes, not hours.

## 1. Verify Installation

```bash
cd /home/nathaniel/src/imageArchive
uv run curator --help
```

Should show the CLI help text. If it hangs, opencv/cv2 initialization might be slow on first import.

## 2. Small Extraction Test (1-2 minutes)

Extract a tiny zip to verify paths and basic flow:

```bash
uv run curate /mnt/l0pht/media extract --only "Photos (1).zip"
```

This extracts a single 5.2 MB file. Check that:
- ✓ `_curator/extracted/Photos--1-001/` exists
- ✓ Internal structure is preserved
- ✓ Sidecar JSON files are present

## 3. Inventory + Hash on Loose Files (3-5 minutes)

Test on the 16 real images already on disk:

```bash
uv run curate /mnt/l0pht/media inventory
uv run curate /mnt/l0pht/media hash
```

Then verify the database:

```bash
uv run python << 'EOF'
import sqlite3
from pathlib import Path
conn = sqlite3.connect("/mnt/l0pht/media/_curator/curator.db")
conn.row_factory = sqlite3.Row

# Count items
count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
print(f"Inventory: {count} items")

# Check hashes
with_hash = conn.execute("SELECT COUNT(*) FROM items WHERE sha256 IS NOT NULL").fetchone()[0]
print(f"Hashed: {with_hash}")

# Sample item
sample = conn.execute("""
    SELECT path, width, height, taken_at, album, sha256
    FROM items
    LIMIT 1
""").fetchone()

if sample:
    print(f"\nSample item:")
    print(f"  Path: {sample['path']}")
    print(f"  Dimensions: {sample['width']}x{sample['height']}")
    print(f"  Date: {sample['taken_at']}")
    print(f"  Album: {sample['album']}")
    print(f"  SHA256: {sample['sha256'][:12]}...")
EOF
```

Expected: 16-20 items, most should have hashes and dimensions.

## 4. Compute Cheap Signals (1-2 minutes)

```bash
uv run curate /mnt/l0pht/media signals
```

Check results:

```bash
uv run python << 'EOF'
import sqlite3
conn = sqlite3.connect("/mnt/l0pht/media/_curator/curator.db")
conn.row_factory = sqlite3.Row

# Blur statistics
stats = conn.execute("""
    SELECT
        COUNT(*) as cnt,
        AVG(blur_var) as avg_blur,
        MIN(blur_var) as min_blur,
        MAX(blur_var) as max_blur
    FROM items
    WHERE blur_var IS NOT NULL
""").fetchone()

print("Blur statistics:")
print(f"  {stats['cnt']} items with blur")
print(f"  Average: {stats['avg_blur']:.1f}")
print(f"  Range: {stats['min_blur']:.1f} - {stats['max_blur']:.1f}")
EOF
```

Expected: Most images should have blur variance > 50 (sharp).

## 5. Test Vision on 20 Items (15-30 minutes)

```bash
uv run curate /mnt/l0pht/media vision --limit 20
```

This queries the VLM for the first 20 items. **Read the output manually.** Check:
- ✓ Can it tell a dog photo from a screenshot?
- ✓ Are tags sensible?
- ✓ Do significance ratings match your intuition (1=blurry/accidental, 5=professional)?

Then verify storage:

```bash
uv run python << 'EOF'
import sqlite3
conn = sqlite3.connect("/mnt/l0pht/media/_curator/curator.db")
conn.row_factory = sqlite3.Row

# Vision results
visions = conn.execute("""
    SELECT i.path, v.description, v.category, v.significance
    FROM items i
    LEFT JOIN vision v ON i.id = v.item_id
    LIMIT 5
""").fetchall()

print("First 5 vision results:")
for v in visions:
    print(f"\n  {v['path'].split('/')[-1]}")
    print(f"    Category: {v['category']}")
    print(f"    Significance: {v['significance']}")
    print(f"    Description: {v['description']}")
EOF
```

Expected: All 20 should have vision results (or errors marked).

## 6. Score the Sample (1-2 minutes)

```bash
uv run curate /mnt/l0pht/media scoring
uv run curate /mnt/l0pht/media report
```

Review the generated files:

```bash
cat /mnt/l0pht/media/IMAGE-INDEX.md | head -50
cat /mnt/l0pht/media/PURGE-CANDIDATES.md | head -20
```

Verify:
- ✓ Markdown is well-formed
- ✓ Known-good images (named album, high significance) score high
- ✓ Known-bad items (screenshot files) score low or appear in PURGE-CANDIDATES

Sample query:

```bash
uv run python << 'EOF'
import sqlite3
conn = sqlite3.connect("/mnt/l0pht/media/_curator/curator.db")
conn.row_factory = sqlite3.Row

# Top scorers
top = conn.execute("""
    SELECT i.path, s.score, v.description
    FROM items i
    LEFT JOIN scores s ON i.id = s.item_id
    LEFT JOIN vision v ON i.id = v.item_id
    WHERE s.score IS NOT NULL
    ORDER BY s.score DESC
    LIMIT 3
""").fetchall()

print("Top scorers:")
for item in top:
    print(f"  {item['score']:.0f}: {item['path'].split('/')[-1]}")

# Bottom scorers
bottom = conn.execute("""
    SELECT i.path, s.score, v.description
    FROM items i
    LEFT JOIN scores s ON i.id = s.item_id
    LEFT JOIN vision v ON i.id = v.item_id
    WHERE s.score IS NOT NULL
    ORDER BY s.score ASC
    LIMIT 3
""").fetchall()

print("\nBottom scorers:")
for item in bottom:
    print(f"  {item['score']:.0f}: {item['path'].split('/')[-1]}")
EOF
```

## 7. Time the Sample

From step 5, measure how long vision took:

```
vision --limit 20 took: ~X minutes
20 items / X minutes = Y items/minute
6,950 total items / Y = ~Z hours projected
```

If Z > 15 hours, consider using `moondream` or a two-pass approach.

## 8. Resumability Test

Kill the database after step 6, then re-run step 5:

```bash
# Simulate partial run
rm -r /mnt/l0pht/media/_curator/curator.db

# Re-run from step 2
uv run curate /mnt/l0pht/media inventory
uv run curate /mnt/l0pht/media hash
uv run curate /mnt/l0pht/media signals

# Count how many items were re-processed
uv run python << 'EOF'
import sqlite3
conn = sqlite3.connect("/mnt/l0pht/media/_curator/curator.db")
count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
print(f"{count} items in database (should be same as before)")
EOF
```

Expected: Same item count, database rebuilt cleanly.

## 9. Full Run

Once all above pass, commit to the full run. Use `nohup` or `screen` so it can run unattended:

```bash
nohup uv run curate /mnt/l0pht/media run --all > /mnt/l0pht/media/_curator/run.log 2>&1 &
# OR
screen -S curator
uv run curate /mnt/l0pht/media run --all
# Then Ctrl-A, D to detach
```

Monitor progress:

```bash
tail -f /mnt/l0pht/media/_curator/run.log
```

Expected runtime: 8-12 hours for 6,950 images on RTX 4060.

## 10. Quarantine Preview (Dry-Run)

After step 9 completes, preview what would be removed:

```bash
uv run curate /mnt/l0pht/media quarantine --threshold 25 --dry-run
```

Review `/mnt/l0pht/media/PURGE-CANDIDATES.md`:
- Does the grouping make sense?
- Are low-scoring duplicates included?
- Is the reclaimable space reasonable?

## 11. Actual Quarantine (Optional)

Only if you're confident in the scores:

```bash
uv run curate /mnt/l0pht/media quarantine --threshold 25 --execute
```

This moves files to `_curator/quarantine/`, recording original paths in `manifest.json`.

To undo:

```bash
uv run curate /mnt/l0pht/media unquarantine
```

## Troubleshooting

### Step 5 hangs or errors

- Check `ollama list` — qwen2.5vl should be present
- Check `ollama serve` is running: `ps aux | grep ollama`
- Try manually: `ollama run qwen2.5vl "Describe this image in one sentence"` (paste base64 image)

### Step 6 produces no files

Check database permissions:

```bash
ls -la /mnt/l0pht/media/_curator/
```

Should be writable. If read-only, you may need to run with `sudo`.

### Memory/GPU issues

Check available VRAM:

```bash
nvidia-smi
```

qwen2.5vl needs ~6 GB. If you have less:
- Use `moondream` (1.8 GB) — faster but less accurate
- Downscale images further in `vision.py`
- Process in batches with `--limit 100` and multiple runs

### Want to re-run just one stage

```bash
# Delete that stage's state
uv run python << 'EOF'
import sqlite3
conn = sqlite3.connect("/mnt/l0pht/media/_curator/curator.db")
conn.execute("DELETE FROM stage_state WHERE stage = 'vision'")
conn.execute("DELETE FROM vision")
conn.commit()
EOF

# Re-run
uv run curate /mnt/l0pht/media vision
```

Repeat for other stages (inventory, hash, signals, scoring, report).
