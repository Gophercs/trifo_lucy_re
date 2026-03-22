# WiFi Provisioning Without the Trifo App

Lucy can be connected to WiFi without the (now-defunct) Trifo Home app.
The robot scans a QR code with her front camera to receive WiFi credentials.

## QR Code Format

The QR code contains a semicolon-delimited string:

```
WIFI:T:<auth_type>;P:"<password>";S:<ssid>;U:<uid>;C:<region>;
```

| Field | Description | Example |
|-------|-------------|---------|
| `T:` | WiFi auth type | `WPA` or empty (empty seems to work for WPA2) |
| `P:` | WiFi password (in quotes) | `"MyWifiPassword123"` |
| `S:` | WiFi SSID (network name) | `MyHomeNetwork` |
| `U:` | User ID — Trifo account UID | `12345` (any number works for local control) |
| `C:` | Cloud region code | `1`, `2`, or `3` (see below) |

### Region Codes

| Code | Region | Cloud domain |
|------|--------|-------------|
| `1` | US | `usiot.trifo.com` |
| `2` | Asia | `cniot.trifo.com` (unconfirmed) |
| `3` | EU | `euiot.trifo.com` |

**For local control, use the region that matches the DNS domain you're
redirecting.** The server defaults to EU (`euiot.trifo.com`). If you're not
sure, use `C:3`.

### User ID (U: field)

The UID was originally a Trifo account identifier. For local control it
doesn't matter — the server accepts any device registration regardless of
UID. Use any number (e.g. `12345`).

## Generating a QR Code

Use the included `generate_wifi_qr.py` script:

```bash
python generate_wifi_qr.py --ssid "MyNetwork" --password "MyPassword" --region 3
```

This creates `lucy_wifi_qr.png` — display it on your phone screen for Lucy
to scan.

Or generate one manually with any QR code generator using this string:
```
WIFI:T:;P:"YourPassword";S:YourSSID;U:12345;C:3;
```

## Provisioning Steps

### 1. Enter Network Configuration Mode

Press and hold the **recharge button** for 5 seconds. It's the button on the
**right side** when looking at Lucy's main camera (front-facing). Lucy will
announce that she's entering network configuration mode.

### 2. Show the QR Code

Display the QR code on your phone screen. Tips for getting Lucy to read it:

- **Brightness matters** — turn your phone screen brightness to maximum
- **Distance** — start about 1 metre (3 feet) away and slowly move closer
- **Alignment** — keep the QR code centred in the camera's field of view
- **Lighting** — a slightly dim environment can help reduce screen glare
- **Elevation** — place Lucy on a table so you can get level with the camera
- **Technique** — hold the QR code steady, start far and approach slowly.
  Usually works within a couple of tries

Lucy will announce when she's successfully read the QR code and is connecting
to WiFi.

### 3. Verify Connection

Once Lucy connects to WiFi, she'll attempt to reach the Trifo cloud servers.
If you've set up DNS redirection to your local server, she'll connect to
`capture_server.py` instead. You should see registration and authentication
messages in the server console within about 30 seconds.

## Firmware Source

The QR parsing code lives in `libtrifo_core_wificfg.so` on the robot. The
`ParseWifiInfo` function uses these regex patterns (in order of specificity):

```
^(?:WIFI:T:)(.*)(?:;P:")(.*)(?:";S:)(.*)
^(?:WIFI:T:)(.*)(?:;P:")(.*)(?:";S:)(.*)(?:;U:)(.*?);(.*)
^(?:WIFI:T:)(.*)(?:;P:")(.*)(?:";S:)(.*)(?:;U:)(.*?)(?:;C:)(.*?);(.*)
```

All three patterns are tried — the minimal format (T/P/S only) should work,
but including U and C ensures Lucy connects to the right cloud region.
