"""Compare OGM grid data with SHM data byte-by-byte to find encoding pattern."""
import struct

with open("latest_map.ogm", "rb") as f:
    ogm = f.read()
with open("shm_visual_2MB.bin", "rb") as f:
    shm = f.read()

grid = ogm[63:171626]  # 171,563 bytes of compressed grid data
shm_cells = shm[8192:]  # raw grid, 1 byte per cell
W, H = 429, 491
total = W * H  # 210,639

print(f"Grid data: {len(grid)} bytes")
print(f"SHM cells: {len(shm_cells)} bytes (using first {total})")
print()

# Show first 100 OGM bytes with their positions
print("First 100 OGM grid bytes:")
for i in range(0, 100, 20):
    hex_part = ' '.join(f'{grid[i+j]:02x}' for j in range(20) if i+j < len(grid))
    print(f"  [{i:3d}] {hex_part}")

print()
print("First 100 SHM cells:")
for i in range(0, 100, 20):
    hex_part = ' '.join(f'{shm_cells[i+j]:02x}' for j in range(20) if i+j < len(shm_cells))
    print(f"  [{i:3d}] {hex_part}")

# The SHM is mostly 0x80 at the start. Find first non-0x80 positions
print("\nSHM first 20 non-0x80 values:")
non80 = []
for i in range(min(total, len(shm_cells))):
    if shm_cells[i] != 0x80:
        non80.append((i, shm_cells[i]))
        if len(non80) >= 20:
            break
for pos, val in non80:
    row, col = divmod(pos, W)
    print(f"  Cell {pos} (row {row}, col {col}): 0x{val:02x} ({val})")

# Look at runs of 0x80 in SHM
print("\nSHM runs of 0x80:")
run_start = 0
runs = []
in_run = shm_cells[0] == 0x80
for i in range(1, min(total, len(shm_cells))):
    is_80 = shm_cells[i] == 0x80
    if is_80 != in_run:
        if in_run:
            runs.append(('0x80', run_start, i - run_start))
        else:
            runs.append(('other', run_start, i - run_start))
        run_start = i
        in_run = is_80
if in_run:
    runs.append(('0x80', run_start, min(total, len(shm_cells)) - run_start))
else:
    runs.append(('other', run_start, min(total, len(shm_cells)) - run_start))

print(f"Total runs: {len(runs)}")
print("First 30 runs:")
for typ, start, length in runs[:30]:
    print(f"  {typ:6s} at {start:6d}, length {length:5d}")

# Now try: hypothesis that OGM uses RLE where each run is (value, varint_count)
# but only for runs, with some flag byte
# Let's try manually decoding the first few bytes against known SHM pattern

# SHM starts with 285 × 0x80, then non-0x80 values
# OGM starts with: 80 01 00 ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff 20 1f b1 24 10 ff ...

# What if:
# 80 = cell value 0x80
# 01 = repeat count extension? Or next cell value?
# 00 = cell value 0x00
# ff ff ff... = cell values 0xff?

# Let's check: how many 0x80 runs appear in SHM first 1000 cells?
print("\nSHM first 1000 cells value distribution:")
from collections import Counter
c = Counter(shm_cells[:1000])
for val, cnt in c.most_common(10):
    print(f"  0x{val:02x}: {cnt}")

# Let's look for simple run-length patterns in OGM data
# Try: when a byte is followed by 0x00, it means "repeat previous"
# Or: alternating value/count bytes

# NEW IDEA: What if it's protobuf-style varint RLE?
# Each run: varint(count << 1 | is_literal) if is_literal, followed by count raw bytes
#           else varint(count << 1) followed by 1 value byte
# This is like Parquet RLE/bit-packing

def try_hybrid_rle(data, target_len):
    """Parquet-style hybrid RLE: varint header, bit0=0 means RLE, bit0=1 means literal"""
    out = bytearray()
    i = 0
    def rv(d, p):
        r = 0; s = 0
        while p < len(d):
            b = d[p]; r |= (b & 0x7f) << s; p += 1
            if not (b & 0x80): break
            s += 7
        return r, p

    while i < len(data) and len(out) < target_len + 1000:
        header, i = rv(data, i)
        if header & 1:  # literal run
            count = header >> 1
            out.extend(data[i:i+count])
            i += count
        else:  # RLE run
            count = header >> 1
            if i < len(data):
                out.extend([data[i]] * count)
                i += 1
    return bytes(out)

print("\n--- Hybrid RLE (Parquet-style) ---")
result = try_hybrid_rle(grid, total)
print(f"Output: {len(result)} cells (target {total})")
if len(result) > 100:
    match = sum(1 for a, b in zip(result[:min(len(result), total)], shm_cells[:total]) if a == b)
    print(f"SHM match: {match}/{min(len(result), total)} = {match/min(len(result), total)*100:.1f}%")
    print(f"First 40 decoded: {result[:40].hex()}")

# Also try inverted bit meaning
def try_hybrid_rle_inv(data, target_len):
    """bit0=1 means RLE, bit0=0 means literal"""
    out = bytearray()
    i = 0
    def rv(d, p):
        r = 0; s = 0
        while p < len(d):
            b = d[p]; r |= (b & 0x7f) << s; p += 1
            if not (b & 0x80): break
            s += 7
        return r, p

    while i < len(data) and len(out) < target_len + 1000:
        header, i = rv(data, i)
        if header & 1:  # RLE run
            count = header >> 1
            if i < len(data):
                out.extend([data[i]] * count)
                i += 1
        else:  # literal run
            count = header >> 1
            out.extend(data[i:i+count])
            i += count
    return bytes(out)

print("\n--- Hybrid RLE inverted ---")
result = try_hybrid_rle_inv(grid, total)
print(f"Output: {len(result)} cells (target {total})")
if len(result) > 100:
    match = sum(1 for a, b in zip(result[:min(len(result), total)], shm_cells[:total]) if a == b)
    print(f"SHM match: {match}/{min(len(result), total)} = {match/min(len(result), total)*100:.1f}%")
    print(f"First 40 decoded: {result[:40].hex()}")
