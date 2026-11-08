#!/usr/bin/env python3
"""
Receive LoRa data from an Ebyte E220-900T22D through a USB-TTL adapter
(E15-USB-T2 or any CH340/CH343/CP2102 adapter).

Cross-platform: works on Windows (COMx) and Linux / NVIDIA Jetson
(/dev/ttyUSB0, /dev/ttyACM0, ...).

Usage:
    python3 lora_read.py                        # auto-detect port, config.env
    python3 lora_read.py /dev/ttyUSB0           # explicit port
    python3 lora_read.py /dev/ttyUSB0 9600      # explicit port + baud
    python3 lora_read.py --log data.log         # also append to a CSV log
    python3 lora_read.py --no-timestamp         # print raw lines only

Port/baud/log defaults come from lora/config.env. Press Ctrl+C to stop.
"""

import argparse
import os
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is not installed.  Run:  pip3 install pyserial")

# Allow both `python3 lora/lora_read.py` and `python3 -m lora.lora_read`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from lora.lora_config import load_lora_config, lora_baud, lora_port
except ImportError:  # when executed with lora/ directly on sys.path
    from lora_config import load_lora_config, lora_baud, lora_port


def find_port():
    """Pick the most likely adapter port (CH340/CH343/WCH, else ttyUSB/ttyACM)."""
    ports = list(list_ports.comports())
    if not ports:
        return None
    for p in ports:
        desc = f"{p.description} {p.manufacturer} {p.device}".lower()
        if any(k in desc for k in ("ch340", "ch341", "ch343", "ch9102", "wch")):
            return p.device
    for p in ports:
        if p.device.startswith(("/dev/ttyUSB", "/dev/ttyACM")):
            return p.device
    return ports[0].device


def maybe_escalate(port):
    """Re-exec via sudo if the port is not accessible (Linux only)."""
    if os.name != "posix" or os.geteuid() == 0 or not port:
        return
    if os.access(port, os.R_OK | os.W_OK):
        return
    print(f"No access to {port} (Permission denied). Re-running with sudo...")
    script = os.path.abspath(__file__)
    try:
        os.execvp("sudo", ["sudo", sys.executable, script] + sys.argv[1:])
    except Exception as e:
        sys.exit(f"Cannot escalate with sudo: {e}")


def main():
    cfg = load_lora_config()
    default_port = lora_port(cfg) or None
    default_baud = lora_baud(cfg)
    default_log = (cfg.get("LORA_TEXT_LOG") or "").strip() or None

    ap = argparse.ArgumentParser(description="Read E220 LoRa data over USB-serial.")
    ap.add_argument("port", nargs="?", default=default_port,
                    help="serial port, e.g. /dev/ttyUSB0 or COM6 (auto-detect if omitted)")
    ap.add_argument("baud", nargs="?", type=int, default=default_baud,
                    help="baud rate (default from lora/config.env, normally 9600)")
    ap.add_argument("--log", default=default_log, help="append received lines to this CSV file")
    ap.add_argument("--no-timestamp", action="store_true", help="do not prefix timestamps")
    args = ap.parse_args()

    maybe_escalate(args.port or find_port())

    logf = None
    if args.log:
        try:
            logf = open(args.log, "a", buffering=1)
        except OSError as exc:
            sys.exit(f"Cannot open log file {args.log}: {exc}")

    while True:
        port = args.port or find_port()
        if not port:
            print("No serial port found. Is the E15-USB-T2 plugged in? Retrying in 3s...")
            time.sleep(3)
            continue

        try:
            ser = serial.Serial(port, args.baud, timeout=1)
        except Exception as e:
            print(f"Cannot open {port}: {e}. Retrying in 3s...")
            time.sleep(3)
            continue

        print(f"Listening on {port} @ {args.baud} 8N1  (Ctrl+C to stop)")
        try:
            ser.reset_input_buffer()
            while True:
                line = ser.readline()
                if not line:
                    continue
                text = line.decode("utf-8", "backslashreplace").rstrip("\r\n")
                if not text:
                    continue
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                print(text if args.no_timestamp else f"[{ts}] {text}")
                if logf:
                    logf.write(f"{ts},{text}\n")
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"Read error: {e}. Reconnecting...")
            time.sleep(2)
        finally:
            try:
                ser.close()
            except Exception:
                pass

    if logf:
        logf.close()


if __name__ == "__main__":
    main()
