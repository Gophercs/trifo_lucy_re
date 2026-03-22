# Trifo Lucy OGM (Occupancy Grid Map) File Format Research

**Status:** Grid data encoding UNSOLVED -- help wanted
**Last updated:** 2026-03-21
**Target hardware:** Trifo Lucy robot vacuum

---

## Table of Contents

1. [Introduction](#introduction)
2. [Hardware & Software Overview](#hardware--software-overview)
3. [Where OGM Files Live](#where-ogm-files-live)
4. [OGM File Structure (Protobuf)](#ogm-file-structure-protobuf)
5. [Byte-Level Layout](#byte-level-layout)
6. [Three Critical Bugs/Quirks](#three-critical-bugsquirks)
7. [Parsing Recipe](#parsing-recipe)
8. [Multiple OGM Files Examined](#multiple-ogm-files-examined)
9. [Grid Data Encoding (UNSOLVED)](#grid-data-encoding-unsolved)
10. [SHM Ground Truth Format](#shm-ground-truth-format)
11. [Software Analysis](#software-analysis)
12. [What We Tried and Ruled Out](#what-we-tried-and-ruled-out)
13. [Leads for Further Research](#leads-for-further-research)
14. [Files Reference](#files-reference)
15. [Robot Access Details](#robot-access-details)

---

## Introduction

This document describes the reverse engineering of the `.ogm` file format used by the Trifo Lucy robot vacuum to store occupancy grid maps. These files contain SLAM-generated maps of the robot's environment and are found on the robot's filesystem.

The outer container is protobuf, but with firmware bugs that break standard parsers. The grid data inside the protobuf uses an unknown compression/encoding scheme that we have not yet cracked. We have a ground-truth reference (shared memory dump) to validate against.

This is a self-contained write-up. You do not need any prior context about this project.

---

## Hardware & Software Overview

| Component | Details |
|-----------|---------|
| Robot | Trifo Lucy robot vacuum |
| SoC | Rockchip RK3399 (aarch64) |
| MCU | STM32F407 (motor/sensor control) |
| OS | Linux 4.4.167, aarch64 |
| Cloud SDK | QLCloud SDK v3 |
| Cloud protocol | MQTT on port 17766 |
| Root access | SSH as root (default vendor password) |
| SLAM | Google Cartographer-based (`slam_node` binary) |

The robot runs several userspace processes. The relevant ones for maps are:

- **`slam_node`** -- runs SLAM, produces the occupancy grid, writes it to shared memory
- **`cloud_node`** -- reads the grid from shared memory, serializes it to protobuf, writes `.ogm` files, and sends maps to the cloud via MQTT

---

## Where OGM Files Live

On the robot's filesystem:

```
/data/user/app/SweepLog/
  latest_map.ogm    -- most recent map (overwritten each run)
  newest_map.ogm    -- another recent map snapshot
  synced_map.ogm    -- map synced to cloud
  *.ogm             -- additional map snapshots
```

There is also a live shared memory segment with the **same map data** in raw uncompressed form:

```
/dev/shm/TrifoSemaphore_visual_mapper-multi_layer_map   (31 MB)
```

This SHM dump is our ground truth for validating any decoding attempt.

---

## OGM File Structure (Protobuf)

The file is a serialized protobuf `CloudMultiMapLayerMsg`. Here is the proto definition:

```protobuf
syntax = "proto3";

message Pose2DItemMsg {
  double x = 1;
  double y = 2;
  double angle = 3;
}

message CloudGridMsg {
  double cell_size = 1;     // grid resolution in meters
  int32 height = 2;         // grid height in cells
  int32 width = 3;          // grid width in cells
  double max_x = 4;         // max X coordinate in meters
  double max_y = 5;         // max Y coordinate in meters
  bytes data = 6;           // compressed grid data (THE UNSOLVED PART)
  int32 type_len = 7;       // data type identifier
}

message CloudMultiMapLayerMsg {
  double align_angle = 4;                    // alignment angle
  map<string, CloudGridMsg> id_to_maps = 2;  // map name -> grid data
  Pose2DItemMsg robot_pos = 3;               // robot position
  Pose2DItemMsg charger_pos = 4;             // charger position
  int64 timestamp = 5;                       // unix timestamp
}
```

The map key is typically `"scan_map"`.

---

## Byte-Level Layout

Annotated hex layout of `latest_map.ogm` (171,693 bytes total):

```
Offset  Field                              Value / Notes
------  -----                              -------------
@0      field 4 fixed64 (align_angle)      -30.035 degrees (9 bytes: 1 tag + 8 data)

@9      field 2 LEN (map entry)            varint=210,692 (LIES -- see quirks)
  @13     field 1 LEN 8                    key = "scan_map"
  @23     field 2 LEN                      varint=210,678 (LIES)
    @27     field 1 fixed64 (cell_size)    **BUG: only 7 value bytes, not 8**
    @35     field 2 varint (height)        491
    @38     field 3 varint (width)         429
    @41     field 4 fixed64 (max_x)        4.91
    @50     field 5 fixed64 (max_y)        5.976
    @59     field 6 tag + varint           tag=0x32, varint=210,639 (= 491 x 429 = H x W)
    @63     DATA STARTS HERE               171,563 bytes of compressed grid data
    ...
    @171626 field 7 varint (type_len)      8

@171628 field 3 LEN 27 (robot_pos)         Pose2DItemMsg {x, y, angle}
@171657 field 4 LEN 27 (charger_pos)       Pose2DItemMsg {x, y, angle}
@171686 field 5 varint (timestamp)         585856878
```

The grid data occupies offsets 63 through 171,625 (171,563 bytes), encoding 210,639 cells (a 429-wide by 491-tall grid).

---

## Three Critical Bugs/Quirks

These are firmware bugs in the Trifo serialization code. Any parser must handle them.

### 1. The 7-byte cell_size bug

Field 1 of `CloudGridMsg` is `cell_size` (type `double`, wire type `fixed64`). Per the protobuf spec, `fixed64` always writes exactly 8 value bytes. Trifo's firmware writes only **7 value bytes**.

The next field's tag byte (field 2, `height`) appears at offset 35 (= 27 + 1 tag + 7 data) instead of the expected offset 36 (= 27 + 1 tag + 8 data).

This is confirmed across **every OGM file examined**. It is a consistent firmware bug.

### 2. Lying length varints

At every nesting level, the protobuf LEN (length-delimited) fields store the **uncompressed** cell count / size rather than the actual byte count of the encoded data:

| Field | Varint value | Actual bytes remaining |
|-------|-------------|----------------------|
| Outer map entry (offset 10) | 210,692 | ~171,684 |
| CloudGridMsg wrapper (offset 24) | 210,678 | ~171,670 |
| Data field (offset 60) | 210,639 (= H x W) | 171,563 |

Standard protobuf parsers use the LEN varint to determine how many bytes to read. They will try to read 210,639 bytes from offset 63 and immediately hit EOF (the file is only 171,693 bytes total).

### 3. Dual use of field number 4

In `CloudMultiMapLayerMsg`, field number 4 appears twice with different wire types:

- At offset 0: field 4 as `fixed64` (wire type 1) -- the `align_angle`
- At offset 171,657: field 4 as `LEN` (wire type 2) -- the `charger_pos`

This is technically valid protobuf (the parser uses the wire type to determine encoding), but highly unusual and breaks some protobuf tooling.

---

## Parsing Recipe

To extract the metadata (dimensions, positions, etc.) and the raw grid data blob, you have two options:

### Option A: Patch the binary and use standard protobuf

1. **Fix cell_size**: Insert 1 zero-padding byte at offset 35 (between the 7th cell_size data byte and the next field tag)
2. **Patch lying varints**: Replace the LEN varints at offsets 10-12, 24-26, and 60-62 with the actual byte counts of the data that follows
3. Parse with standard protobuf library

### Option B: Custom wire-format parser (recommended)

Write a parser that:
- Reads field tags and wire types manually
- For `fixed64` fields inside `CloudGridMsg`, reads only 7 value bytes (not 8)
- For LEN fields, ignores the varint length and instead uses EOF / next-field-tag detection to determine actual data boundaries
- Handles duplicate field numbers with different wire types

We used Option B for our analysis.

---

## Multiple OGM Files Examined

| File | Total size | Grid dims | Cell count | Data bytes | Compression ratio |
|------|-----------|-----------|------------|------------|-------------------|
| `latest_map.ogm` | 171,693 | 429 x 491 | 210,639 | 171,563 | 1.227 (81.4%) |
| `newest_map.ogm` | 133,378 | 417 x 473 | 197,241 | 127,891 | 1.542 (64.9%) |
| `synced_map.ogm` | ~same | 417 x 473 | 197,241 | ~same | ~1.542 |

The compression ratio varies by map content. Maps with more "unknown" (unexplored) space compress better.

**Note:** `newest_map.ogm` has a 2-byte non-protobuf header `f1 0f` before the protobuf data begins. The reason for this prefix is unknown.

---

## Grid Data Encoding (UNSOLVED)

The core unsolved problem: how do 171,563 bytes at offsets 63-171,625 decode into 210,639 grid cells?

### Raw data characteristics

**Header pattern (consistent across all files):**
```
First 3 bytes: 80 01 00
```

This could be:
- Protobuf: field 16, varint value 0
- Custom header: type=0x80, version=1, flags=0

**Byte frequency distribution (top values):**
```
0x00 -- 11.1%
0x08 --  9.0%
0x04 --  7.7%
0x60 --  5.8%
0x0d --  5.7%
0x22 --  5.2%
```

**Dominant repeating pattern:**
```
60 0d 04 08 00 22   (6 bytes, appears ~6,371 times)
```

This pattern repeats with a dominant **8-byte spacing** (4,899 instances at exactly 8 bytes apart). In context, incrementing values appear between repetitions:

```
...60 0d 04 08 00 22 30 02  60 0d 04 08 00 22 40 02  60 0d 04 08 00 22 50 02...
```

The `30 02`, `40 02`, `50 02` are incrementing varint-like values between the fixed pattern.

**FF byte runs:**
- 1,869 separate runs of `0xFF` bytes
- 4,505 total `0xFF` bytes
- Mean run length: 2.4 bytes

---

## SHM Ground Truth Format

The shared memory segment at `/dev/shm/TrifoSemaphore_visual_mapper-multi_layer_map` (31 MB) contains the raw, uncompressed map.

### Structure
```
Offset 0x0000 - 0x1FFF:  8192-byte header (metadata, dimensions, etc.)
Offset 0x2000+:          Raw grid data, 1 byte per cell
```

### Cell values (from SHM)
| Value | Meaning | Frequency |
|-------|---------|-----------|
| `0x80` (128) | Unknown / unexplored | ~78% |
| `0x00` (0) | Free / open space | ~12.5% |
| `0x74` (116) | Partial occupancy | ~2.3% |
| `0xFF` (255) | Wall / obstacle | ~2% |
| Other | Various occupancy levels | remaining |

### Indexing
The APK code confirms **column-major** indexing:
```
row = index % height
col = floor(index / height)
```

The APK also applies a **128-transform** when rendering:
```
displayValue = 128 - rawByteValue
```

### Grid dimensions match
The SHM grid dimensions match the OGM metadata exactly (e.g., 429 x 491 for `latest_map.ogm`). This confirms the SHM is the ground truth for the same map.

---

## Software Analysis

### C++ Robot Libraries

**`libtrifo_core_cloud.so`** (aarch64 shared library on the robot) contains these relevant symbols:

| Function | Offset | Size | Notes |
|----------|--------|------|-------|
| `trifo::Huffman::compress` | 0x126d28 | -- | Compresses data |
| `trifo::Huffman::decompress` | 0x126e38 | 36 bytes | Thin wrapper |
| `trifo::Huffman::rebuild_huffman_tree` | 0x125a60 | 1,136 bytes | Builds tree from encoded data |
| `trifo::Huffman::decode_huffman` | 0x1260f8 | 2,612 bytes | Main decode loop |
| `SaveOGM` | 0x12ead0 | 884 bytes | Writes .ogm file |
| `ReadOGMFromFile` | 0xca644 | 1,776 bytes | Reads .ogm file |
| `convert_to_ogm_format` | -- | -- | Pre-processing step |

Key findings from static analysis:

1. **`SaveOGM`** calls `ostream::write()` to write the protobuf directly -- there is no additional compression at file-write time. The grid data is already encoded when it reaches `SaveOGM`.

2. **`ReadOGMFromFile`** reads the protobuf and calls `Base64Encode` for cloud upload.

3. **`convert_to_ogm_format`** replaces `0xFF` with `0x7F` in cell values. This frees up the `0xFF` byte value for use as a structural/control byte in the encoding.

4. **The Huffman class** uses ASCII `'0'`/`'1'` text representation internally. This is unusual -- most Huffman implementations work with bit-level operations, not character strings of '0' and '1'.

### Android APK (Trifo Home 2.6.3)

The Android app uses LZ4 decompression for maps received via MQTT:

```java
// Decoding pipeline for MQTT-received maps:
// 1. QLRPDataItem.getData() returns Base64 string
// 2. Base64.decode() -> binary bytes
// 3. LZ4SafeDecompressor.decompress(bytes, outputBuffer) -> decompressed protobuf
// 4. Parse protobuf CloudMultiMapLayerMsg
```

The `lz4_source_size` field provides the uncompressed size hint (buffer allocated as `lz4_source_size + 10000`).

**CRITICAL DISTINCTION:** The LZ4 + Base64 encoding is for the **MQTT transport layer** -- the *entire protobuf message* is compressed for network transmission. This is a separate layer from the grid data encoding within the protobuf's `data` field. The grid cells are already encoded before protobuf serialization.

The `type_len` field maps to: 0=MAP_LIST, 1=MAP_INFO, 2=MAP_PATH, 3=PATH_COMMAND, 4=MAP_LAYER.

### Data Flow

```
slam_node
    |
    v
SHM (raw 1-byte-per-cell grid, 0x80/0x00/0x74/0xFF values)
    |
    v
cloud_node reads SHM
    |
    v
convert_to_ogm_format (0xFF -> 0x7F mapping)
    |
    v
[UNKNOWN ENCODING STEP] -- grid cells -> compressed bytes
    |
    v
Serialize to protobuf (CloudGridMsg.data = compressed bytes)
    |
    +-----> Write to .ogm file on disk (SaveOGM)
    |
    +-----> LZ4 compress entire protobuf + Base64 encode -> send via MQTT
```

The encoding of grid cells into the protobuf `data` field happens somewhere between the SHM read and protobuf serialization. The Huffman class in the shared library is the prime suspect.

---

## What We Tried and Ruled Out

### Standard compression algorithms (ALL FAIL)

| Algorithm | Result |
|-----------|--------|
| zlib (raw deflate) | Decompression error |
| zlib (with header) | Decompression error |
| gzip | Decompression error |
| LZ4 frame format | No LZ4 magic bytes (`04 22 4D 18`) found anywhere |
| LZ4 block format | Tried offsets 59-80, sizes 210,639-300,000 -- all fail |
| LZ4 manual trace | First token `0x80` reads 8 literals, then match offset=0xFFFF (invalid) |
| Snappy | Decompression error |
| LZMA | Decompression error |
| Brotli | Decompression error |
| zstd | Not tested (library unavailable at analysis time) |

### Encoding scheme attempts (ALL FAIL)

| Approach | Result |
|----------|--------|
| Raw bytes, row-major | 8.1% match with SHM (noise) |
| Raw bytes, column-major + 128-transform | Still noise, no map structure |
| PackBits RLE | 237,803 cells (close to 210,639!) but 2% SHM match |
| RLE: value/count pairs | Wrong cell count |
| RLE: 00-escape variant | Wrong cell count |
| RLE: FF-as-single (non-run) | Wrong cell count |
| RLE: varint-length runs | Wrong cell count |
| Huffman with pre-order tree | Only 1 leaf found (wrong format) |
| Huffman with canonical code tables | Code lengths don't look valid |
| Protobuf packed repeated field | `0xFF` bytes kill varint parsing |
| Nibble encoding (4 bits/cell) | Wrong cell count |

### Near misses
- **PackBits RLE** produced a cell count (237,803) close to the expected 210,639. This suggests the encoding *might* be RLE-adjacent, but the actual values don't match the ground truth.

---

## Leads for Further Research

These are the most promising avenues for cracking the encoding, roughly in order of expected payoff:

### 1. Disassemble the Huffman functions (HIGH PRIORITY)

The `trifo::Huffman` class in `libtrifo_core_cloud.so` is almost certainly the encoder. The key functions to reverse engineer:

- **`rebuild_huffman_tree`** at offset `0x125a60` (1,136 bytes) -- reconstructs the Huffman tree from encoded data. This tells you the tree format.
- **`decode_huffman`** at offset `0x1260f8` (2,612 bytes) -- the main decode loop. This IS the decoding algorithm.
- **`decompress`** at offset `0x126e38` (36 bytes) -- thin wrapper, but shows calling convention.

The binary is aarch64. The library is at `/usr/local/bin/libtrifo_core_cloud.so` on the robot.

The unusual ASCII `'0'`/`'1'` representation suggests the Huffman implementation may be non-standard (e.g., tree serialized as a text string of bits, or codes stored as strings rather than packed bits).

### 2. Analyze slam_node

`slam_node` (at `/usr/local/bin/slam_node`) is the binary that creates the grid data. It links against Google Cartographer, which uses `uint16` probability grids internally. The encoding might happen here rather than in `cloud_node`.

### 3. Intercept the data before compression

Hook or patch `cloud_node` to dump the grid data at the point between SHM read and protobuf serialization. This could confirm whether the encoding is Huffman, reveal the exact input format, or bypass the encoding entirely.

Options:
- `LD_PRELOAD` hook on the Huffman compress function
- Patch the binary to write a debug dump
- Use `gdb` on the robot (if available)

### 4. Try Trifo's specific Huffman format

Given what we know:
- Header: `80 01 00` (possibly: tree-size or encoding parameters)
- `0xFF` bytes serve a structural role (freed by the `0xFF -> 0x7F` value remapping)
- The 6-byte repeating pattern `60 0d 04 08 00 22` with 8-byte spacing could be Huffman tree nodes or code table entries
- The incrementing values (`30 02`, `40 02`, `50 02`) between patterns might be cell value mappings
- ASCII `'0'`/`'1'` internal representation might mean codes are stored as byte strings

### 5. Test with zstd

This is the one standard compression algorithm we didn't test (library wasn't available). Low probability but worth eliminating.

### 6. Compare with other Trifo products

The encoding might be shared across Trifo's robot line (Ironpie, Emma, Ollie, etc.). Other researchers may have encountered the same format. The QLCloud SDK and `libtrifo_core_cloud.so` are likely shared components.

---

## Files Reference

All analysis files are in the `mqtt_capture/` directory relative to the project root (`<project_root>/`).

### OGM files and ground truth
| File | Description |
|------|-------------|
| `latest_map.ogm` | Primary test file (171,693 bytes, 429x491 grid) |
| `newest_map.ogm` | Additional OGM (133,378 bytes, 417x473 grid) |
| `synced_map.ogm` | Cloud-synced OGM snapshot |
| `shm_visual_fresh.bin` | SHM dump -- raw ground truth grid data |

### Proto definitions
| File | Description |
|------|-------------|
| `cloud_map.proto` | Protobuf message definitions |
| `cloud_map_pb2.py` | Compiled Python protobuf module |

### Robot binaries (copies)
| File | Description |
|------|-------------|
| `libtrifo_core_cloud.so` | Cloud SDK shared library (contains Huffman code) |
| `libtrifo_core_semanticmap.so` | Semantic map library |
| `cloud_node` | Cloud service binary |

### Analysis scripts
| File | Description |
|------|-------------|
| `compare_ogms.py` | Compares multiple OGM files |
| `try_lz4_huffman.py` | LZ4 and Huffman decoding attempts |
| `try_huffman.py` | Huffman-specific decoding attempts |
| `find_period.py` | Finds repeating patterns in grid data |
| `visualize_raw.py` | Visualizes raw grid data |
| `apk_decompiled_lz4.txt` | Decompiled APK Java classes (LZ4 handling) |

### APK
| File | Description |
|------|-------------|
| APK source | Trifo Home 2.6.3 (decompiled with jadx) |
| Key path | Located in `apk_extract/` at project root |

---

## Robot Access Details

For anyone continuing this research with physical access to a Trifo Lucy:

```bash
# SSH connection (old crypto required)
ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oMACs=+hmac-sha1 \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    root@<ROBOT_IP>
# Password: default vendor root password
```

### Key paths on robot
| Path | Description |
|------|-------------|
| `/usr/local/bin/libtrifo_core_cloud.so` | Cloud SDK library (Huffman code here) |
| `/usr/local/bin/slam_node` | SLAM binary (grid creation) |
| `/usr/local/bin/cloud_node` | Cloud service (OGM file writing) |
| `/data/user/app/SweepLog/*.ogm` | OGM map files |
| `/dev/shm/TrifoSemaphore_visual_mapper-multi_layer_map` | Live SHM map (31 MB) |

### Dumping fresh data

```bash
# Copy latest OGM
scp <SSH_FLAGS> root@<ROBOT_IP>:/data/user/app/SweepLog/latest_map.ogm .

# Dump SHM (must be done while slam_node is running)
ssh <SSH_FLAGS> root@<ROBOT_IP> "cat /dev/shm/TrifoSemaphore_visual_mapper-multi_layer_map" > shm_dump.bin

# Copy binaries for analysis
scp <SSH_FLAGS> root@<ROBOT_IP>:/usr/local/bin/libtrifo_core_cloud.so .
scp <SSH_FLAGS> root@<ROBOT_IP>:/usr/local/bin/slam_node .
```

---

## Contributing

If you pick this up and make progress, the key validation test is:

1. Decode the grid data from `latest_map.ogm` (171,563 bytes at offset 63) into 210,639 cell values
2. Apply the 128-transform (`displayValue = 128 - rawByteValue`) and column-major indexing (`row = index % height`)
3. Compare against the SHM dump (`shm_visual_fresh.bin`, skip 8192-byte header)
4. A correct decode should show >90% match (exact match minus timing differences between captures)

The SHM values are: `0x80`=unknown, `0x00`=free, `0x74`=occupied, `0xFF`=wall.
Remember that `convert_to_ogm_format` maps `0xFF -> 0x7F` before encoding, so wall cells in the OGM will have value `0x7F`, not `0xFF`.
