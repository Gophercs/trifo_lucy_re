---
id: TASK-004
title: Disassemble cloud_node/slam_node to find OGM encoder
status: open
priority: high
depends_on: []
estimated_effort: medium
skills: [reverse-engineering, aarch64, ghidra, binary-analysis]
requires_robot: false
---

# Disassemble cloud_node / slam_node to Find OGM Encoder

## Context

We have proven that the `trifo::Huffman` class in `libtrifo_core_cloud.so`
is NOT the OGM grid encoder (it uses a text header format; OGM data is binary).
The actual encoding must happen in `cloud_node`, `slam_node`, or another
library they link against.

Both binaries are small (~80-130KB) thin wrappers that link against shared
libraries. The encoding happens in this chain:

```
SHM read (raw 1-byte-per-cell grid)
  -> convert_to_ogm_format (0xFF -> 0x7F remapping)
  -> [UNKNOWN ENCODE STEP]
  -> protobuf serialize (CloudGridMsg.data = encoded bytes)
  -> SaveOGM (writes .ogm file)
```

## Objective

Find and reverse-engineer the function that encodes raw grid cells into the
compressed format seen in .ogm files. Produce pseudocode or a description
detailed enough to write a decoder.

## Inputs

All in `docs/firmware/`:

- `cloud_node` (127 KB, aarch64 ELF) — cloud service binary
- `slam_node` (79 KB, aarch64 ELF) — SLAM binary
- `libtrifo_core_cloud.so` (2.1 MB, aarch64 ELF) — shared library
- `huffman_disasm_analysis.md` — full disassembly of the Huffman class
  (proves it's NOT the OGM encoder — read this first to avoid wasted effort)

## Approach

1. Load `cloud_node` into Ghidra/IDA with `libtrifo_core_cloud.so` as a
   secondary binary (for symbol resolution)
2. Find `SaveOGM` (at offset 0x12ead0 in the .so) — trace what calls it
3. Find `convert_to_ogm_format` — trace what it calls AFTER the 0xFF->0x7F
   remapping
4. Look for functions that generate the `0d 04 08 00` marker pattern (appears
   8,496 times in encoded data at ~10.9 byte intervals)
5. Look for functions that generate `0f 02 00` (appears 456 times, likely
   row delimiter)
6. Check for other encoding functions beyond `trifo::Huffman` — the actual
   encoder might be in a different namespace or be a standalone function

## What the Encoded Data Looks Like

- Starts with `80 01 00` (3-byte header)
- `0d 04 08 00` structural marker repeats every ~10.9 bytes
- `0f 02 00` appears ~once per row
- 0xFF runs encode unexplored regions
- 5.19 bits/cell compression ratio
- Full byte range (0x00-0xFF) used

## Constraints

- Do NOT attempt to connect to or modify the robot
- The `trifo::Huffman` class is already fully analyzed — don't repeat that work

## Deliverable

Pseudocode or annotated disassembly of the encoding function, sufficient to
write a Python decoder. Even partial progress (identifying which function does
the encoding, its signature, calling convention) is valuable.
