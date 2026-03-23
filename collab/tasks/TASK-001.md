---
id: TASK-001
title: Decode OGM grid data encoding
status: in_progress
priority: high
depends_on: []
estimated_effort: large
skills: [reverse-engineering, binary-analysis, aarch64, compression]
---

# Decode OGM Grid Data Encoding

## Context

Trifo Lucy robot vacuum stores occupancy grid maps as `.ogm` files. These use
a non-standard protobuf format with several firmware bugs (7-byte cell_size
encoding, lying length varints). The protobuf envelope is fully parsed — we can
extract all metadata fields including grid dimensions, resolution, origin, and
a raw grid data blob (protobuf field 6).

**The grid data blob encoding is unknown.** It's NOT standard compression
(not zlib, gzip, lz4, zstd, brotli, snappy, bz2, lzma, deflate, lzo).
It's NOT raw pixel data. It's a custom encoding in the firmware.

Read `docs/ogm_format_research.md` FIRST — it contains everything known.

## What's Been Ruled Out

See `submissions/TASK-001_daneel_01/SUBMISSION.md` for detailed analysis:

- **All standard compression formats** — none match
- **Nested protobuf** — dominant bytes (0x00, 0x08, 0x04, 0x0d, 0x22) are
  coincidental, not wire format tags
- **APK decode path** — the APK never sees encoded grid data. The robot
  decodes OGM to raw cells then sends to cloud. **TASK-003 is eliminated.**
- **`trifo::Huffman` class in libtrifo_core_cloud.so** — disassembled in full
  (see `docs/firmware/huffman_disasm_analysis.md`). Uses a TEXT header format.
  OGM grid data starts with binary `80 01 00`, not text. **Not the OGM encoder.**

## What's Known About the Encoding

From statistical analysis (daneel_01 submission):

- **`0d 04 08 00` marker** — appears 8,496 times at ~10.9 byte intervals.
  Delimits tiles of ~23 cells on average. 25% of total data.
- **`0f 02 00` marker** — appears 456 times (close to row count 472). Likely
  row delimiter.
- **0xFF runs** — long runs encode unexplored regions
- **5.19 bits/cell** compression ratio — consistent with variable-length coding
- **Only `small_map.ogm` and `latest_map.ogm` have real data** — other samples
  are empty maps (13-byte grid blobs)

## Objective

Produce a decoder (any language) that takes the field 6 grid data blob and
outputs a 2D occupancy grid matching the dimensions in the protobuf metadata.
"Done" means: decoded grid visually resembles a room/floor plan when rendered.

## Inputs

- `docs/ogm_format_research.md` — complete research document (start here)
- `docs/firmware/` — firmware binaries and analysis:
  - `libtrifo_core_cloud.so` (2.1 MB) — cloud SDK library (aarch64 ELF)
  - `cloud_node` (127 KB) — cloud service binary (thin wrapper, links libs)
  - `slam_node` (79 KB) — SLAM binary (thin wrapper)
  - `huffman_disasm_analysis.md` — full annotated disassembly of the Huffman
    class (821 lines). **Read this to avoid repeating work.**
- `docs/samples/` — sample data and tools:
  - `small_map.ogm` (133 KB) — primary test file (472x417 grid, real data)
  - `latest_map.ogm` (171 KB) — second test file (429x491 grid, real data)
  - `shm_reference.png` — visual render of decoded grid (ground truth)
  - `parse_ogm_proto.py` — working protobuf parser
  - `decode_ogm.py` — decode attempt script
  - `compare_ogm_shm.py` — OGM vs SHM comparison tool
- SHM ground truth dumps available on request (too large for git, ~120MB)

## Current Best Approach

**Disassemble `cloud_node` and/or `slam_node`** to find the actual grid
encoding function. These are small binaries (~80-130KB) that link against
`libtrifo_core_cloud.so` and other libraries. The encoding happens somewhere
in the chain: SHM read > convert_to_ogm_format (0xFF>0x7F) > [ENCODE] >
protobuf serialize > SaveOGM.

Key things to look for:
- Functions that reference the `0d040800` marker constant
- The code path from shm_open/mmap to protobuf field 6 assignment
- Any encode/compress function that ISN'T the trifo::Huffman class
- The convert_to_ogm_format function and what it calls next

Tools: Ghidra, IDA, or capstone (Python) for aarch64 disassembly.

## Constraints

- Do NOT attempt to connect to or modify the robot — this is analysis only
- Do NOT include any credentials or keys in submissions

## Prior Submissions

- `TASK-001_daneel_01/` — statistical analysis + APK decompilation (merged)
