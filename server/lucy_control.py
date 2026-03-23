"""
Lucy Local Control — Command-line interface for Trifo Lucy robot vacuum.
Sends commands via capture_server.py's control port (9999).

Usage:
    py lucy_control.py status          # Query current status
    py lucy_control.py start           # Start cleaning
    py lucy_control.py stop            # Stop cleaning
    py lucy_control.py dock            # Return to dock
    py lucy_control.py locate          # Find me (voice/beep)
    py lucy_control.py mute            # Toggle mute
    py lucy_control.py volume 75       # Set volume (0-100)
    py lucy_control.py volume +10      # Volume up
    py lucy_control.py volume -10      # Volume down
    py lucy_control.py blower 24       # Set blower/suction mode
    py lucy_control.py raw <string>    # Send raw DP 2 value

IMPORTANT: Smart speaker commands must be lowercase/camelCase.
           UPPERCASE commands only return status, they don't trigger actions.
"""
import sys
import os
import socket
import json
import time

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 9999

# Try to read control port from config.yaml if available
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
try:
    import yaml
    with open(_config_path) as _f:
        _cfg = yaml.safe_load(_f) or {}
    CONTROL_PORT = _cfg.get("server", {}).get("control_port", CONTROL_PORT)
except Exception:
    pass

# Safe commands that can be sent without confirmation
SAFE_COMMANDS = {
    "status": ("GET_WORK_STATUS", "Query robot status"),
    "battery": ("BATTERY", "Query battery level"),
    "config": ("GET_SYSTEM_CONFIG", "Query system config"),
    "pose": ("GET_LUCY_POSE", "Query robot position"),
    "map": ("GET_MAP", "Query map data"),
    "locate": ("locate", "Find me — triggers voice/beep"),
    "mute": ("mute", "Toggle mute"),
}

# Action commands that trigger physical movement
ACTION_COMMANDS = {
    "start": ("start", "Start cleaning run"),
    "pause": ("stop", "Pause cleaning (stop in place)"),
    "stop": ("stop", "Stop cleaning"),
    "dock": ("dock", "Return to charging dock"),
}


def send_raw(dp_id, value):
    """Send a raw command via control socket."""
    msg = f"{dp_id}:{value}"
    try:
        sock = socket.create_connection((CONTROL_HOST, CONTROL_PORT), timeout=10)
        sock.sendall(msg.encode("utf-8"))
        response = sock.recv(4096).decode("utf-8")
        sock.close()
        return response
    except ConnectionRefusedError:
        print("ERROR: Cannot connect to capture_server.py on port 9999.")
        print("       Make sure capture_server.py is running.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def cmd_status():
    """Query and display robot status."""
    resp = send_raw(2, "GET_WORK_STATUS")
    print(f"Server: {resp}")
    print("(Status will be printed by capture_server.py in its console)")


def cmd_action(name, value, description):
    """Send an action command."""
    print(f"Sending: {description}")
    print(f"  Command: {value}")
    resp = send_raw(2, value)
    print(f"  Server: {resp}")


def cmd_volume(args):
    """Handle volume commands."""
    if not args:
        print("Usage: py lucy_control.py volume <level|+delta|-delta>")
        print("  volume 50     — set to 50")
        print("  volume +10    — increase by 10")
        print("  volume -10    — decrease by 10")
        return

    val = args[0]
    if val.startswith("+") or val.startswith("-"):
        resp = send_raw(2, f"volumeRelative:{val}")
        print(f"Volume adjust {val}: {resp}")
    else:
        resp = send_raw(2, f"setVolume:{val}")
        print(f"Volume set to {val}: {resp}")


BLOWER_MODES = {
    "off": "MANBLOWER_CLOSE",
    "low": "MANBLOWER_LOW",
    "normal": "MANBLOWER_MIDDLE",
    "high": "MANBLOWER_HIGHT",
}

def cmd_blower(args):
    """Handle blower/suction commands."""
    if not args or args[0].lower() not in BLOWER_MODES:
        print("Usage: py lucy_control.py blower <off|low|normal|high>")
        for name, cmd in BLOWER_MODES.items():
            print(f"  {name:8s} -> {cmd}")
        return

    name = args[0].lower()
    cmd = BLOWER_MODES[name]
    resp = send_raw(2, cmd)
    print(f"Blower set to {name} ({cmd}): {resp}")


def print_help():
    """Print usage information."""
    print("Lucy Local Control")
    print("=" * 50)
    print()
    print("Safe commands (read-only):")
    for name, (_, desc) in sorted(SAFE_COMMANDS.items()):
        print(f"  {name:12s} — {desc}")
    print()
    print("Action commands (triggers physical movement):")
    for name, (_, desc) in sorted(ACTION_COMMANDS.items()):
        print(f"  {name:12s} — {desc}")
    print()
    print("Parameterized commands:")
    print(f"  {'volume <N>':12s} — Set volume (0-100)")
    print(f"  {'volume +/-N':12s} — Adjust volume relatively")
    print(f"  {'blower <N>':12s} — Set blower/suction mode")
    print()
    print("Raw access:")
    print(f"  {'raw <string>':12s} — Send raw string as DP 2 value")
    print()
    print("Examples:")
    print("  py lucy_control.py status")
    print("  py lucy_control.py start")
    print("  py lucy_control.py dock")
    print("  py lucy_control.py volume 75")
    print("  py lucy_control.py raw 'some_command:param'")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_help()
        sys.exit(0)

    cmd = sys.argv[1].lower()
    rest = sys.argv[2:]

    if cmd in ("help", "-h", "--help"):
        print_help()

    elif cmd in SAFE_COMMANDS:
        value, desc = SAFE_COMMANDS[cmd]
        print(f"{desc}...")
        resp = send_raw(2, value)
        print(f"  Server: {resp}")

    elif cmd in ACTION_COMMANDS:
        value, desc = ACTION_COMMANDS[cmd]
        if "--confirm" not in rest and cmd == "start":
            print(f"ACTION: {desc}")
            print(f"  This will make Lucy start moving!")
            print(f"  Run with --confirm to send, or press Ctrl+C to cancel.")
            print()
            try:
                input("  Press Enter to send anyway, or Ctrl+C to cancel: ")
            except KeyboardInterrupt:
                print("\n  Cancelled.")
                sys.exit(0)
        cmd_action(cmd, value, desc)

    elif cmd == "volume":
        cmd_volume(rest)

    elif cmd == "blower":
        cmd_blower(rest)

    elif cmd == "raw":
        if not rest:
            print("Usage: py lucy_control.py raw <value>")
            sys.exit(1)
        value = " ".join(rest)
        print(f"Sending raw DP 2: {value}")
        resp = send_raw(2, value)
        print(f"  Server: {resp}")

    else:
        print(f"Unknown command: {cmd}")
        print("Run 'py lucy_control.py help' for usage.")
        sys.exit(1)
