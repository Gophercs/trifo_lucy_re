"""
Lucy Cloud Traffic Capture Server

Protocol-detecting server on port 17766 that handles:
- TLS + HTTP (QLCloud dispatch registration)
- TLS + MQTT (QLCloud broker connection)
- HTTPS on port 443 (voice package downloads)

Logs ALL traffic for protocol analysis.
"""

import json
import ssl
import os
import sys
import socket
import struct
import hashlib
import time
import random
import threading
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

def _detect_local_ip():
    """Auto-detect local IP by connecting to a known external address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def load_config():
    """Load configuration from config.yaml, falling back to defaults."""
    config_path = os.path.join(BASE_DIR, "config.yaml")
    cfg = {}
    try:
        import yaml
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    except ImportError:
        # No pyyaml — try simple manual parse for key values
        try:
            with open(config_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or not line or ":" not in line:
                        continue
        except FileNotFoundError:
            pass
    except FileNotFoundError:
        pass

    server = cfg.get("server", {})
    qlcloud = cfg.get("qlcloud", {})
    regions = cfg.get("regions", {})
    logging_cfg = cfg.get("logging", {})
    schedule = cfg.get("schedule") or []

    ip = server.get("ip", "auto")
    if ip == "auto" or not ip:
        ip = _detect_local_ip()

    return {
        "ip": ip,
        "dispatch_port": server.get("dispatch_port", 7990),
        "mqtt_port": server.get("mqtt_port", 17766),
        "https_port": server.get("https_port", 443),
        "control_port": server.get("control_port", 9999),
        "api_port": server.get("api_port", 8080),
        "cert_file": os.path.join(BASE_DIR, server.get("cert_file", "mqtt_cert.pem")),
        "key_file": os.path.join(BASE_DIR, server.get("key_file", "mqtt_key.pem")),
        "qlcloud_seed": qlcloud.get("seed", 0x7ecf),
        "magic_eu": qlcloud.get("magic_eu", 0xf1eb),
        "magic_us": qlcloud.get("magic_us", 0xf1ec),
        "regions": {
            "eu": {
                "product_id": regions.get("eu", {}).get("product_id", 1009506721),
                "product_key": regions.get("eu", {}).get("product_key", "376c3878d6666a48c628226b6453d923"),
            },
            "us": {
                "product_id": regions.get("us", {}).get("product_id"),
                "product_key": regions.get("us", {}).get("product_key", "a734264a61794bfc346463762762f429"),
            },
        },
        "log_file": os.path.join(BASE_DIR, logging_cfg.get("file", "capture_log.txt")),
        "verbose": logging_cfg.get("verbose", True),
        "schedule": schedule,
    }


def load_state():
    """Load device state from state.yaml if it exists."""
    state_path = os.path.join(BASE_DIR, "state.yaml")
    try:
        import yaml
        with open(state_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def save_state(state):
    """Save device state to state.yaml."""
    state_path = os.path.join(BASE_DIR, "state.yaml")
    try:
        import yaml
        with open(state_path, "w") as f:
            yaml.dump(state, f, default_flow_style=False)
    except ImportError:
        # Fallback: write as JSON
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)


# Load config at startup
CONFIG = load_config()
DEVICE_STATE = load_state()

LOG_FILE = CONFIG["log_file"]
CERT_FILE = CONFIG["cert_file"]
KEY_FILE = CONFIG["key_file"]

LOCAL_IP = CONFIG["ip"]
DISPATCH_PORT = CONFIG["dispatch_port"]
MQTT_PORT = CONFIG["mqtt_port"]

# Region credentials (default to EU, auto-detected from dispatch magic at runtime)
PRODUCT_ID_EU = CONFIG["regions"]["eu"]["product_id"]
PRODUCT_KEY_EU = CONFIG["regions"]["eu"]["product_key"]

# Active MQTT connection (set when Lucy connects and authenticates)
active_mqtt_conn = None
active_subscribe_topic = None
active_seq_counter = 100  # Start above Lucy's seq numbers
active_conn_lock = threading.Lock()

# Robot status cache (updated from updps/data_b06 messages)
robot_status = {
    "connected": False,
    "status": None,
    "battery": None,
    "position": None,
    "mute": None,
    "volume": None,
    "blower": None,
    "last_update": None,
}
robot_status_lock = threading.Lock()


def update_robot_status(json_payload):
    """Parse a status update from Lucy and cache it."""
    if not json_payload or "data" not in json_payload:
        return
    data_list = json_payload["data"]
    if not isinstance(data_list, list):
        return
    for item in data_list:
        if item.get("i") == 2 and "v" in item:
            try:
                status_str = item["v"]
                if isinstance(status_str, str):
                    status = json.loads(status_str)
                else:
                    status = status_str
                with robot_status_lock:
                    for key in ("status", "battery", "position", "mute", "volume", "blower"):
                        if key in status:
                            robot_status[key] = status[key]
                    robot_status["last_update"] = datetime.now().isoformat()
                    robot_status["connected"] = True
            except (json.JSONDecodeError, TypeError):
                pass

# QLCloud opcode names
OPCODE_NAMES = {
    0x0503: "updps (upload data points)",
    0x0504: "downdps (download/commands)",
    0x0401: "tx",
    0x0402: "rx",
    0x0301: "hrt (heartbeat)",
    0x0101: "reg (registration)",
    0x0201: "auth (authentication)",
    0x0a00: "reg_resp_fmt",
    0x0a01: "OTA upgrade",
    0x0a02: "OTA_02",
    0x0a03: "OTA_03",
    0x0a04: "OTA_04",
    0x0a05: "OTA_05",
    0x0a09: "OTA_09",
    0x0b01: "data_b01",
    0x0b06: "data_b06 (updps alt)",
    0x0102: "msg_0102",
    0x0103: "msg_0103",
    0x0303: "file_upload",
    0x0403: "msg_0403",
}

# QLCloud dispatch protocol constants (from config)
QLCLOUD_SEED = CONFIG["qlcloud_seed"]
QLCLOUD_MAGIC_EU = CONFIG["magic_eu"]


def log_msg(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except:
        pass


# ---------------------------------------------------------------------------
# QLCloud Dispatch Binary Protocol
#
# Packet format (request and response share the same framing):
#   Bytes 0-3:  32-bit BE nonce (low byte = rolling XOR initial key)
#   Bytes 4-5:  16-bit BE magic (0xf1eb=EU, 0xf1ec=US)
#   Bytes 6-9:  32-bit BE data_length (N)
#   Bytes 10..10+N-1: data payload
#   Bytes 10+N..10+N+1: 16-bit BE CRC-16/MODBUS checksum
#
# Encryption: QLCloud rolling XOR on bytes [4 .. total_len-4), seed=0x7ecf
# CRC: CRC-16/MODBUS (poly 0x8005 reflected, init=0xFFFF) over decrypted [0..10+N)
#
# Request data: MAC address as ASCII "c0:e7:bf:4a:97:d8"
# Response data: broker address as ASCII "IP:PORT" e.g. "192.168.1.35:17766"
# ---------------------------------------------------------------------------

def _ql_setup_steps(seed):
    hi_raw = (seed >> 10) & 0x3FF
    lo_raw = seed & 0x3FF
    return hi_raw + 0x173, lo_raw + 0x33D

def _ql_next_key(key_input, step_hi, step_lo):
    t = (key_input * step_hi + step_lo) & 0xFFF
    return (t + 0x6F) & 0xFF

def qlcloud_decrypt(buf, start, end, initial_key, seed):
    step_hi, step_lo = _ql_setup_steps(seed)
    key = initial_key & 0xFF
    for i in range(start, end):
        ct = buf[i]
        key_input = (ct + key) & 0xFFFFFFFF
        buf[i] = (ct ^ key) & 0xFF
        key = _ql_next_key(key_input, step_hi, step_lo)
    return buf

def qlcloud_encrypt(buf, start, end, initial_key, seed):
    step_hi, step_lo = _ql_setup_steps(seed)
    key = initial_key & 0xFF
    for i in range(start, end):
        ct = (buf[i] ^ key) & 0xFF
        key_input = (ct + key) & 0xFFFFFFFF
        buf[i] = ct
        key = _ql_next_key(key_input, step_hi, step_lo)
    return buf

def crc16_modbus(data, init=0xFFFF):
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF

def qlcloud_parse_dispatch(raw_data):
    """Parse a QLCloud binary dispatch packet. Returns (nonce, magic, data_bytes) or None."""
    buf = bytearray(raw_data)
    pkt_len = len(buf)
    if pkt_len < 12:
        return None
    nonce = struct.unpack_from(">I", buf, 0)[0]
    key = nonce & 0xFF
    # Decrypt bytes [4, pkt_len-4)
    qlcloud_decrypt(buf, 4, pkt_len - 4, key, QLCLOUD_SEED)
    magic = struct.unpack_from(">H", buf, 4)[0]
    data_len = struct.unpack_from(">I", buf, 6)[0]
    if 10 + data_len + 2 > pkt_len:
        return None
    data = bytes(buf[10:10 + data_len])
    stored_crc = struct.unpack_from(">H", buf, 10 + data_len)[0]
    computed_crc = crc16_modbus(buf[0:10 + data_len])
    crc_ok = stored_crc == computed_crc
    return nonce, magic, data, crc_ok, stored_crc, computed_crc

def qlcloud_build_dispatch_response(data_str, magic=QLCLOUD_MAGIC_EU):
    """Build a QLCloud binary dispatch response packet.
    data_str: e.g. "192.168.1.35:17766"
    """
    data_bytes = data_str.encode('ascii')
    data_len = len(data_bytes)
    total_len = 10 + data_len + 2  # header(10) + data + crc(2)

    buf = bytearray(total_len)
    # Generate nonce (random, low byte becomes encryption key)
    nonce = random.randint(0, 0xFFFFFFFF)
    key = nonce & 0xFF
    struct.pack_into(">I", buf, 0, nonce)
    struct.pack_into(">H", buf, 4, magic)
    struct.pack_into(">I", buf, 6, data_len)
    buf[10:10 + data_len] = data_bytes

    # CRC over decrypted bytes [0, 10+data_len)
    crc = crc16_modbus(buf[0:10 + data_len])
    struct.pack_into(">H", buf, 10 + data_len, crc)

    # Encrypt bytes [4, total_len-4)
    qlcloud_encrypt(buf, 4, total_len - 4, key, QLCLOUD_SEED)
    return bytes(buf)


# ---------------------------------------------------------------------------
# MQTT Protocol Helpers
# ---------------------------------------------------------------------------

def decode_mqtt_remaining_length(data, offset=1):
    """Decode MQTT variable-length encoding."""
    multiplier = 1
    value = 0
    idx = offset
    while idx < len(data):
        encoded_byte = data[idx]
        value += (encoded_byte & 0x7F) * multiplier
        multiplier *= 128
        idx += 1
        if (encoded_byte & 0x80) == 0:
            break
    return value, idx


def decode_mqtt_string(data, offset):
    """Decode MQTT UTF-8 string (2-byte length prefix)."""
    if offset + 2 > len(data):
        return "", offset
    length = struct.unpack("!H", data[offset:offset+2])[0]
    offset += 2
    s = data[offset:offset+length].decode("utf-8", errors="replace")
    return s, offset + length


def parse_mqtt_connect(data):
    """Parse MQTT CONNECT packet and return details."""
    info = {}
    if len(data) < 2 or (data[0] & 0xF0) != 0x10:
        return None

    remaining_len, offset = decode_mqtt_remaining_length(data, 1)
    log_msg(f"  MQTT CONNECT: remaining_length={remaining_len}")

    # Protocol name
    proto_name, offset = decode_mqtt_string(data, offset)
    info["protocol"] = proto_name

    if offset >= len(data):
        return info

    # Protocol level
    info["protocol_level"] = data[offset]
    offset += 1

    # Connect flags
    if offset >= len(data):
        return info
    flags = data[offset]
    offset += 1
    info["flags"] = {
        "username": bool(flags & 0x80),
        "password": bool(flags & 0x40),
        "will_retain": bool(flags & 0x20),
        "will_qos": (flags >> 3) & 0x03,
        "will_flag": bool(flags & 0x04),
        "clean_session": bool(flags & 0x02),
    }

    # Keep alive
    if offset + 2 <= len(data):
        info["keepalive"] = struct.unpack("!H", data[offset:offset+2])[0]
        offset += 2

    # Client ID
    client_id, offset = decode_mqtt_string(data, offset)
    info["client_id"] = client_id

    # Will topic/message
    if info["flags"]["will_flag"]:
        will_topic, offset = decode_mqtt_string(data, offset)
        info["will_topic"] = will_topic
        will_msg_len = struct.unpack("!H", data[offset:offset+2])[0] if offset + 2 <= len(data) else 0
        offset += 2
        info["will_message"] = data[offset:offset+will_msg_len].hex()
        offset += will_msg_len

    # Username
    if info["flags"]["username"]:
        username, offset = decode_mqtt_string(data, offset)
        info["username"] = username

    # Password
    if info["flags"]["password"]:
        password, offset = decode_mqtt_string(data, offset)
        info["password"] = password

    return info


def make_mqtt_connack(return_code=0):
    """Create MQTT CONNACK packet."""
    # Fixed header: type=2 (CONNACK), remaining=2
    # Variable header: session_present=0, return_code
    return bytes([0x20, 0x02, 0x00, return_code])


def parse_mqtt_subscribe(data):
    """Parse MQTT SUBSCRIBE packet."""
    if len(data) < 2 or (data[0] & 0xF0) != 0x80:
        return None
    remaining_len, offset = decode_mqtt_remaining_length(data, 1)
    # Packet identifier
    if offset + 2 > len(data):
        return {"topics": []}
    packet_id = struct.unpack("!H", data[offset:offset+2])[0]
    offset += 2
    topics = []
    end = min(len(data), offset + remaining_len - 2)
    while offset < end:
        topic, offset = decode_mqtt_string(data, offset)
        qos = data[offset] if offset < len(data) else 0
        offset += 1
        topics.append({"topic": topic, "qos": qos})
    return {"packet_id": packet_id, "topics": topics}


def make_mqtt_suback(packet_id, qos_list):
    """Create MQTT SUBACK packet."""
    payload = bytes(qos_list)
    remaining = 2 + len(payload)
    header = bytes([0x90, remaining])
    return header + struct.pack("!H", packet_id) + payload


def parse_mqtt_publish(data):
    """Parse MQTT PUBLISH packet."""
    if len(data) < 2:
        return None
    byte0 = data[0]
    if (byte0 & 0xF0) != 0x30:
        return None
    dup = bool(byte0 & 0x08)
    qos = (byte0 >> 1) & 0x03
    retain = bool(byte0 & 0x01)
    remaining_len, offset = decode_mqtt_remaining_length(data, 1)
    topic, offset = decode_mqtt_string(data, offset)
    packet_id = None
    if qos > 0 and offset + 2 <= len(data):
        packet_id = struct.unpack("!H", data[offset:offset+2])[0]
        offset += 2
    payload = data[offset:]
    return {
        "topic": topic, "qos": qos, "retain": retain, "dup": dup,
        "packet_id": packet_id, "payload": payload
    }


def make_mqtt_puback(packet_id):
    """Create MQTT PUBACK packet."""
    return bytes([0x40, 0x02]) + struct.pack("!H", packet_id)


def make_mqtt_pingresp():
    """Create MQTT PINGRESP packet."""
    return bytes([0xD0, 0x00])


def encode_mqtt_remaining_length(length):
    """Encode MQTT variable-length encoding."""
    encoded = bytearray()
    while True:
        byte = length % 128
        length //= 128
        if length > 0:
            byte |= 0x80
        encoded.append(byte)
        if length == 0:
            break
    return bytes(encoded)


def make_mqtt_publish(topic, payload, qos=0, retain=False, packet_id=None):
    """Create MQTT PUBLISH packet."""
    topic_bytes = topic.encode('utf-8')
    variable_header = struct.pack("!H", len(topic_bytes)) + topic_bytes
    if qos > 0 and packet_id is not None:
        variable_header += struct.pack("!H", packet_id)
    remaining = variable_header + payload
    flags = 0x30  # PUBLISH
    if retain:
        flags |= 0x01
    if qos:
        flags |= (qos << 1)
    return bytes([flags]) + encode_mqtt_remaining_length(len(remaining)) + remaining


def build_qlcloud_mqtt_payload(json_obj, msg_type=0x00, msg_subtype=0x10,
                                cmd_type=0x02, cmd_sub=0x01,
                                flags1=0x01, flags2=0x00,
                                flags3=0x01, flags4=0x01, seq=0):
    """Build a QLCloud MQTT payload with 16-byte binary header + JSON."""
    json_bytes = json.dumps(json_obj).encode('ascii')
    total_len = 16 + len(json_bytes)
    header = bytearray(16)
    struct.pack_into(">I", header, 0, total_len)
    header[4] = msg_type
    header[5] = msg_subtype
    header[6] = cmd_type
    header[7] = cmd_sub
    header[8] = flags1
    header[9] = flags2
    header[10] = flags3
    header[11] = flags4
    struct.pack_into(">I", header, 12, seq)
    return bytes(header) + json_bytes


def send_downdps_command(dp_id, value_str):
    """Send a downdps command to Lucy via the active MQTT connection.
    dp_id: data point ID (2 = robot_control, 1 = bind_success, 18 = tmall, 19 = upload_log)
    value_str: command string (e.g. "GET_WORK_STATUS")
    """
    global active_seq_counter
    with active_conn_lock:
        conn = active_mqtt_conn
        topic = active_subscribe_topic
    if conn is None or topic is None:
        log_msg("ERROR: No active MQTT connection to Lucy")
        return False

    active_seq_counter += 1
    # Use subid from device state (learned during handshake), fall back to config/default
    subid = DEVICE_STATE.get("device", {}).get("subid", "LUCPB4ACC26M0017")
    cmd_json = {
        "data": [{"i": dp_id, "v": value_str}],
        "subid": subid,
        "expire": int(time.time()) + 300
    }
    # Build QLCloud payload with downdps opcode 0x0504
    ql_payload = build_qlcloud_mqtt_payload(
        cmd_json,
        flags3=0x05, flags4=0x04,  # opcode 0x0504 = downdps
        seq=active_seq_counter
    )
    mqtt_pkt = make_mqtt_publish(topic, ql_payload)
    try:
        with active_conn_lock:
            active_mqtt_conn.sendall(mqtt_pkt)
        log_msg(f">>> SENT COMMAND: dp_id={dp_id} value=\"{value_str}\" seq={active_seq_counter}")
        log_msg(f"    JSON: {json.dumps(cmd_json)}")
        return True
    except Exception as e:
        log_msg(f"ERROR sending command: {e}")
        return False


def send_heartbeat_response(seq):
    """Send a heartbeat response to Lucy."""
    with active_conn_lock:
        conn = active_mqtt_conn
        topic = active_subscribe_topic
    if conn is None or topic is None:
        return
    hrt_response = {"res": {"errcode": 0}}
    ql_payload = build_qlcloud_mqtt_payload(
        hrt_response,
        flags3=0x03, flags4=0x01,  # opcode 0x0301 = heartbeat
        seq=seq
    )
    mqtt_pkt = make_mqtt_publish(topic, ql_payload)
    try:
        with active_conn_lock:
            active_mqtt_conn.sendall(mqtt_pkt)
        log_msg(f"  >>> Sent heartbeat response (seq={seq})")
    except Exception as e:
        log_msg(f"  Heartbeat response error: {e}")


KNOWN_COMMANDS = [
    "GET_WORK_STATUS", "BATTERY", "CLEAN_STATE", "CLEAN_AREA", "CLEAN_TIME",
    "GET_SYSTEM_CONFIG", "GET_LUCY_POSE", "GET_LUCY_MAP", "GET_MAP",
    "GET_MOPPING_MODE", "GET_MANUAL_MOPPING_MODE", "GET_MONITOR_STATE",
    "GET_MONITOR_VIDEO", "GET_MONITOR_DETAIL", "GET_MONITOR_TASKS",
    "GET_MONITOR_PERSON_ONLY_STATE", "GET_DONOT_DISTURB_MODE",
    "GET_LOG_STATE", "GET_TEST_BOT",
    "SET_RUNNING_MODE_WITH_DOCK", "SET_RUNNING_MODE_WITHOUT_DOCK",
    "RESTART_CLEAN", "PAUSE_DOWNLOAD",
    "SET_DEBUG_LOG", "SET_LOG_STATE", "SET_MAP_MANAGE_STATE",
    "SET_MONITOR_RECORD", "SET_MOTION_DETECT_MODE", "SET_TIRVIS_ENABLE",
    "SET_UPLOAD_LOG_SLAM_MAP", "SET_USER_COUNTRY", "SET_WALL_FOLLOW_ENABLE",
    "RESET_DEVICE_DATA", "RESET_MTNC",
    "LOG_CLEAN", "LOG_MAP", "LOG_QUERY", "LOG_RECORD_CONTROL",
    "LUCY_MAP_MGR",
]


def command_input_thread():
    """Thread that reads commands from stdin and sends them to Lucy."""
    log_msg("")
    log_msg("=== Command Interface ===")
    log_msg("Commands:  send <CMD>  |  list  |  status  |  raw <dp_id> <value>")
    log_msg("Safe read-only: GET_WORK_STATUS, BATTERY, CLEAN_STATE, GET_SYSTEM_CONFIG")
    log_msg("========================")
    log_msg("")

    while True:
        try:
            line = input("> ").strip()
            if not line:
                continue

            parts = line.split(None, 1)
            cmd = parts[0].lower()

            if cmd == "list":
                print("Known commands:")
                for c in KNOWN_COMMANDS:
                    safety = "READ-ONLY" if c.startswith("GET_") or c == "BATTERY" or c.endswith("_STATE") or c.endswith("_TIME") or c.endswith("_AREA") else "ACTION"
                    print(f"  {c:45s} [{safety}]")

            elif cmd == "status":
                with active_conn_lock:
                    connected = active_mqtt_conn is not None
                    topic = active_subscribe_topic
                print(f"Connected: {connected}")
                print(f"Subscribe topic: {topic}")
                print(f"Seq counter: {active_seq_counter}")

            elif cmd == "send":
                if len(parts) < 2:
                    print("Usage: send <COMMAND_NAME>")
                    continue
                command = parts[1].strip().upper()
                if command not in KNOWN_COMMANDS:
                    print(f"WARNING: '{command}' not in known commands list. Send anyway? (y/n)")
                    confirm = input("  ").strip().lower()
                    if confirm != "y":
                        continue
                # Safety check for action commands
                if command.startswith("SET_RUNNING") or command == "RESTART_CLEAN":
                    print(f"SAFETY: '{command}' is an action command that may move the robot!")
                    print("Type 'confirm' to send:")
                    confirm = input("  ").strip().lower()
                    if confirm != "confirm":
                        print("Cancelled.")
                        continue
                send_downdps_command(2, command)

            elif cmd == "raw":
                if len(parts) < 2:
                    print("Usage: raw <dp_id> <value>")
                    continue
                raw_parts = parts[1].split(None, 1)
                if len(raw_parts) < 2:
                    print("Usage: raw <dp_id> <value>")
                    continue
                dp_id = int(raw_parts[0])
                value = raw_parts[1]
                send_downdps_command(dp_id, value)

            else:
                # Try as a direct command name
                command = line.strip().upper()
                if command in KNOWN_COMMANDS:
                    send_downdps_command(2, command)
                else:
                    print(f"Unknown: '{line}'. Try: send <CMD>, list, status, raw <dp_id> <value>")

        except EOFError:
            break
        except Exception as e:
            print(f"Error: {e}")


def run_control_server(port):
    """Simple TCP control server for receiving commands from send_command.py."""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", port))
    server_sock.listen(5)
    log_msg(f"Control server listening on port {port}")

    while True:
        conn, addr = server_sock.accept()
        try:
            data = conn.recv(4096).decode('utf-8', errors='replace').strip()
            if not data:
                conn.close()
                continue

            log_msg(f"Control command received: {data}")

            # Parse: "dp_id:value" format
            if ":" in data:
                dp_id_str, value = data.split(":", 1)
                dp_id = int(dp_id_str)
            else:
                dp_id = 2
                value = data

            result = send_downdps_command(dp_id, value)
            response = "OK" if result else "ERROR: No active connection"
            conn.sendall(response.encode('utf-8'))
        except Exception as e:
            log_msg(f"Control server error: {e}")
            try:
                conn.sendall(f"ERROR: {e}".encode('utf-8'))
            except:
                pass
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Protocol-detecting TLS server on port 17766
# ---------------------------------------------------------------------------

class ProtocolHandler:
    """Handles a single TLS connection, detecting HTTP vs MQTT."""

    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.mqtt_state = "init"

    def handle(self):
        try:
            # Wrap with TLS
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(CERT_FILE, KEY_FILE)
            # Be permissive with TLS versions
            ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            try:
                tls_conn = ctx.wrap_socket(self.conn, server_side=True)
            except ssl.SSLError as e:
                log_msg(f"TLS handshake failed from {self.addr}: {e}")
                # Maybe it's plain TCP (no TLS)?
                log_msg("Trying plain TCP fallback...")
                self.handle_plain(self.conn)
                return

            log_msg(f"TLS connection established from {self.addr}")
            log_msg(f"  TLS version: {tls_conn.version()}")
            log_msg(f"  Cipher: {tls_conn.cipher()}")

            self.handle_data(tls_conn)

        except Exception as e:
            log_msg(f"Connection error from {self.addr}: {e}")
            traceback.print_exc()
        finally:
            try:
                self.conn.close()
            except:
                pass

    def handle_plain(self, conn):
        """Handle plain TCP (no TLS) connection."""
        try:
            data = conn.recv(8192)
            if not data:
                return
            log_msg(f"Plain TCP data from {self.addr} ({len(data)} bytes):")
            log_msg(f"  Hex: {data[:100].hex()}")
            log_msg(f"  Str: {data[:200]!r}")
            self.detect_and_handle(conn, data)
        except Exception as e:
            log_msg(f"Plain TCP error: {e}")

    def handle_data(self, conn):
        """Read data and detect protocol."""
        try:
            data = conn.recv(8192)
            if not data:
                log_msg(f"Empty read from {self.addr}")
                return
            self.detect_and_handle(conn, data)
        except Exception as e:
            log_msg(f"Data read error from {self.addr}: {e}")

    def detect_and_handle(self, conn, data):
        """Detect whether this is HTTP or MQTT and handle accordingly."""
        log_msg(f"Received {len(data)} bytes from {self.addr}")
        log_msg(f"  First bytes hex: {data[:32].hex()}")
        log_msg(f"  First bytes str: {data[:100]!r}")

        # Check for HTTP methods
        if data[:4] in (b"GET ", b"POST", b"PUT ", b"HEAD"):
            log_msg(">>> Detected: HTTP request")
            self.handle_http(conn, data)
        # Check for MQTT CONNECT (first nibble = 0x1)
        elif len(data) > 0 and (data[0] & 0xF0) == 0x10:
            log_msg(">>> Detected: MQTT CONNECT")
            self.handle_mqtt(conn, data)
        # Check for QLCloud binary dispatch (29 bytes, starts with 0x0000)
        elif len(data) == 29 and data[0:2] == b'\x00\x00':
            log_msg(">>> Detected: QLCloud binary dispatch request (29 bytes)")
            self.handle_dispatch_binary(conn, data)
        else:
            log_msg(f">>> Unknown protocol, raw data ({len(data)} bytes):")
            log_msg(f"    Hex dump: {data[:256].hex()}")
            # Check if it could be a short dispatch packet
            if len(data) < 64 and data[0:2] == b'\x00\x00':
                log_msg(">>> Looks like a short dispatch packet, trying dispatch handler")
                self.handle_dispatch_binary(conn, data)
            else:
                # Try to read more
                try:
                    more = conn.recv(8192)
                    if more:
                        log_msg(f"    More data ({len(more)} bytes): {more[:256].hex()}")
                except:
                    pass

    def handle_dispatch_binary(self, conn, data):
        """Handle QLCloud binary dispatch request with correct protocol."""
        # Parse the dispatch request
        parsed = qlcloud_parse_dispatch(data)
        if parsed is None:
            log_msg("  Failed to parse dispatch packet!")
            log_msg(f"  Raw hex: {data.hex()}")
            return

        nonce, magic, payload, crc_ok, stored_crc, computed_crc = parsed
        payload_str = payload.decode('ascii', errors='replace')

        log_msg(f"  Nonce: 0x{nonce:08x} (key=0x{nonce & 0xFF:02x})")
        log_msg(f"  Magic: 0x{magic:04x} ({'EU' if magic == 0xf1eb else 'US' if magic == 0xf1ec else '??'})")
        log_msg(f"  Data ({len(payload)} bytes): '{payload_str}'")
        log_msg(f"  CRC: stored=0x{stored_crc:04x} computed=0x{computed_crc:04x} {'OK' if crc_ok else 'MISMATCH'}")

        # Build response: "IP:PORT" pointing to our local MQTT broker
        broker_addr = f"{LOCAL_IP}:{MQTT_PORT}"
        response = qlcloud_build_dispatch_response(broker_addr, magic=magic)

        # Verify our own response can be parsed
        verify = qlcloud_parse_dispatch(response)
        if verify:
            v_nonce, v_magic, v_data, v_crc_ok, _, _ = verify
            log_msg(f">>> Sending dispatch response ({len(response)} bytes):")
            log_msg(f"    Data: '{v_data.decode('ascii')}'")
            log_msg(f"    Magic: 0x{v_magic:04x}, CRC: {'OK' if v_crc_ok else 'BAD'}")
            log_msg(f"    Hex: {response.hex()}")
        else:
            log_msg(f">>> WARNING: self-verification failed!")
            log_msg(f"    Hex: {response.hex()}")

        try:
            conn.sendall(response)
            log_msg("    Sent successfully!")
        except Exception as e:
            log_msg(f"    Send error: {e}")
            return

        # Wait for follow-up data (auth packet, MQTT, or retry)
        try:
            conn.settimeout(30)
            while True:
                more_data = conn.recv(8192)
                if not more_data:
                    log_msg("  Dispatch: connection closed by client")
                    break
                log_msg(f"  Dispatch: received {len(more_data)} more bytes:")
                log_msg(f"    Hex: {more_data[:256].hex()}")

                # Another dispatch request (retry = our response was rejected)
                if len(more_data) >= 12 and more_data[0:2] == b'\x00\x00':
                    log_msg("    >>> Another dispatch request (retrying)")
                    # Try to parse and re-respond
                    p2 = qlcloud_parse_dispatch(more_data)
                    if p2:
                        log_msg(f"    Data: '{p2[2].decode('ascii', errors='replace')}'")
                    response = qlcloud_build_dispatch_response(broker_addr, magic=magic)
                    conn.sendall(response)
                    log_msg("    >>> Resent dispatch response")
                # MQTT CONNECT
                elif len(more_data) > 0 and (more_data[0] & 0xF0) == 0x10:
                    log_msg("    >>> MQTT CONNECT on dispatch socket!")
                    self.handle_mqtt(conn, more_data)
                    return
                # HTTP
                elif more_data[:4] in (b"GET ", b"POST", b"PUT ", b"HEAD"):
                    log_msg("    >>> HTTP on dispatch socket!")
                    self.handle_http(conn, more_data)
                    return
                else:
                    # Could be a QLCloud auth/registration packet - log it
                    log_msg(f"    >>> Unknown follow-up ({len(more_data)} bytes)")
                    log_msg(f"    Str: {more_data[:256]!r}")
                    # Try parsing as QLCloud packet
                    p3 = qlcloud_parse_dispatch(more_data)
                    if p3:
                        log_msg(f"    Parsed as QLCloud: magic=0x{p3[1]:04x} data='{p3[2].decode('ascii', errors='replace')}'")
        except socket.timeout:
            log_msg("  Dispatch: timeout waiting for follow-up")
        except Exception as e:
            log_msg(f"  Dispatch: error reading follow-up: {e}")

    def handle_http(self, conn, initial_data):
        """Handle HTTP request and respond."""
        request_text = initial_data.decode("utf-8", errors="replace")
        lines = request_text.split("\r\n")
        log_msg(f"HTTP Request: {lines[0] if lines else '(empty)'}")
        for line in lines[1:]:
            if line:
                log_msg(f"  {line}")

        # Find body (after blank line)
        body = b""
        if b"\r\n\r\n" in initial_data:
            body = initial_data.split(b"\r\n\r\n", 1)[1]
            # Check if we need more body data
            for line in lines:
                if line.lower().startswith("content-length:"):
                    expected = int(line.split(":", 1)[1].strip())
                    while len(body) < expected:
                        more = conn.recv(8192)
                        if not more:
                            break
                        body += more

        if body:
            log_msg(f"HTTP Body ({len(body)} bytes):")
            try:
                body_json = json.loads(body)
                log_msg(f"  JSON: {json.dumps(body_json, indent=2)}")

                # Check for dispatch request
                if "product_id" in str(body_json) or "product_sign" in str(body_json):
                    log_msg(">>> DISPATCH REQUEST DETECTED!")
                    response = self.build_dispatch_response(body_json)
                    self.send_http_response(conn, response)
                    return
            except:
                log_msg(f"  Raw: {body[:500]!r}")

        # Generic response
        if "language_pack" in request_text:
            log_msg(">>> Voice package config request")
            response = {"code": 0, "data": {"version": "1.0.0", "packages": []}}
        else:
            response = {"code": 0, "msg": "ok"}

        self.send_http_response(conn, response)

    def build_dispatch_response(self, data):
        """Build dispatch response pointing to our local MQTT broker."""
        req_data = data.get("data", {})
        mac = req_data.get("mac", "")
        sn = req_data.get("sn", "")
        rand_val = req_data.get("rand", 0)
        product_sign = req_data.get("product_sign", "")
        crypt_type = req_data.get("crypt_type", 0)

        log_msg(f"  Device MAC: {mac}")
        log_msg(f"  Device SN: {sn}")
        log_msg(f"  Crypt type: {crypt_type}")

        if product_sign and rand_val:
            expected = hashlib.md5(
                f"{PRODUCT_KEY_EU}{rand_val}{mac}".encode()
            ).hexdigest()
            match = "OK" if expected == product_sign else f"MISMATCH (expected {expected})"
            log_msg(f"  Product sign verify: {match}")

        token = hashlib.sha256(
            f"{time.time()}{random.randint(0, 0xFFFFFFFF)}".encode()
        ).hexdigest()[:32]

        response = {
            "res": {"code": 0, "msg": "ok"},
            "data": {
                "ip": LOCAL_IP,
                "port": DISPATCH_PORT,
                "token": token,
                "dev_id": sn or mac.replace(":", ""),
                "dev_key": "local_key_" + token[:8],
            }
        }
        log_msg(f"  Dispatch response: {json.dumps(response, indent=2)}")
        return response

    def send_http_response(self, conn, obj):
        """Send HTTP 200 JSON response."""
        body = json.dumps(obj).encode()
        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode() + body
        conn.sendall(response)

    def handle_mqtt(self, conn, data):
        """Handle MQTT connection — parse, respond, and log everything."""
        self.subscribe_topic = None
        self.registration_responded = False
        self.device_token = None
        self.mqtt_conn = conn

        # Parse CONNECT
        connect_info = parse_mqtt_connect(data)
        if connect_info:
            log_msg(f"MQTT CONNECT details:")
            for k, v in connect_info.items():
                log_msg(f"  {k}: {v}")

        # Send CONNACK (accept)
        conn.sendall(make_mqtt_connack(0))
        log_msg(">>> Sent CONNACK (accepted)")

        # Keep reading MQTT packets
        self.mqtt_loop(conn)

    def mqtt_loop(self, conn):
        """Read and handle MQTT packets in a loop."""
        conn.settimeout(120)  # 2 min timeout
        buffer = b""
        try:
            while True:
                try:
                    chunk = conn.recv(8192)
                except socket.timeout:
                    log_msg("MQTT: Read timeout, sending PINGRESP")
                    continue
                if not chunk:
                    log_msg("MQTT: Connection closed by client")
                    break

                buffer += chunk

                while len(buffer) >= 2:
                    pkt_type = (buffer[0] & 0xF0) >> 4
                    remaining_len, hdr_end = decode_mqtt_remaining_length(buffer, 1)
                    total_len = hdr_end + remaining_len

                    if len(buffer) < total_len:
                        break  # Need more data

                    pkt = buffer[:total_len]
                    buffer = buffer[total_len:]

                    self.handle_mqtt_packet(conn, pkt, pkt_type)

        except Exception as e:
            log_msg(f"MQTT loop error: {e}")
            traceback.print_exc()

    def handle_mqtt_packet(self, conn, pkt, pkt_type):
        """Handle a single MQTT packet."""
        type_names = {
            1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK",
            5: "PUBREC", 6: "PUBREL", 7: "PUBCOMP", 8: "SUBSCRIBE",
            9: "SUBACK", 10: "UNSUBSCRIBE", 11: "UNSUBACK",
            12: "PINGREQ", 13: "PINGRESP", 14: "DISCONNECT"
        }
        name = type_names.get(pkt_type, f"UNKNOWN({pkt_type})")
        log_msg(f"MQTT <<< {name} ({len(pkt)} bytes)")

        if pkt_type == 3:  # PUBLISH
            pub = parse_mqtt_publish(pkt)
            if pub:
                log_msg(f"  Topic: {pub['topic']}")
                log_msg(f"  QoS: {pub['qos']}, Retain: {pub['retain']}")
                payload = pub["payload"]
                log_msg(f"  Payload ({len(payload)} bytes):")

                # Parse QLCloud 16-byte header + JSON
                json_payload = None
                seq_num = 0
                if len(payload) >= 16:
                    ql_total = struct.unpack(">I", payload[0:4])[0]
                    ql_proto = struct.unpack(">H", payload[4:6])[0]
                    ql_cmd = struct.unpack(">H", payload[6:8])[0]
                    ql_flags = struct.unpack(">H", payload[8:10])[0]
                    ql_opcode = struct.unpack(">H", payload[10:12])[0]
                    seq_num = struct.unpack(">I", payload[12:16])[0]
                    opcode_name = OPCODE_NAMES.get(ql_opcode, f"unknown")
                    log_msg(f"    QLCloud header: total={ql_total} proto=0x{ql_proto:04x} cmd=0x{ql_cmd:04x} flags=0x{ql_flags:04x} opcode=0x{ql_opcode:04x}({opcode_name}) seq={seq_num}")
                    json_part = payload[16:]
                    try:
                        json_payload = json.loads(json_part)
                        log_msg(f"    JSON: {json.dumps(json_payload, indent=2)}")
                    except:
                        log_msg(f"    Hex: {payload[:256].hex()}")
                        try:
                            log_msg(f"    Str: {payload[:256].decode('utf-8', errors='replace')}")
                        except:
                            pass
                else:
                    log_msg(f"    Hex: {payload[:256].hex()}")

                # Cache robot status from updps/data_b06 messages
                if json_payload and len(payload) >= 12:
                    opcode_for_status = struct.unpack(">H", payload[10:12])[0]
                    if opcode_for_status in (0x0503, 0x0b06):  # updps or alt updps
                        update_robot_status(json_payload)

                # Send PUBACK if QoS > 0
                if pub["qos"] > 0 and pub["packet_id"] is not None:
                    conn.sendall(make_mqtt_puback(pub["packet_id"]))
                    log_msg(f"  >>> Sent PUBACK (id={pub['packet_id']})")

                # Respond to heartbeats
                if len(payload) >= 12:
                    ql_opcode_check = struct.unpack(">H", payload[10:12])[0]
                    if ql_opcode_check == 0x0301:  # heartbeat
                        send_heartbeat_response(seq_num)

                # Detect and respond to registration/auth messages
                if json_payload and "data" in json_payload and isinstance(json_payload.get("data"), dict):
                    import base64

                    if "dev_sign" in json_payload["data"] and self.subscribe_topic:
                        # AUTHENTICATION packet (state 10) — has dev_sign
                        log_msg("  *** Auth request detected! Sending auth response...")
                        sn = json_payload["data"].get("dev_id", DEVICE_STATE.get("device", {}).get("subid", "LUCPB4ACC26M0017"))
                        auth_token = hashlib.sha256(
                            f"lucy_auth_{time.time()}".encode()).hexdigest()[:44]
                        auth_response = {
                            "res": {"errcode": 0},
                            "data": {
                                "iotid": sn,
                                "token": auth_token,
                                "ts": int(time.time())
                            }
                        }
                        # Auth uses opcode 0x0201 (bytes 10-11)
                        ql_payload = build_qlcloud_mqtt_payload(
                            auth_response,
                            flags3=0x02, flags4=0x01,
                            seq=seq_num
                        )
                        mqtt_pkt = make_mqtt_publish(self.subscribe_topic, ql_payload)
                        conn.sendall(mqtt_pkt)
                        log_msg(f"  >>> Sent Auth Response: {json.dumps(auth_response)}")
                        # Store active connection for command sending
                        global active_mqtt_conn, active_subscribe_topic
                        with active_conn_lock:
                            active_mqtt_conn = conn
                            active_subscribe_topic = self.subscribe_topic

                        # Update device state
                        if "device" not in DEVICE_STATE:
                            DEVICE_STATE["device"] = {}
                        DEVICE_STATE["device"]["auth_token"] = auth_token
                        DEVICE_STATE["device"]["last_seen"] = datetime.now().isoformat()
                        try:
                            save_state(DEVICE_STATE)
                        except Exception:
                            pass

                        log_msg("  *** Lucy is now ONLINE — ready for commands!")
                        log_msg(f"  *** Device: {DEVICE_STATE.get('device', {}).get('serial', 'unknown')}")
                        log_msg(f"  *** Topic: {self.subscribe_topic}")
                        log_msg("  *** Use: py lucy_control.py start|stop|dock|locate")

                    elif "product_sign" in json_payload["data"] and self.subscribe_topic:
                        # REGISTRATION packet (state 7) — has product_sign, no dev_sign
                        log_msg("  *** Registration request detected! Sending reg response...")
                        sn = json_payload["data"].get("sn", "LUCPB4ACC26M00174")
                        mac = json_payload["data"].get("mac", "")
                        product_id = json_payload.get("req", {}).get("product_id")

                        # Auto-learn device info and save to state
                        if "device" not in DEVICE_STATE:
                            DEVICE_STATE["device"] = {}
                        DEVICE_STATE["device"]["serial"] = sn
                        DEVICE_STATE["device"]["subid"] = sn[:-1] if len(sn) > 1 else sn
                        DEVICE_STATE["device"]["mac"] = mac
                        DEVICE_STATE["device"]["subscribe_topic"] = self.subscribe_topic
                        if product_id:
                            DEVICE_STATE["device"]["product_id"] = product_id
                        DEVICE_STATE["device"]["last_seen"] = datetime.now().isoformat()
                        try:
                            save_state(DEVICE_STATE)
                            log_msg(f"  Device state saved: sn={sn} mac={mac} topic={self.subscribe_topic}")
                        except Exception as e:
                            log_msg(f"  Warning: could not save state: {e}")

                        raw_dev_key = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10'
                        dev_key_b64 = base64.b64encode(raw_dev_key).decode('ascii')
                        reg_response = {
                            "res": {"errcode": 0},
                            "data": {
                                "dev_id": sn,
                                "dev_key": dev_key_b64
                            }
                        }
                        # Registration uses opcode 0x0101 (bytes 10-11)
                        ql_payload = build_qlcloud_mqtt_payload(
                            reg_response,
                            flags3=0x01, flags4=0x01,
                            seq=seq_num
                        )
                        mqtt_pkt = make_mqtt_publish(self.subscribe_topic, ql_payload)
                        conn.sendall(mqtt_pkt)
                        log_msg(f"  >>> Sent Reg Response: {json.dumps(reg_response)}")
        elif pkt_type == 8:  # SUBSCRIBE
            sub = parse_mqtt_subscribe(pkt)
            if sub:
                log_msg(f"  Packet ID: {sub['packet_id']}")
                for t in sub["topics"]:
                    log_msg(f"  Subscribe: {t['topic']} (QoS {t['qos']})")
                # Save subscribe topic for sending responses
                if sub["topics"] and not self.subscribe_topic:
                    self.subscribe_topic = sub["topics"][0]["topic"]
                    log_msg(f"  Saved subscribe topic: {self.subscribe_topic}")
                # Send SUBACK
                qos_list = [t["qos"] for t in sub["topics"]]
                conn.sendall(make_mqtt_suback(sub["packet_id"], qos_list))
                log_msg(f"  >>> Sent SUBACK")

        elif pkt_type == 12:  # PINGREQ
            conn.sendall(make_mqtt_pingresp())
            log_msg("  >>> Sent PINGRESP")

        elif pkt_type == 14:  # DISCONNECT
            log_msg("  Client disconnected cleanly")

        elif pkt_type == 1:  # CONNECT (shouldn't happen again but just in case)
            info = parse_mqtt_connect(pkt)
            if info:
                for k, v in info.items():
                    log_msg(f"  {k}: {v}")
            conn.sendall(make_mqtt_connack(0))

        else:
            log_msg(f"  Raw: {pkt[:128].hex()}")


def run_protocol_server(port):
    """Run the protocol-detecting server on the given port."""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("0.0.0.0", port))
    server_sock.listen(5)
    log_msg(f"Protocol capture server listening on port {port}")

    while True:
        conn, addr = server_sock.accept()
        log_msg(f"New connection from {addr} on port {port}")
        handler = ProtocolHandler(conn, addr)
        t = threading.Thread(target=handler.handle, daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# HTTPS voice package server on port 443
# ---------------------------------------------------------------------------

class VoicePackageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        log_msg(f"HTTPS GET {self.path} from {self.client_address}")
        for k, v in self.headers.items():
            log_msg(f"  {k}: {v}")

        if "language_pack" in self.path:
            response = {"code": 0, "data": {"version": "1.0.0", "packages": []}}
        else:
            response = {"code": 0, "msg": "ok"}

        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        log_msg(f"HTTPS POST {self.path} from {self.client_address}")
        log_msg(f"  Body: {body[:500]!r}")
        resp = json.dumps({"code": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, format, *args):
        pass


def run_https_server(port):
    server = HTTPServer(("0.0.0.0", port), VoicePackageHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    log_msg(f"HTTPS capture server listening on port {port}")
    server.serve_forever()


# ---------------------------------------------------------------------------
# REST API Server (port 8080)
# ---------------------------------------------------------------------------

# Status name mapping
STATUS_NAMES = {
    "1": "cleaning", "2": "cleaning", "3": "paused", "4": "error",
    "5": "returning", "6": "charging", "12": "docked",
}

# Scheduler state
scheduler_state = {
    "next_run": None,       # ISO timestamp of next scheduled command
    "next_command": None,   # What command will run next
    "last_run": None,       # ISO timestamp of last scheduled command
    "last_command": None,   # What command ran last
    "enabled": True,
    "entries": [],          # Current schedule entries (for UI display)
}
scheduler_lock = threading.Lock()

SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")


def _load_schedule_file():
    """Load schedule entries from schedule.json, falling back to config.yaml."""
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE) as f:
                data = json.load(f)
            return data.get("entries", []), data.get("enabled", True)
        except Exception:
            pass
    # Fall back to config.yaml entries
    return CONFIG.get("schedule") or [], True


def _save_schedule_file(entries, enabled):
    """Save schedule entries to schedule.json."""
    with open(SCHEDULE_FILE, "w") as f:
        json.dump({"entries": entries, "enabled": enabled}, f, indent=2)

class APIHandler(BaseHTTPRequestHandler):
    """REST API for controlling Lucy from web UI / Home Assistant."""

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/status":
            with robot_status_lock:
                status = dict(robot_status)
            status["status_name"] = STATUS_NAMES.get(str(status.get("status", "")), "unknown")
            with scheduler_lock:
                status["schedule"] = dict(scheduler_state)
            self._send_json(status)

        elif path == "/api/connection":
            with active_conn_lock:
                connected = active_mqtt_conn is not None
                topic = active_subscribe_topic
            device = DEVICE_STATE.get("device", {})
            self._send_json({
                "connected": connected,
                "subscribe_topic": topic,
                "serial": device.get("serial"),
                "mac": device.get("mac"),
                "last_seen": device.get("last_seen"),
            })

        elif path == "/api/commands":
            self._send_json({
                "action_commands": {
                    "start": "Start cleaning run",
                    "pause": "Pause cleaning (stop in place)",
                    "stop": "Stop cleaning (same as pause)",
                    "dock": "Return to charging dock",
                    "locate": "Find me (voice/beep)",
                    "mute": "Toggle mute",
                },
                "parameterized_commands": {
                    "setVolume": {"description": "Set volume", "parameter": "0-100"},
                    "volumeRelative": {"description": "Adjust volume", "parameter": "+/-N"},
                    "blowerMode": {"description": "Set suction level", "parameter": "21-24"},
                },
                "status_queries": {
                    "GET_WORK_STATUS": "Full status",
                    "BATTERY": "Battery level",
                    "CLEAN_STATE": "Cleaning state",
                    "GET_SYSTEM_CONFIG": "System config",
                },
            })

        elif path == "/api/schedule":
            with scheduler_lock:
                sched = dict(scheduler_state)
            self._send_json(sched)

        elif path == "/api/events":
            # Server-Sent Events stream
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                last_status = None
                while True:
                    with robot_status_lock:
                        current = json.dumps(robot_status)
                    if current != last_status:
                        status = json.loads(current)
                        status["status_name"] = STATUS_NAMES.get(str(status.get("status", "")), "unknown")
                        with scheduler_lock:
                            status["schedule"] = dict(scheduler_state)
                        self.wfile.write(f"data: {json.dumps(status)}\n\n".encode())
                        self.wfile.flush()
                        last_status = current
                    time.sleep(2)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        elif path == "/" or path == "/index.html":
            # Serve web UI
            web_dir = os.path.join(BASE_DIR, "web")
            index_path = os.path.join(web_dir, "index.html")
            if os.path.exists(index_path):
                with open(index_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json({"error": "Web UI not found. Create web/index.html"}, 404)

        else:
            # Try serving static files from web/
            web_dir = os.path.join(BASE_DIR, "web")
            safe_path = os.path.normpath(path.lstrip("/"))
            file_path = os.path.join(web_dir, safe_path)
            # Prevent directory traversal
            if os.path.commonpath([web_dir, file_path]) == web_dir and os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1]
                content_types = {
                    ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
                    ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
                }
                with open(file_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", content_types.get(ext, "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/schedule/toggle":
            with scheduler_lock:
                scheduler_state["enabled"] = not scheduler_state["enabled"]
                enabled = scheduler_state["enabled"]
                entries = list(scheduler_state["entries"])
            _save_schedule_file(entries, enabled)
            self._send_json({"ok": True, "enabled": enabled})
            return

        elif path == "/api/schedule/add":
            try:
                body = self._read_body()
                time_str = body.get("time", "")
                command = body.get("command", "")
                days = body.get("days")  # optional list like ["mon","tue"]
                value = body.get("value")  # optional

                if not time_str or not command:
                    self._send_json({"error": "need 'time' (HH:MM) and 'command'"}, 400)
                    return
                # Validate time format
                parts = time_str.split(":")
                if len(parts) != 2:
                    self._send_json({"error": "time must be HH:MM"}, 400)
                    return
                int(parts[0]); int(parts[1])  # ValueError if bad

                entry = {"time": time_str, "command": command}
                if days:
                    entry["days"] = days
                if value:
                    entry["value"] = value

                with scheduler_lock:
                    entries = list(scheduler_state["entries"])
                    entries.append(entry)
                    scheduler_state["entries"] = entries
                    enabled = scheduler_state["enabled"]
                _save_schedule_file(entries, enabled)
                log_msg(f"SCHEDULE: Added entry via web UI: {entry}")
                self._send_json({"ok": True, "entries": entries})
            except (ValueError, TypeError) as e:
                self._send_json({"error": str(e)}, 400)
            return

        elif path == "/api/schedule/remove":
            try:
                body = self._read_body()
                index = body.get("index")
                if index is None:
                    self._send_json({"error": "need 'index'"}, 400)
                    return
                index = int(index)
                with scheduler_lock:
                    entries = list(scheduler_state["entries"])
                    if 0 <= index < len(entries):
                        removed = entries.pop(index)
                        scheduler_state["entries"] = entries
                        enabled = scheduler_state["enabled"]
                    else:
                        self._send_json({"error": "index out of range"}, 400)
                        return
                _save_schedule_file(entries, enabled)
                log_msg(f"SCHEDULE: Removed entry via web UI: {removed}")
                self._send_json({"ok": True, "entries": entries})
            except (ValueError, TypeError) as e:
                self._send_json({"error": str(e)}, 400)
            return

        elif path == "/api/command":
            try:
                body = self._read_body()
                command = body.get("command", "")
                value = body.get("value")

                if not command:
                    self._send_json({"error": "missing 'command' field"}, 400)
                    return

                # Pause is an alias for stop
                if command == "pause":
                    command = "stop"

                # Map blower UI names to MANBLOWER commands
                blower_map = {
                    "blower_low": "MANBLOWER_LOW",
                    "blower_mid": "MANBLOWER_MIDDLE",
                    "blower_high": "MANBLOWER_HIGHT",
                    "blower_off": "MANBLOWER_CLOSE",
                }
                if command in blower_map:
                    command = blower_map[command]

                # Build the DP 2 value string
                if value is not None:
                    dp_value = f"{command}:{value}"
                else:
                    dp_value = command

                result = send_downdps_command(2, dp_value)
                if result:
                    self._send_json({"ok": True, "command": dp_value})
                else:
                    self._send_json({"ok": False, "error": "No active connection to Lucy"}, 503)

            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)

        else:
            self._send_json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        pass  # Suppress default HTTP logging


def run_api_server(port):
    """Run the REST API server."""
    server = ThreadingHTTPServer(("0.0.0.0", port), APIHandler)
    log_msg(f"REST API server listening on http://0.0.0.0:{port}")
    log_msg(f"  Status:  GET  http://localhost:{port}/api/status")
    log_msg(f"  Command: POST http://localhost:{port}/api/command")
    log_msg(f"  Events:  GET  http://localhost:{port}/api/events")
    log_msg(f"  Web UI:  GET  http://localhost:{port}/")
    server.serve_forever()


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

def _parse_schedule(cfg):
    """Parse schedule entries from config."""
    entries = cfg.get("schedule") or []
    parsed = []
    for entry in entries:
        if not isinstance(entry, dict) or "time" not in entry or "command" not in entry:
            continue
        try:
            hour, minute = map(int, entry["time"].split(":"))
        except (ValueError, AttributeError):
            log_msg(f"SCHEDULE: bad time format: {entry['time']!r} (expected HH:MM)")
            continue
        days = entry.get("days")
        if days:
            day_set = set()
            for d in days:
                d = d.lower().strip()
                if d in DAY_NAMES:
                    day_set.add(DAY_NAMES.index(d))
            if not day_set:
                day_set = set(range(7))
        else:
            day_set = set(range(7))
        command = entry["command"]
        value = entry.get("value")
        parsed.append({"hour": hour, "minute": minute, "days": day_set,
                        "command": command, "value": value})
    return parsed


def _find_next_run(schedule):
    """Find the next scheduled run time and command."""
    if not schedule:
        return None, None
    from datetime import timedelta
    now = datetime.now()
    best_dt = None
    best_entry = None
    for entry in schedule:
        # Check today and next 7 days
        for day_offset in range(8):
            candidate = now.replace(hour=entry["hour"], minute=entry["minute"],
                                    second=0, microsecond=0)
            candidate += timedelta(days=day_offset)
            if candidate <= now:
                continue
            if candidate.weekday() not in entry["days"]:
                continue
            if best_dt is None or candidate < best_dt:
                best_dt = candidate
                best_entry = entry
            break  # Found nearest for this entry
    if best_dt and best_entry:
        cmd = best_entry["command"]
        if best_entry.get("value"):
            cmd = f"{cmd}:{best_entry['value']}"
        return best_dt, cmd
    return None, None


def _reload_schedule():
    """Reload schedule from file, re-parse, and update state."""
    raw_entries, enabled = _load_schedule_file()
    # Build a config-like dict for _parse_schedule
    schedule = _parse_schedule({"schedule": raw_entries})
    with scheduler_lock:
        scheduler_state["enabled"] = enabled
        scheduler_state["entries"] = raw_entries
    return schedule


def run_scheduler():
    """Background thread that fires scheduled commands."""
    schedule = _reload_schedule()

    if schedule:
        log_msg(f"SCHEDULE: Loaded {len(schedule)} schedule entries")
        for entry in schedule:
            days_str = ", ".join(DAY_NAMES[d] for d in sorted(entry["days"]))
            cmd = entry["command"]
            if entry.get("value"):
                cmd = f"{cmd}:{entry['value']}"
            log_msg(f"  {entry['hour']:02d}:{entry['minute']:02d} [{days_str}] -> {cmd}")
    else:
        log_msg("SCHEDULE: No schedule entries configured (add via web UI or config.yaml)")

    while True:
        # Re-read schedule each cycle (picks up web UI changes)
        schedule = _reload_schedule()
        now = datetime.now()
        next_dt, next_cmd = _find_next_run(schedule)

        with scheduler_lock:
            scheduler_state["next_run"] = next_dt.isoformat() if next_dt else None
            scheduler_state["next_command"] = next_cmd

        if next_dt is None:
            time.sleep(30)
            continue

        wait_seconds = (next_dt - now).total_seconds()
        if wait_seconds > 0:
            # Sleep in small increments so we can pick up changes
            while wait_seconds > 0:
                time.sleep(min(wait_seconds, 30))
                with scheduler_lock:
                    if not scheduler_state["enabled"]:
                        break
                wait_seconds = (next_dt - datetime.now()).total_seconds()

            with scheduler_lock:
                if not scheduler_state["enabled"]:
                    time.sleep(30)
                    continue

        # Time to fire
        log_msg(f"SCHEDULE: Firing scheduled command: {next_cmd}")

        # Parse command:value format
        if ":" in next_cmd:
            cmd_name, cmd_val = next_cmd.split(":", 1)
            dp_value = next_cmd
        else:
            dp_value = next_cmd

        result = send_downdps_command(2, dp_value)

        with scheduler_lock:
            scheduler_state["last_run"] = datetime.now().isoformat()
            scheduler_state["last_command"] = next_cmd

        if result:
            log_msg(f"SCHEDULE: Command sent successfully: {next_cmd}")
        else:
            log_msg(f"SCHEDULE: Failed to send command (no connection): {next_cmd}")

        # Wait a minute to avoid re-firing the same slot
        time.sleep(61)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log_msg("=" * 60)
    log_msg("Lucy Local Control Server")
    log_msg(f"Local IP: {LOCAL_IP}")
    log_msg(f"Config: {os.path.join(BASE_DIR, 'config.yaml')}")
    log_msg(f"Cert: {CERT_FILE}")
    if DEVICE_STATE.get("device"):
        dev = DEVICE_STATE["device"]
        log_msg(f"Known device: {dev.get('serial', '?')} ({dev.get('mac', '?')})")
        log_msg(f"  Topic: {dev.get('subscribe_topic', '?')}")
    else:
        log_msg("No known device — will auto-learn on first connection")
    log_msg("=" * 60)

    # HTTPS for voice package requests
    https_port = CONFIG["https_port"]
    t443 = threading.Thread(target=run_https_server, args=(https_port,), daemon=True)
    t443.start()

    # Protocol-detecting server on MQTT port (handles both HTTP dispatch and MQTT)
    mqtt_port = CONFIG["mqtt_port"]
    t_mqtt = threading.Thread(target=run_protocol_server, args=(mqtt_port,), daemon=True)
    t_mqtt.start()

    # Dispatch server
    dispatch_port = CONFIG["dispatch_port"]
    if dispatch_port != mqtt_port:
        t_dispatch = threading.Thread(target=run_protocol_server, args=(dispatch_port,), daemon=True)
        t_dispatch.start()

    # Also listen on port 80 for any plain HTTP
    t80 = threading.Thread(target=lambda: run_protocol_server(80), daemon=True)
    t80.start()

    # REST API on port 8080
    api_port = CONFIG.get("api_port", 8080)
    t_api = threading.Thread(target=run_api_server, args=(api_port,), daemon=True)
    t_api.start()

    log_msg("")
    log_msg("Servers running. To connect Lucy:")
    log_msg(f"  Option 1 (router DNS): Point euiot.trifo.com + eudispatch.trifo.com → {LOCAL_IP}")
    log_msg(f"  Option 2 (on Lucy):    echo \"{LOCAL_IP} eudispatch.trifo.com\" >> /etc/hosts")
    log_msg(f"                         echo \"{LOCAL_IP} euiot.trifo.com\" >> /etc/hosts")
    log_msg(f"                         kill $(pidof cloud_node)")
    log_msg("")
    log_msg("Waiting for Lucy to connect...")
    log_msg(f"  CLI:    py lucy_control.py start|stop|dock|locate|status")
    log_msg(f"  Web UI: http://localhost:{api_port}/")
    log_msg(f"  API:    http://localhost:{api_port}/api/status")
    log_msg("")

    # Start control socket server
    control_port = CONFIG["control_port"]
    ctrl_thread = threading.Thread(target=run_control_server, args=(control_port,), daemon=True)
    ctrl_thread.start()

    # Scheduler
    sched_thread = threading.Thread(target=run_scheduler, daemon=True)
    sched_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log_msg("Shutting down.")
