#!/usr/bin/env python3
"""Generate and publish the 500 Hz, 24-bit GeoSense spike-event mock.

Spec highlights:
- 500 Hz, sample period 2000 us
- 24-bit signed ADC counts, +/-7,000,000 counts -> +/-14 mV
- 0-5 s idle, 5-15 s burst/spike, then continuous idle until stopped
- non-ideal waveform: random walk, random spikes, jagged burst envelope
- 3 irregular GPS nodes around Lapangan Merah Unpad Jatinangor
- event snapshot is persisted; stream chunks are not

Examples:
    python spike_event_500hz.py --generate-only
    python spike_event_500hz.py --publish
    python spike_event_500hz.py --publish --stream
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None

BASE_DIR = Path(__file__).resolve().parent
PROCESSING_DIR = BASE_DIR.parent
ENV_PATH = PROCESSING_DIR / ".env"
BACKEND_ENV_PATH = Path(r"C:\geosense-backend\.env")
OUTPUT_JSON = BASE_DIR / "spike_event_500hz.json"
OUTPUT_CSV = BASE_DIR / "spike_event_500hz_windows.csv"

RATE = 500.0
SAMPLE_PERIOD_US = 2000.0
DURATION_S = 15.0  # 5 s idle + 10 s spike, then idle is streamed continuously
BASELINE_S = 5.0
EVENT_S = 10.0
STREAM_CHUNK_S = 0.5
N_SAMPLES = int(RATE * DURATION_S)
EVENT_START = int(BASELINE_S * RATE)
EVENT_END = EVENT_START + int(EVENT_S * RATE)

COUNTS_FULL_SCALE = 8_388_607
COUNTS_LIMIT = 7_000_000
MV_PER_COUNT = 14.0 / COUNTS_LIMIT
COUNTS_PER_MV = 1.0 / MV_PER_COUNT
WAVE_SPEED = 300.0

# Irregular triangle around Lapangan Merah, Unpad Jatinangor.
NODES = {
    "geo_1": {"name": "Lapangan Merah - sisi utara", "lat": -6.9253200, "lon": 107.7741000},
    "geo_2": {"name": "Lapangan Merah - sisi timur", "lat": -6.9255000, "lon": 107.7744200},
    "geo_3": {"name": "Lapangan Merah - sisi barat", "lat": -6.9251800, "lon": 107.7743300},
}
EVENT_ID = "spike-500hz-%s" % time.strftime("%Y%m%d-%H%M%S")


def clamp(value, low, high):
    return max(low, min(high, value))


def local_xy(lat, lon, origin_lat, origin_lon):
    x = (lon - origin_lon) * 111_320.0 * math.cos(math.radians(origin_lat))
    y = (lat - origin_lat) * 110_540.0
    return x, y


def gps_from_local(x, y, origin_lat, origin_lon):
    lat = origin_lat + y / 110_540.0
    lon = origin_lon + x / (111_320.0 * math.cos(math.radians(origin_lat)))
    return round(lat, 7), round(lon, 7)


def baseline_signal(rng, count, phase):
    """Quiet idle baseline, kept below roughly +/-0.08 mV."""
    walk = 0.0
    values = []
    for index in range(count):
        t = index / RATE
        walk = 0.96 * walk + rng.gauss(0.0, 0.006)
        ripple = 0.018 * math.sin(2.0 * math.pi * 0.65 * t + phase)
        value = walk + ripple + rng.gauss(0.0, 0.004)
        values.append(clamp(value, -0.08, 0.08))
    return values


def burst_signal(rng, count, delay_s, phase):
    """Damped, jagged, bursty event; normalized to a visible p2p level."""
    values = []
    walk = 0.0
    for index in range(count):
        t = index / RATE - BASELINE_S - delay_s
        if t < 0:
            values.append(0.0)
            continue

        attack = 1.0 - math.exp(-t / 0.06)
        # Higher damping: the burst envelope settles more quickly.
        decay = math.exp(-t / 1.8)
        envelope = attack * decay
        burst = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(2.0 * math.pi * 3.1 * t + phase))
        carrier = (
            0.55 * math.sin(2.0 * math.pi * 7.0 * t + phase)
            + 0.30 * math.sin(2.0 * math.pi * 19.0 * t + 0.7 * phase)
            + 0.20 * math.sin(2.0 * math.pi * 43.0 * t + 1.3 * phase)
        )
        # A small amount of randomness gives the mock event a natural texture.
        values.append(envelope * (carrier + rng.gauss(0.0, 0.008)))
    return values


def to_counts(values_mv):
    counts = [int(round(value / MV_PER_COUNT)) for value in values_mv]
    return [max(-COUNTS_LIMIT, min(COUNTS_LIMIT, value)) for value in counts]


def stats(values):
    p2p = max(values) - min(values)
    rms = math.sqrt(sum(value * value for value in values) / len(values))
    peak = max(abs(value) for value in values)
    return {
        "p2p": round(p2p, 4),
        "rms": round(rms, 4),
        "peak": round(peak, 4),
        "unit": "mV",
    }


def build_data():
    origin = NODES["geo_1"]
    local_positions = {
        node: local_xy(info["lat"], info["lon"], origin["lat"], origin["lon"])
        for node, info in NODES.items()
    }
    # Epicentre exactly at the centroid of the three irregular nodes.
    event_local = {
        "x": round(sum(x for x, _ in local_positions.values()) / len(local_positions), 3),
        "y": round(sum(y for _, y in local_positions.values()) / len(local_positions), 3),
    }
    event_lat, event_lon = gps_from_local(
        event_local["x"], event_local["y"], origin["lat"], origin["lon"]
    )

    nodes = {}
    full_signals = {}
    for index, (node, info) in enumerate(NODES.items()):
        x, y = local_positions[node]
        distance = math.hypot(event_local["x"] - x, event_local["y"] - y)
        delay = distance / WAVE_SPEED

        # Change phase on each run while keeping the waveform smooth.
        rng = random.Random()
        baseline = baseline_signal(rng, N_SAMPLES, index * 0.8)
        phase = rng.uniform(-math.pi, math.pi) + index * 0.9
        burst = burst_signal(rng, N_SAMPLES, delay, phase)

        # Restore the prior visible event amplitude for the dashboard.
        target_p2p = 27.4 + index * 0.4
        burst_p2p = max(burst) - min(burst)
        scale = target_p2p / burst_p2p if burst_p2p > 0 else 0.0
        burst = [value * scale for value in burst]
        peak = max(abs(value) for value in burst) or 1.0
        if peak > 14.0:
            burst = [value * (14.0 / peak) for value in burst]

        values_mv = []
        for sample_index in range(N_SAMPLES):
            if sample_index < EVENT_START:
                values_mv.append(baseline[sample_index])
            else:
                values_mv.append(baseline[sample_index] + burst[sample_index])

        values_mv = [clamp(value, -14.0, 14.0) for value in values_mv]
        counts = to_counts(values_mv)
        full_signals[node] = counts
        event_stats = stats(values_mv[EVENT_START:EVENT_END])
        nodes[node] = {
            "online": True,
            "p2p": event_stats["p2p"],
            "rms": event_stats["rms"],
            "peak": event_stats["peak"],
            "unit": "counts",
            "adc_bits": 24,
            "adc_signed": True,
            "adc_full_scale": COUNTS_FULL_SCALE,
            "mv_per_count": MV_PER_COUNT,
            "counts_per_mv": COUNTS_PER_MV,
            "signal": counts,
            "sample_period_us": SAMPLE_PERIOD_US,
            "sample_count": len(counts),
            "aktivitas": "TERDETEKSI GETARAN",
            "gps": {
                "lat": info["lat"],
                "lon": info["lon"],
                "fix": True,
                "satellites": 9,
                "hdop": 1.1,
            },
        }

    distances = {
        node: math.hypot(event_local["x"] - x, event_local["y"] - y)
        for node, (x, y) in local_positions.items()
    }
    delays = {node: distances[node] / WAVE_SPEED for node in NODES}
    payload = {
        "timestamp": int(time.time()),
        "source": "spike_event_500hz",
        "mock": True,
        "persist": True,
        "stream": False,
        "broadcast": False,
        "silent": True,
        "event": True,
        "event_id": EVENT_ID,
        "status": "WASPADA",
        "aktivitas": "TERDETEKSI GETARAN",
        "ml_status": "TERDETEKSI GETARAN",
        "p2p_amplitude": max(node["p2p"] for node in nodes.values()),
        "rms": max(node["rms"] for node in nodes.values()),
        "sampling_rate": RATE,
        "sample_period_us": SAMPLE_PERIOD_US,
        "duration_s": DURATION_S,
        "baseline_s": BASELINE_S,
        "event_s": EVENT_S,
        "adc_bits": 24,
        "adc_full_scale": COUNTS_FULL_SCALE,
        "mv_per_count": MV_PER_COUNT,
        "nodes": nodes,
        "location": {
            "x": event_local["x"],
            "y": event_local["y"],
            "latitude": event_lat,
            "longitude": event_lon,
            "estimated_latitude": event_lat,
            "estimated_longitude": event_lon,
            "estimated_valid": True,
            "tdoa": {
                "dt12_ms": round((delays["geo_2"] - delays["geo_1"]) * 1000.0, 3),
                "dt13_ms": round((delays["geo_3"] - delays["geo_1"]) * 1000.0, 3),
                "quality_12": 0.93,
                "quality_13": 0.91,
            },
            "direction": {
                "bearing_degrees": round(
                    (math.degrees(math.atan2(event_local["x"], event_local["y"])) + 360.0) % 360.0,
                    1,
                ),
                "compass": "Tenggara",
                "source": "location.x_y",
            },
        },
    }
    return payload, full_signals


def write_outputs(payload, full_signals):
    OUTPUT_JSON.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    rows = []
    for node, counts in full_signals.items():
        start = EVENT_START + 128
        window = counts[start:start + 256]
        if len(window) < 256:
            window = (window + [0] * 256)[:256]
        row = {"node": node, "event_id": EVENT_ID, "status": payload["status"]}
        for index, value in enumerate(window):
            row[f"col_{index}"] = value
        rows.append(row)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def load_env():
    if dotenv_values is None or not ENV_PATH.is_file():
        return {}
    return {key: value for key, value in dotenv_values(ENV_PATH).items() if value is not None}


def connect_mqtt():
    if mqtt is None:
        raise SystemExit("paho-mqtt belum terpasang.")
    env = load_env()
    broker = env.get("MQTT_BROKER", "").strip()
    if not broker:
        raise SystemExit("MQTT_BROKER tidak ada di processing/.env")
    port = int(env.get("MQTT_PORT", "8883"))
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"geosense_spike_500hz_{os.getpid()}",
    )
    client.username_pw_set(env.get("MQTT_USERNAME", ""), env.get("MQTT_PASSWORD", ""))
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
    client.connect(broker, port, keepalive=30)
    client.loop_start()

    # Tunggu CONNACK sebelum publish. Tanpa ini paket QoS 0/1 pertama bisa
    # hilang saat TLS handshake masih berjalan, sehingga dashboard tidak update.
    deadline = time.time() + 10
    while not client.is_connected() and time.time() < deadline:
        time.sleep(0.1)
    if not client.is_connected():
        client.loop_stop()
        raise SystemExit("MQTT gagal terhubung ke broker dalam 10 detik.")
    return client


def publish_payload(client, payload, retain=False):
    info = client.publish(
        "rawdata",
        json.dumps(payload, separators=(",", ":")),
        qos=1,
        retain=retain,
    )
    try:
        info.wait_for_publish(timeout=3)
    except Exception as exc:
        # HiveMQ free-tier can briefly reconnect; do not stall the timeline.
        print("publish warning:", exc)
        return False
    return info.rc == mqtt.MQTT_ERR_SUCCESS


def post_backend_baseline(payload, backend="http://127.0.0.1:8000"):
    """HTTP fallback for the final idle state when MQTT reconnects mid-stream."""
    if dotenv_values is None or not BACKEND_ENV_PATH.is_file():
        return False
    env = {key: value for key, value in dotenv_values(BACKEND_ENV_PATH).items() if value is not None}
    token = env.get("MOCK_TRIGGER_TOKEN", "").strip()
    if not token:
        return False
    request = urllib.request.Request(
        backend.rstrip("/") + "/api/mock/baseline",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Mock-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError) as exc:
        print("backend baseline warning:", exc)
        return False


def publish_stream(client, payload):
    nodes = payload["nodes"]
    generation = int(time.time())
    chunk_samples = int(RATE * STREAM_CHUNK_S)
    event_end = EVENT_START + int(EVENT_S * RATE)
    total_samples = N_SAMPLES

    seq = 0
    for start in range(0, total_samples, chunk_samples):
        end = min(total_samples, start + chunk_samples)
        event_mode = EVENT_START <= start < event_end

        chunk_nodes = {}
        p2p_values = []
        rms_values = []
        for node, record in nodes.items():
            values = record["signal"][start:end]
            p2p = max(values) - min(values)
            rms = math.sqrt(sum(value * value for value in values) / len(values))
            p2p_values.append(p2p * MV_PER_COUNT)
            rms_values.append(rms * MV_PER_COUNT)
            chunk_nodes[node] = {
                **{key: value for key, value in record.items() if key != "signal"},
                "signal": values,
                "p2p": round(p2p * MV_PER_COUNT, 4),
                "rms": round(rms * MV_PER_COUNT, 4),
                "peak": round(max(abs(value) for value in values) * MV_PER_COUNT, 4),
                "sample_count": len(values),
            }

        chunk = {
            "timestamp": int(time.time()),
            "source": payload["source"],
            "mock": True,
            "persist": False,
            "stream": True,
            "stream_generation": generation,
            "chunk_seq": seq,
            "silent": True,
            "event": event_mode,
            "event_id": payload["event_id"] if event_mode else None,
            "status": "WASPADA" if event_mode else "AMAN",
            "aktivitas": "TERDETEKSI GETARAN" if event_mode else "diem",
            "p2p_amplitude": round(max(p2p_values), 4),
            "rms": round(max(rms_values), 4),
            "sampling_rate": RATE,
            "sample_period_us": SAMPLE_PERIOD_US,
            "nodes": chunk_nodes,
        }
        if event_mode:
            chunk["location"] = payload["location"]
            chunk["clear_location"] = False
        else:
            chunk["clear_location"] = True
        publish_payload(client, chunk)
        label = "SPIKE" if event_mode else "IDLE"
        print(
            "stream %02d  %s  p2p=%.2f mV"
            % (seq + 1, label, chunk["p2p_amplitude"])
        )
        seq += 1
        time.sleep(STREAM_CHUNK_S)

    # Persist the complete event after its live chunks have been broadcast.
    snapshot = dict(payload)
    snapshot.update(
        {
            "stream": True,
            "stream_generation": generation,
            "chunk_seq": (event_end - 1) // chunk_samples,
            "broadcast": False,
        }
    )
    publish_payload(client, snapshot)
    print("event snapshot persisted; switching to continuous idle (Ctrl+C to stop)")

    # Continue sending fresh low-noise idle data indefinitely. A new spike
    # happens only when the user stops and starts this command again.
    idle_seq = seq
    idle_chunk_count = max(1, int(RATE * STREAM_CHUNK_S))
    idle_rngs = {node: random.Random() for node in nodes}
    while True:
        chunk_nodes = {}
        p2p_values = []
        rms_values = []
        for index, (node, record) in enumerate(nodes.items()):
            idle_mv = baseline_signal(idle_rngs[node], idle_chunk_count, index * 0.8)
            values = to_counts(idle_mv)
            p2p = (max(values) - min(values)) * MV_PER_COUNT
            rms = math.sqrt(sum(value * value for value in values) / len(values)) * MV_PER_COUNT
            p2p_values.append(p2p)
            rms_values.append(rms)
            chunk_nodes[node] = {
                **{key: value for key, value in record.items() if key != "signal"},
                "signal": values,
                "p2p": round(p2p, 4),
                "rms": round(rms, 4),
                "peak": round(max(abs(value) for value in values) * MV_PER_COUNT, 4),
                "sample_count": len(values),
                "aktivitas": "diem",
            }

        idle_payload = {
            "timestamp": int(time.time()),
            "source": payload["source"],
            "mock": True,
            "persist": False,
            "stream": True,
            "stream_generation": generation,
            "chunk_seq": idle_seq,
            "silent": True,
            "event": False,
            "event_id": None,
            "clear_location": True,
            "status": "AMAN",
            "aktivitas": "diem",
            "p2p_amplitude": round(max(p2p_values), 4),
            "rms": round(max(rms_values), 4),
            "sampling_rate": RATE,
            "sample_period_us": SAMPLE_PERIOD_US,
            "nodes": chunk_nodes,
        }
        publish_payload(client, idle_payload)
        if idle_seq % 10 == 0:
            print("IDLE terus | p2p=%.4f mV" % idle_payload["p2p_amplitude"])
        idle_seq += 1
        time.sleep(STREAM_CHUNK_S)


def main():
    global EVENT_ID
    parser = argparse.ArgumentParser(description="Publish one 500 Hz spike, then continuous idle")
    parser.add_argument("--generate-only", action="store_true", help="hanya tulis JSON/CSV")
    parser.add_argument("--publish", action="store_true", help="publish ke MQTT rawdata (streaming default)")
    parser.add_argument("--stream", action="store_true", help="kompatibilitas: publish streaming")
    parser.add_argument("--snapshot", action="store_true", help="kirim satu snapshot statis (bukan streaming)")
    parser.add_argument("--delay", type=float, default=2.0, help="jeda sebelum publish")
    args = parser.parse_args()

    if args.generate_only or not args.publish:
        payload, full_signals = build_data()
        write_outputs(payload, full_signals)
        print("generated:", OUTPUT_JSON)
        print("generated:", OUTPUT_CSV)
        for node, record in payload["nodes"].items():
            print(
                "%s p2p=%.2f mV rms=%.2f mV peak=%.2f mV counts=%d..%d"
                % (node, record["p2p"], record["rms"], record["peak"],
                   min(record["signal"]), max(record["signal"]))
            )
        print("TDOA:", payload["location"]["x"], payload["location"]["y"])
        return

    if args.delay > 0:
        time.sleep(args.delay)
    client = connect_mqtt()
    try:
        if args.snapshot:
            payload, _ = build_data()
            payload["broadcast"] = True
            publish_payload(client, payload)
            print("static event snapshot published: p2p=%.2f mV" % payload["p2p_amplitude"])
            return

        print("Single run: 5 s IDLE -> 10 s SPIKE -> continuous IDLE")
        EVENT_ID = "spike-500hz-%s" % time.strftime("%Y%m%d-%H%M%S")
        payload, full_signals = build_data()
        write_outputs(payload, full_signals)
        print("event_id=%s | TDOA=(%s, %s)" % (
            EVENT_ID, payload["location"]["x"], payload["location"]["y"]
        ))
        publish_stream(client, payload)
    except KeyboardInterrupt:
        print("\nMock stopped by user.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
