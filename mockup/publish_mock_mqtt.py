#!/usr/bin/env python3
"""Publish the ML mockup event to MQTT and call the local backend.

Default mode is SAFE:
- publishes an event without a ``status`` field
- backend stores telemetry and updates last_updated
- system status is not changed
- WhatsApp / Web Push notifications are not triggered

Use ``--status SIAGA`` only if you intentionally want a status change and
notifications.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    raise SystemExit("paho-mqtt belum terpasang. Jalankan: pip install paho-mqtt")

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None

BASE_DIR = Path(__file__).resolve().parent
PROCESSING_DIR = BASE_DIR.parent
DEFAULT_OUTPUT = BASE_DIR / "mock_ml_output.json"
DEFAULT_SAMPLES = BASE_DIR / "mock_ml_data.json"
ENV_PATH = PROCESSING_DIR / ".env"


def load_env() -> dict:
    if dotenv_values is None or not ENV_PATH.is_file():
        return {}
    return {key: value for key, value in dotenv_values(ENV_PATH).items() if value is not None}


def backend_get(base_url: str, path: str) -> dict:
    url = base_url.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def build_payload(
    result: dict,
    condition: str = "normal",
    include_status: str = "",
    samples_data: dict | None = None,
) -> dict:
    scenario = result[condition]
    location = result.get("event_location") if condition == "event" else None
    sample_rate = 90.0
    sample_period_us = 1_000_000.0 / sample_rate
    samples = (samples_data or {}).get(condition, {}).get("samples", {})
    nodes = {}
    rms_values = []
    for node, item in scenario["nodes"].items():
        stats = item["stats"]
        rms_values.append(float(stats["rms"]))
        signal = samples.get(node, [])
        nodes[node] = {
            "online": True,
            "p2p": stats["p2p"],
            "rms": stats["rms"],
            "peak": stats["peak"],
            "unit": stats.get("unit", "mV"),
            "signal": signal,
            "sample_period_us": round(sample_period_us, 3),
            "aktivitas": item["activity"],
            "confidence": item["confidence"],
            "gps": {
                "lat": item["gps"]["lat"],
                "lon": item["gps"]["lon"],
                "fix": True,
                "satellites": 8,
                "hdop": 1.2,
            },
        }

    payload = {
        "timestamp": int(time.time()),
        "source": "ml_mock_original",
        "mock": True,
        "persist": True,
        "stream": False,
        "silent": True,
        "aktivitas": scenario["nodes"]["geo_1"]["activity"],
        "p2p_amplitude": scenario["max_p2p"],
        "rms": round(max(rms_values), 6),
        "sampling_rate": sample_rate,
        "nodes": nodes,
        "event": condition == "event",
        "event_id": "mock-%s-%s" % (condition, int(time.time())),
        "clear_location": condition != "event",
    }
    if location:
        payload["location"] = {
            "x": location["x"],
            "y": location["y"],
            "latitude": location["lat"],
            "longitude": location["lon"],
            "estimated_latitude": location["lat"],
            "estimated_longitude": location["lon"],
            "tdoa": {
                "dt12_ms": location["dt12_ms"],
                "dt13_ms": location["dt13_ms"],
                "quality_12": location["quality_12"],
                "quality_13": location["quality_13"],
            },
        }
    status = include_status.upper() if include_status else ("AMAN" if condition == "normal" else "")
    if status:
        payload["status"] = status
    return payload


def publish_mqtt(env: dict, topic: str, payload: dict) -> None:
    broker = env.get("MQTT_BROKER", "").strip()
    if not broker:
        raise SystemExit("MQTT_BROKER tidak ada di processing/.env")
    port = int(env.get("MQTT_PORT", "8883"))
    tls = int(env.get("MQTT_TLS", "1")) == 1
    username = env.get("MQTT_USERNAME", "")
    password = env.get("MQTT_PASSWORD", "")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="geosense_ml_mock")
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id="geosense_ml_mock")
    if username:
        client.username_pw_set(username, password)
    if tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)

    print(f"MQTT connect: {broker}:{port} TLS={int(tls)} topic={topic}")
    client.connect(broker, port, keepalive=30)
    client.loop_start()
    try:
        info = client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1)
        info.wait_for_publish(timeout=10)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise SystemExit(f"Publish gagal rc={info.rc}")
        print("Publish: OK")
    finally:
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Kirim mock ML ke MQTT dan cek backend")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="mock_ml_output.json")
    parser.add_argument(
        "--samples",
        default=str(DEFAULT_SAMPLES),
        help="mock_ml_data.json berisi waveform asli per node",
    )
    parser.add_argument("--topic", default="rawdata", help="topik MQTT")
    parser.add_argument("--backend", default="http://127.0.0.1:8000", help="URL backend")
    parser.add_argument("--wait", type=float, default=5.0, help="jeda sebelum cek backend")
    parser.add_argument(
        "--condition",
        choices=("normal", "event"),
        default="normal",
        help="normal = getaran kecil, event = getaran di area",
    )
    parser.add_argument("--loop", action="store_true", help="kirim terus menerus")
    parser.add_argument("--interval", type=float, default=10.0, help="jeda antar kiriman")
    parser.add_argument("--count", type=int, default=0, help="jumlah kiriman; 0 = tanpa batas")
    parser.add_argument(
        "--status",
        default="",
        help="opsional: SIAGA/WASPADA. Default normal=AMAN, event tanpa status.",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    if not output_path.is_file():
        raise SystemExit(f"File tidak ditemukan: {output_path}")

    result = json.loads(output_path.read_text(encoding="utf-8"))
    samples_path = Path(args.samples)
    samples_data = (
        json.loads(samples_path.read_text(encoding="utf-8"))
        if samples_path.is_file()
        else {}
    )
    if not samples_data:
        print(f"PERINGATAN: waveform asli tidak ditemukan di {samples_path}")
    env = load_env()

    print("== sebelum MQTT ==")
    before = backend_get(args.backend, "/api/system/status")
    print(json.dumps(before, indent=2))

    if args.status:
        print("")
        print("PERINGATAN: --status dipakai. Backend dapat mengirim notifikasi.")
    elif args.condition == "normal":
        print("")
        print("Mode normal: status AMAN, getaran kecil, notifikasi tidak dipicu.")
    else:
        print("")
        print("Mode event tanpa --status: data masuk, status tidak diubah.")

    total = args.count if args.count > 0 else (0 if args.loop else 1)
    sent = 0
    while True:
        payload = build_payload(result, args.condition, args.status, samples_data)
        print("")
        print(
            "== kirim #%d | %s | p2p=%s | status=%s =="
            % (
                sent + 1,
                args.condition,
                payload["p2p_amplitude"],
                payload.get("status", "(tidak diubah)"),
            )
        )
        publish_mqtt(env, args.topic, payload)
        print(f"Menunggu backend {args.wait:.1f} detik...")
        time.sleep(max(0.0, args.wait))

        after = backend_get(args.backend, "/api/system/status")
        print(
            "backend: status=%s last_updated=%s"
            % (after.get("status"), after.get("last_updated"))
        )
        if before.get("last_updated") != after.get("last_updated"):
            print("Backend memproses MQTT: last_updated berubah.")
        else:
            print("PERINGATAN: last_updated tidak berubah. Cek koneksi MQTT backend.")
        before = after
        sent += 1

        if not args.loop:
            break
        if total and sent >= total:
            break
        time.sleep(max(0.0, args.interval - args.wait))

    health = backend_get(args.backend, "/health")
    print("health:", json.dumps(health, separators=(",", ":")))


if __name__ == "__main__":
    main()
