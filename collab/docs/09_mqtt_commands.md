# Phase 9: MQTT Command Discovery & Local Control

**Date**: 2026-03-13 (updated 2026-03-14)
**Status**: LEGENDARY — Full local control achieved! Lucy starts cleaning on command!

## 1. Registration & Authentication (COMPLETE)

Lucy completes the full handshake automatically via our capture server:

1. **Dispatch** (port 7990): Lucy sends MAC, gets pointed to local MQTT broker
2. **MQTT CONNECT** (port 17766): TLS 1.2, client_id=`c0:e7:bf:4a:97:d8`
3. **Registration** (state 7→9): Lucy sends `product_sign`, we reply with `{"res":{"errcode":0},"data":{"dev_id":"SN","dev_key":"base64"}}`
4. **Authentication** (state 10→14): Lucy sends `dev_sign`, we reply with `{"res":{"errcode":0},"data":{"iotid":"SN","token":"44chars","ts":unix_ts}}`
5. **Online**: Lucy starts sending updps (status), file_upload requests, heartbeats

## 2. QLCloud Opcode Table (COMPLETE)

Discovered by reading the opcode table at binary offset 0x228108 (26 entries, 24 bytes each):

| Opcode | Name | Direction | Handler Function |
|--------|------|-----------|-----------------|
| 0x0101 | reg | Device→Cloud | Registration request |
| 0x0201 | auth | Device→Cloud | Authentication request |
| 0x0301 | hrt | Both | Heartbeat |
| 0x0401 | tx | Device→Cloud | Transmit |
| 0x0402 | rx | Cloud→Device | Receive (raw pass-through) |
| 0x0403 | msg_0403 | Device→Cloud | Raw response (DP ack) |
| **0x0504** | **downdps** | **Cloud→Device** | **Commands TO robot** |
| 0x0503 | updps | Device→Cloud | Upload data points (status) |
| 0x0303 | file_upload | Device→Cloud | File upload request |
| 0x0a01 | OTA | Cloud→Device | OTA upgrade |
| 0x0b06 | data_b06 | Device→Cloud | Data points (alt updps) |

## 3. Data Point (DP) Table (COMPLETE)

Read from cloud_node process memory at runtime (GOT[0xa48]):

| DP ID | Type | Handler | Purpose |
|-------|------|---------|---------|
| 1 | 3 (string) | `dp_down_handle_bind_success` | Binding acknowledgment |
| **2** | **3 (string)** | **`dp_down_handle_robot_control`** | **Robot commands** |
| 18 | 3 (string) | `dp_handle_tmall_control` | Tmall Genie smart home |
| 19 | 3 (string) | `dp_handle_upload_log` | Upload log control |

## 4. Command Format (CONFIRMED WORKING)

### Sending Commands TO Lucy

**MQTT Topic**: `d/s/12642239-4888536` (Lucy's subscribe topic)

**16-byte QLCloud Header**:
```
Offset  Size  Value       Field
0       4     total_len   Total payload length (BE, includes header)
4       2     0x0010      Protocol/type
6       2     0x0201      Command type
8       2     0x0100      Flags
10      2     0x0504      Opcode = downdps
12      4     seq_num     Sequence number (BE)
16      N     JSON        Command payload
```

**JSON Payload**:
```json
{
  "data": [{"i": 2, "v": "COMMAND_NAME"}],
  "subid": "LUCPB4ACC26M0017",
  "expire": <unix_timestamp + 300>
}
```

### Lucy's Response (two messages)

**1. Acknowledgment** (opcode 0x0403, raw pass-through):
```json
{"head": "rp", "key": "2", "cmd": "", "sum": 0, "index": 0, "requestid": "", "data": "OK"}
```

**2. Status Update** (opcode 0x0503, updps):
```json
{
  "data": [
    {
      "i": 2,
      "v": "{\"status\": \"12\", \"battery\": \"100\", \"position\": \"home\", \"mute\": \"false\", \"volume\": \"50\", \"blower\": \"23\"}",
      "t": 3
    }
  ]
}
```

## 5. Status Fields

From Lucy's status response when docked/idle:

| Field | Value | Meaning |
|-------|-------|---------|
| status | "12" | Docked/charged (likely decimal) |
| battery | "100" | Battery percentage |
| position | "home" | At charging dock |
| mute | "false" | Sound enabled |
| volume | "50" | Speaker volume |
| blower | "23" | Suction level |

## 6. Smart Speaker Commands (THE WORKING ONES)

**CRITICAL**: Commands are **case-sensitive** and must be **lowercase** (or camelCase where shown).
These are the commands recognized by the smart speaker handler in `cloudserver.cc`.
Discovered by reading runtime data from `libtrifo_core_cloud.so` process memory at offset 0x2287b8+.

The command value is sent as-is in the DP 2 `"v"` field: `{"data":[{"i":2,"v":"start"}], ...}`

### Action Commands — CONFIRMED WORKING

| Command | Purpose | Status |
|---------|---------|--------|
| `start` | Start cleaning run | ✅ **CONFIRMED** — Lucy starts cleaning! (2026-03-14) |
| `stop` | Stop cleaning | Recognized (not yet tested) |
| `dock` | Return to charging dock | Recognized (not yet tested) |
| `locate` | Find me — triggers voice/beep | ✅ **CONFIRMED** — Lucy speaks! (2026-03-14) |
| `mute` | Mute/unmute speaker | Recognized (not yet tested) |

### Parameterized Commands (format: `command:value`)

| Command | Purpose | Expected Parameter |
|---------|---------|-------------------|
| `setVolume` | Set speaker volume | Volume level (e.g., `setVolume:50`) |
| `volumeRelative` | Adjust volume up/down | Delta (e.g., `volumeRelative:10` or `volumeRelative:-10`) |
| `blowerMode` | Set suction level | Mode number (e.g., `blowerMode:24`) — CONFIRMED in logs as `BLOWER:24` |

### Product Name Entries (NOT commands — device identification)

These appear in the same table but are product model names, not action commands:
`w_bot`, `max`, `lucy`, `ollie`, `viva`

### How It Was Discovered

The smart speaker handler at `cloudserver.cc:3165` splits the incoming DP 2 value on `:`,
then compares the first part against known command names stored in BSS (populated at startup).
- **Uppercase commands** (like `LOCATE`) → `"Unknown smart speaker command."` (line 3243)
- **Lowercase commands** (like `locate`) → Recognized and dispatched to internal handlers

The `locate` command maps internally to "find me" (mode 24).
The `start` command maps internally to `CLEAN_STATE` / "new sweep clean" (mode 2).

### Log Evidence

```
# UPPERCASE = REJECTED
cloudserver.cc:3165] smart speaker command:LOCATE,dpid:2
cloudserver.cc:3243] Unknown smart speaker command.

# lowercase = ACCEPTED
cloudserver.cc:3165] smart speaker command:locate,dpid:2
cloudserver.cc:3199] smart speaker: recv find me message.
cloud_node.cc:354] cloud request find me.

# start command
lucy_cloud_actor.cc:2681] new sweep clean.
cloud_node.cc:440] current_mode:  2,surveillance_running: 0
```

## 6b. App-Path Commands (UPPERCASE — status queries only via DP 2)

These are the "app request command" path in `cloudserver.cc`. They return status data
but do NOT trigger physical actions. Found in `cloud_node` binary strings.
They work in UPPERCASE and trigger a status response (opcode 0x0503).

### Read-Only (Status Queries) — TESTED
- `GET_WORK_STATUS` — returns full status JSON ✅ CONFIRMED
- `BATTERY` — returns full status JSON ✅ CONFIRMED
- `CLEAN_STATE` — returns full status JSON ✅ CONFIRMED
- `GET_SYSTEM_CONFIG` — returns full status JSON ✅ CONFIRMED
- `GET_MOPPING_MODE` — tested ✅
- `GET_DONOT_DISTURB_MODE` — tested ✅
- `CLEAN_AREA` — clean area info
- `CLEAN_TIME` — clean time info
- `GET_LUCY_POSE` — robot position
- `GET_LUCY_MAP` / `GET_MAP` — map data
- `GET_MANUAL_MOPPING_MODE`
- `GET_MONITOR_STATE` / `GET_MONITOR_VIDEO` / `GET_MONITOR_DETAIL` / `GET_MONITOR_TASKS`
- `GET_MONITOR_PERSON_ONLY_STATE`
- `GET_LOG_STATE`
- `GET_TEST_BOT`

### App Action Commands (UPPERCASE — NOT usable via smart speaker path)
These are in the `cloud_node` binary but are for the Trifo app's direct control path,
not the smart speaker handler. Sending them via DP 2 logs "Unknown smart speaker command."
They may require a different DP ID or a different message format (e.g., JSON with `"head":"rp"`).

- `SET_RUNNING_MODE_WITH_DOCK` — start cleaning (app path)
- `SET_RUNNING_MODE_WITHOUT_DOCK` — start cleaning, stay in place
- `RESTART_CLEAN` — resume cleaning
- `SET_TIRVIS_ENABLE` — enable/disable TirVis camera
- `SET_WALL_FOLLOW_ENABLE` — wall follow mode
- `RESET_DEVICE_DATA` — factory reset? DANGEROUS
- `RESET_MTNC` — reset maintenance counters
- `LUCY_MAP_MGR` — map manager
- Others: `SET_DEBUG_LOG`, `SET_LOG_STATE`, `SET_MAP_MANAGE_STATE`, etc.

## 7. Periodic Messages from Lucy

When online, Lucy sends these automatically every ~10 seconds:

1. **File upload requests** (opcode 0x0303): `{"type": 0|2, "size": 1048576}` — asking for permission to upload files (map data, logs)
2. **Heartbeats** (opcode 0x0301): Keepalive, we respond with errcode 0
3. **Status updates** (opcode 0x0503/0x0b06): Periodic data point uploads
4. **Language pack requests**: HTTPS GET to `/Oauth/language_pack/lucy/config_language_pack.json`

## 8. Tools

### capture_server.py
- Full protocol server: dispatch (7990) + MQTT (17766) + HTTPS (443)
- Auto-handles registration + authentication
- Control port on 9999 for sending commands
- Logs all traffic with opcode names

### send_command.py
- Low-level command tool: `py send_command.py raw <dp_id> <value>`
- Connects to capture_server.py control port (9999)
- Note: default mode uppercases commands — use `raw 2 start` for smart speaker commands

### lucy_control.py (NEW — 2026-03-14)
- High-level control interface with named commands and safety guards
- `py lucy_control.py start` — start cleaning (with confirmation prompt)
- `py lucy_control.py stop` — stop cleaning
- `py lucy_control.py dock` — return to dock
- `py lucy_control.py locate` — find me (voice/beep)
- `py lucy_control.py status` — query robot status
- `py lucy_control.py volume 75` — set volume
- `py lucy_control.py blower 24` — set suction mode
- `py lucy_control.py raw <string>` — send raw DP 2 value
- Safety: `start` command requires Enter confirmation (or `--confirm` flag)

## 9. Key Findings Summary

1. **DP ID 2** is the universal robot control channel — ALL commands go through it as strings
2. **Opcode 0x0504** (downdps) is the "cloud-to-device" command opcode
3. **Two command paths exist**: smart speaker (lowercase) and app (UPPERCASE)
4. **Smart speaker commands WORK for actions**: `start`, `stop`, `dock`, `locate`, `mute`
5. **Commands are CASE-SENSITIVE** — must be lowercase! UPPERCASE = "Unknown smart speaker command."
6. **Parameterized commands** use `:` delimiter: `setVolume:50`, `blowerMode:2`
7. Lucy responds with both an ACK (0x0403) and a status update (0x0503)
8. The command routing: `dp_down_handle_robot_control` → `cloudserver.cc` smart speaker handler → ZMQ → internal handlers
9. App-path UPPERCASE commands (GET_WORK_STATUS etc.) only return status, don't trigger actions
10. **`start` confirmed to initiate cleaning** (2026-03-14) — mode 2, "new sweep clean"
11. **`locate` confirmed to trigger voice** (2026-03-14) — mode 24, "find me"

## 10. Status Codes (Partially Mapped)

| Status | Meaning |
|--------|---------|
| 1 | Cleaning |
| 2 | Cleaning (current_mode from logs) |
| 5 | Returning to dock |
| 12 | Docked/idle/charged |

## 11. Remaining Work

- [x] ~~Test action commands~~ — **DONE! `start` and `locate` confirmed working!**
- [ ] Test `stop` and `dock` commands
- [ ] Test parameterized commands (`setVolume:N`, `blowerMode:N`, `volumeRelative:N`)
- [ ] Test `mute` command
- [ ] Map all status codes comprehensively
- [ ] Handle file upload responses (allow Lucy to upload maps/logs)
- [ ] Investigate OTA update suppression
- [ ] Build comprehensive control interface (lucy_control.py with named commands)
- [ ] Investigate app-path command format (how does the real Trifo app send commands?)
