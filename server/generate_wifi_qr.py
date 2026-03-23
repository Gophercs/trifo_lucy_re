#!/usr/bin/env python3
"""
Generate a WiFi provisioning QR code for Trifo Lucy robot vacuum.

Usage:
    python generate_wifi_qr.py --ssid "MyNetwork" --password "MyPassword"
    python generate_wifi_qr.py --ssid "MyNetwork" --password "MyPassword" --region 3 --output lucy_qr.png

The QR code is displayed on your phone screen for Lucy to scan during
network configuration mode (hold recharge button for 5 seconds).

Requires: pip install qrcode pillow
"""

import argparse
import sys

def build_wifi_string(ssid, password, uid="12345", region="3", auth_type=""):
    """Build the Trifo Lucy WiFi provisioning string.

    Format: WIFI:T:<type>;P:"<password>";S:<ssid>;U:<uid>;C:<region>;

    Fields:
        T: WiFi auth type (empty works for WPA/WPA2)
        P: WiFi password (quoted)
        S: WiFi SSID
        U: User ID (any number — not validated by local server)
        C: Cloud region (1=US, 2=Asia, 3=EU)
    """
    return f'WIFI:T:{auth_type};P:"{password}";S:{ssid};U:{uid};C:{region};'


def main():
    parser = argparse.ArgumentParser(
        description="Generate WiFi provisioning QR code for Trifo Lucy"
    )
    parser.add_argument("--ssid", required=True, help="WiFi network name")
    parser.add_argument("--password", required=True, help="WiFi password")
    parser.add_argument("--region", default="3", choices=["1", "2", "3"],
                        help="Cloud region: 1=US, 2=Asia, 3=EU (default: 3)")
    parser.add_argument("--uid", default="12345",
                        help="User ID (default: 12345 — any number works)")
    parser.add_argument("--auth-type", default="",
                        help="WiFi auth type (default: empty, works for WPA2)")
    parser.add_argument("--output", default="lucy_wifi_qr.png",
                        help="Output filename (default: lucy_wifi_qr.png)")
    parser.add_argument("--show", action="store_true",
                        help="Display the QR code on screen")

    args = parser.parse_args()

    wifi_string = build_wifi_string(
        ssid=args.ssid,
        password=args.password,
        uid=args.uid,
        region=args.region,
        auth_type=args.auth_type,
    )

    print(f"QR content: {wifi_string}")

    try:
        import qrcode
    except ImportError:
        print("\nError: qrcode package not installed.")
        print("Install with: pip install qrcode pillow")
        print(f"\nYou can also manually create a QR code with this string:")
        print(f"  {wifi_string}")
        sys.exit(1)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(wifi_string)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img.save(args.output)
    print(f"QR code saved to: {args.output}")

    if args.show:
        try:
            img.show()
        except Exception:
            print("(Could not display image — open the file manually)")

    print("\nInstructions:")
    print("  1. Hold Lucy's recharge button (right button) for 5 seconds")
    print("  2. Wait for 'entering network configuration' announcement")
    print("  3. Display QR code on phone screen (max brightness)")
    print("  4. Start ~1m away, slowly move phone toward Lucy's camera")
    print("  5. Keep QR centred in camera view — usually works in 2-3 tries")


if __name__ == "__main__":
    main()
