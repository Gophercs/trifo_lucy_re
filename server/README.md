# Lucy Local Control Server

Single-file Python server that replaces Trifo's cloud. No pip dependencies.

## Setup

1. **Generate TLS certificates** (self-signed, Lucy doesn't verify):
   ```bash
   openssl req -x509 -newkey rsa:2048 -keyout mqtt_key.pem -out mqtt_cert.pem \
     -days 3650 -nodes -subj "/CN=euiot.trifo.com"
   ```

2. **Edit `config.yaml`** — set `server.ip` to your server's IP or leave as `"auto"`

3. **Redirect DNS** — point `euiot.trifo.com` and `eudispatch.trifo.com` to your server
   (see main [README](../README.md) for methods)

4. **Run:**
   ```bash
   python capture_server.py
   ```

5. **Open Web UI:** `http://<your-server-ip>:8080`

## Files

| File | Description |
|------|-------------|
| `capture_server.py` | Main server — dispatch, MQTT, REST API, web UI, scheduler |
| `config.yaml` | Server configuration (IP, ports, schedule, regions) |
| `lucy_control.py` | CLI tool for sending commands |
| `send_command.py` | Low-level command sender |
| `generate_wifi_qr.py` | Generate WiFi provisioning QR codes |
| `web/index.html` | Web UI (served automatically by the server) |

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 7990 | TLS | QLCloud dispatch (Lucy connects here first) |
| 17766 | TLS/MQTT | MQTT broker (commands and status) |
| 443 | HTTPS | Voice packages, OTA |
| 8080 | HTTP | REST API + Web UI |
| 9999 | TCP | Local control socket (CLI tool) |
