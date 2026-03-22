# Trifo Lucy — Local Control & Reverse Engineering

Trifo Robotics went under, leaving Lucy robot vacuum owners with a
cloud-dependent device and no cloud. This project gives Lucy a fully
local brain — no internet, no Trifo servers, no app required.

![Lucy](images/Lucy1.jpg)

## What Works

| Feature | Status |
|---------|--------|
| Root SSH access | Working — see [rooting guide](Getting_root_on_lucy.md) |
| Local control server | Working — replaces Trifo cloud entirely |
| Start / Stop / Pause / Dock | Working |
| Find my robot (voice/beep) | Working |
| Suction power (0-100%) | Working |
| Volume control | Working |
| Real-time status (battery, state) | Working |
| Web UI | Working — dark theme, mobile-friendly |
| Scheduling | Working — via web UI or config file |
| Map decoding | **Unsolved** — [help wanted](collab/) |

## Quick Start

### Option A: Rooted Lucy (Full Access)

1. [Root your Lucy](Getting_root_on_lucy.md) — requires UART + soldering
2. Redirect DNS on Lucy to point at your server:
   ```bash
   echo "<YOUR_PC_IP> euiot.trifo.com" >> /etc/hosts
   echo "<YOUR_PC_IP> eudispatch.trifo.com" >> /etc/hosts
   ```
3. Run the server: `python capture_server.py`
4. Open `http://<YOUR_PC_IP>:8080` for the web UI
5. Lucy auto-connects and you have full control

### Option B: No Root Required (DNS Redirect Only)

**You don't need root access to use the local control server.** The server
impersonates Trifo's cloud — Lucy doesn't know the difference. All you need
is to redirect Lucy's DNS so it connects to your server instead of the dead
Trifo servers.

**Requirements:**
- Lucy previously set up on your WiFi (so it knows how to connect)
- Ability to change DNS on your router, or run a local DNS server

**DNS entries needed** (point to your server's IP):
```
euiot.trifo.com       → <YOUR_SERVER_IP>
eudispatch.trifo.com  → <YOUR_SERVER_IP>
```

**How to redirect DNS — pick one:**

1. **Router DNS override** (easiest) — most routers let you add custom DNS
   entries or static host overrides. Look for "DNS", "Host Override", or
   "Local DNS" in your router's admin panel. This affects only your network.

2. **Pi-hole / AdGuard Home** — if you run a network-wide DNS blocker, add
   the two entries as custom DNS rewrites.

3. **Local DNS server** — run dnsmasq or similar with the two overrides.

Once DNS is redirected, just run `python capture_server.py` and power-cycle
Lucy. She'll connect to your server instead of Trifo's.

**What if Lucy isn't on WiFi yet?**
If Lucy was never set up, or has been factory-reset, she needs to join your
WiFi first. The original Trifo app is dead, but Lucy accepts WiFi credentials
via QR code scanned by her camera. See [WiFi Setup Without App](#wifi-setup-without-app)
below.

## Server

The server (`mqtt_capture/capture_server.py`) is a single Python file with
no pip dependencies. It handles:

- **Dispatch protocol** (port 7990) — TLS handshake, device registration
- **MQTT broker** (port 17766) — command/status protocol
- **HTTPS** (port 443) — voice packages, OTA (passthrough)
- **REST API** (port 8080) — JSON API + web UI
- **Control socket** (port 9999) — CLI tool interface

### Web UI

Dark-themed control panel at `http://<server>:8080`:
- Status display (cleaning state, battery, position)
- Control buttons (Start, Pause, Dock, Find)
- Power percentage buttons (0-100% in steps of 10)
- Volume slider
- Mute toggle
- Custom command input for experimentation
- Command reference (collapsible)
- Schedule management (add/remove/toggle entries)

### CLI

```bash
python lucy_control.py start      # start cleaning
python lucy_control.py stop       # stop / pause
python lucy_control.py dock       # return to dock
python lucy_control.py locate     # find me (voice/beep)
python lucy_control.py status     # query status
python lucy_control.py volume 75  # set volume
python lucy_control.py raw 'blowerMode:50'  # raw command
```

### Configuration

Edit `config.yaml`:
- `server.ip` — set to `"auto"` or your server's IP
- `schedule` — add timed cleaning runs
- `regions` — EU/US product keys (pre-configured)

## WiFi Setup Without App

If Lucy needs to join WiFi for the first time (no Trifo app required):

1. Generate a QR code: `python generate_wifi_qr.py --ssid "YourNetwork" --password "YourPassword"`
2. Hold Lucy's **recharge button** (right button, looking at camera) for **5 seconds**
3. Wait for "entering network configuration" announcement
4. Display QR on your phone at max brightness, start ~1m from camera, slowly approach
5. Lucy announces success and connects to WiFi

See [wifi_provisioning.md](wifi_provisioning.md) for full details, QR format
documentation, and tips for getting the camera to read the code.

## Hardware

| Component | Detail |
|-----------|--------|
| SoC | RK3399 (dual Cortex-A72 + quad Cortex-A53) |
| MCU | STM32F407 (motor/sensor control) |
| OS | Linux 4.4.167, aarch64 |
| WiFi | RTL8822BS |
| Cameras | RGB + ToF depth |
| SLAM | Google Cartographer-based |

## Research & Reverse Engineering

Full RE documentation in the [research docs](collab/docs/):

| Phase | Topic | Status |
|-------|-------|--------|
| 1 | Process architecture | Complete |
| 2 | MQTT message protocol | Complete |
| 3 | ZeroMQ internal services | Complete |
| 4 | MCU serial protocol | Complete |
| 5 | Cloud (QLCloud) protocol | Complete |
| 6 | Proof of concept | Complete |
| 8 | Dispatch protocol | Complete |
| 9 | MQTT commands | Complete — full local control |
| 10 | Local control platform | In progress — server + web UI |
| — | Map/OGM grid decoding | **Unsolved** — [help wanted](collab/) |

## Contributing

The `collab/` directory contains a self-contained research framework with
open tasks, sample data, and documentation. See [collab/README.md](collab/README.md).

The main unsolved problem is decoding the OGM (occupancy grid map) file
format. We can parse the protobuf envelope but the grid data uses an unknown
encoding — likely a custom Huffman in the firmware. Sample files, ground truth
reference images, and working parser scripts are all included.

## Images

Hardware photos in `images/`:

| File | Description |
|------|-------------|
| [Lucy1.jpg](images/Lucy1.jpg) | Board overview |
| [Lucy2.jpg](images/Lucy2.jpg) | Board detail |
| [Lucy3.jpg](images/Lucy3.jpg) | Board detail |
| [Lucy4.jpg](images/Lucy4.jpg) | Board detail |
| [Lucy5.jpg](images/Lucy5.jpg) | Board detail |

## Credits

- **Chloe** ([@Gophercs](https://github.com/Gophercs)) — rooting, RE, local control server
- **Claude** (Anthropic) — AI pair programmer
- **Victor Drijkoningen** — [prior Trifo Max RE work](https://github.com/VictorDrijkoningen/trifo-robotics-rev-eng)
- **Reddit r/RobotVacuums community** — initial research and motivation

## License

This project is provided as-is for educational and personal use. Use at your
own risk. Not affiliated with Trifo Robotics.
