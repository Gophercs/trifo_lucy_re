# Trifo Lucy Reverse Engineering — Findings

Reverse engineering documentation for the Trifo Lucy robot vacuum (model with RK3399 SoC + STM32F407 MCU). Trifo Robotics is defunct; this work documents the robot's internal architecture to enable local control without cloud infrastructure.

**Method**: SSH root access to live device + static analysis of firmware dump in `lucy_dump/`.

---

## Quick Reference

| Question | Answer | Document |
|----------|--------|----------|
| What processes run? | 9 nodes managed by `manager_node` | [Phase 1] |
| How do processes talk? | ZeroMQ IPC over UNIX sockets | [Phase 1], [Phase 3] |
| What message format? | Protocol Buffers (`ServiceMsg`) | [Phase 2] |
| What encryption? | Rolling XOR (cloud), AES-128-CTR (MCU serial) | [Phase 2] |
| What ports are open? | 22 (SSH), 5678 (WiFi provisioning), 5556/7777 (factory only) | [Phase 3] |
| How does it talk to its MCU? | UART `/dev/ttyS3`, AES-128-CTR + CRC-16, magic `0x7466` | [Phase 4] |
| What cloud protocol? | QLCloud (Chinese IoT SDK), MQTT on port 17766, TLS | [Phase 5] |
| How to replace the cloud? | Local MQTT broker + HTTP dispatch + DNS redirect | [Phase 5], [Phase 6] |
| What's the dispatch protocol? | TLS port 7990, binary with rolling XOR (seed=0x7ecf), CRC-16/MODBUS | [Phase 8] |
| How to send commands? | MQTT downdps (opcode 0x0504), DP ID 2, **lowercase** strings | [Phase 9] |
| What commands work? | `start`, `stop`, `dock`, `locate`, `mute`, `blowerMode:N`, `setVolume:N` | [Phase 9] |
| Is local control working? | **YES!** Full cleaning runs triggered from local PC, no cloud needed | [Phase 9] |

---

## Phase Documents

### [Phase 1: Process Architecture](01_process_architecture.md)
Complete map of the Lucy software stack: boot sequence, node topology, binary inventory, transport architecture, mode state machine, DL models.

**Key finding**: All robot control is via ZeroMQ UNIX IPC. The external TCP ports (5556, 7777) are factory-only. Port 5678 belongs to the Android WiFi provisioning app.

### [Phase 2: Message Protocol](02_message_protocol.md)
Message formats for all three communication paths:
- **Internal IPC**: ZeroMQ + Protocol Buffers (`trifo::proto::ServiceMsg`)
- **Cloud MQTT**: QLCloud JSON + rolling XOR or AES-128-CBC
- **MCU serial**: binary packet with AES-128-CTR + CRC-16/CCITT

Includes working Python implementations of all cipher algorithms.

### [Phase 3: ZeroMQ Services](03_zeromq_services.md)
Complete ZeroMQ socket map: all 35+ pub/sub topics, all RPC endpoint pairs, shared memory channels, external TCP ports with their purposes.

**Key finding**: There is no external ZMQ command interface for normal operation. Local control requires either replacing `cloud_node` or injecting into the IPC bus on the robot.

### [Phase 4: MCU Protocol](04_mcu_protocol.md)
RK3399 ↔ STM32F407 communication: UART serial on `/dev/ttyS3`, `TrifoCommPayload` packet format (magic `tf`, group ID, CRC-16), AES-128-CTR encryption, complete `TBot` C++ API with all motor/sensor commands.

**Key finding**: The AES key/IV for the serial link is not statically recoverable — it requires runtime extraction from the MCU firmware or `libtrifo_robot.so` initialisation code.

### [Phase 5: Cloud Protocol](05_cloud_protocol.md)
Full QLCloud protocol reconstruction: 3-region configuration (US/CN/EU), registration/authentication flow (MD5 product sign + device sign), 50+ MQTT data point keys (robot→cloud and cloud→robot), OTA firmware flow, video streaming CDN config, AWS S3 upload.

**Key finding**: `activate_enabled: false` is already set — the device may not need full cloud activation. A local MQTT broker with HTTP dispatch redirect is the path to local control.

### [Phase 6: Proof of Concept](06_proof_of_concept.md)
Documentation of three Python scripts:
- `lucy_decrypt.py` — all cipher algorithms, packet builder, auth helpers
- `lucy_status.py` — read-only IPC subscriber (run on robot via SSH)
- `lucy_mqtt_broker.py` — skeleton local cloud replacement

### [Phase 8: Dispatch Protocol](08_dispatch_protocol.md)
The TLS dispatch server protocol on port 7990: binary framing with rolling XOR (seed=0x7ecf), CRC-16/MODBUS validation, and how Lucy discovers MQTT broker addresses.

### [Phase 9: MQTT Command Discovery & Local Control](09_mqtt_commands.md)
**The big one.** Complete local control achieved:
- Full registration/authentication handshake (automated in `capture_server.py`)
- QLCloud opcode table (26 entries), data point table (4 entries)
- Two command paths: smart speaker (lowercase, triggers actions) and app (UPPERCASE, status only)
- **Confirmed working**: `start` (cleaning), `locate` (find me), `blowerMode:N` (suction)
- Complete command reference with log evidence

---

## Scripts

| Script | Purpose | Run Where |
|--------|---------|-----------|
| `scripts/lucy_decrypt.py` | Crypto utilities + self-test | Anywhere |
| `scripts/lucy_status.py` | Read robot status via IPC | On robot (SSH) |
| `scripts/lucy_mqtt_broker.py` | Local cloud skeleton (superseded) | Your PC |
| `mqtt_capture/capture_server.py` | **Full protocol server** — dispatch, MQTT, HTTPS, commands | Your PC |
| `mqtt_capture/send_command.py` | Low-level command sender (raw DP access) | Your PC |
| `mqtt_capture/lucy_control.py` | **High-level control CLI** — `start`, `stop`, `dock`, etc. | Your PC |

---

## Hardware Summary

| Component | Details |
|-----------|---------|
| SoC | Rockchip RK3399 (dual A72 + quad A53, aarch64) |
| MCU | STM32F407 (motors, sensors) via `/dev/ttyS3` |
| Camera | Structured light RGBD (1920×1080 RGB + ToF depth) |
| WiFi | RTL8822BS |
| Kernel | Linux 4.4.167 SMP PREEMPT |
| OS | Android-based (AOSP, init.rc, system partitions) |

---

## Software Stack Summary

```
Your PC (<YOUR_PC_IP>)                Lucy Robot
──────────────────────────────        ──────────────────────────────────────
capture_server.py                     manager_node (supervisor)
  ├── Dispatch (7990 TLS)   ←──────→   ├── cloud_node (QLCloud SDK)
  ├── MQTT broker (17766 TLS)←─────→   │     ├── register → auth → online
  ├── HTTPS (443)            ←──────→   │     ├── downdps (0x0504) ← commands
  └── Control (9999 TCP)               │     └── updps (0x0503) → status
                                        ├── sensor_node → /dev/ttyS3 → STM32
lucy_control.py ──→ port 9999          ├── camera_node → RGB+ToF cameras
  start/stop/dock/locate/...           ├── slam_node → Cartographer RGBD SLAM
                                        ├── guidance_node → path planning
DNS redirect on Lucy:                  ├── perception_node → obstacle + dock detect
  euiot.trifo.com → <YOUR_PC_IP>       └── offline_node → semantic AI (TFLite)
  eudispatch.trifo.com → <YOUR_PC_IP>

SSH root@<ROBOT_IP>:22
```

---

## Local Control — WORKING!

### Current Setup (Option A — COMPLETE)

1. DNS redirect on Lucy: `euiot.trifo.com` / `eudispatch.trifo.com` → your PC (`<YOUR_PC_IP>`)
2. Run `capture_server.py` on your PC (handles dispatch, MQTT TLS, HTTPS, control port)
3. Lucy auto-connects, registers, authenticates, goes online
4. Send commands via `lucy_control.py`:
   ```
   py lucy_control.py start       # start cleaning
   py lucy_control.py stop        # stop cleaning
   py lucy_control.py dock        # return to dock
   py lucy_control.py locate      # find me (voice/beep)
   py lucy_control.py status      # query status
   py lucy_control.py blower 24   # set suction level
   py lucy_control.py volume 50   # set volume
   ```

### DNS Redirect (on Lucy via SSH)
```bash
cp /etc/hosts /data/local/tmp/hosts
echo "<YOUR_PC_IP> euiot.trifo.com" >> /data/local/tmp/hosts
echo "<YOUR_PC_IP> eudispatch.trifo.com" >> /data/local/tmp/hosts
mount --bind /data/local/tmp/hosts /etc/hosts
kill $(pidof manager_node) 2>/dev/null
cd /data/user/app && ./run.sh &
```

### Future Options

**Option B: IPC Injection** — publish protobuf directly to ZeroMQ sockets on the robot. More direct but requires compiled proto definitions.

**Option C: Replace cloud_node** — write a local replacement that exposes HTTP/WebSocket API. Cleanest long-term solution.

---

## Key Unknowns Remaining

| Unknown | Impact | How to Resolve |
|---------|--------|----------------|
| AES serial key/IV | Cannot decode `/dev/ttyS3` traffic | Disassemble `TBot` constructor or runtime extract |
| ~~QLCloud MQTT topic names~~ | ~~Cannot send commands~~ | **RESOLVED** — `d/s/12642239-4888536` (commands TO), `d/p/12642239-4888536` (data FROM) |
| `CloudStatusDataItemMsg` proto fields | Cannot use IPC injection | Compile from reconstructed `.proto` or use `protoscope` |
| ~~Rolling XOR for dispatch~~ | ~~Cannot decrypt~~ | **RESOLVED** — seed=0x7ecf, CRC-16/MODBUS |
| Exact serial baud rate | Cannot monitor `/dev/ttyS3` | `strace` on `sensor_node` or hardware UART sniffer |
| `MessageGroupID` enum values | Cannot craft MCU commands | Disassemble `TrifoProtocolParser::Encode` |
| blowerMode value mapping | Don't know which number = which suction level | Test empirically (21-24 range likely) |
| setVolume/volumeRelative format | Untested | Send test commands |
| App-path action commands | Can't use UPPERCASE action commands | Investigate different DP ID or message format |
| File upload handling | Lucy wants to upload maps/logs but we don't respond | Implement upload endpoint |

---

## Confidence Levels

| Finding | Confidence | Basis |
|---------|-----------|-------|
| Process list and roles | High | Live SSH observation |
| ZMQ IPC topic names | High | Node config JSON files |
| `ServiceMsg` protobuf type names | High | Binary symbol names (unstripped) |
| Serial packet format (magic, CRC) | High | Disassembly confirmed |
| AES-128-CTR for serial | High | `EVP_aes_128_ctr` symbol confirmed |
| QLCloud auth packet format | **High** | **Live capture + working implementation** |
| Data point key names | **High** | **Runtime process memory read + confirmed working** |
| Rolling XOR algorithm | **High** | **Implemented and working in capture_server.py** |
| MQTT topic scheme | **High** | **Live capture: `d/s/` and `d/p/` prefix + product/device IDs** |
| Smart speaker command names | **High** | **Runtime BSS read + confirmed by log output + physical robot response** |
| Local control commands | **High** | **`start` triggers cleaning, `locate` triggers voice — confirmed 2026-03-14** |
| AES key/IV sources (serial) | Low | Speculative; needs runtime extraction |
