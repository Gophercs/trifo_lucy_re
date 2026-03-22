# Phase 10+: Local Control Platform Plan

**Date**: 2026-03-14
**Status**: PLANNED — Phase 0 in progress

## Architecture

```
Lucy (robot)                    Server (PC/RPi)                  Frontend
  cloud_node ──────────────►  lucy_server.py                    Browser / HA / App
    dispatch (7990 TLS)          ├─ protocol emulation              │
    MQTT (17766 TLS)             ├─ REST API (port 8080) ◄─────────┘
    HTTPS (443)                  ├─ file upload handler
                                 ├─ web UI (static files)
  DNS redirect:                  └─ SSE event stream
  euiot.trifo.com → server IP
```

Server is the middleman — must exist because Lucy speaks QLCloud binary protocol.
All frontends (web, HA, Android) talk to a clean REST API.

## Phase 0: Stabilize & Persist (PREREQUISITE)

### 0A. DNS Persistence
Three options in order of preference:
1. **Router-level DNS** (best, no root needed): Configure router to resolve
   `euiot.trifo.com` and `eudispatch.trifo.com` to server IP.
2. **Remount /system**: `mount -o remount,rw /system` then edit `/system/etc/hosts` directly.
3. **Boot script**: Add 3 lines to `/data/user/app/run.sh` to re-apply bind mount.

### 0B. Server as Service
- systemd unit (Linux/RPi) or Task Scheduler (Windows)
- Graceful shutdown (SIGTERM handling)
- Auto-reconnection detection

### 0C. Config Extraction
Move hardcoded values to `config.yaml`:
- `LOCAL_IP` (auto-detect or configurable)
- `PRODUCT_ID_EU`, `PRODUCT_KEY_EU`
- `subid` (serial number)
- Subscribe/publish topics
- Auto-learn device-specific values from handshake, persist to state file

## Phase 1: REST API (~3 days)

Refactor `capture_server.py` to asyncio + aiohttp on port 8080:
- `GET  /api/status` — cached robot status
- `POST /api/command` — send command `{"command":"start"}` or `{"command":"blowerMode","value":"24"}`
- `GET  /api/commands` — available commands with descriptions
- `GET  /api/connection` — connection state, uptime, last heartbeat
- `GET  /api/events` — SSE stream for real-time updates
- `GET  /api/config` / `PUT /api/config`

State management: parse every updps, cache status, emit events on change.
Keep control socket (9999) for backward compat.

## Phase 2: Web UI (~3 days)

Plain HTML + CSS + vanilla JS. No build tooling. Served as static files.
- Start/stop/dock/locate buttons
- Battery, status display
- Volume slider, suction selector
- Real-time updates via SSE
- Map view placeholder (Phase 3)

## Phase 3: Maps & File Uploads (~5 days)

- Handle file_upload requests (opcode 0x0303) — RE the response format
- Lucy uses Google Cartographer, maps are `.pbstream` protobuf
- Test `GET_MAP` / `GET_LUCY_MAP` commands for simpler data
- Render as interactive canvas (pan/zoom)
- Later: zone cleaning, no-go zones, robot position overlay
- Key commands: `LUCY_MAP_MGR`, `room_segmentation_info`

## Phase 4: Home Assistant Integration (~5 days)

HACS-installable `custom_components/lucy_vacuum/`:
- Talks to lucy_server REST API (not directly to Lucy)
- `StateVacuumEntity`: start, stop, return_to_base, locate, set_fan_speed
- Sensors: battery, status, cleaning area/time
- Camera entity: map image
- Number entity: volume
- Config flow: enter server IP, auto-discover robot

## Phase 5: Replication Package (parallel)

### DNS-only approach (NO ROOT NEEDED)
1. User configures router DNS: `euiot.trifo.com` → server IP
2. Run lucy_server
3. Lucy auto-connects

### Server auto-discovery
- Accept any Lucy (US/CN/EU)
- Auto-detect region from dispatch magic (0xf1eb=EU, 0xf1ec=US)
- Learn product_id, serial, topics from handshake
- Persist to state file

### Distribution
- Python package + Docker image
- Router DNS guides for common routers
- TLS cert generation script

### OTA-based root (speculative)
- `run.sh` checks `/data/user/ota/app/` at boot, applies updates if MD5 matches
- Could deliver DNS redirect via MQTT OTA (opcode 0x0a01)
- Needs more RE of OTA package format

## Phase 6: Camera (exploratory, lowest priority)

- Test `GET_MONITOR_STATE` / `GET_MONITOR_VIDEO` first
- RTSP built in but disabled (`enable_rtsp: false`)
- With root: edit config or run ZMQ-to-RTSP forwarder
- Without root: redirect RTMP push to local nginx-rtmp

## Sequencing

```
Phase 0 → Phase 1 (API) → Phase 2 (Web UI) → Phase 5 (replication)
                        → Phase 4 (HA)
                        → Phase 3 (maps)
                        → Phase 6 (camera)
```

## Key Risks

| Risk | Mitigation |
|------|------------|
| File upload protocol unknown | Test GET_MAP first; RE file upload response |
| TLS cert validation | Already works with self-signed; test on fresh device |
| Asyncio refactor introduces bugs | Keep capture_server_legacy.py as fallback |
| .pbstream parsing complex | Try GET_MAP for simpler format; Cartographer tools exist |
| Camera needs root | Lower priority; focus on control first |
