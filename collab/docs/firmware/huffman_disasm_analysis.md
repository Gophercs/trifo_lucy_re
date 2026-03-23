# Trifo Huffman Codec - Full Disassembly Analysis

Library: `libtrifo_core_cloud.so` (aarch64, ELF)
First LOAD segment: vaddr=0x0, offset=0x0 (file offset == virtual address for .text)

---

## Data Structures

### Huffman_node (0x38 = 56 bytes)
```
+0x00: int32   value       // character/symbol value (0x100 = pseudo-EOF marker)
+0x04: int32   frequency   // occurrence count (used during tree building)
+0x08: string  code        // Huffman code as ASCII '0'/'1' text (std::string, 24 bytes SSO)
+0x20: ptr     left        // left child (bit '0')
+0x28: ptr     right       // right child (bit '1')
+0x30: ptr     parent      // parent pointer
```

### Huffman object (partial, relevant fields)
```
+0x870: ptr     root           // Huffman tree root node
+0x878: int32   num_nodes      // number of unique symbols
+0x87C: byte    use_stream     // 1 = use stringstream I/O, 0 = use raw string
+0x880: ostringstream          // output stream (for serialized data)
+0x9E8: string  result_string  // output string (alternative to stream)
+0xB30: ptr     input_string   // pointer to input std::string
+0xB38: ptr     output_string  // pointer to output std::string
+0xB40: ptr     map_root       // BST for char->code lookup (std::map)
+0xB48:         map_sentinel   // end node for the map
+0xB58: vector<Huffman_node*>  // priority queue / node storage
```

---

## 1. decompress (0x126e38, 36 bytes) - Thin Wrapper

```asm
0x126e38: str    x19, [sp, #-0x20]!     // save x19
0x126e3c: stp    x29, x30, [sp, #0x10]  // save frame pointer + return addr
0x126e40: add    x29, sp, #0x10         // set frame pointer
0x126e44: mov    x19, x0                // save 'this' pointer
0x126e48: bl     rebuid_huffman_tree    // PLT -> GOT 0x224bd8
0x126e4c: ldp    x29, x30, [sp, #0x10]  // restore frame
0x126e50: mov    x0, x19                // pass 'this' as arg
0x126e54: ldr    x19, [sp], #0x20       // restore x19
0x126e58: b      decode_huffman         // TAIL CALL -> PLT -> GOT 0x226e90
```

**Analysis:** `decompress()` simply calls `rebuid_huffman_tree()` then tail-calls `decode_huffman()`. Both take `this` as their only argument.

Pseudocode:
```cpp
void Huffman::decompress() {
    this->rebuid_huffman_tree();
    this->decode_huffman();
}
```

---

## 2. rebuid_huffman_tree (0x125a60, 1136 bytes) - Tree Reconstruction

### High-Level Flow

This function reads the serialized Huffman tree from `this->input_string` (at +0xB30). The serialization format stores each symbol's Huffman code as a path from root to leaf using ASCII `'0'` and `'1'` characters.

### Annotated Disassembly (key sections)

```asm
// === PROLOGUE & SETUP ===
0x125a60: sub    sp, sp, #0x1c0        // 448 bytes of stack
0x125a64-0x125a7c: save callee-saved registers, set frame pointer
0x125a80-0x125a84: zero-init local strings [x29-0x78] through [x29-0x68]
                   // This is a local std::string for tree path codes

0x125aa0: mov    x21, x0               // x21 = this

// === INIT STRINGSTREAM from input ===
0x125ab0-0x125ad0: construct std::istringstream on stack [sp+0x20]
0x125ad0: bl     ios_base::init()

// === ALLOCATE ROOT NODE ===
0x125b44: orr    w0, wzr, #0x38        // allocate 56 bytes
0x125b48: bl     operator new(56)       // new Huffman_node
0x125b4c-0x125b5c: zero-init the node (all fields = 0)
0x125b54: str    x0, [x21, #0x870]     // this->root = new_node

// === READ num_nodes FROM STREAM ===
// if (this->use_stream) {  // check byte at +0x87C
0x125b60: ldrb   w8, [x21, #0x87c]
0x125b64: cbz    w8, #0x125dd0         // branch if !use_stream

// Stream path:
0x125b80: ldr    x1, [x21, #0xb30]     // get input string
0x125b84: mov    x0, x22               // stringstream
0x125b88: bl     stringbuf::str(string) // set stream content
0x125b8c: add    x19, x21, #0x878      // &this->num_nodes
0x125b90-0x125b98: stream >> num_nodes  // extract int (calls 0x1105a4)

// === VALIDATE num_nodes ===
0x125b9c: ldr    w8, [x19]             // w8 = this->num_nodes
0x125ba0: cmp    w8, #0x10e            // compare to 270
0x125ba4: b.gt   #0x125e20            // ERROR: > 270 nodes invalid
0x125ba8: cmp    w8, #1
0x125bac: b.lt   #0x125d40            // skip if < 1 node

// === MAIN LOOP: for each symbol, rebuild tree path ===
0x125bbc: mov    w28, wzr              // w28 = loop counter = 0

// --- Loop body ---
// Read two values from stream:
//   1. An integer (the symbol value) -> stored at [x29-0x5C]
//   2. A string (the Huffman code) -> stored at [x29-0x78] (local string)

0x125bc4: ldrb   w8, [x21, #0x87c]    // check use_stream flag
0x125bc8: cbz    w8, #0x125be8         // branch for non-stream path

// Stream path: extract int then string
0x125bcc: add    x0, sp, #0x20         // stringstream
0x125bd0: sub    x1, x29, #0x5c        // &symbol_value
0x125bd4: bl     extract_int           // stream >> symbol_value
0x125bd8: add    x0, sp, #0x20         // stringstream
0x125bdc: sub    x1, x29, #0x78        // &code_string
0x125be0: bl     std::getline / >>     // stream >> code_string
                                        // (operator>> reads whitespace-delimited)
0x125be4: b      #0x125c00

// Non-stream path:
0x125be8: ldr    x0, [sp, #0x10]       // output stream
0x125bec: sub    x1, x29, #0x5c        // &symbol_value
0x125bf0: bl     extract_int           // >> symbol_value
0x125bf4: ldr    x0, [sp, #0x10]       // output stream
0x125bf8: sub    x1, x29, #0x78        // &code_string
0x125bfc: bl     operator>>            // >> code_string

// === GET CODE STRING LENGTH ===
0x125c00: ldurb  w19, [x29, #-0x78]    // w19 = SSO byte of code_string
0x125c04: ldur   x8, [x29, #-0x70]     // x8 = size (non-SSO)
0x125c08: lsr    x9, x19, #1           // x9 = SSO size (if SSO)
0x125c0c: tst    w19, #1               // test SSO flag (bit 0)
0x125c10: csel   x8, x9, x8, eq        // x8 = string length
0x125c14: cmp    w8, #1
0x125c18: b.lt   #0x125d2c            // skip if empty code

// === WALK THE TREE, CREATING NODES ===
0x125c1c: ldr    x22, [x21, #0x870]    // x22 = current_node = this->root
0x125c20: ldur   x23, [x29, #-0x68]    // x23 = code string data ptr (non-SSO)
0x125c24: mov    x27, xzr              // x27 = char index = 0
0x125c28: sub    w20, w8, #1           // w20 = last_index = len-1
0x125c2c: sxtw   x25, w8              // x25 = string length (signed extend)

// --- Inner loop: walk code string char by char ---
0x125c30: tst    w19, #1               // SSO check
0x125c34: csel   x8, x26, x23, eq      // x8 = string data pointer
0x125c38: ldrb   w8, [x8, x27]         // w8 = code_string[index]

// === CHECK IF '0' (LEFT) ===
0x125c3c: cmp    w8, #0x30             // compare to ASCII '0'
0x125c40: b.ne   #0x125c7c            // if not '0', check '1'

// Go LEFT: node->left
0x125c44: add    x8, x22, #0x20        // &current_node->left
0x125c48: ldr    x24, [x8]             // x24 = current_node->left
0x125c4c: cbz    x24, #0x125c9c        // if NULL, create new node

// Node exists - check if at last char
0x125c50: cmp    x20, x27              // last_index == current_index?
0x125c54: b.eq   #0x125de4            // if yes: LEAF - store value

// Not leaf yet, check children exist (internal node validation)
0x125c58: ldr    x8, [x24, #0x20]      // left child of child
0x125c5c: cbnz   x8, #0x125c68        // has left -> continue
0x125c60: ldr    x8, [x24, #0x28]      // right child
0x125c64: cbz    x8, #0x125e0c        // no children = ERROR (invalid code)

// Advance to next character
0x125c68: add    x27, x27, #1
0x125c6c: cmp    x27, x25              // index < length?
0x125c70: mov    x22, x24              // current_node = child
0x125c74: b.lt   #0x125c30            // continue loop

// === CHECK IF '1' (RIGHT) ===
0x125c7c: tst    w19, #1
0x125c80: csel   x8, x26, x23, eq
0x125c84: ldrb   w8, [x8, x27]         // re-read char
0x125c88: cmp    w8, #0x31             // compare to ASCII '1'
0x125c8c: b.ne   #0x125df8            // ERROR: not '0' or '1'

// Go RIGHT: node->right
0x125c90: add    x8, x22, #0x28        // &current_node->right
0x125c94: ldr    x24, [x8]             // x24 = current_node->right
0x125c98: cbnz   x24, #0x125c50        // if exists, check if leaf

// === CREATE NEW NODE (for both left and right paths) ===
0x125c9c: orr    w0, wzr, #0x38        // allocate 56 bytes
0x125ca0: bl     operator new(56)
0x125ca4: mov    x24, x0               // x24 = new_node
0x125ca8-0x125cb8: zero-init new_node
0x125cb8: str    x22, [x0, #0x30]      // new_node->parent = current_node

// Check if this is the LAST character (leaf node)
0x125cbc: b.ne   #0x125cec            // not last char -> just link

// === LEAF NODE: store symbol value and code string ===
0x125cc0: ldur   w8, [x29, #-0x5c]     // w8 = symbol_value
0x125cc4: ldur   x9, [x29, #-0x70]     // x9 = code string size
0x125cc8: mov    x0, x24               // x0 = new_node
// Store the symbol value
0x125cd8: str    w8, [x0], #8          // new_node->value = symbol_value
                                        // x0 now points to +0x08 (code string)
// Copy the code string into the node
0x125ce0: bl     string::assign(ptr, len) // new_node->code = code_string
0x125ce4: ldurb  w19, [x29, #-0x78]    // re-read SSO byte (may have been invalidated)

// === LINK NEW NODE TO PARENT ===
// Re-read the current code char to determine left vs right
0x125cec: tst    w19, #1
0x125cf0: csel   x8, x26, x23, eq
0x125cf4: ldrb   w8, [x8, x27]
0x125cf8: cmp    w8, #0x30             // '0'?
0x125cfc: b.ne   #0x125d18            // if not '0' -> store as right

// Store as LEFT child
0x125d00: str    x24, [x22, #0x20]     // current_node->left = new_node
0x125d04: add    x27, x27, #1          // index++
0x125d08: cmp    x27, x25              // continue?
0x125d0c: mov    x22, x24
0x125d10: b.lt   #0x125c30

// Store as RIGHT child
0x125d18: str    x24, [x22, #0x28]     // current_node->right = new_node
0x125d1c: add    x27, x27, #1
0x125d20: cmp    x27, x25
0x125d24: mov    x22, x24
0x125d28: b.lt   #0x125c30

// === OUTER LOOP: next symbol ===
0x125d2c: ldr    x8, [sp, #0x18]       // reload &num_nodes pointer
0x125d30: add    w28, w28, #1          // counter++
0x125d34: ldr    w8, [x8]              // w8 = num_nodes
0x125d38: cmp    w28, w8               // done?
0x125d3c: b.lt   #0x125bc4            // loop back

// === CLEANUP & RETURN ===
0x125d40-0x125dcc: destroy stringstream, restore registers, return

// === ERROR HANDLERS ===
0x125de4: puts("Huffman code is not valid, maybe the compressed file has been broken.")
          exit(1)
0x125df8: puts("Decode error, huffman code is not made up with 0 or 1")
          exit(1)
0x125e0c: puts("Huffman code is not valid, maybe the compressed file has been broken.")
          exit(1)
0x125e20: puts("The number of nodes is not valid, maybe the compressed file has been broken.")
          exit(1)
```

### Pseudocode for rebuild_huffman_tree

```cpp
void Huffman::rebuid_huffman_tree() {
    istringstream iss(*(this->input_string));

    // Read number of unique symbols
    iss >> this->num_nodes;

    if (this->num_nodes > 270) {
        puts("The number of nodes is not valid...");
        exit(1);
    }

    // Allocate root node (all zeros)
    this->root = new Huffman_node();  // 56 bytes, zeroed

    for (int i = 0; i < this->num_nodes; i++) {
        int symbol_value;
        string code_string;

        iss >> symbol_value >> code_string;
        // code_string is like "010" or "1101" (ASCII '0' and '1')

        if (code_string.length() < 1) continue;

        Huffman_node* current = this->root;

        for (int j = 0; j < code_string.length(); j++) {
            char c = code_string[j];

            if (c == '0') {
                // Go left
                if (current->left == NULL) {
                    // Create new node
                    Huffman_node* node = new Huffman_node();
                    node->parent = current;

                    if (j == code_string.length() - 1) {
                        // Leaf node: store symbol
                        node->value = symbol_value;
                        node->code = code_string;
                    }

                    current->left = node;
                    current = node;
                } else {
                    current = current->left;
                }
            } else if (c == '1') {
                // Go right
                if (current->right == NULL) {
                    Huffman_node* node = new Huffman_node();
                    node->parent = current;

                    if (j == code_string.length() - 1) {
                        node->value = symbol_value;
                        node->code = code_string;
                    }

                    current->right = node;
                    current = node;
                } else {
                    current = current->right;
                }
            } else {
                puts("Decode error, huffman code is not made up with 0 or 1");
                exit(1);
            }
        }
    }
}
```

### KEY FINDING: Serialized Tree Format

The serialized data in `input_string` has this format:
```
<num_nodes>\n
<symbol_value_1> <code_1>\n
<symbol_value_2> <code_2>\n
...
<symbol_value_N> <code_N>\n
<compressed_bitstream_as_binary_bytes>
```

Where:
- `num_nodes` is a decimal integer (max 270, which is 256 byte values + 1 pseudo-EOF + some margin)
- Each symbol entry is: decimal integer (symbol value 0-255, or 256 for pseudo-EOF) followed by space, followed by its Huffman code as ASCII `'0'` and `'1'` characters
- The entries are whitespace-delimited (read via `operator>>`)
- Symbol value 0x100 (256) is the pseudo-EOF marker
- After the code table, the remaining data is the compressed bitstream

---

## 3. decode_huffman (0x1260f8, 2612 bytes) - Main Decode Loop

### High-Level Flow

This function reads the compressed bitstream byte by byte, traverses the Huffman tree bit by bit, and outputs decoded symbols. It processes 8 bits per byte, MSB first.

### Key Observations from Disassembly

#### Setup Phase (0x1260f8-0x1261e0)
- Creates istringstream from input data
- Loads `this->root` into x25 (tree root pointer)
- Checks `use_stream` flag at +0x87C

#### Initial Stream Processing (0x1261e4-0x126550)
When NOT using stringstream (use_stream == 0):
```asm
0x126240: add    x22, x20, #0x880      // x22 = &this->output_stream
0x126244: mov    x0, x22
0x126248: bl     istringstream::get()   // read first char from input
0x12624c: ldr    x8, [x20, #0x880]     // check stream state
0x126250: add    x19, x20, #0x8a0      // stream state flags
0x126254: ldur   x8, [x8, #-0x18]      // get vtable offset
0x126258: ldrb   w8, [x19, x8]         // read ios_base state bits
0x12625c: tbnz   w8, #1, #0x126510     // if eofbit set -> done
```

When using stringstream (use_stream == 1):
```asm
// Reads the encoded string, converts to istringstream
// Extracts the first byte to establish initial position
0x1261f8-0x12654c: setup stringstream, seek past the code table
```

After skipping the code table (using the stream position after `rebuid_huffman_tree` consumed the table), it begins byte-by-byte processing.

#### Main Decode Loop (0x12627c-0x12650c) - 8 UNROLLED BIT CHECKS

The core decoding loop is **FULLY UNROLLED for 8 bits per byte**. For each byte read from the stream, it tests bits from MSB (bit 7) to LSB (bit 0), traversing the tree at each step.

```asm
// Read a byte via istringstream::get()
0x12627c: mov    x0, x22
0x126280: bl     istringstream::get()   // w0 = next byte (or -1 for EOF)
0x126284: cmn    w0, #1                 // check for EOF (-1)
0x126288: csel   w23, w23, w0, eq       // if EOF, keep previous; else w23 = byte

// === BIT 7 (MSB, 0x80) ===
0x12628c: tst    w23, #0x80            // test bit 7
0x126290: csel   x8, x27, x20, eq      // x27=0x20 (left offset), x20=0x28 (right offset)
                                        // if bit==0: offset=0x20 (left)
                                        // if bit==1: offset=0x28 (right)
0x126294: ldr    x8, [x25, x8]         // x8 = node->left or node->right

// Check if we reached a leaf
0x126298: mov    x9, x8
0x12629c: ldr    x10, [x9, #0x20]!     // x10 = x8->left
0x1262a0: cbnz   x10, #0x1262cc        // has left child -> internal node, continue
0x1262a4: ldr    x10, [x8, #0x28]      // x10 = x8->right
0x1262a8: cbnz   x10, #0x1262cc        // has right child -> internal node, continue

// LEAF NODE reached
0x1262ac: ldr    w1, [x8]              // w1 = node->value (the decoded symbol)
0x1262b0: cmp    w1, #0x100            // check for pseudo-EOF (256)
0x1262b4: b.eq   #0x126510            // if pseudo-EOF -> done decoding

// Append decoded byte to output string
0x1262b8: sub    x0, x29, #0x78        // output string (local)
0x1262bc: bl     string::push_back(w1)  // output += (char)symbol

// Reset to tree root for next symbol
0x1262c0: ldr    x8, [sp, #0x30]       // reload 'this'
0x1262c4: ldr    x8, [x8, #0x870]      // x8 = this->root
0x1262c8: add    x9, x8, #0x20         // prepare for next bit

// === BIT 6 (0x40) ===
0x1262cc: and    w21, w23, #0xff       // w21 = byte (zero-extended)
0x1262d0: add    x8, x8, #0x28        // right child offset
0x1262d4: tst    w21, #0x40            // test bit 6
0x1262d8: csel   x8, x9, x8, eq        // left if 0, right if 1
0x1262dc: ldr    x8, [x8]             // follow pointer
// ... leaf check same pattern ...
// if leaf: push_back symbol, reset to root

// === BIT 5 (0x20) ===
0x126318: tst    w21, #0x20
// ... same pattern ...

// === BIT 4 (0x10) ===
0x12635c: tst    w21, #0x10
// ... same pattern ...

// === BIT 3 (0x08) ===
0x1263a0: tst    w21, #8
// ... same pattern ...

// === BIT 2 (0x04) ===
0x1263e4: tst    w21, #4
// ... same pattern ...

// === BIT 1 (0x02) ===
0x126428: tst    w21, #2
// ... same pattern ...

// === BIT 0 (LSB, 0x01) ===
0x12646c: tst    w21, #1
0x126470: csel   x8, x9, x8, eq
0x126474: ldr    x25, [x8]            // x25 = next node (carried to next byte)
// leaf check ...
// if leaf: push_back, reset root -> x25
```

After processing all 8 bits:
```asm
// Check output buffer - if >= 11 chars, flush to result
0x1264a4: ldurb  w8, [x29, #-0x78]     // local output string
0x1264a8-0x1264b8: get string length
0x1264b8: cmp    x2, #0xa              // length > 10?
0x1264bc: b.ls   #0x1264e0            // if <= 10, skip flush

// Flush: append local string to this->result_string (+0x9E8)
0x1264cc: mov    x0, x24               // x24 = &this->result_string
0x1264d0: bl     put_character_sequence // append to result

// Clear local output string
0x1264dc: sturh  wzr, [x29, #-0x78]    // reset SSO string to empty

// Check stream EOF
0x1264e0: ldr    x8, [x22]             // stream vtable
0x1264e4: ldur   x8, [x8, #-0x18]      // vtable offset
0x1264e8: ldrb   w8, [x19, x8]         // ios state flags
0x1264ec: tbz    w8, #1, #0x12627c     // if NOT eof -> loop back
```

#### Finalization (0x126510-0x12697c)
```asm
// Flush remaining output buffer to this->result_string
0x126510-0x126548: check if local output has data, append to result

// Copy result to this->output_string
0x126528: cbz    x2, #0x12697c        // if empty, skip
0x12652c: add    x0, x9, #0x9e8       // this->result_string
0x126544: bl     put_character_sequence

// Then extract the stringstream content
0x12688c-0x1268d8: result = stringstream.str()
// Copy result to *(this->output_string)
0x1268cc-0x1268d8: *(this->output_string) = result
```

### Pseudocode for decode_huffman

```cpp
void Huffman::decode_huffman() {
    istringstream iss(*(this->input_string));

    // Skip past the code table (stream position is after rebuid_huffman_tree consumed it)
    // Actually: creates new stream, reads to skip the header
    // The stream position after reading all code entries is where bitstream starts

    Huffman_node* current = this->root;
    string output_buffer;

    while (true) {
        int byte = iss.get();  // read one byte
        if (byte == EOF) byte = previous_byte;  // reuse last on EOF

        // Process 8 bits, MSB first
        for (int bit = 7; bit >= 0; bit--) {
            if (byte & (1 << bit)) {
                current = current->right;  // bit 1 -> right
            } else {
                current = current->left;   // bit 0 -> left
            }

            // Check if leaf (no children)
            if (current->left == NULL && current->right == NULL) {
                int symbol = current->value;

                if (symbol == 0x100) {  // pseudo-EOF
                    goto done;
                }

                output_buffer += (char)symbol;
                current = this->root;  // reset to root
            }
        }

        // Periodic flush of output buffer
        if (output_buffer.length() > 10) {
            this->result_string += output_buffer;
            output_buffer.clear();
        }

        // Check for stream EOF
        if (iss.eof()) break;
    }

done:
    // Flush remaining
    if (!output_buffer.empty()) {
        this->result_string += output_buffer;
    }
    *(this->output_string) = this->result_string;
}
```

---

## 4. do_compress (0x124ad4, 3724 bytes) - Compression (Encoder)

### Key Insights from Encoder

The encoder confirms the format. Key sections:

#### Writing the code table
```asm
// For each entry in the map (BST at +0xB40):
0x124c60: ldr    w1, [x19, #0x20]     // symbol value from map node
0x124c68: bl     ostream::operator<<(int)  // write symbol value as decimal text

0x124c6c: orr    w2, wzr, #1          // len=1
0x124c70: mov    x1, x24              // x24 points to " " (space separator)
0x124c74: bl     put_character_sequence // write " "

// Then write the Huffman code string (at map_node+0x28)
0x124c78: ldrb   w8, [x19, #0x28]     // SSO byte of code string
0x124c7c: ldp    x9, x10, [x19, #0x30] // size, data ptr
// ... get string data ...
0x124c94: bl     put_character_sequence // write code like "0110"
// Then flush (endl)
```

#### Converting ASCII '0'/'1' codes to packed bytes
```asm
// After writing all code entries, process the codes:
// Read 8 ASCII chars at a time, pack into one byte

0x125030: ldurb  w8, [x29, #-0x80]     // local string SSO byte
0x125038: tst    w8, #1
0x12503c: csel   x8, x27, x9, eq       // get string data
0x125040: add    x8, x8, x26           // offset by position

// Read 8 consecutive ASCII chars and pack into one byte:
0x125044: ldurb  w9, [x8, #-7]         // char at position-7
0x125048: ldurb  w10, [x8, #-6]
0x12504c: ldurb  w11, [x8, #-5]
0x125050: ldurb  w12, [x8, #-4]
0x125054: ldurb  w13, [x8, #-3]

0x125058: cmp    w9, #0x30             // if char != '0' then bit=1
0x12505c: cset   w9, ne
0x125060: cmp    w10, #0x30
0x125064: cset   w10, ne
// ...
0x125078: lsl    w10, w10, #6          // bit 6
0x12507c: bfi    w10, w9, #7, #1       // bit 7 (MSB)
0x12508c: bfi    w10, w11, #5, #1      // bit 5
0x12509c: bfi    w10, w9, #4, #1       // bit 4
0x1250a8: bfi    w10, w11, #3, #1      // bit 3
0x1250ac: bfi    w10, w9, #2, #1       // bit 2
0x1250b4: bfi    w10, w9, #1, #1       // bit 1
0x1250b8: cmp    w8, #0x30
0x1250bc: cinc   w1, w10, ne           // bit 0 (LSB)

0x1250c0: sub    x0, x29, #0x98        // packed output string
0x1250c4: bl     string::push_back(byte) // append packed byte
```

This confirms: **The encoder takes each symbol's Huffman code (stored as a string of ASCII '0' and '1'), concatenates all codes for the input data, then packs every 8 ASCII chars into one byte, MSB first.**

#### Writing pseudo-EOF code at end
```asm
// After encoding all input bytes, look up symbol 0x100 in the map:
0x125208-0x125240: search map for key 0x100 (pseudo-EOF)
// Get its code string and pack it the same way
0x12524c: puts("Can't find the huffman code of pseudo-EOF")  // error if not found
```

---

## 5. COMPLETE FORMAT SPECIFICATION

### Encoded Data Format

```
HEADER (text, newline-delimited):
  <num_symbols>\n                    // decimal integer, 1-270
  <value_1> <code_1>\n              // e.g., "97 010\n" means byte 0x61 has code "010"
  <value_2> <code_2>\n
  ...
  <value_N> <code_N>\n              // value 256 = pseudo-EOF

BODY (packed binary):
  <byte_1><byte_2>...<byte_M>      // Huffman-coded bitstream, MSB first
```

### Bit Packing (MSB first)

For a code string like `"01101"`:
- Position 0 = '0' -> bit 7 of first byte = 0
- Position 1 = '1' -> bit 6 of first byte = 1
- Position 2 = '1' -> bit 5 of first byte = 1
- Position 3 = '0' -> bit 4 of first byte = 0
- Position 4 = '1' -> bit 3 of first byte = 1
- ...continues with next symbol's code bits filling remaining positions

### Decoding Algorithm

1. Parse the text header to get the code table
2. Build a binary tree: for each `(value, code)` pair, walk from root following '0'=left, '1'=right, creating nodes; store value at leaf
3. Read the binary body byte by byte
4. For each byte, extract bits MSB first (bit 7, 6, 5, 4, 3, 2, 1, 0)
5. For each bit: go left (0) or right (1) in the tree
6. When reaching a leaf: output `value` as a byte; reset to root
7. Stop when encountering pseudo-EOF (value == 0x100 / 256)

### Integration with OGM Grid Data -- CRITICAL FINDING

**The Huffman class is NOT directly responsible for the OGM grid encoding.**

Evidence:
1. The grid data in `.ogm` files starts with `80 01 00`, which is NOT a text-based Huffman header (which would start with a decimal ASCII number like "42\n")
2. The Huffman class methods (`compress`/`decompress`) are exported but NOT called from any function within `libtrifo_core_cloud.so` itself -- they must be called by external binaries
3. The `SetOGM` function simply converts doubles to bytes (value * 10.0) and stores them in a vector -- no Huffman encoding
4. The `SaveOGM` function writes the protobuf directly to disk without additional compression

The grid encoding (which produces the `80 01 00` + binary data in field 6 of `CloudGridMsg`) is performed by `slam_node` or `cloud_node` before the data reaches the protobuf serialization layer. The Huffman class may be used as part of that pipeline, but the encoded format in the OGM file is NOT the raw Huffman text-header format.

**Possible scenarios:**
1. The Huffman class is used for a different purpose (e.g., MQTT data compression) and the grid uses a completely different encoding
2. The grid data goes through a pipeline: raw cells -> some binary encoding -> possibly Huffman -> stored in protobuf
3. The `80 01 00` header is a custom binary format that wraps or replaces the Huffman text header

The `0xFF -> 0x7F` remapping (in `convert_to_ogm_format`) still applies to the grid cells before encoding, and the Huffman alphabet (0x00-0x7F plus 0x100 for pseudo-EOF) is compatible with this. But the actual encoding format in the OGM file needs further analysis of the calling code (likely in `slam_node` or `cloud_node` binaries).

---

## 6. PYTHON DECODER (for Huffman text-header format)

This decoder handles data that uses the Huffman class's native text-header format. This format is confirmed by the disassembly but may not be what's used in `.ogm` files directly.

```python
def huffman_decompress(data: bytes) -> bytes:
    """
    Decompress Trifo Huffman-encoded data (text-header format).

    The input format is:
        <num_symbols>\n
        <value_1> <code_1>\n
        <value_2> <code_2>\n
        ...
        <value_N> <code_N>\n
        <packed_binary_bitstream>

    Where codes are ASCII '0'/'1' strings, values are decimal integers,
    and the bitstream is MSB-first packed bytes terminated by pseudo-EOF
    (symbol value 256).

    Args:
        data: The full encoded payload (text header + binary body)
    Returns:
        Decoded bytes
    """
    # The C++ code uses istringstream with operator>> which reads
    # whitespace-delimited tokens. We simulate this.
    text = data.decode('latin-1')

    # Use a stream-like approach matching the C++ istringstream behavior
    import re
    tokens = re.split(r'\s+', text, maxsplit=1)

    # Parse header: first token is num_symbols
    pos = 0
    # Find num_symbols (first whitespace-delimited token)
    while pos < len(data) and data[pos] in (0x20, 0x09, 0x0a, 0x0d):
        pos += 1
    end = pos
    while end < len(data) and data[end] not in (0x20, 0x09, 0x0a, 0x0d):
        end += 1
    num_symbols = int(data[pos:end].decode('ascii'))
    pos = end

    # Parse code table entries
    codes = {}
    for _ in range(num_symbols):
        # Skip whitespace
        while pos < len(data) and data[pos] in (0x20, 0x09, 0x0a, 0x0d):
            pos += 1
        # Read symbol value
        end = pos
        while end < len(data) and data[end] not in (0x20, 0x09, 0x0a, 0x0d):
            end += 1
        symbol_value = int(data[pos:end].decode('ascii'))
        pos = end

        # Skip whitespace
        while pos < len(data) and data[pos] in (0x20, 0x09, 0x0a, 0x0d):
            pos += 1
        # Read code string
        end = pos
        while end < len(data) and data[end] not in (0x20, 0x09, 0x0a, 0x0d):
            end += 1
        code_string = data[pos:end].decode('ascii')
        pos = end

        codes[code_string] = symbol_value

    # Skip exactly one whitespace char (the delimiter between header and body)
    if pos < len(data) and data[pos] in (0x20, 0x09, 0x0a, 0x0d):
        pos += 1

    # Build Huffman tree
    class Node:
        __slots__ = ['value', 'left', 'right']
        def __init__(self):
            self.value = None
            self.left = None
            self.right = None

    root = Node()
    for code_str, value in codes.items():
        current = root
        for bit_char in code_str:
            if bit_char == '0':
                if current.left is None:
                    current.left = Node()
                current = current.left
            else:  # '1'
                if current.right is None:
                    current.right = Node()
                current = current.right
        current.value = value

    # Decode bitstream (remaining bytes after the text header)
    bitstream = data[pos:]

    output = bytearray()
    current = root

    for byte_val in bitstream:
        for bit_pos in range(7, -1, -1):  # MSB first: 7,6,5,4,3,2,1,0
            bit = (byte_val >> bit_pos) & 1

            if bit == 0:
                current = current.left
            else:
                current = current.right

            if current is None:
                raise ValueError("Invalid Huffman code encountered")

            # Check if leaf (no children)
            if current.left is None and current.right is None:
                if current.value == 0x100:  # pseudo-EOF (256)
                    return bytes(output)
                output.append(current.value & 0xFF)
                current = root

    return bytes(output)
```

### Stream Position Details

The C++ code uses a single `istringstream`. `rebuid_huffman_tree` consumes the header via `operator>>` (which skips leading whitespace, reads until next whitespace). Then `decode_huffman` reads the remaining bytes via `istream::get()` (one char at a time, no whitespace skipping).

After `operator>>` reads the last code string, the stream position is at the whitespace character AFTER that string. `decode_huffman` then calls `get()` which returns that whitespace character as the first "byte" of the bitstream. This first byte is effectively a garbage/padding byte before the real bitstream starts.

Actually, looking more carefully at the decode_huffman code: it reads the stream content as a string, then iterates over the raw bytes. The stream position after `rebuid_huffman_tree` determines where the bitstream starts. The first `get()` call in decode_huffman corresponds to reading the first byte of the packed bitstream.
