#!/usr/bin/env python3
"""Run the GeoSense ML model against static mock data.

This script is intentionally local-only:
- no MQTT publish
- no backend request
- no frontend change

It writes mock_ml_output.json and mock_ml_report.html.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
import warnings
import webbrowser
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
PROCESSING_DIR = BASE_DIR.parent
sys.path.insert(0, str(PROCESSING_DIR))

from event_tdoa import TdoaConfig, TdoaEngine  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)

DEFAULT_DATA = BASE_DIR / "mock_ml_data.json"


def default_model_path() -> Path:
    candidates = [
        BASE_DIR / "models" / "geosense_model.pkl",
        PROCESSING_DIR / "models" / "geosense_model.pkl",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


DEFAULT_MODEL = default_model_path()
OUTPUT_JSON = BASE_DIR / "mock_ml_output.json"
OUTPUT_HTML = BASE_DIR / "mock_ml_report.html"

NODE_TO_FIRMWARE = {
    "geo_1": "NODE01",
    "geo_2": "NODE02",
    "geo_3": "NODE03",
}


def status_from_p2p(p2p: float) -> str:
    if p2p >= 400.0:
        return "WASPADA"
    if p2p >= 150.0:
        return "SIAGA"
    return "AMAN"


def local_to_gps(data: dict, x: float, y: float) -> tuple[float, float]:
    origin = data["nodes"]["geo_1"]
    lat = origin["lat"] + y / 110540.0
    lon = origin["lon"] + x / (111320.0 * math.cos(math.radians(origin["lat"])))
    return lat, lon


def predict_window(model, samples: list[float], trigger_index: int) -> dict:
    start = max(0, trigger_index - 128)
    window = samples[start:start + 256]
    if len(window) < 256:
        window = (window + [0.0] * 256)[:256]
    prediction = str(model.predict([window])[0])
    probabilities = model.predict_proba([window])[0]
    classes = [str(item) for item in model.classes_]
    confidence = float(max(probabilities))
    return {
        "activity": prediction,
        "confidence": round(confidence, 4),
        "probabilities": {
            label: round(float(probabilities[index]), 4)
            for index, label in enumerate(classes)
        },
        "window_start": start,
    }


def analyze_scenario(model, data: dict, scenario_name: str) -> dict:
    scenario = data[scenario_name]
    nodes = {}
    for node, samples in scenario["samples"].items():
        nodes[node] = {
            "name": data["nodes"][node]["name"],
            "gps": {
                "lat": data["nodes"][node]["lat"],
                "lon": data["nodes"][node]["lon"],
            },
            "stats": scenario["stats"][node],
            **predict_window(model, samples, scenario["trigger_index"]),
        }

    max_p2p = max(item["stats"]["p2p"] for item in nodes.values())
    return {
        "label": scenario["label"],
        "utc": scenario["utc"],
        "expected_activity": scenario["expected_activity"],
        "expected_status": scenario["expected_status"],
        "status": status_from_p2p(max_p2p),
        "max_p2p": round(max_p2p, 4),
        "nodes": nodes,
    }


def run_tdoa(data: dict) -> dict | None:
    scenario = data["event"]
    cfg = TdoaConfig.from_env()
    cfg.positions = {
        node: np.array(info["local"], dtype=float)
        for node, info in data["nodes"].items()
    }
    cfg.bounds_m = 500.0
    cfg.window_samples = 512
    cfg.preroll_samples = 180
    cfg.max_lag_s = 0.6

    engine = TdoaEngine(cfg)
    result = None
    for node, samples in scenario["samples"].items():
        payload = {
            "type": "event",
            "n": NODE_TO_FIRMWARE[node],
            "event_id": "mock-event-001",
            "utc": scenario["utc"],
            "samples": samples,
            "trigger_index": scenario["trigger_index"],
            "sample_period_us": data["meta"]["sample_period_us"],
            "p2p": scenario["stats"][node]["p2p"],
            "rms": scenario["stats"][node]["rms"],
            "peak": scenario["stats"][node]["peak"],
            "unit": scenario["stats"][node]["unit"],
        }
        event = engine.parse_event(payload)
        if event is None:
            continue
        result = engine.add_event(event) or result

    if not result or "location" not in result:
        return None

    location = result["location"]
    x = float(location["x"])
    y = float(location["y"])
    lat, lon = local_to_gps(data, x, y)
    nearest = min(
        data["nodes"].items(),
        key=lambda item: math.hypot(
            x - item[1]["local"][0],
            y - item[1]["local"][1],
        ),
    )
    distance = math.hypot(
        x - nearest[1]["local"][0],
        y - nearest[1]["local"][1],
    )
    tdoa = location.get("tdoa") or {}
    return {
        "x": round(x, 3),
        "y": round(y, 3),
        "lat": round(lat, 7),
        "lon": round(lon, 7),
        "nearest_node": nearest[0],
        "nearest_name": nearest[1]["name"],
        "nearest_distance_m": round(distance, 2),
        "dt12_ms": round(float(tdoa.get("dt12_ms", 0.0)), 3),
        "dt13_ms": round(float(tdoa.get("dt13_ms", 0.0)), 3),
        "quality_12": round(float(tdoa.get("quality_12", 0.0)), 4),
        "quality_13": round(float(tdoa.get("quality_13", 0.0)), 4),
    }


def build_map_svg(data: dict, event_location: dict | None) -> str:
    width, height = 640, 360
    pad = 44
    xs = [info["local"][0] for info in data["nodes"].values()]
    ys = [info["local"][1] for info in data["nodes"].values()]
    if event_location:
        xs.append(event_location["x"])
        ys.append(event_location["y"])
    min_x, max_x = min(xs) - 20, max(xs) + 20
    min_y, max_y = min(ys) - 20, max(ys) + 20

    def project(x: float, y: float) -> tuple[float, float]:
        px = pad + (x - min_x) / (max_x - min_x) * (width - 2 * pad)
        py = height - pad - (y - min_y) / (max_y - min_y) * (height - 2 * pad)
        return px, py

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Peta lokal node dan event">',
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="16" fill="#f1f5f9"/>',
        '<path d="M0 300 L640 300" stroke="#cbd5e1" stroke-width="1"/>',
    ]
    for node, info in data["nodes"].items():
        x, y = project(*info["local"])
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" fill="#2f6690"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#ffffff"/>')
        parts.append(
            f'<text x="{x + 16:.1f}" y="{y + 5:.1f}" font-size="13" '
            f'fill="#16425b" font-family="Inter, Arial, sans-serif">{html.escape(info["name"])}</text>'
        )
    if event_location:
        x, y = project(event_location["x"], event_location["y"])
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="17" fill="#dc2626" opacity="0.18"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="#dc2626"/>')
        parts.append(
            f'<text x="{x + 16:.1f}" y="{y - 10:.1f}" font-size="13" '
            f'fill="#b91c1c" font-weight="700" font-family="Inter, Arial, sans-serif">'
            f'Event</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def build_html(data: dict, result: dict, event_location: dict | None) -> str:
    normal = result["normal"]
    event = result["event"]
    map_svg = build_map_svg(data, event_location)

    def node_rows(scenario: dict) -> str:
        rows = []
        for node, item in scenario["nodes"].items():
            rows.append(
                "<tr>"
                f"<td>{html.escape(item['name'])}</td>"
                f"<td>{item['stats']['p2p']:.4f}</td>"
                f"<td>{item['stats']['rms']:.4f}</td>"
                f"<td>{html.escape(item['activity'])}</td>"
                f"<td>{item['confidence'] * 100:.1f}%</td>"
                "</tr>"
            )
        return "".join(rows)

    location_html = "<p class='muted'>Tidak ada event. Node hanya mengirim kondisi normal.</p>"
    if event_location:
        location_html = f"""
        <div class="location">
          <div><span>Estimasi TDOA</span><strong>{event_location['lat']:.7f}, {event_location['lon']:.7f}</strong></div>
          <div><span>Paling dekat</span><strong>{html.escape(event_location['nearest_name'])} ({event_location['nearest_distance_m']} m)</strong></div>
          <div><span>dt12 / dt13</span><strong>{event_location['dt12_ms']} ms / {event_location['dt13_ms']} ms</strong></div>
          <div><span>Kualitas</span><strong>{event_location['quality_12']:.3f} / {event_location['quality_13']:.3f}</strong></div>
          <div><a href="https://www.google.com/maps?q={event_location['lat']},{event_location['lon']}" target="_blank" rel="noreferrer">Buka di Google Maps</a></div>
        </div>
        """

    nodes_html = "".join(
        f"<li><strong>{html.escape(info['name'])}</strong> "
        f"({info['lat']:.7f}, {info['lon']:.7f})</li>"
        for info in data["nodes"].values()
    )

    return f"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GeoSense ML Mockup</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f8fafc; color: #16425b; font-family: Inter, Arial, sans-serif; }}
  main {{ max-width: 1080px; margin: 0 auto; padding: 32px 20px 56px; }}
  h1 {{ font-size: 28px; margin: 0 0 6px; letter-spacing: -0.02em; }}
  h2 {{ font-size: 18px; margin: 0 0 12px; }}
  p {{ line-height: 1.6; }}
  .muted {{ color: #5b7083; }}
  .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin-top: 22px; }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 20px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); }}
  .badge {{ display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }}
  .dot {{ width: 8px; height: 8px; border-radius: 999px; display: inline-block; }}
  .safe {{ color: #047857; }} .safe .dot {{ background: #10b981; }}
  .alert {{ color: #b45309; }} .alert .dot {{ background: #f59e0b; }}
  .danger {{ color: #b91c1c; }} .danger .dot {{ background: #ef4444; }}
  .metric {{ font-size: 28px; font-weight: 800; margin: 8px 0 2px; }}
  .label {{ font-size: 12px; color: #5b7083; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 14px; }}
  th, td {{ text-align: left; padding: 8px 6px; border-bottom: 1px solid #e2e8f0; }}
  th {{ color: #5b7083; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }}
  .location {{ display: grid; gap: 8px; margin-top: 14px; font-size: 13px; }}
  .location div {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px dashed #e2e8f0; padding-bottom: 7px; }}
  .location span {{ color: #5b7083; }}
  .location a {{ color: #2f6690; font-weight: 700; }}
  .map {{ margin-top: 24px; }}
  .map svg {{ width: 100%; height: auto; display: block; }}
  ul {{ padding-left: 18px; line-height: 1.8; }}
  .note {{ margin-top: 24px; padding: 14px 16px; border-radius: 12px; background: #fffbeb; color: #92400e; font-size: 13px; }}
  @media (max-width: 760px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
  <h1>GeoSense ML Mockup</h1>
  <p class="muted">Data statis lokal. Tidak mengirim MQTT, tidak mengubah backend, dan tidak mengubah frontend.</p>

  <div class="grid">
    <section class="card">
      <div class="badge safe"><span class="dot"></span>{normal['status']}</div>
      <h2 style="margin-top:10px">{html.escape(normal['label'])}</h2>
      <div class="metric">{html.escape(normal['nodes']['geo_1']['activity'])}</div>
      <div class="label">Prediksi ML node geo_1, confidence {normal['nodes']['geo_1']['confidence'] * 100:.1f}%</div>
      <p class="muted">p2p maksimum {normal['max_p2p']:.4f} mV. Tidak ada event yang dikirim.</p>
      <table>
        <thead><tr><th>Node</th><th>p2p</th><th>RMS</th><th>ML</th><th>Conf.</th></tr></thead>
        <tbody>{node_rows(normal)}</tbody>
      </table>
    </section>

    <section class="card">
      <div class="badge alert"><span class="dot"></span>{event['status']}</div>
      <h2 style="margin-top:10px">{html.escape(event['label'])}</h2>
      <div class="metric">{html.escape(event['nodes']['geo_1']['activity'])}</div>
      <div class="label">Prediksi ML node geo_1, confidence {event['nodes']['geo_1']['confidence'] * 100:.1f}%</div>
      <p class="muted">p2p maksimum {event['max_p2p']:.4f} mV. Event disimulasikan di area antara PPBS dan Lapangan Merah.</p>
      {location_html}
      <table>
        <thead><tr><th>Node</th><th>p2p</th><th>RMS</th><th>ML</th><th>Conf.</th></tr></thead>
        <tbody>{node_rows(event)}</tbody>
      </table>
    </section>
  </div>

  <section class="card map">
    <h2>Peta lokal node dan event</h2>
    {map_svg}
    <p class="muted">Koordinat node:</p>
    <ul>{nodes_html}</ul>
  </section>

  <div class="note">
    Ini mockup lokal. Data tidak dipublikasikan ke MQTT. Backend dan frontend tidak terpengaruh.
    Model yang dipakai: <strong>{html.escape(str(result['model']['path']))}</strong>,
    {result['model']['n_features']} fitur, kelas {html.escape(str(result['model']['classes']))}.
  </div>
</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Mockup ML GeoSense lokal")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="file data statis")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="file model .pkl")
    parser.add_argument("--open", action="store_true", help="buka report HTML di browser")
    args = parser.parse_args()

    data_path = Path(args.data)
    model_path = Path(args.model)
    if not data_path.is_file():
        raise SystemExit(f"Data tidak ditemukan: {data_path}")
    if not model_path.is_file():
        raise SystemExit(f"Model tidak ditemukan: {model_path}")

    data = json.loads(data_path.read_text(encoding="utf-8"))
    model = joblib.load(model_path)

    normal = analyze_scenario(model, data, "normal")
    event = analyze_scenario(model, data, "event")
    event_location = run_tdoa(data)

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "local-mockup-no-mqtt",
        "model": {
            "path": str(model_path),
            "n_features": int(getattr(model, "n_features_in_", 0)),
            "classes": [str(item) for item in getattr(model, "classes_", [])],
        },
        "normal": normal,
        "event": event,
        "event_location": event_location,
    }

    OUTPUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    OUTPUT_HTML.write_text(build_html(data, result, event_location), encoding="utf-8")

    print("GeoSense ML mockup selesai.")
    print(f"Normal : {normal['nodes']['geo_1']['activity']} ({normal['nodes']['geo_1']['confidence'] * 100:.1f}%), status {normal['status']}")
    print(f"Event  : {event['nodes']['geo_1']['activity']} ({event['nodes']['geo_1']['confidence'] * 100:.1f}%), status {event['status']}")
    if event_location:
        print(
            "Lokasi : "
            f"{event_location['lat']:.7f}, {event_location['lon']:.7f} "
            f"(dekat {event_location['nearest_name']}, {event_location['nearest_distance_m']} m)"
        )
    else:
        print("Lokasi : TDOA tidak menghasilkan estimasi.")
    print(f"JSON   : {OUTPUT_JSON}")
    print(f"HTML   : {OUTPUT_HTML}")

    if args.open:
        webbrowser.open(OUTPUT_HTML.as_uri())


if __name__ == "__main__":
    main()
