"""Try various RLE/encoding schemes on OGM grid data."""
import struct
import sys

# Read OGM file
with open("latest_map.ogm", "rb") as f:
    ogm_raw = f.read()

# Read SHM data for comparison
with open("shm_visual_2MB.bin", "rb") as f:
    shm_raw = f.read()
shm_grid = shm_raw[8192:]  # Skip 8192-byte header

# Extract grid data from OGM (offset 63 to 171625 based on previous analysis)
grid_data = ogm_raw[63:171626]
print(f"OGM grid data: {len(grid_data)} bytes")
print(f"SHM grid data: {len(shm_grid)} bytes")
print(f"Expected cells: 491 * 429 = {491*429}")
print(f"First 40 OGM bytes: {grid_data[:40].hex()}")
print(f"First 40 SHM bytes: {shm_grid[:40].hex()}")
print()

# SHM grid value distribution (first 210639 cells)
from collections import Counter
shm_cells = shm_grid[:210639]
shm_counts = Counter(shm_cells)
print(f"SHM top values: {shm_counts.most_common(10)}")
print()

# Scheme 1: Simple RLE - (value, count) pairs, count as 1 byte
def try_rle_vc(data, count_bytes=1, big_endian=False):
    """value, count pairs"""
    out = bytearray()
    i = 0
    step = 1 + count_bytes
    while i + step <= len(data):
        val = data[i]
        if count_bytes == 1:
            cnt = data[i+1]
        elif count_bytes == 2:
            cnt = struct.unpack('>H' if big_endian else '<H', data[i+1:i+3])[0]
        out.extend([val] * cnt)
        i += step
    return bytes(out)

# Scheme 2: Simple RLE - (count, value) pairs
def try_rle_cv(data, count_bytes=1, big_endian=False):
    """count, value pairs"""
    out = bytearray()
    i = 0
    step = 1 + count_bytes
    while i + step <= len(data):
        if count_bytes == 1:
            cnt = data[i]
        elif count_bytes == 2:
            cnt = struct.unpack('>H' if big_endian else '<H', data[i:i+2])[0]
        val = data[i + count_bytes]
        out.extend([val] * cnt)
        i += step
    return bytes(out)

# Scheme 3: Escape-based RLE - literal bytes, with escape byte signaling a run
def try_escape_rle(data, escape=0x80):
    """escape byte followed by count and value means run; other bytes are literal"""
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == escape and i + 2 < len(data):
            cnt = data[i+1]
            val = data[i+2]
            if cnt == 0:
                out.append(escape)  # escaped escape
                i += 2
            else:
                out.extend([val] * cnt)
                i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)

# Scheme 4: PackBits-style RLE (like TIFF)
def try_packbits(data):
    """PackBits: n>=0 means n+1 literals, n<0 means 1-n copies of next byte"""
    out = bytearray()
    i = 0
    while i < len(data):
        n = data[i]
        if n < 128:  # 0-127: n+1 literal bytes
            count = n + 1
            i += 1
            out.extend(data[i:i+count])
            i += count
        elif n > 128:  # 129-255: copy next byte (257-n) times
            count = 257 - n
            i += 1
            if i < len(data):
                out.extend([data[i]] * count)
                i += 1
            else:
                break
        else:  # 128: no-op
            i += 1
    return bytes(out)

# Scheme 5: Varint-count RLE
def read_varint(data, pos):
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7f) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos

def try_varint_rle_vc(data):
    """value, varint-count pairs"""
    out = bytearray()
    i = 0
    while i < len(data):
        val = data[i]
        i += 1
        cnt, i = read_varint(data, i)
        if cnt > 500000:
            return None  # bogus
        out.extend([val] * cnt)
    return bytes(out)

def try_varint_rle_cv(data):
    """varint-count, value pairs"""
    out = bytearray()
    i = 0
    while i < len(data):
        cnt, i = read_varint(data, i)
        if cnt > 500000 or i >= len(data):
            return None
        val = data[i]
        i += 1
        out.extend([val] * cnt)
    return bytes(out)

# Scheme 6: What if 0x80 (unknown) cells are RLE-encoded but others are literal?
# Since 78% of SHM is 0x80, a scheme that efficiently encodes 0x80 runs makes sense
def try_0x80_rle(data):
    """0x00 byte means 'N following 0x80 cells' (count in next byte), other bytes are literal"""
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0x00 and i + 1 < len(data):
            cnt = data[i+1]
            out.extend([0x80] * cnt)
            i += 2
        else:
            out.append(data[i])
            i += 1
    return bytes(out)

def try_0x80_rle_v2(data):
    """0x80 byte followed by varint count = run of 0x80s, other bytes are literal"""
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0x80:
            i += 1
            cnt, i = read_varint(data, i)
            if cnt > 500000:
                return None
            out.extend([0x80] * cnt)
        else:
            out.append(data[i])
            i += 1
    return bytes(out)

# Try all schemes
schemes = [
    ("RLE v,c (1B)", lambda: try_rle_vc(grid_data, 1)),
    ("RLE c,v (1B)", lambda: try_rle_cv(grid_data, 1)),
    ("RLE v,c (2B LE)", lambda: try_rle_vc(grid_data, 2, False)),
    ("RLE v,c (2B BE)", lambda: try_rle_vc(grid_data, 2, True)),
    ("RLE c,v (2B LE)", lambda: try_rle_cv(grid_data, 2, False)),
    ("RLE c,v (2B BE)", lambda: try_rle_cv(grid_data, 2, True)),
    ("Escape RLE (0x80)", lambda: try_escape_rle(grid_data, 0x80)),
    ("Escape RLE (0xFF)", lambda: try_escape_rle(grid_data, 0xFF)),
    ("Escape RLE (0x00)", lambda: try_escape_rle(grid_data, 0x00)),
    ("PackBits", lambda: try_packbits(grid_data)),
    ("Varint RLE v,c", lambda: try_varint_rle_vc(grid_data)),
    ("Varint RLE c,v", lambda: try_varint_rle_cv(grid_data)),
    ("0x80 RLE (0x00=marker)", lambda: try_0x80_rle(grid_data)),
    ("0x80 RLE (0x80=marker,varint)", lambda: try_0x80_rle_v2(grid_data)),
]

target = 491 * 429  # 210639

for name, fn in schemes:
    try:
        result = fn()
        if result is None:
            print(f"{name:35s} -> BOGUS (overflow)")
            continue
        n = len(result)
        # Check if output matches expected size
        match = "*** MATCH ***" if n == target else ""
        close = "CLOSE" if abs(n - target) < 1000 else ""
        # Compare with SHM
        if n >= 100:
            same = sum(1 for a, b in zip(result[:min(n, len(shm_cells))], shm_cells) for _ in [1] if a == b)
            pct = same / min(n, len(shm_cells)) * 100
        else:
            pct = 0
        print(f"{name:35s} -> {n:>10d} cells (target {target}) {match}{close}  SHM match: {pct:.1f}%")
    except Exception as e:
        print(f"{name:35s} -> ERROR: {e}")

# Also try starting from different offsets (skip potential header bytes)
print("\n--- Trying with offset adjustments ---")
for skip in [1, 2, 3, 4, 5]:
    data = grid_data[skip:]
    for name_base, fn_factory in [
        ("PackBits", lambda d: try_packbits(d)),
        ("Varint RLE v,c", lambda d: try_varint_rle_vc(d)),
        ("0x80 RLE (varint)", lambda d: try_0x80_rle_v2(d)),
    ]:
        try:
            result = fn_factory(data)
            if result is None:
                continue
            n = len(result)
            match = "*** MATCH ***" if n == target else ""
            if abs(n - target) < 2000:
                same = sum(1 for a, b in zip(result[:min(n, len(shm_cells))], shm_cells) if a == b)
                pct = same / min(n, len(shm_cells)) * 100
                print(f"  skip={skip} {name_base:30s} -> {n:>10d} cells {match} SHM: {pct:.1f}%")
        except:
            pass
