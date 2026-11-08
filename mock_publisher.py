#!/usr/bin/env python3
"""Standalone MQTT mock publisher for the processing live plot.

No backend and no frontend are needed. Run this in one terminal and
``live_mock_plot.py`` in another.

Timeline is one-shot:
    0-5 s   baseline
    5-15 s  spike/burst
    15+ s   dynamic idle baseline (no repeat spike)
"""

from __future__ import annotations

import json
import math
import random
import ssl
import threading
import time

import paho.mqtt.client as mqtt
from dotenv import dotenv_values

RATE = 500.0
CHUNK = 500
INTERVAL = 1.0
BASELINE_SECONDS = 5.0
EVENT_SECONDS = 10.0
HEARTBEAT_TOPIC = "geosense/processing/viewer_heartbeat"
NODES = {
    "geo_1": ("NODE01", -6.9253400, 107.7741200),
    "geo_2": ("NODE02", -6.9254900, 107.7744000),
    "geo_3": ("NODE03", -6.9252000, 107.7743000),
}
EVENT_LOCATION = {
    "x": 15.5,
    "y": 4.4,
    "latitude": -6.9253000,
    "longitude": 107.7742600,
    "estimated_latitude": -6.9253000,
    "estimated_longitude": 107.7742600,
    "estimated_valid": True,
    "direction": {"bearing_degrees": 74.1, "compass": "Timur Laut", "source": "location.x_y"},
}


def baseline(seed):
    rng = random.Random(seed)
    return rng.uniform(-0.5, 0.5)


def event(seed, elapsed):
    rng = random.Random(seed)
    if elapsed < 0:
        return baseline(seed)
    attack = min(1.0, max(0.0, elapsed / 2.0))
    decay = 0.5 + 0.5 * math.exp(-elapsed / 5.0)
    burst = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(2 * math.pi * 2.1 * elapsed))
    carrier = (
        0.5 * math.sin(2 * math.pi * 11.0 * elapsed)
        + 0.28 * math.sin(2 * math.pi * 29.0 * elapsed)
        + 0.18 * math.sin(2 * math.pi * 71.0 * elapsed)
    )
    jagged = rng.uniform(-0.75, 0.75)
    if rng.random() < 0.05:
        jagged += rng.choice((-1.0, 1.0)) * rng.uniform(0.8, 2.0)
    value = 14.0 * 0.42 * attack * decay * burst * (carrier + jagged)
    return max(-14.0, min(14.0, value))


def stats(values):
    p2p = max(values) - min(values)
    rms = math.sqrt(sum(v * v for v in values) / len(values))
    return round(p2p, 4), round(rms, 4), round(max(abs(v) for v in values), 4)


def payload(event_mode, elapsed, seq):
    nodes = {}
    p2p_all = []
    rms_all = []
    for index, (node, (_fw, lat, lon)) in enumerate(NODES.items()):
        values = []
        for i in range(CHUNK):
            seed = int(time.time() * 1000) + index * 100000 + i
            values.append(
                round(
                    event(seed, elapsed + i / RATE)
                    if event_mode
                    else baseline(seed),
                    4,
                )
            )
        p2p, rms, peak = stats(values)
        p2p_all.append(p2p)
        rms_all.append(rms)
        nodes[node] = {
            "online": True,
            "p2p": p2p,
            "rms": rms,
            "peak": peak,
            "unit": "mV",
            "signal": values,
            "sample_period_us": 2000.0,
            "aktivitas": "jalan" if event_mode else "diem",
            "gps": {"latitude": lat, "longitude": lon, "fix": True, "satellites": 8, "hdop": 1.2},
        }
    data = {
        "timestamp": int(time.time()),
        "source": "processing_mock",
        "mock": True,
        "persist": False,
        "stream": True,
        "stream_generation": 1,
        "chunk_seq": seq,
        "sampling_rate": RATE,
        "status": "WASPADA" if event_mode else "AMAN",
        "aktivitas": "jalan" if event_mode else "diem",
        "p2p_amplitude": max(p2p_all),
        "rms": max(rms_all),
        "silent": True,
        "clear_location": not event_mode,
        "event": event_mode,
        "nodes": nodes,
    }
    if event_mode:
        data["location"] = EVENT_LOCATION
        data["event_id"] = "processing-mock-%s" % int(time.time())
    return data


def main():
    env = dotenv_values(r"C:\geosense-processing\.env")
    broker = env["MQTT_BROKER"]
    port = int(env.get("MQTT_PORT", "8883"))
    viewer_ready = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties=None):
        print(f"MQTT connected rc={reason_code}; subscribe {HEARTBEAT_TOPIC}")
        client.subscribe(HEARTBEAT_TOPIC, qos=0)

    def on_message(client, userdata, message):
        if message.topic == HEARTBEAT_TOPIC:
            viewer_ready.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="geosense_processing_mock")
    client.username_pw_set(env["MQTT_USERNAME"], env["MQTT_PASSWORD"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker, port, 30)
    client.loop_start()
    print(f"Publishing mock stream to {broker}:{port} rawdata")
    print("Waiting for live_mock_plot.py; spike starts 5s after the viewer connects.")
    scheduled_start = None
    seq = 0
    try:
        while True:
            now = time.monotonic()
            if scheduled_start is None and viewer_ready.is_set():
                scheduled_start = now + BASELINE_SECONDS
                print("Viewer ready; one-shot spike starts in 5 seconds.")
            if scheduled_start is None:
                event_mode = False
                elapsed = -1.0
            else:
                elapsed = now - scheduled_start
                event_mode = 0.0 <= elapsed < EVENT_SECONDS
            data = payload(event_mode, elapsed, seq)
            seq += 1
            info = client.publish("rawdata", json.dumps(data, separators=(",", ":")), qos=1)
            info.wait_for_publish(timeout=10)
            label = "WAITING" if scheduled_start is None else "EVENT" if event_mode else "idle"
            print(
                f"{time.strftime('%H:%M:%S')} seq={seq} "
                f"{label} p2p={data['p2p_amplitude']:.2f} mV"
            )
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
