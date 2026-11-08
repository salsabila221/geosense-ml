#!/usr/bin/env python3
"""Plot the static GeoSense mock data locally.

This reads mock_ml_data.json and renders 3 node waveforms for the normal and
event scenarios. It does not use MQTT, backend, or frontend.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = BASE_DIR / "mock_ml_data.json"
DEFAULT_RESULT = BASE_DIR / "mock_ml_output.json"
DEFAULT_OUT = BASE_DIR / "mock_waveforms.png"

NODES = ("geo_1", "geo_2", "geo_3")
COLORS = {"geo_1": "#16a34a", "geo_2": "#d97706", "geo_3": "#0284c7"}


def stats(values: list[float]) -> dict:
    arr = np.asarray(values, dtype=float)
    return {
        "p2p": float(np.ptp(arr)),
        "rms": float(np.sqrt(np.mean(arr**2))),
        "peak": float(np.max(np.abs(arr))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot GeoSense mock waveforms")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="mock_ml_data.json")
    parser.add_argument("--result", default=str(DEFAULT_RESULT), help="mock_ml_output.json")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output PNG")
    args = parser.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    result = {}
    result_path = Path(args.result)
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))

    rate = float(data["meta"]["sample_rate"])
    fig, axes = plt.subplots(2, 3, figsize=(15, 7), sharex="row")
    fig.suptitle(
        "GeoSense Mock Data - 3 Node Geofon\n"
        "Atas: kondisi normal | Bawah: event getaran",
        fontsize=13,
        fontweight="bold",
    )

    scenarios = (("normal", "Normal / Aman"), ("event", "Event Getaran"))
    for row, (key, label) in enumerate(scenarios):
        scenario = data[key]
        trigger_index = int(scenario.get("trigger_index", 0))
        for col, node in enumerate(NODES):
            ax = axes[row][col]
            values = scenario["samples"][node]
            t = np.arange(len(values)) / rate
            ax.plot(t, values, color=COLORS[node], linewidth=0.9)
            ax.axhline(0, color="#94a3b8", linewidth=0.6, alpha=0.7)
            if key == "event":
                ax.axvline(
                    trigger_index / rate,
                    color="#dc2626",
                    linestyle="--",
                    linewidth=1,
                    alpha=0.8,
                    label="trigger",
                )
            s = scenario.get("stats", {}).get(node) or stats(values)
            ax.set_title(
                f"Geofon {node[-1]} | p2p {s['p2p']:.2f} mV | "
                f"RMS {s['rms']:.2f} | PEAK {s['peak']:.2f}",
                fontsize=9,
                fontweight="bold",
            )
            ax.set_xlabel("Waktu (s)", fontsize=8)
            ax.set_ylabel("Amplitudo (mV)", fontsize=8)
            ax.tick_params(labelsize=7)
            ax.grid(True, linestyle=":", alpha=0.4)
            if key == "event":
                ax.legend(fontsize=7, loc="upper right")
        fig.text(
            0.01,
            0.52 - row * 0.47,
            label,
            rotation=90,
            va="center",
            fontsize=10,
            fontweight="bold",
            color="#16425b",
        )

    if result:
        event_activity = (result.get("event") or {}).get("nodes", {}).get("geo_1", {})
        normal_activity = (result.get("normal") or {}).get("nodes", {}).get("geo_1", {})
        location = result.get("event_location") or {}
        info = (
            f"ML normal: {normal_activity.get('activity', '-')} "
            f"({float(normal_activity.get('confidence', 0)) * 100:.1f}%) | "
            f"ML event: {event_activity.get('activity', '-')} "
            f"({float(event_activity.get('confidence', 0)) * 100:.1f}%)"
        )
        if location:
            info += (
                f" | TDOA: {location.get('lat', '-')}, {location.get('lon', '-')}"
                f" (dekat {location.get('nearest_name', '-')})"
            )
        fig.text(0.5, 0.01, info, ha="center", fontsize=8, color="#475569")

    fig.tight_layout(rect=(0.03, 0.04, 1, 0.93))
    out_path = Path(args.out)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Plot disimpan: {out_path}")


if __name__ == "__main__":
    main()
