#!/usr/bin/env python3
"""Live 3-node mock waveform viewer for GeoSense Processing.

This runs entirely in the processing folder:
    MQTT rawdata -> this script -> matplotlib

No backend and no frontend are required.

Examples:
    python live_mock_plot.py
    python live_mock_plot.py --seconds 20 --save mock_plot.png
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import threading
import time
from collections import deque
from pathlib import Path

import matplotlib
import numpy as np

try:
    import paho.mqtt.client as mqtt
except ImportError:
    raise SystemExit("paho-mqtt belum terpasang.")

try:
    from dotenv import load_dotenv, dotenv_values
except ImportError:
    load_dotenv = None
    dotenv_values = None

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

NODES = ("geo_1", "geo_2", "geo_3")
COLORS = {"geo_1": "#16a34a", "geo_2": "#d97706", "geo_3": "#0284c7"}
MAX_SAMPLES = 5000
RATE_HZ = 500.0
HEARTBEAT_TOPIC = "geosense/processing/viewer_heartbeat"

buffers = {node: deque(maxlen=MAX_SAMPLES) for node in NODES}
latest = {node: {"p2p": 0.0, "rms": 0.0, "peak": 0.0} for node in NODES}
state = {"status": "MENUNGGU", "source": "", "location": None, "last": 0.0}
lock = threading.Lock()


def env_values():
    if load_dotenv is not None and ENV_PATH.is_file():
        load_dotenv(ENV_PATH)
    if dotenv_values is not None and ENV_PATH.is_file():
        return {k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None}
    return dict(os.environ)


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"MQTT connected rc={reason_code}; subscribe rawdata")
    client.subscribe("rawdata", qos=1)


def on_message(client, userdata, message):
    try:
        payload = json.loads(message.payload.decode("utf-8", "replace"))
    except ValueError:
        return

    if not isinstance(payload, dict):
        return

    with lock:
        state["status"] = str(payload.get("status") or state["status"])
        state["source"] = str(payload.get("status_source") or state["source"])
        state["location"] = payload.get("location")
        state["last"] = time.time()

        nodes = payload.get("nodes")
        if not isinstance(nodes, dict):
            return
        for node, record in nodes.items():
            if node not in buffers or not isinstance(record, dict):
                continue
            signal = record.get("signal")
            if isinstance(signal, list) and signal:
                buffers[node].extend(signal)
            for key in ("p2p", "rms", "peak"):
                try:
                    latest[node][key] = float(record.get(key) or 0.0)
                except (TypeError, ValueError):
                    pass


def main():
    parser = argparse.ArgumentParser(description="Live mock waveform viewer")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="durasi tampil; 0 = terus menerus")
    parser.add_argument("--save", default="", help="simpan PNG terakhir")
    parser.add_argument("--interval", type=int, default=200, help="update ms")
    args = parser.parse_args()

    if args.save:
        matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import matplotlib.animation as animation

    values = env_values()
    broker = values.get("MQTT_BROKER", "").strip()
    port = int(values.get("MQTT_PORT", "8883"))
    tls = int(values.get("MQTT_TLS", "1")) == 1
    if not broker:
        raise SystemExit("MQTT_BROKER tidak ada di processing/.env")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="geosense_mock_plot")
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id="geosense_mock_plot")
    if values.get("MQTT_USERNAME"):
        client.username_pw_set(values.get("MQTT_USERNAME"), values.get("MQTT_PASSWORD", ""))
    if tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker, port, 30)
    client.loop_start()

    stop_heartbeat = threading.Event()

    def heartbeat_loop():
        while not stop_heartbeat.is_set():
            client.publish(HEARTBEAT_TOPIC, "1", qos=0)
            stop_heartbeat.wait(2.0)

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    fig, axes = plt.subplots(3, 1, figsize=(11, 7), sharex=True)
    fig.suptitle("GeoSense Processing", fontsize=13, fontweight="bold")
    lines = {}
    stats_text = {}
    for axis, node in zip(axes, NODES):
        (line,) = axis.plot([], [], lw=1.1, color=COLORS[node], label=node)
        lines[node] = line
        axis.set_ylim(-15, 15)
        axis.set_xlim(0, MAX_SAMPLES / RATE_HZ)
        axis.set_ylabel("mV")
        axis.grid(True, linestyle=":", alpha=0.5)
        axis.legend(loc="upper right", fontsize=8)
        stats_text[node] = axis.text(
            0.01, 0.95, "", transform=axis.transAxes, va="top", fontsize=8,
            bbox=dict(facecolor="white", alpha=0.75, edgecolor="none"),
        )

    def update(_frame):
        with lock:
            status = state["status"]
            source = state["source"]
            location = state["location"]
            for node, line in lines.items():
                values_list = np.asarray(buffers[node], dtype=float)
                line.set_data(np.arange(len(values_list)) / RATE_HZ, values_list)
                stats = latest[node]
                stats_text[node].set_text(
                    f"{node}  p2p={stats['p2p']:.2f}  rms={stats['rms']:.2f}  peak={stats['peak']:.2f}"
                )
        axes[0].set_title(
            f"Status: {status} | {source or '-'} | "
            f"TDOA: {'ada' if location else 'tidak ada'}",
            fontsize=10,
        )
        return [*lines.values(), *stats_text.values()]

    anim = animation.FuncAnimation(
        fig, update, interval=args.interval, blit=False, cache_frame_data=False
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))

    try:
        if args.seconds > 0:
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                update(0)
                fig.canvas.draw()
                if args.save:
                    time.sleep(0.1)
                else:
                    plt.pause(0.1)
            if args.save:
                fig.savefig(args.save, dpi=130)
                print(f"Saved: {args.save}")
        else:
            plt.show()
        _ = anim
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2.0)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
