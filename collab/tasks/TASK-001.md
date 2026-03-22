---
id: TASK-001
title: Decode OGM grid data encoding
status: open
priority: high
depends_on: []
estimated_effort: large
skills: [reverse-engineering, binary-analysis, compression, protobuf]
---

# Decode OGM Grid Data Encoding

## Context

Trifo Lucy robot vacuum stores occupancy grid maps as `.ogm` files. These use
a non-standard protobuf format with several firmware bugs (7-byte cell_size
encoding, lying length varints). The protobuf envelope is fully parsed — we can
extract all metadata fields including grid dimensions, resolution, origin, and
a raw grid data blob (protobuf field 8).

**The grid data blob encoding is unknown.** It's NOT standard compression
(not zlib, gzip, lz4, zstd, brotli, snappy, bz2, lzma, deflate, lzo).
It's NOT raw pixel data. It's likely a custom Huffman variant compiled into
the `navigation_node` binary on the robot.

Read `docs/ogm_format_research.md` FIRST — it contains everything known, including
hex dumps, firmware analysis, and a full list of what's been tried and ruled out.

## Objective

Produce a decoder (any language) that takes the field 8 grid data blob and
outputs a 2D occupancy grid matching the dimensions in the protobuf metadata.
"Done" means: decoded grid visually resembles a room/floor plan when rendered.

## Inputs

- `docs/ogm_format_research.md` — the complete research document (start here)
- `docs/samples/` — sample data and tools:
  - `small_map.ogm` (133 KB) — smaller OGM file
  - `newest_map.ogm` (128 KB) — recent OGM file
  - `synced_map.ogm` (128 KB) — OGM with matching SHM ground truth (request bins from maintainer)
  - `shm_reference.png` — visual render of the SHM grid (this is what decoded OGM should look like)
  - `parse_ogm_proto.py` — working protobuf parser (extracts all fields including grid blob)
  - `decode_ogm.py` — decode attempt script (shows field extraction)
  - `compare_ogm_shm.py` — comparison tool for OGM vs SHM data
- Firmware binary: `navigation_node` (aarch64 ELF) contains the decoder — not
  included due to size, request from maintainer if needed

## Approaches to Try

In rough priority order (see research doc section 13 for full rationale):

1. **Firmware disassembly** — find the Huffman/decode routine in `navigation_node`.
   Look for the SHM write path (`shm_open` / `mmap`) and trace backwards to find
   what transforms field 8 data into the grid buffer. Needs aarch64 disassembly
   (Ghidra/IDA).

2. **Runtime capture** — if you have robot access, dump the SHM segment while
   an OGM file is being loaded. Compare raw grid (SHM) with encoded grid (field 8)
   to identify the encoding.

3. **Statistical / entropy analysis** — the research doc has some initial byte
   frequency analysis. A custom Huffman would have a code table embedded either in
   the data or in the binary. Look for it.

4. **APK analysis** — the Trifo Home Android app (`com.trifo.home`) has map
   rendering code. The APK is decompiled. The LZ4+Base64 transport layer is
   understood, but the grid data decoder in the app hasn't been fully traced yet.

## Constraints

- Do NOT attempt to connect to or modify the robot — this is analysis only
- Do NOT include any credentials or keys in submissions
- If you need sample data beyond what's in the research doc, describe what you
  need and the maintainer will extract it

## Hints

- The grid data likely has a small header (code table?) followed by bitstream
- Values should map to 0-255 occupancy (0=free, 255=occupied, 128=unknown — or similar)
- Grid dimensions from protobuf metadata: typically 2001x2001 or 1001x1001
- The `cell_size` field has a firmware bug — see research doc section 6
- Look at byte offset patterns in field 8 data — if Huffman, there may be
  a recognizable code table structure in the first N bytes
