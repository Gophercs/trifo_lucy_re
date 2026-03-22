# Project: Trifo Lucy Robot Vacuum — Local Control & Map Decoding

## Overview

Reverse engineering the Trifo Lucy robot vacuum to achieve full local control
without cloud dependency. The robot runs Linux (kernel 4.4.167) on an RK3399
SoC with an STM32F407 MCU. It communicates with Trifo's "QLCloud" via MQTT
over TLS. We've built a local server that impersonates the cloud, giving us
full command and status capability.

## What's SOLVED

- **Cloud protocol** — fully reverse-engineered (dispatch, MQTT, TLS handshake)
- **Command protocol** — smart speaker commands confirmed working:
  `start`, `stop`, `dock`, `locate`, `mute`, `blowerMode:N` (0-100%), `setVolume:N`
- **Local server** — Python server handles dispatch + MQTT + REST API + Web UI
- **Scheduling** — cron-style scheduling via config or web UI
- **Status tracking** — real-time status, battery, position via SSE

## What's UNSOLVED

### The Big One: OGM Map Grid Decoding

The robot stores occupancy grid maps as `.ogm` files in a custom protobuf
format. We can parse the protobuf envelope and extract metadata, but the
actual grid data (field 8) uses an unknown encoding — likely a custom Huffman
scheme compiled into the firmware.

**Full research document:** See `ogm_format_research.md` in the project root.
This is a 550-line self-contained writeup of everything known, tried, and
ruled out. START HERE.

### Other Unknowns
- Camera access (RTSP disabled, monitor commands untested)
- OTA update package format
- App-path action command format (UPPERCASE commands beyond status queries)

## Key Documents

All in `docs/`:

| File | Description |
|------|-------------|
| `docs/ogm_format_research.md` | Complete OGM format research (the main reference) |
| `docs/09_mqtt_commands.md` | Full command protocol documentation |
| `docs/10_local_control_plan.md` | Platform roadmap |
| `docs/project_overview.md` | Project overview with all phase summaries |

## Robot Access

Contributors do NOT need direct robot access for most tasks. The research
documents include hex dumps, sample files, and firmware analysis. If a task
requires live robot interaction, it will be marked accordingly.

## Tech Stack

- **Robot:** aarch64 Linux, RK3399, STM32F407
- **Firmware:** C++ (`libtrifo_core_cloud.so`, `navigation_node`)
- **Server:** Python 3, stdlib only (no pip dependencies)
- **Analysis tools:** protobuf, IDA/Ghidra (for firmware RE)
