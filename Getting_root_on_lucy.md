# Getting Root on the Trifo Lucy

**Author:** Chloe (3many@Fifi) with AI assistance
**Date:** March 2026
**Device:** Trifo Lucy Robot Vacuum
**Hardware:** RK3399 (ARM64) SoC, STM32F407 MCU, RTL8822BS WiFi

---

## Background

Trifo Robotics went under, leaving owners of their robot vacuums with devices dependent on cloud services that no longer exist. Previous reverse engineering work by [Victor Drijkoningen](https://github.com/VictorDrijkoningen/trifo-robotics-rev-eng) and [others on Reddit](https://www.reddit.com/r/RobotVacuums/comments/1d1120l/trifo_robotics_appears_to_have_gone_under_they/) focused on the Trifo Max model and achieved root access via a buggy UART shell that ran as root. The Lucy model presents a harder challenge: its UART shell runs as an unprivileged user (`uid=2000(shell)`), and the eMMC partitions are hardware-encrypted, making direct partition modification impossible from U-Boot or Rockusb.

This guide documents a working method to achieve persistent root SSH access on the Trifo Lucy.

## What You Need

- **UART adapter** — FT232RL recommended (handles 1.5Mbaud reliably). Any 3.3V UART adapter that supports 1,500,000 baud should work
- **Micro USB cable** — to connect Lucy's micro USB port to your PC for ADB
- **Soldering iron** — to attach wires to the UART test pads on the board
- **Terminal software** — TeraTerm, PuTTY, or similar. UART settings: 1500000 baud, 8N1, no flow control
- **ADB (Android Debug Bridge)** — install via `winget install Google.PlatformTools` on Windows
- **SSH keypair** — generate with `ssh-keygen -t rsa -b 2048 -f trifo_lucy` (use an empty passphrase). The public key MUST start with `ssh-rsa` — if it doesn't, regenerate it

## Hardware Overview

| Component | Detail |
|-----------|--------|
| SoC | RK3399 (dual Cortex-A72 + quad Cortex-A53) |
| MCU | STM32F407 |
| Kernel | Linux 4.4.167 aarch64, SMP PREEMPT |
| WiFi | RTL8822BS |
| eMMC | GPT, 19 partitions, inline encryption on system/boot/kernel |
| SELinux | Permissive |
| SSH | Dropbear 0.52 (custom Android build) |

## Security Model (What We're Up Against)

The Trifo engineers were competent but pragmatic. Here's what's locked down and what isn't:

**Locked down:**
- UART boot shell runs as `shell` (uid=2000), not root
- Boot image kernel command line is baked in — U-Boot `setenv bootargs` is ignored by `boot_android`
- System, boot, and kernel partitions are hardware-encrypted at the eMMC level (reads return encrypted data from both U-Boot `mmc read` and Rockusb)
- Userdata partition uses Android File-Based Encryption
- ADB switches to MTP mode shortly after boot, leaving only a brief window

**Not locked down:**
- U-Boot console is interruptible with Ctrl+C (no password protection)
- SELinux is permissive (logs but doesn't enforce)
- SSH (Dropbear) is present on the device with a known configuration
- The `/data` partition is writable once mounted
- An alternate boot mode (triggered accidentally) extends the ADB window

## The Method

### Step 1: Open Lucy and Connect UART

Disassembly requires removing the top cover, bumper, and both wheels to access all the screws that hold the bottom panel in place — you can't get the bottom off without removing these first.

Once inside, locate the UART test pads. The TX and RX headers are part of a group of headers on the black board, near the largest ribbon cable coming from the green board. You can verify them by testing continuity to labelled test points on the green board. Solder wires to TX, RX, and GND. Connect to your UART adapter (remember: Lucy TX → adapter RX, Lucy RX → adapter TX).

Open your terminal software at **1,500,000 baud, 8N1, no flow control**.

### Step 2: Enter U-Boot Console

Power on Lucy while spamming **Ctrl+C** in the terminal. Timing is important: you can start holding Ctrl+C a beat *before* pressing the power button and it will normally catch the interrupt window. You should land at a U-Boot prompt:

```
=>
```

If you miss the window, turn off immediately (hold power for a second or two, then press Home at the same time) and try again. No need to wait — just power cycle and have another go.

### Step 3: Trigger Alternate Boot Mode

This is the key discovery. From the U-Boot prompt, run:

```
ext4ls mmc 0:e
```

This command was found by accident — it attempts to list files on partition 14 (0xe in hex) which contains a bootable image. Instead of listing files, it triggers an alternate boot mode that crucially **makes ADB accessible**. During a normal boot, Lucy briefly appears as an ADB device before switching to MTP mode, but ADB connections always fail (returning "device offline" or "no devices/emulators found"). The alternate boot mode makes ADB actually usable — commands can be sent and files pushed during the boot window.

**Note:** During testing, a yellow LED was sometimes observed in this boot mode, suggesting it may be entering a setup or recovery state. It's also possible that a similar mode can be entered via a button combination (holding Power then Home) without U-Boot, but this hasn't been confirmed — the U-Boot method is the known reliable path.

The device will boot. You'll see boot messages scrolling on the UART console.

### Step 4: Connect ADB During Boot Window

**Before triggering the boot**, have ADB ready on your PC. You may need to add the Rockchip vendor ID to ADB's device list:

Create the file `%USERPROFILE%\.android\adb_usb.ini` containing:
```
0x2207
```

Then:
```
adb kill-server
adb start-server
```

Connect Lucy's **micro USB port** to your PC with a standard USB cable (not OTG).

After running the `ext4ls` command in Step 3, the device boots. During boot, the USB device will appear briefly as an ADB device (VID `2207`, PID `0011`). You have a window of several seconds where ADB commands will work.

### Step 5: Generate Host Keys and Push Your Public Key

**Prepare this batch file in advance** and run it immediately after triggering the boot:

```bat
@echo off
adb wait-for-device
adb shell mkdir -p /data/.ssh
adb shell /system/bin/dropbearkey -t rsa -f /data/.ssh/dropbear_rsa_host_key
adb push YOUR_PUBLIC_KEY.pub /data/.ssh/authorized_keys
adb shell ls -la /data/.ssh/
echo DONE
pause
```

Replace `YOUR_PUBLIC_KEY.pub` with the path to your SSH public key file.

**Expected output:**
```
Will output 1024 bit rsa secret key to '/data/.ssh/dropbear_rsa_host_key'
Generating key, this may take a while...
Public key portion is:
ssh-rsa AAAA... @localhost
Fingerprint: md5 xx:xx:xx:...
```

**Note:** If the host key already exists from a previous attempt, `dropbearkey` will say "File exists" — that's fine, the key is already there.

The key paths (`/data/.ssh/`) are critical. This is where the Dropbear init script expects to find them, as defined in `/system/etc/init/trifo.rc`:

```
service sshd /system/bin/dropbear -A -N root -C toor -R /data/.ssh/authorized_keys -r /data/.ssh/dropbear_rsa_host_key -F -s -p 22
```

### Step 6: Reboot Normally and SSH In

Power cycle Lucy **without** interrupting U-Boot — let it boot normally. Once booted, Lucy will connect to your WiFi network. The init system will start Dropbear, which will find the host key and your authorized_keys file.

Find Lucy's IP address (check your router's DHCP lease table, or if you previously knew it). Then:

```
ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oMACs=+hmac-sha1 \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    -o StrictHostKeyChecking=no \
    -i /path/to/your/trifo_lucy_key \
    root@<LUCY_IP_ADDRESS>
```

You should see:

```
:/storage/emulated/0 #
```

Confirm with:

```
# id
uid=0(root) gid=0(root) groups=0(root), context=u:r:system_server:s0
```

**You have persistent root access over SSH.** This survives reboots — the keys are on the `/data` partition which is persistent. You can reassemble the device and access it wirelessly whenever it's powered on and connected to WiFi.

## Dropbear Configuration Details

The custom Android Dropbear build (`/system/bin/dropbear`, which is a symlink to `/system/bin/dropbearmulti`) supports these Android-specific flags:

| Flag | Purpose |
|------|---------|
| `-A` | Android mode |
| `-N` | Username (set to `root` in init config) |
| `-C` | Password (set to `toor` in init config — but password auth is disabled by `-s`) |
| `-R` | Path to authorized_keys file |
| `-U` | UID |
| `-G` | GID |

The init script disables password authentication (`-s` flag), so only public key authentication works. This is why you must push your public key to `/data/.ssh/authorized_keys`.

## SSH Connection Notes

The legacy algorithms required by Dropbear 0.52 must be explicitly enabled in modern SSH clients:

- `KexAlgorithms=+diffie-hellman-group1-sha1`
- `HostKeyAlgorithms=+ssh-rsa`
- `MACs=+hmac-sha1`
- `PubkeyAcceptedAlgorithms=+ssh-rsa`

The connection may timeout during long idle periods or large file transfers. To improve stability, you can kill the default Dropbear instance and restart with keepalive:

```
killall dropbear
/system/bin/dropbear -A -N root -C toor -R /data/.ssh/authorized_keys \
    -r /data/.ssh/dropbear_rsa_host_key -F -s -p 22 -K 60
```

The `-K 60` sends a keepalive every 60 seconds. Note: this doesn't survive a reboot — the init script will restart Dropbear with the default settings.

## Network Services

Once rooted, nmap reveals the following services on Lucy's WiFi interface:

| Port | Service | Protocol |
|------|---------|----------|
| 22 | SSH (Dropbear 0.52) | TCP |
| 5556 | ZeroMQ ZMTP 2.0 | TCP |
| 5678 | ZeroMQ ZMTP 2.0 | TCP |
| 7777 | ZeroMQ ZMTP 2.0 | TCP |

The ZeroMQ services are the inter-process communication layer used by the Trifo application stack. Port 5678 accepts DEALER-type connections and responds to protobuf-framed messages (responding with "undefine wificmd" to unrecognised commands). These services are the primary target for reverse engineering the robot's control protocol.

## Filesystem Layout

Key locations on a rooted Lucy:

| Path | Contents |
|------|----------|
| `/data/user/app/` | Trifo application stack — scripts, configs, binaries, maps, calibration data |
| `/data/user/app/run.sh` | Main application launch script |
| `/data/user/app/config` | Application configuration |
| `/data/user/app/data.db` | Application database |
| `/data/user/app/bin/` | Trifo binary executables |
| `/data/user/app/lib/` | Trifo shared libraries |
| `/data/user/app/node_config/` | ZeroMQ node configuration |
| `/data/user/app/lucy_map/` | Stored floor maps |
| `/system/etc/init/trifo.rc` | Init script for all Trifo services |
| `/system/bin/dropbearmulti` | Dropbear SSH binary |
| `/data/.ssh/` | SSH host keys and authorized_keys |

## Trifo Init Script (trifo.rc)

For reference, the complete init script that controls all Trifo services:

```
# Added by trifo
on init
    # handle wifi led on
    write /sys/class/gpio/export 98
    write /sys/class/gpio/gpio98/direction out
    write /sys/class/gpio/gpio98/value 1

on boot
    # Disable foo
    stop foo
    # ssh server
    start sshd

on property:init.svc.bootanim=running
    start bootsound

on property:sys.boot_completed=1
    # Start trifo apps
    start sys_config
    start trifoapp
    start check_reset
    start temp_ctrl
    # Enable tcpip mode for adb
    # setprop persist.adb.tcp.port 5555

service trifoapp /system/bin/sh -c "cd /data/user/app/ && ./run.sh"
    disabled
    oneshot
    seclabel u:r:su:s0

service bootsound /system/bin/tinyplay /system/media/audio/start.wav
    disabled
    oneshot
    seclabel u:r:init:s0

service temp_ctrl /system/bin/sh -c "cd /data/user/app/ && ./temp_ctrl.sh"
    disabled
    seclabel u:r:su:s0

service sys_config /system/bin/sh -c "cd /data/user/app/ && ./sys_config.sh"
    disabled
    seclabel u:r:su:s0

service check_reset /system/bin/sh -c "cd /data/user/app/ && ./check_reset.sh"
    disabled
    seclabel u:r:su:s0

service sshd /system/bin/dropbear -A -N root -C toor -R /data/.ssh/authorized_keys -r /data/.ssh/dropbear_rsa_host_key -F -s -p 22
    disabled
    seclabel u:r:system_server:s0
```

Notable: ADB over TCP on port 5555 is commented out but present. Once rooted, you can enable it with `setprop persist.adb.tcp.port 5555` for wireless ADB access alongside SSH.

## The Journey: How We Got Here

The method described above is clean and repeatable, but finding it was anything but. What follows is a rough chronology of the approaches attempted, to give context for why this specific path works and to help anyone exploring similar devices.

**Phase 1: UART exploration.** The first step was establishing UART access and discovering the U-Boot console. Unlike the Max, the Lucy's boot shell runs as an unprivileged user, not root. The shell prompt appears briefly during boot but gets swallowed by init output, and once boot completes the console is dead — you can't type into it at all. Commands had to be fired into a split-second window, and long commands were truncated at roughly 80 characters. This made the UART shell effectively unusable for anything beyond quick reads.

**Phase 2: U-Boot bootargs modification.** The classic embedded device trick — `setenv bootargs init=/bin/sh` to boot into a root shell. Confirmed dead: `boot_android` loads the kernel command line from the Android boot image header and completely ignores U-Boot environment variables.

**Phase 3: Rockusb and partition analysis.** Rockusb mode works (enter with `rockusb 0 mmc 0` from U-Boot), but reads of the important partitions (system, boot, kernel) returned encrypted data (0xCC fill or high-entropy noise). The eMMC inline encryption operates below U-Boot, so even `mmc read` from the U-Boot console returns encrypted data. The `trust` partition contained a readable ARM Trusted Firmware header, and `cache` was empty (all zeros), but nothing writable and useful was accessible.

**Phase 4: USB storage from U-Boot.** The U-Boot command list includes `usb start`, `fatload`, and `fatwrite`, which initially looked very promising. USB controllers initialised successfully (6 buses detected), but the USB-A port on the board never detected any storage device. Likely a VBUS power GPIO issue — the port needs a pin set high to supply power, and U-Boot doesn't enable it.

**Phase 5: Dropbear vulnerability research.** Dropbear 0.52 is from 2008 and has known CVEs, most notably CVE-2012-0920 (use-after-free). However, all exploitable vulnerabilities require prior authentication — and authentication is precisely what we couldn't do because Dropbear was crashing on startup due to missing host keys. A locked door behind another locked door.

**Phase 6: ZeroMQ service probing.** An nmap scan revealed three ZeroMQ ports open on Lucy's WiFi interface (5556, 5678, 7777). Port 5678 accepted DEALER-type connections and returned protobuf-framed error messages ("undefine wificmd") — a real command interface. However, without knowing the protobuf message definitions (which would require decompiling the Trifo Home APK), this was blind guessing. Port 7777 responded to REQ/REP but just echoed back the first byte. This remains an open avenue for future RE work.

**Phase 7: ADB discovery.** Monitoring Windows Device Manager during boot revealed that Lucy briefly exposes an ADB interface (Rockchip VID `2207`, PID `0011`) before switching to MTP mode (PID `0001`). During normal boot, this window is too short — `adb shell` returned "device offline" before the connection could establish.

**Phase 8: The ext4ls breakthrough.** The `ext4ls mmc 0:e` command, tried during U-Boot filesystem exploration, accidentally triggered a boot from partition 14. This alternate boot mode made ADB actually accessible — during normal boot, Lucy appears briefly as an ADB device in Device Manager but connections always fail with "device offline". In the alternate boot mode, ADB commands actually execute. `adb shell id` returned `uid=2000(shell)` — the same unprivileged user as UART, but now with the ability to push files and run scripts without paste buffer limits.

**Phase 9: Dropbear configuration.** With ADB access, `cat /system/etc/init/trifo.rc` revealed the actual Dropbear launch configuration — including that it expected host keys in `/data/.ssh/` (not the path in the Dropbear binary's defaults), and the username/password (`root`/`toor`). Generating host keys and pushing an authorized_keys file to `/data/.ssh/` via ADB, then rebooting normally, gave persistent root SSH access.

The entire process involved dead ends, accidental discoveries, and a fair amount of creative profanity. The key insight was that the combination of an accidental boot mode and a brief ADB window provided just enough access to write files to the right location — something that no single approach could achieve on its own.

## Paths That Don't Work (Saving You Time)

These approaches were explored and confirmed non-viable on the Lucy:

- **U-Boot `setenv bootargs init=/bin/sh`** — `boot_android` ignores U-Boot environment bootargs entirely, pulls command line from the Android boot image header
- **Rockusb partition dumping/writing** — eMMC inline encryption means all reads of system/boot/kernel partitions return encrypted data. Writing plaintext back won't work
- **U-Boot `mmc read`/`mmc write` to modify partitions** — same encryption barrier
- **U-Boot filesystem commands (`ext4ls`, `fatload`)** — this U-Boot build has no filesystem driver support compiled in (all partition reads fail with "unrecognized filesystem type"). The `ext4ls mmc 0:e` command works not because it reads ext4, but because it triggers a boot from that partition
- **USB storage from U-Boot** — USB controller initialises fine (`usb start` works, 6 buses detected) but the USB-A port doesn't detect storage devices, likely due to a VBUS power GPIO not being enabled by U-Boot
- **Dropbear vulnerabilities** — CVE-2012-0920 (use-after-free) affects 0.52 but requires prior authentication, which is the thing we can't do. All other CVEs either don't apply to this version or require conditions we can't meet
- **Direct ADB during normal boot** — ADB is visible briefly during boot (device shows as `I4YE1UGF5N` in Device Manager) but connections always fail with "device offline" before switching to MTP mode. The alternate boot mode from `ext4ls` is required to make ADB actually functional
- **Writing to Dropbear's default key paths** — `/data/data/br.com.bott.droidsshd/files/etc/` is not writable by the shell user, and the actual Dropbear config doesn't use this path anyway (it uses `/data/.ssh/`)

## What Comes Next

With root access established, the community can now work on:

- **Reverse engineering the ZeroMQ control protocol** — decompiling the Trifo Home APK (available on APKPure, version 2.6.3) to extract protobuf message definitions, and mapping them to the three ZeroMQ services
- **Building a local control interface** — replacing the dead cloud dependency with direct robot control via ZeroMQ, potentially as a Home Assistant integration
- **Understanding the STM32 MCU communication** — the RK3399 talks to the STM32F407 which handles motor control, sensors, and low-level hardware. This protocol needs mapping
- **OTA/firmware management** — understanding how firmware updates work to enable community-maintained updates

## Credits

- **Victor Drijkoningen** — original Trifo Max reverse engineering work and [GitHub repository](https://github.com/VictorDrijkoningen/trifo-robotics-rev-eng)
- **Reddit community** — particularly the user who built full cloud emulation for the Max, establishing that ZeroMQ and MQTT are the core communication protocols
- **The Trifo Lucy Discord community** — for testing, encouragement, and immediately jumping on the dump when access was achieved
