#!/usr/bin/env python3
"""Publish synthetic GeoSense event JSON to MQTT for ML/TDOA testing.

This does not use LoRa. It injects the same JSON that the patched
``lora_receiver.py`` publishes to ``rawdata`` after receiving an event JSON
from an ESP dummy node.

Examples:
    python tools/publish_dummy_events.py
    python tools/publish_dummy_events.py --host 127.0.0.1 --port 1883
    python tools/publish_dummy_events.py --count 5 --interval 3
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime, timedelta, timezone

try:
    import paho.mqtt.client as mqtt
except ImportError:
    raise SystemExit("paho-mqtt belum terpasang. Jalankan: pip install paho-mqtt")

# node index is 0-based on the firmware side:
# NODE01=0 -> geo_1, NODE02=1 -> geo_2, NODE03=2 -> geo_3
NODES = (
    ("NODE01", 0.0),
    ("NODE02", 0.0015),
    ("NODE03", 0.0030),
)


def make_wave(samples: int, sample_rate: float, delay_s: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    center_s = (samples / 2.0) / sample_rate
    values = []
    for index in range(samples):
        t = index / sample_rate - delay_s
        pulse = math.exp(-((t - center_s) ** 2) / 0.0008)
        wave = 0.35 * math.sin(2.0 * math.pi * 7.0 * t) * pulse
        wave += rng.uniform(-0.015, 0.015)
        values.append(round(wave, 6))
    return values


def make_event(node: str, node_index: int, base_utc: datetime, delay_s: float,
               samples: int, sample_rate: float) -> dict:
    values = make_wave(samples, sample_rate, delay_s, seed=node_index + 1)
    p2p = max(values) - min(values)
    rms = math.sqrt(sum(value * value for value in values) / len(values))
    peak = max(abs(value) for value in values)
    utc = (base_utc + timedelta(seconds=delay_s)).isoformat(timespec="milliseconds")
    if utc.endswith("+00:00"):
        utc = utc[:-6] + "Z"
    return {
        "type": "event",
        "n": node,
        "event_id": f"dummy-{int(base_utc.timestamp())}-{node}",
        "utc": utc,
        "samples": values,
        "trigger_index": samples // 2,
        "sample_period_us": round(1_000_000.0 / sample_rate, 3),
        "p2p": round(p2p, 6),
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "unit": "adc_counts",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish dummy GeoSense events to MQTT")
    parser.add_argument("--host", default="127.0.0.1", help="MQTT broker")
    parser.add_argument("--port", type=int, default=1883, help="MQTT port")
    parser.add_argument("--topic", default="rawdata", help="event topic")
    parser.add_argument("--count", type=int, default=1, help="jumlah event batch")
    parser.add_argument("--interval", type=float, default=2.0, help="jeda antar batch (detik)")
    parser.add_argument("--samples", type=int, default=1024, help="jumlah sampel per event")
    parser.add_argument("--rate", type=float, default=90.0, help="sample rate Hz")
    parser.add_argument("--user", default="", help="MQTT username (opsional)")
    parser.add_argument("--password", default="", help="MQTT password (opsional)")
    parser.add_argument("--dry-run", action="store_true", help="cetak JSON tanpa MQTT")
    args = parser.parse_args()

    if args.samples < 64:
        raise SystemExit("--samples minimal 64")
    if args.rate <= 0:
        raise SystemExit("--rate harus > 0")

    if args.dry_run:
        for batch in range(args.count):
            base_utc = datetime.now(timezone.utc)
            for index, (node, delay) in enumerate(NODES):
                event = make_event(
                    node, index, base_utc, delay, args.samples, args.rate
                )
                print(json.dumps(event, separators=(",", ":")))
        return

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except (AttributeError, TypeError):
        client = mqtt.Client()
    if args.user:
        client.username_pw_set(args.user, args.password)

    print(f"MQTT {args.host}:{args.port}, topic={args.topic}")
    client.connect(args.host, args.port, keepalive=60)
    client.loop_start()

    try:
        for batch in range(args.count):
            base_utc = datetime.now(timezone.utc)
            for node, delay in NODES:
                event = make_event(
                    node,
                    NODES.index((node, delay)),
                    base_utc,
                    delay,
                    args.samples,
                    args.rate,
                )
                payload = json.dumps(event, separators=(",", ":"))
                info = client.publish(args.topic, payload, qos=1)
                info.wait_for_publish(timeout=5)
                print(
                    f"  {event['n']} {event['event_id']} "
                    f"samples={len(event['samples'])} utc={event['utc']}"
                )
            if batch + 1 < args.count:
                time.sleep(args.interval)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
