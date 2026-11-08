#!/usr/bin/env python3
"""Create static dummy data for the GeoSense ML mockup.

The data is written to mock_ml_data.json. It does not publish MQTT, does not
touch the backend, and does not touch the frontend.
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
PROCESSING_DIR = BASE_DIR.parent
DATASET = PROCESSING_DIR / "data" / "dataset_geophone_gabungan.csv"
OUTPUT = BASE_DIR / "mock_ml_data.json"
WINDOW_CSV = BASE_DIR / "mock_ml_windows.csv"

SAMPLE_RATE = 90.0
SAMPLE_PERIOD_US = 1_000_000.0 / SAMPLE_RATE

# Local metric frame, origin at Gedung PPBS.
# x = east (m), y = north (m). Negative y is south.
NODES = {
    "geo_1": {
        "name": "Gedung PPBS",
        "lat": -6.9233875,
        "lon": 107.7731538,
        "local": [0.0, 0.0],
    },
    "geo_2": {
        "name": "Parking Jembatan Cincin",
        "lat": -6.9244170,
        "lon": 107.7738624,
        "local": [78.3, -113.8],
    },
    "geo_3": {
        "name": "Lapangan Merah",
        "lat": -6.9253700,
        "lon": 107.7742400,
        "local": [120.0, -219.1],
    },
}

# Source of the simulated vibration, between PPBS and Lapangan Merah.
SOURCE_LOCAL = [60.0, -100.0]
WAVE_SPEED = 300.0
EVENT_UTC = "2026-10-27T09:15:42.000Z"


def local_to_gps(x: float, y: float) -> tuple[float, float]:
    origin = NODES["geo_1"]
    lat = origin["lat"] + y / 110540.0
    lon = origin["lon"] + x / (111320.0 * math.cos(math.radians(origin["lat"])))
    return lat, lon


def event_wave(samples: int, delay_s: float, seed: int, amplitude: float = 500.0) -> list[float]:
    rng = random.Random(seed)
    center_s = (samples / 2.0) / SAMPLE_RATE
    values = []
    for index in range(samples):
        t = index / SAMPLE_RATE - delay_s
        pulse = math.exp(-((t - center_s) ** 2) / 0.0008)
        wave = amplitude * math.sin(2.0 * math.pi * 7.0 * t) * pulse
        wave += rng.uniform(-0.02 * amplitude, 0.02 * amplitude)
        values.append(round(wave, 6))
    return values


def stats(values: list[float]) -> dict:
    p2p = max(values) - min(values)
    rms = math.sqrt(sum(value * value for value in values) / len(values))
    peak = max(abs(value) for value in values)
    return {
        "p2p": round(p2p, 6),
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "unit": "mV",
    }


def load_normal_rows() -> dict[str, list[float]]:
    if not DATASET.is_file():
        raise SystemExit(f"Dataset tidak ditemukan: {DATASET}")

    frame = pd.read_csv(DATASET)
    columns = [column for column in frame.columns if column.startswith("col_")]
    if len(columns) != 256:
        raise SystemExit(f"Dataset harus punya 256 kolom col_*, ditemukan {len(columns)}")

    # Rows with very high confidence for the 'diem' class.
    selected = [80, 79, 72]
    samples = {}
    for node, row_index in zip(("geo_1", "geo_2", "geo_3"), selected):
        values = frame.iloc[row_index][columns].astype(float).tolist()
        samples[node] = [round(value, 6) for value in values]
    return samples


def main() -> None:
    normal_samples = load_normal_rows()

    event_samples = {}
    event_meta = {}
    for index, (node, info) in enumerate(NODES.items()):
        distance = math.hypot(
            SOURCE_LOCAL[0] - info["local"][0],
            SOURCE_LOCAL[1] - info["local"][1],
        )
        delay = distance / WAVE_SPEED
        values = event_wave(1024, delay, seed=index + 1, amplitude=300.0)
        event_samples[node] = values
        event_meta[node] = {
            "distance_m": round(distance, 3),
            "delay_ms": round(delay * 1000.0, 3),
            **stats(values),
        }

    source_lat, source_lon = local_to_gps(*SOURCE_LOCAL)
    data = {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sample_rate": SAMPLE_RATE,
            "sample_period_us": round(SAMPLE_PERIOD_US, 6),
            "wave_speed_m_s": WAVE_SPEED,
            "note": "Data statis untuk mockup ML lokal. Tidak publish MQTT.",
        },
        "nodes": NODES,
        "normal": {
            "label": "Getaran rendah / kondisi normal",
            "utc": EVENT_UTC,
            "trigger_index": 128,
            "samples": normal_samples,
            "stats": {node: stats(values) for node, values in normal_samples.items()},
            "expected_activity": "diem",
            "expected_status": "AMAN",
        },
        "event": {
            "label": "Getaran di area antara Gedung PPBS dan Lapangan Merah",
            "utc": EVENT_UTC,
            "trigger_index": 512,
            "samples": event_samples,
            "stats": event_meta,
            "source_local": SOURCE_LOCAL,
            "source_lat": round(source_lat, 7),
            "source_lon": round(source_lon, 7),
            "expected_activity": "jalan",
            "expected_status": "SIAGA",
        },
    }

    OUTPUT.write_text(json.dumps(data, indent=2), encoding="utf-8")

    rows = []
    for scenario_name in ("normal", "event"):
        scenario = data[scenario_name]
        start = max(0, scenario["trigger_index"] - 128)
        for node, values in scenario["samples"].items():
            window = values[start:start + 256]
            if len(window) < 256:
                window = (window + [0.0] * 256)[:256]
            row = {
                "scenario": scenario_name,
                "node": node,
                "expected_activity": scenario["expected_activity"],
                "expected_status": scenario["expected_status"],
            }
            for index, value in enumerate(window):
                row[f"col_{index}"] = value
            rows.append(row)
    pd.DataFrame(rows).to_csv(WINDOW_CSV, index=False)

    print(f"Data statis dibuat: {OUTPUT}")
    print(f"Window ML dibuat  : {WINDOW_CSV}")
    print(f"Normal p2p max: {max(item['p2p'] for item in data['normal']['stats'].values()):.4f}")
    print(f"Event p2p max : {max(item['p2p'] for item in data['event']['stats'].values()):.4f}")
    print(f"Event GPS     : {source_lat:.7f}, {source_lon:.7f}")


if __name__ == "__main__":
    main()
