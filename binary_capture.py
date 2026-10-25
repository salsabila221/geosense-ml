#!/usr/bin/env python3
"""Capture frame biner dari node GeoSense lewat serial LoRa (diagnostik).

Node STM32 mengirim frame biner (EVENT_META / DATA / EVENT_END) melalui LoRa.
Tool ini hanya **membaca** byte apa adanya dan menampilkannya sebagai hex,
supaya layout frame (header, versi, CRC-16, varint) bisa diperiksa sebelum
decoder/ACK dibuat.

Contoh:

    python binary_capture.py --port COM5 --baud 9600 --seconds 30

Kalau ingin mengirim balasan ACK mentah setelah setiap frame (untuk uji coba):

    python binary_capture.py --port COM5 --ack-hex "AA 55 01 02"

Hasil capture juga ditulis ke ``binary_capture.jsonl`` (satu baris per frame).
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial belum terpasang. Jalankan: pip install pyserial")


def port_hint(port):
    desc = f"{port.description} {port.manufacturer or ''}".upper()
    if any(key in desc for key in ("CH9102", "CP210", "CP2102")):
        return "ESP32/board USB serial"
    if any(key in desc for key in ("CH343", "CH340", "CH341")):
        return "kemungkinan E15/E220 LoRa"
    return ""


def choose_port(explicit=None):
    if explicit:
        return explicit
    ports = list(list_ports.comports())
    if not ports:
        sys.exit("Tidak ada port serial. Colok adapter LoRa lalu coba lagi.")

    candidates = [p for p in ports if port_hint(p) == "kemungkinan E15/E220 LoRa"]
    if len(candidates) == 1:
        return candidates[0].device
    if len(ports) == 1:
        return ports[0].device

    print("Port serial yang terdeteksi:")
    for index, port in enumerate(ports, 1):
        desc = " ".join(x for x in (port.description, port.manufacturer) if x)
        hint = port_hint(port)
        print(f"  {index}. {port.device}  {desc}" + (f"  <-- {hint}" if hint else ""))
    try:
        choice = input("Pilih nomor port: ").strip()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nDibatalkan.")
    if not choice.isdigit() or not (1 <= int(choice) <= len(ports)):
        sys.exit("Pilihan tidak valid.")
    return ports[int(choice) - 1].device


def hex_dump(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def ascii_dump(data: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte < 127 else "." for byte in data)


def emit_frame(buffer: bytearray, output_path: str) -> None:
    if not buffer:
        return
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    hex_text = hex_dump(buffer)
    print(f"\n[{stamp}] frame {len(buffer)} bytes")
    print(f"  HEX  : {hex_text}")
    print(f"  ASCII: {ascii_dump(buffer)}")
    try:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "captured_at": stamp,
                        "length": len(buffer),
                        "hex": hex_text,
                        "ascii": ascii_dump(buffer),
                    }
                )
                + "\n"
            )
    except OSError as exc:
        print(f"  (gagal menulis {output_path}: {exc})")
    buffer.clear()


def main():
    parser = argparse.ArgumentParser(description="Capture frame biner node GeoSense.")
    parser.add_argument("--port", default="", help="port serial, mis. COM5 (kosong = auto)")
    parser.add_argument("--baud", type=int, default=9600, help="baud serial (default 9600)")
    parser.add_argument("--seconds", type=float, default=30.0, help="durasi capture (default 30 s)")
    parser.add_argument("--gap-ms", type=float, default=60.0,
                        help="jeda diam yang dianggap akhir frame (default 60 ms)")
    parser.add_argument("--out", default="binary_capture.jsonl",
                        help="file hasil capture (default binary_capture.jsonl)")
    parser.add_argument("--ack-hex", default="",
                        help="byte ACK mentah yang dikirim setelah tiap frame (opsional)")
    args = parser.parse_args()

    ack_bytes = b""
    if args.ack_hex:
        try:
            ack_bytes = bytes.fromhex(args.ack_hex.replace(",", " ").replace("0x", ""))
        except ValueError as exc:
            sys.exit(f"--ack-hex tidak valid: {exc}")

    port = choose_port(args.port or None)
    ser = serial.Serial(port, args.baud, timeout=0.05)
    print("=" * 66)
    print(f" Capture biner GeoSense | {port} @ {args.baud} baud")
    print(f" Durasi {args.seconds:.0f} s | gap frame {args.gap_ms:.0f} ms")
    print(f" Output: {args.out}")
    if ack_bytes:
        print(f" ACK otomatis: {hex_dump(ack_bytes)}")
    else:
        print(" ACK: tidak dikirim (mode aman / hanya baca)")
    print("=" * 66)

    buffer = bytearray()
    last_rx = time.monotonic()
    started = last_rx
    total = 0

    try:
        while (time.monotonic() - started) < args.seconds:
            chunk = ser.read(256)
            now = time.monotonic()
            if chunk:
                buffer.extend(chunk)
                total += len(chunk)
                last_rx = now
                continue

            if buffer and (now - last_rx) * 1000 >= args.gap_ms:
                emit_frame(buffer, args.out)
                if ack_bytes:
                    ser.write(ack_bytes)
                    ser.flush()
                    print(f"  TX ACK: {hex_dump(ack_bytes)}")
    except KeyboardInterrupt:
        print("\nDihentikan.")
    finally:
        emit_frame(buffer, args.out)
        ser.close()

    print(f"\nSelesai. Total {total} byte. Hasil: {args.out}")


if __name__ == "__main__":
    main()
