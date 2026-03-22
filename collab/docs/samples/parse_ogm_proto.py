"""Carefully re-parse the OGM file protobuf structure."""
import struct

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

def parse_field(data, pos):
    """Parse one protobuf field, return (field_num, wire_type, value, next_pos)"""
    tag, pos = read_varint(data, pos)
    field_num = tag >> 3
    wire_type = tag & 0x07

    if wire_type == 0:  # varint
        val, pos = read_varint(data, pos)
        return field_num, wire_type, val, pos
    elif wire_type == 1:  # 64-bit
        val = struct.unpack('<d', data[pos:pos+8])[0]
        return field_num, wire_type, val, pos + 8
    elif wire_type == 2:  # length-delimited
        length, pos = read_varint(data, pos)
        val = data[pos:pos+length]
        return field_num, wire_type, (length, val, pos), pos + length
    elif wire_type == 5:  # 32-bit
        val = struct.unpack('<f', data[pos:pos+4])[0]
        return field_num, wire_type, val, pos + 4
    else:
        return field_num, wire_type, None, pos

with open("latest_map.ogm", "rb") as f:
    data = f.read()

print(f"File size: {len(data)} bytes")
print(f"First 100 bytes hex: {data[:100].hex()}")
print()

# Parse outer message (CloudMultiMapLayerMsg)
pos = 0
while pos < len(data):
    try:
        field_num, wire_type, val, next_pos = parse_field(data, pos)
    except:
        print(f"  Parse error at pos {pos}")
        break

    wt_names = {0: 'varint', 1: 'fixed64', 2: 'len-delim', 5: 'fixed32'}
    wt_name = wt_names.get(wire_type, f'wt{wire_type}')

    if wire_type == 2:
        length, payload, payload_start = val
        print(f"Pos {pos}: field={field_num} type={wt_name} length={length} payload_start={payload_start} payload_end={payload_start+length}")

        # If this is the map entry (field 2), parse the map entry
        if field_num == 2:
            print(f"  Map entry, parsing inner...")
            ipos = 0
            while ipos < len(payload):
                try:
                    fn2, wt2, v2, ipos2 = parse_field(payload, ipos)
                except:
                    break

                if wt2 == 2:
                    l2, p2, ps2 = v2
                    if fn2 == 1:  # key (string)
                        print(f"  Inner field={fn2} type=string value=\"{p2.decode('utf-8', errors='replace')}\"")
                    elif fn2 == 2:  # value (CloudGridMsg)
                        print(f"  Inner field={fn2} type=CloudGridMsg length={l2} starts_at_in_payload={ps2}")
                        # Parse CloudGridMsg
                        gpos = 0
                        while gpos < len(p2):
                            try:
                                fn3, wt3, v3, gpos2 = parse_field(p2, gpos)
                            except:
                                break
                            if wt3 == 2:
                                l3, p3, ps3 = v3
                                # This is the data field
                                abs_offset = payload_start + ps2 + ps3
                                print(f"    GridMsg field={fn3} type=bytes length_varint={l3} actual_bytes={len(p3)} abs_file_offset={abs_offset}")
                                print(f"    Data first 40: {p3[:40].hex()}")
                                print(f"    Data last 20: {p3[-20:].hex()}")

                                # Check if length varint > actual data
                                if l3 > len(p2) - ps3 + gpos:
                                    remaining_in_gridmsg = len(p2) - gpos2
                                    print(f"    *** LENGTH MISMATCH: varint says {l3}, available in GridMsg = {len(p2)-ps3+gpos}")
                                    # The varint length extends beyond the GridMsg bounds
                                    # This means the protobuf is malformed OR the length IS the uncompressed size
                            elif wt3 == 0:
                                print(f"    GridMsg field={fn3} type=varint value={v3}")
                            elif wt3 == 1:
                                print(f"    GridMsg field={fn3} type=fixed64 value={v3}")
                            else:
                                print(f"    GridMsg field={fn3} type={wt3} value={v3}")
                            gpos = gpos2
                    else:
                        print(f"  Inner field={fn2} type=len({l2})")
                elif wt2 == 0:
                    print(f"  Inner field={fn2} type=varint value={v2}")
                elif wt2 == 1:
                    print(f"  Inner field={fn2} type=fixed64 value={v2}")
                ipos = ipos2

        # For short payloads, print content
        if length < 100 and field_num != 2:
            print(f"  Content: {payload.hex()}")
    elif wire_type == 0:
        print(f"Pos {pos}: field={field_num} type={wt_name} value={val}")
    elif wire_type == 1:
        print(f"Pos {pos}: field={field_num} type={wt_name} value={val}")
    else:
        print(f"Pos {pos}: field={field_num} type={wt_name} value={val}")

    if next_pos <= pos:
        print("  No progress, stopping")
        break
    pos = next_pos

    # Safety: stop if we've gone too far without finding structure
    if pos > len(data):
        break

print(f"\nFinal pos: {pos}, file size: {len(data)}")
if pos < len(data):
    remaining = data[pos:]
    print(f"Remaining {len(remaining)} bytes: {remaining[:60].hex()}")
