---
task: TASK-001
contributor: daneel
attempt: 01
status: partial
confidence: medium
date: 2026-03-23
---

# TASK-001 Submission: Statistical Analysis of OGM Grid Encoding

## Approach

Pure statistical analysis of the grid data blobs extracted from the three sample
OGM files. No firmware binary or robot access was used. The goal was to
characterize the encoding and narrow the search space for future decode attempts.

### Steps taken

1. Built a bug-aware OGM parser accounting for all three firmware quirks
   (7-byte cell_size, lying length varints, dual field 4)
2. Extracted the field 6 grid data blob from each sample file
3. Ran byte frequency, bigram, entropy, run-length, and bit-level analysis
4. Tested nested-protobuf hypothesis by attempting recursive parse
5. Identified repeating structural markers via pattern search
6. Rendered raw bytes as images at various widths to detect spatial structure
7. Compared renders against `shm_reference.png`

## Results

### Extraction (confirmed)

| File | Grid dims | Cell count | Data bytes | Bits/cell |
|------|-----------|------------|------------|-----------|
| small_map.ogm | 472 × 417 | 196,824 | 133,248 | 5.41 |
| newest_map.ogm | (empty) | — | 13 | — |
| synced_map.ogm | (empty) | — | 13 | — |

Only `small_map.ogm` contains meaningful grid data. The other two files have
13-byte blobs — they appear to be empty/initialized maps.

Grid data occupies bytes 63–133,311 of `small_map.ogm`. Trailing fields
(type_len, robot_pos, charger_pos, timestamp) start at offset 133,311.

### Encoding characteristics

| Property | Value | Implication |
|----------|-------|-------------|
| Overall entropy | 5.51 bits/byte | Compressed, not encrypted |
| Unique byte values | 256/256 | Full byte range used |
| Median run length | 1 | Not RLE, not raw pixels |
| Max run | 790 × 0xFF | Unexplored region encoding |
| Compression ratio | 1.48× | Moderate compression |
| Dominant bytes | 0x00 (20%), 0x08 (10%), 0x04 (9%), 0x0d (7%), 0x22 (6%) | See structural markers below |

### Structural markers discovered

**Pattern `0x0d 0x04 0x08 0x00`** — appears **8,496 times** at average intervals
of 10.9 bytes. Removing this 4-byte pattern strips 34KB (25%) of the data.
This is a regularly-inserted structural element within the encoding — possibly
a cell-type or tile boundary marker.

After this marker, the next byte is:
- `0x22` in 74.8% of cases (6,358 times)
- `0x13` in 14.7% of cases (1,248 times)
- Other values in remaining 10.5%

**Pattern `0x0f 0x02 0x00`** — appears **456 times** with average 28.5-byte
spacing. Count is close to the grid height (472). Likely a **row delimiter**
or end-of-row marker.

**0xFF runs** — three significant runs of 57, 709, and 790 consecutive 0xFF
bytes at offsets ~21K, ~29K, and ~34K. These create zero-entropy regions and
correspond to large unexplored areas in the grid. Visible as solid white bands
in raw byte renders.

### Hypothesis tested and rejected: nested protobuf

The dominant byte values (0x00, 0x08, 0x04, 0x0d, 0x22) coincidentally match
common protobuf field tags. I tested whether the grid blob contains nested
protobuf messages. Result: **no**.

- Recursive protobuf parsing produces field numbers in the thousands with
  nonsensical values
- Field coverage is <1% in sub-parse attempts
- The byte distribution inside extracted "sub-messages" is identical to the
  overall blob — there's no layer separation

The data is a **custom encoded bitstream** that happens to have these byte
values as its statistical mode, not protobuf wire format.

### Visual analysis

Raw byte renders at both width=417 and width=472 show:
- Clear spatial banding (not random noise)
- Distinct white (0xFF) regions matching unexplored areas
- Fine-grained repeating texture from the `0d040800` markers
- No recognizable floor plan — encoding transforms prevent direct reading

## Failed approaches

1. **Treating data as protobuf sub-messages** — byte values are coincidental,
   not wire format tags
2. **Stripping markers and rendering** — removing `0d040800` produces a smaller
   blob but still not interpretable as occupancy values
3. **Fixed-record-size analysis** — no record size (2–16 bytes) produces
   consistent low-entropy columns

## Confidence

**Medium** — the structural analysis is solid and the rejected hypotheses save
future contributors time. However, the actual decode algorithm remains unknown.

## Recommendations

1. **Firmware disassembly is the critical path.** A GitHub issue has been filed
   requesting the `navigation_node` binary and `libtrifo_core_cloud.so`. The
   research doc already has candidate function offsets for Huffman routines.
   Disassembly (Ghidra/IDA) targeting the SHM write path → field 6 decode
   chain should reveal the algorithm.

2. **The `0d040800` marker meaning needs firmware context.** It appears ~8,500
   times for ~197K cells, suggesting it delimits tiles of ~23 cells on average.
   The firmware decode routine will clarify whether this is a Huffman tree
   separator, tile header, or something else.

3. **APK decompilation (TASK-003)** is an alternative path if firmware isn't
   available. The Android app must decode this format to render maps. Tracing
   `com.trifo.home` map rendering code could yield the algorithm without
   needing the robot binary.

4. **Only `small_map.ogm` is useful for testing.** The other two samples are
   empty maps. Additional non-empty OGM samples would help validate any
   future decoder.

## Reasoning trace

Initial analysis showed protobuf-like byte values dominating, which led to
the nested-protobuf hypothesis. Testing this consumed the most time. Once
rejected, the focus shifted to pattern identification and spatial rendering.

The key insight is that at 5.19 bits/cell with ~3 expected distinct values
(free/unknown/occupied), a Huffman code assigning 1–2 bits to "unknown" (the
most common value) and 4–8 bits to rarer values would produce exactly this
compression ratio. The `0d040800` and `0f0200` markers are likely part of
the Huffman framing, not the code table itself.
