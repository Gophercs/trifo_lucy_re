"""
Send a command to Lucy via the capture server's control port.
The capture server forwards it through the active MQTT connection.
"""
import sys
import socket

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 9999


def send_command(dp_id, value_str):
    """Send command via control socket to capture_server.py"""
    msg = f"{dp_id}:{value_str}"
    print(f"Sending: dp_id={dp_id} value=\"{value_str}\"")

    sock = socket.create_connection((CONTROL_HOST, CONTROL_PORT), timeout=10)
    sock.sendall(msg.encode('utf-8'))
    response = sock.recv(1024).decode('utf-8')
    sock.close()

    print(f"Response: {response}")
    return response == "OK"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py send_command.py <COMMAND>")
        print("       py send_command.py GET_WORK_STATUS")
        print("       py send_command.py BATTERY")
        print("       py send_command.py raw <dp_id> <value>")
        print()
        print("Safe read-only commands:")
        print("  GET_WORK_STATUS  BATTERY  CLEAN_STATE  GET_SYSTEM_CONFIG")
        print("  GET_LUCY_POSE  GET_MAP  GET_MOPPING_MODE")
        sys.exit(1)

    if sys.argv[1].lower() == "raw":
        if len(sys.argv) < 4:
            print("Usage: py send_command.py raw <dp_id> <value>")
            sys.exit(1)
        dp_id = int(sys.argv[2])
        value = " ".join(sys.argv[3:])
        send_command(dp_id, value)
    else:
        command = " ".join(sys.argv[1:]).upper()
        send_command(2, command)
