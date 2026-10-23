#!/usr/bin/env python3
"""Bridge LoRa E220-900T22D (E15-USB-T2) -> JSON / HTTP / POST.

Plug-and-play untuk laptop teman B:
- baca data node dari modul LoRa USB,
- parsing JSON + RSSI,
- tampilkan, simpan JSONL, kirim POST ke backend,
- sekaligus menyediakan HTTP API lokal (/latest, /history, /stream, /health).
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlrequest
from urllib.parse import parse_qs, urlparse

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial belum terpasang. Jalankan: pip install pyserial")

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(BASE, "config.json")
DEFAULT_LOG = os.path.join(BASE, "lora_data.jsonl")

CONFIG_KEYS = {
    "port",
    "baud",
    "rssi",
    "serve_port",
    "host",
    "post_url",
    "token",
    "log",
    "max_history",
    "quiet",
}


def port_hint(port):
    desc = f"{port.description} {port.manufacturer or ''}".upper()
    if any(key in desc for key in ("CH9102", "CP210", "CP2102")):
        return "kemungkinan ESP32/board lain"
    if any(key in desc for key in ("CH343", "CH340", "CH341")):
        return "kemungkinan E15 LoRa"
    return ""


def choose_port(explicit=None):
    if explicit:
        return explicit
    ports = list(list_ports.comports())
    if not ports:
        sys.exit("Tidak ada port serial. Install driver CH340/CH343 lalu colok E15-USB-T2.")
    candidates = [p for p in ports if port_hint(p) == "kemungkinan E15 LoRa"]
    if len(candidates) == 1:
        return candidates[0].device
    if len(ports) == 1:
        return ports[0].device

    print("Port serial yang terdeteksi:")
    default_index = None
    for index, port in enumerate(ports, 1):
        desc = " ".join(x for x in (port.description, port.manufacturer) if x)
        hint = port_hint(port)
        if hint == "kemungkinan E15 LoRa" and default_index is None:
            default_index = index
        label = f"  <-- {hint}" if hint else ""
        print(f"  {index}. {port.device}  {desc}{label}")

    prompt = "Pilih port E15"
    if default_index is not None:
        prompt += f" [Enter = {default_index}]"
    prompt += ": "
    while True:
        try:
            choice = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            sys.exit("\nDibatalkan.")
        if not choice and default_index is not None:
            return ports[default_index - 1].device
        if choice.isdigit() and 1 <= int(choice) <= len(ports):
            return ports[int(choice) - 1].device


def load_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"PERINGATAN: config tidak terbaca ({exc}). Pakai default.")
        return {}


def read_line_with_rssi(ser, rssi_enabled):
    line = ser.read_until(b"\n")
    if not line or not line.endswith(b"\n"):
        return None, None
    text = line.decode("utf-8", "replace").strip("\r\n")
    if not text:
        return None, None

    rssi_byte = None
    if rssi_enabled:
        old_timeout = ser.timeout
        ser.timeout = 0.2
        try:
            data = ser.read(1)
        finally:
            ser.timeout = old_timeout
        if data:
            rssi_byte = data[0]
    return text, rssi_byte


def parse_payload(text, rssi_byte, port):
    record = {
        "received_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "source": "lora",
        "source_port": port,
    }
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            record.update(value)
        else:
            record["value"] = value
    except Exception:
        record["raw"] = text

    if rssi_byte is not None and rssi_byte != 0:
        record["rssi_dbm"] = -(256 - rssi_byte)
    return record


class Store:
    def __init__(self, max_history=1000):
        self.lock = threading.Lock()
        self.records = deque(maxlen=max_history)
        self.latest = None
        self.count = 0
        self.last_seen = None
        self.port = None
        self.started = time.time()
        self.subscribers = set()

    def add(self, record):
        with self.lock:
            self.records.append(record)
            self.latest = record
            self.count += 1
            self.last_seen = record.get("received_at")
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(record)
            except queue.Full:
                pass

    def subscribe(self, subscriber):
        with self.lock:
            self.subscribers.add(subscriber)

    def unsubscribe(self, subscriber):
        with self.lock:
            self.subscribers.discard(subscriber)

    def snapshot_latest(self):
        with self.lock:
            return self.latest

    def snapshot_history(self, limit):
        with self.lock:
            items = list(self.records)
        if limit > 0:
            items = items[-limit:]
        return items

    def health(self):
        with self.lock:
            return {
                "ok": True,
                "port": self.port,
                "records": self.count,
                "last_seen": self.last_seen,
                "uptime_s": round(time.time() - self.started, 1),
            }


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/latest":
                self._send_json(200, store.snapshot_latest() or {})
                return

            if path == "/history":
                query = parse_qs(parsed.query)
                try:
                    limit = int(query.get("limit", ["100"])[0])
                except ValueError:
                    limit = 100
                records = store.snapshot_history(limit)
                self._send_json(200, {"count": len(records), "records": records})
                return

            if path == "/health":
                self._send_json(200, store.health())
                return

            if path == "/stream":
                self._stream()
                return

            if path == "/":
                self._send_json(200, {
                    "service": "lora_bridge",
                    "endpoints": ["/latest", "/history?limit=100", "/stream", "/health"],
                })
                return

            self._send_json(404, {"error": "not found"})

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            subscriber = queue.Queue(maxsize=100)
            store.subscribe(subscriber)
            try:
                latest = store.snapshot_latest()
                if latest is not None:
                    self.wfile.write(f"data: {json.dumps(latest, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()

                while True:
                    try:
                        record = subscriber.get(timeout=15)
                        chunk = f"data: {json.dumps(record, ensure_ascii=False)}\n\n"
                        self.wfile.write(chunk.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                store.unsubscribe(subscriber)

        def log_message(self, *args):
            pass

    return Handler


def post_json(url, token, record):
    data = json.dumps(record, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urlrequest.Request(url, data=data, headers=headers, method="POST")
    with urlrequest.urlopen(request, timeout=3) as response:
        response.read()


def build_parser(config):
    parser = argparse.ArgumentParser(
        description="Bridge LoRa E220 -> JSON/HTTP untuk backend/frontend teman B."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help="file config.json (default: config.json di folder ini)")
    parser.add_argument("--port", default="",
                        help="port E15 (contoh COM5); kosong = auto-detect")
    parser.add_argument("--baud", type=int, default=9600,
                        help="baud E15 (default 9600)")
    parser.add_argument("--rssi", dest="rssi", action="store_true",
                        help="paksa parsing byte RSSI ON")
    parser.add_argument("--no-rssi", dest="rssi", action="store_false",
                        help="matikan parsing byte RSSI")
    parser.add_argument("--serve-port", type=int, default=8000,
                        help="port HTTP API lokal; 0 = matikan (default 8000)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="alamat bind HTTP (default 127.0.0.1; 0.0.0.0 untuk LAN)")
    parser.add_argument("--post-url", default="",
                        help="URL backend untuk POST JSON; kosong = tidak POST")
    parser.add_argument("--token", default="",
                        help="Bearer token untuk POST (opsional)")
    parser.add_argument("--log", default=DEFAULT_LOG,
                        help="file JSONL; kosong = tidak menyimpan")
    parser.add_argument("--no-log", dest="log", action="store_const", const="",
                        help="matikan penyimpanan JSONL")
    parser.add_argument("--max-history", type=int, default=1000,
                        help="jumlah record yang disimpan di RAM (default 1000)")
    parser.add_argument("--quiet", action="store_true",
                        help="jangan cetak setiap record ke layar")

    parser.set_defaults(rssi=True, log=DEFAULT_LOG, serve_port=8000,
                        host="127.0.0.1", max_history=1000, quiet=False)
    parser.set_defaults(**{key: value for key, value in config.items() if key in CONFIG_KEYS})
    return parser


def main():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=DEFAULT_CONFIG)
    pre_args, _ = pre.parse_known_args()
    config = load_config(pre_args.config)

    parser = build_parser(config)
    args = parser.parse_args()

    if args.baud <= 0:
        sys.exit("--baud harus > 0.")
    if args.max_history <= 0:
        sys.exit("--max-history harus > 0.")

    port = choose_port(args.port or None)
    port_desc = ""
    for item in list_ports.comports():
        if item.device == port:
            port_desc = " ".join(x for x in (item.description, item.manufacturer) if x)
            break

    if args.log and not os.path.isabs(args.log):
        args.log = os.path.join(BASE, args.log)

    print("=" * 60)
    print(" LoRa Bridge - E220-900T22D")
    print("=" * 60)
    print(f" Port      : {port} ({port_desc or 'deskripsi tidak diketahui'})")
    print(f" Baud      : {args.baud} 8N1")
    print(f" RSSI      : {'ON' if args.rssi else 'OFF'}")
    print(f" Log       : {args.log or '(tidak menyimpan)'}")
    if args.post_url:
        print(f" POST      : {args.post_url}")
    else:
        print(" POST      : (tidak aktif)")
    if args.serve_port:
        shown_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
        print(f" HTTP API  : http://{shown_host}:{args.serve_port}/latest")
        print(f"             http://{shown_host}:{args.serve_port}/history?limit=100")
        print(f"             http://{shown_host}:{args.serve_port}/stream")
        print(f"             http://{shown_host}:{args.serve_port}/health")
    else:
        print(" HTTP API  : (tidak aktif)")
    print("=" * 60)
    print("Tekan Ctrl+C untuk berhenti.")

    store = Store(max_history=args.max_history)
    store.port = port

    server = None
    if args.serve_port:
        try:
            server = ThreadingHTTPServer((args.host, args.serve_port), make_handler(store))
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
        except OSError as exc:
            print(f"PERINGATAN: HTTP API gagal aktif di port {args.serve_port}: {exc}")

    log_handle = None
    if args.log:
        try:
            log_handle = open(args.log, "a", encoding="utf-8")
        except OSError as exc:
            print(f"PERINGATAN: tidak bisa menulis log {args.log}: {exc}")

    ser = None
    try:
        while True:
            try:
                ser = serial.Serial(port, args.baud, timeout=1.0)
                try:
                    ser.dtr = False
                    ser.rts = False
                except Exception:
                    pass
                ser.reset_input_buffer()
                print(f"\nMendengarkan data di {port} ...")

                while True:
                    text, rssi_byte = read_line_with_rssi(ser, args.rssi)
                    if text is None:
                        continue
                    record = parse_payload(text, rssi_byte, port)
                    store.add(record)

                    line = json.dumps(record, ensure_ascii=False)
                    if not args.quiet:
                        print(line, flush=True)
                    if log_handle:
                        log_handle.write(line + "\n")
                        log_handle.flush()

                    if args.post_url:
                        try:
                            post_json(args.post_url, args.token, record)
                        except Exception as exc:
                            print(f"[POST] gagal: {exc}")

            except KeyboardInterrupt:
                raise
            except (serial.SerialException, OSError) as exc:
                print(f"[SERIAL] {exc}. Coba lagi dalam 3 detik...")
                time.sleep(3)
            finally:
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None

    except KeyboardInterrupt:
        print("\nBerhenti.")
    finally:
        if log_handle:
            log_handle.close()
        if server is not None:
            server.shutdown()


if __name__ == "__main__":
    main()
