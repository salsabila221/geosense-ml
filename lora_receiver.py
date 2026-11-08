#!/usr/bin/env python3
"""Receiver LoRa GeoSense untuk Jetson/laptop.

Membaca stream radio EBYTE lewat USB-UART, memisahkan frame biner v2, event
JSON (mode uji ESP dummy), dan paket GPS JSON, mengirim ACK, menyusun event
waveform, lalu publish ke MQTT.

Alur::

    Node STM32 --LoRa--> EBYTE (USB-UART) --> skrip ini
                                              |-- ACK --> node
                                              |-- event JSON --> rawdata
                                              |-- GPS JSON   --> gpsdata
                                                              --> backend

Contoh (radio langsung ke laptop, UART radio 9600)::

    python lora_receiver.py --port COM5 --baud 9600

Catatan: ESP32 dummy receiver bawaan firmware hanya mencetak log/plot ke USB,
tidak meneruskan frame biner. Untuk menjalankan receiver di Jetson, hubungkan
radio EBYTE ke USB-UART Jetson. ``--no-ack`` dipakai kalau ada jembatan lain
yang sudah mengirim ACK dan meneruskan frame biner.

Konfigurasi port/baud/ACK/log ada di ``lora/config.env`` (satu sumber untuk
receiver biner dan reader teks diagnostik).
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
from collections import deque
from datetime import datetime, timezone

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial belum terpasang. Jalankan: pip install pyserial")

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

import lora_events
import lora_protocol as proto
from lora.lora_config import (
    config_path as lora_config_path,
    load_lora_config,
    lora_ack_enabled,
    lora_baud,
    lora_port,
)

DEFAULT_TOPIC_RAW = "rawdata"
DEFAULT_TOPIC_GPS = "gpsdata"


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


class MqttPublisher:
    """Publisher MQTT dengan buffer kecil saat koneksi terputus."""

    def __init__(self, host: str, port: int, username: str, password: str, *, tls: bool):
        if mqtt is None:
            raise RuntimeError("paho-mqtt belum terpasang (pip install paho-mqtt)")
        self.host = host
        self.port = port
        self.connected = False
        self.queue: deque[tuple[str, bytes]] = deque(maxlen=100)
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"geosense_jetson_{int(time.time())}",
        )
        if username:
            self.client.username_pw_set(username, password)
        if tls:
            self.client.tls_set(
                cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2
            )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=20)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        code = getattr(reason_code, "value", reason_code)
        self.connected = code == 0
        log(f"MQTT connect rc={reason_code} ({self.host}:{self.port})")
        if not self.connected:
            return
        pending = len(self.queue)
        while self.queue:
            topic, payload = self.queue.popleft()
            client.publish(topic, payload, qos=1)
        if pending:
            log(f"MQTT flush {pending} pesan tertunda")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        log(f"MQTT disconnect rc={reason_code}; mencoba reconnect...")

    def start(self) -> None:
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def publish(self, topic: str, payload: bytes | str) -> bool:
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        if not self.connected:
            self.queue.append((topic, data))
            return False
        info = self.client.publish(topic, data, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            self.queue.append((topic, data))
            return False
        return True

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def port_hint(port) -> str:
    desc = f"{port.description} {port.manufacturer or ''}".upper()
    if any(key in desc for key in ("CH343", "CH340", "CH341", "CP210", "CH9102")):
        return "USB serial"
    return ""


def choose_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    ports = list(list_ports.comports())
    if not ports:
        sys.exit("Tidak ada port serial. Colok radio EBYTE/UART lalu coba lagi.")
    if len(ports) == 1:
        return ports[0].device
    print("Port serial yang terdeteksi:")
    for index, port in enumerate(ports, 1):
        desc = " ".join(x for x in (port.description, port.manufacturer) if x)
        hint = port_hint(port)
        print(f"  {index}. {port.device}  {desc}" + (f"  <-- {hint}" if hint else ""))
    choice = input("Pilih nomor port: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(ports)):
        sys.exit("Pilihan tidak valid.")
    return ports[int(choice) - 1].device


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def main() -> None:
    if load_dotenv is not None:
        load_dotenv(ENV_PATH)

    lora_cfg = load_lora_config()
    default_port = lora_port(lora_cfg)
    default_baud = lora_baud(lora_cfg)
    default_ack = lora_ack_enabled(lora_cfg)
    default_event_log = lora_cfg.get("LORA_EVENT_LOG") or "lora_events.jsonl"
    log(f"LoRa config: {lora_config_path()}")

    parser = argparse.ArgumentParser(description="Receiver LoRa GeoSense -> MQTT")
    parser.add_argument("--port", default=default_port,
                        help="port serial (kosong = auto; default dari lora/config.env)")
    parser.add_argument("--baud", type=int, default=default_baud,
                        help="baud UART radio (default 9600; ESP32 USB 115200)")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="durasi (0 = terus menerus)")
    parser.add_argument("--out", default=default_event_log,
                        help="file JSONL event lengkap")
    parser.add_argument("--no-ack", dest="ack", action="store_false",
                        help="jangan kirim ACK (kalau ESP32 yang meng-ACK)")
    parser.add_argument("--selected-node", type=int, default=255,
                        help="hanya terima node index tertentu, zero-based (default 255=semua)")
    parser.add_argument("--no-mqtt", dest="mqtt_enabled", action="store_false",
                        help="jalankan tanpa publish MQTT")
    parser.add_argument("--mqtt-host", default=os.getenv("MQTT_BROKER", ""))
    parser.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "8883")))
    parser.add_argument("--mqtt-user", default=os.getenv("MQTT_USERNAME", ""))
    parser.add_argument("--mqtt-pass", default=os.getenv("MQTT_PASSWORD", ""))
    parser.add_argument("--mqtt-tls", type=int, default=int(os.getenv("MQTT_TLS", "1")))
    parser.add_argument("--topic", default=os.getenv("MQTT_TOPIC_RAW", DEFAULT_TOPIC_RAW))
    parser.add_argument("--gps-topic", default=os.getenv("MQTT_TOPIC_GPS", DEFAULT_TOPIC_GPS))
    parser.set_defaults(ack=default_ack, mqtt_enabled=True)
    args = parser.parse_args()

    publisher = None
    if args.mqtt_enabled:
        if not args.mqtt_host:
            sys.exit(
                "MQTT_BROKER belum diisi. Set di .env atau pakai --no-mqtt."
            )
        publisher = MqttPublisher(
            args.mqtt_host,
            args.mqtt_port,
            args.mqtt_user,
            args.mqtt_pass,
            tls=bool(args.mqtt_tls),
        )

    port = choose_port(args.port or None)
    ser = serial.Serial(port, args.baud, timeout=0.05)
    demux = proto.PacketDemux()
    assembler = lora_events.EventAssembler()

    print("=" * 74)
    print(f" GeoSense LoRa receiver | {port} @ {args.baud} baud")
    print(f" ACK ke node: {'ON' if args.ack else 'OFF'}")
    print(f" MQTT: {'ON ' + args.topic + '/' + args.gps_topic if publisher else 'OFF'}")
    print(f" Output event: {args.out}")
    print("=" * 74)

    if publisher:
        publisher.start()

    started = time.monotonic()
    events = 0
    frames = 0
    gps_packets = 0
    handle = None
    try:
        handle = open(args.out, "a", encoding="utf-8")
        while args.seconds <= 0 or (time.monotonic() - started) < args.seconds:
            chunk = ser.read(512)
            if not chunk:
                continue
            for kind, data in demux.feed(chunk):
                if kind == "json":
                    try:
                        payload = json.loads(data)
                    except ValueError:
                        log(f"JSON invalid: {data[:80]!r}")
                        continue
                    if not isinstance(payload, dict):
                        log(f"JSON bukan object: {data[:80]!r}")
                        continue

                    msg_type = str(payload.get("type") or "").strip().lower()

                    if msg_type in {"hb", "heartbeat"}:
                        log(
                            f"HEARTBEAT node={payload.get('n')} "
                            f"t={payload.get('t')} (tidak dipakai ML)"
                        )
                        continue

                    if msg_type in {"event", "evt"}:
                        samples = payload.get("samples")
                        if not isinstance(samples, list) or len(samples) < 64:
                            log("EVENT JSON ditolak: samples minimal 64")
                            continue
                        if not payload.get("n"):
                            log("EVENT JSON ditolak: field n wajib")
                            continue
                        if not payload.get("event_id"):
                            log("EVENT JSON ditolak: field event_id wajib")
                            continue
                        if not payload.get("utc"):
                            log("EVENT JSON ditolak: field utc wajib")
                            continue
                        log(
                            f"EVENT JSON node={payload.get('n')} "
                            f"event={payload.get('event_id')} "
                            f"samples={len(samples)}"
                        )
                        if publisher:
                            publisher.publish(
                                args.topic,
                                json.dumps(payload, separators=(",", ":")),
                            )
                        continue

                    if msg_type in {"gps", "gnss"} or (
                        payload.get("lat") is not None
                        and payload.get("lon") is not None
                    ):
                        gps_packets += 1
                        log(
                            f"GPS node={payload.get('n')} "
                            f"lat={payload.get('lat')} lon={payload.get('lon')} "
                            f"sat={payload.get('sat')} utc={payload.get('utc')}"
                        )
                        if publisher:
                            publisher.publish(
                                args.gps_topic,
                                json.dumps(payload, separators=(",", ":")),
                            )
                        continue

                    if msg_type in {"feature", "features"}:
                        log(
                            "FEATURE JSON diabaikan: ML memakai waveform "
                            "256 sampel, bukan 7 fitur"
                        )
                        continue

                    log(f"JSON tidak dikenal: {data[:120]!r}")
                    continue

                frames += 1
                try:
                    frame = proto.decode_frame(data)
                except proto.FrameError as exc:
                    log(f"frame invalid: {exc}")
                    continue
                if frame.type == proto.TYPE_ACK:
                    continue
                if args.selected_node != 255 and frame.node_id != args.selected_node:
                    continue

                result = assembler.handle(frame)
                if result.accept and args.ack:
                    try:
                        ser.write(proto.encode_ack(frame))
                    except serial.SerialException as exc:
                        log(f"gagal kirim ACK: {exc}")

                if frame.type == proto.TYPE_EVENT_META and result.accept:
                    log(
                        f"META node={frame.node_id} event={frame.event_id} "
                        f"packets={frame.total_packets} "
                        f"(seq={frame.packet_sequence}, status={result.status})"
                    )
                elif frame.type == proto.TYPE_DATA and result.status == "data":
                    pass
                elif result.status not in ("data", "data-duplicate"):
                    log(
                        f"{frame.name} node={frame.node_id} event={frame.event_id} "
                        f"seq={frame.packet_sequence} -> {result.status} {result.detail}".rstrip()
                    )

                if result.event is not None:
                    events += 1
                    record = result.event
                    record["captured_at"] = utc_now()
                    log(
                        f"EVENT selesai node={record['n']} event={record['event_id']} "
                        f"samples={record['sample_count']} p2p={record['p2p']} "
                        f"unit={record['unit']} utc={record['utc']}"
                    )
                    handle.write(json.dumps(record) + "\n")
                    handle.flush()
                    if publisher:
                        publisher.publish(
                            args.topic,
                            json.dumps(record, separators=(",", ":")),
                        )
    except KeyboardInterrupt:
        print("\nDihentikan.")
    finally:
        if handle:
            handle.close()
        ser.close()
        if publisher:
            time.sleep(0.5)
            publisher.stop()

    print(
        f"Total: {frames} frame, {events} event, {gps_packets} paket GPS, "
        f"buffer sisa {len(demux.buffer)} byte, error {demux.errors}."
    )


if __name__ == "__main__":
    main()
