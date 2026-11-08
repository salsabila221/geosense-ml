#!/usr/bin/env python3
"""TDOA event mode GeoSense.

Berlangganan event waveform dari MQTT ``rawdata`` (hasil ``lora_receiver.py``),
memasangkan event dari 3 node, menghitung TDOA dengan cross-correlation, lalu
mempublish hasilnya ke ``geosense/data`` untuk backend/dashboard.

Alur::

    lora_receiver.py --rawdata--> event_tdoa.py --geosense/data--> backend

Asumsi/konvensi:

- ``location.x`` = meter ke timur, ``location.y`` = meter ke utara.
- Posisi node default: geo_1 (0,0), geo_2 (10,0), geo_3 (5,8.66).
- Wave speed default 300 m/s (bisa diubah lewat env).
- Event dari tiap node dipasangkan berdasarkan UTC (perkiraan GPS) dengan
  toleransi tertentu. UTC bukan PPS, jadi cross-correlation dipakai untuk
  memperhalus delay.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import ssl
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize
from scipy.signal import correlate

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
NODES = ("geo_1", "geo_2", "geo_3")
DEFAULT_POSITIONS = {
    "geo_1": [0.0, 0.0],
    "geo_2": [10.0, 0.0],
    "geo_3": [5.0, 8.66],
}


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


def canonical_node(payload: dict) -> str | None:
    """Map payload ke geo_1..geo_3. Mendukung node_index 0-based."""
    if not isinstance(payload, dict):
        return None
    if "node_index" in payload:
        try:
            index = int(payload["node_index"])
            if 0 <= index <= 2:
                return f"geo_{index + 1}"
        except (TypeError, ValueError):
            pass
    for key in ("n", "node", "node_id", "sensor_id"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip().lower()
        if text in NODES:
            return text
        match = re.search(r"(\d+)$", text)
        if match:
            number = int(match.group(1))
            if 1 <= number <= 3:
                return f"geo_{number}"
    return None


def parse_utc(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def time_delay(
    sig1: np.ndarray,
    sig2: np.ndarray,
    fs: float,
    max_lag_s: float,
) -> tuple[float, float]:
    """
    Delay ``sig2`` relatif terhadap ``sig1`` (positif = sig2 datang belakangan).

    Cross-correlation + interpolasi parabola untuk sub-sample delay. Quality
    adalah normalized correlation peak (0..1).
    """
    a = np.asarray(sig1, dtype=float)
    b = np.asarray(sig2, dtype=float)
    a = a - float(np.mean(a))
    b = b - float(np.mean(b))
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    if norm < 1e-12:
        return 0.0, 0.0

    corr = correlate(a, b, mode="full")
    mid = len(a) - 1
    max_lag = max(1, int(round(max_lag_s * fs)))
    low = max(0, mid - max_lag)
    high = min(len(corr), mid + max_lag + 1)
    peak = low + int(np.argmax(corr[low:high]))
    lag = peak - mid

    fraction = 0.0
    if 0 < peak < len(corr) - 1:
        denom = corr[peak - 1] - 2.0 * corr[peak] + corr[peak + 1]
        if abs(denom) > 1e-12:
            fraction = 0.5 * (corr[peak - 1] - corr[peak + 1]) / denom

    # scipy.correlate(a, b) memberi lag negatif kalau b tertunda.
    delay = -(lag + fraction) / fs
    quality = float(corr[peak] / norm)
    return delay, quality


def estimate_location(
    dt12: float,
    dt13: float,
    positions: dict[str, np.ndarray],
    wave_speed: float,
    bounds_m: float,
) -> tuple[float, float, float] | None:
    """Cari (x, y) sumber dari dua TDOA. Return (x, y, residual)."""
    p1 = positions["geo_1"]
    p2 = positions["geo_2"]
    p3 = positions["geo_3"]

    def loss(position):
        d1 = float(np.linalg.norm(position - p1))
        d2 = float(np.linalg.norm(position - p2))
        d3 = float(np.linalg.norm(position - p3))
        return (dt12 - (d2 - d1) / wave_speed) ** 2 + (
            dt13 - (d3 - d1) / wave_speed
        ) ** 2

    guess = np.mean([p1, p2, p3], axis=0)
    result = minimize(
        loss,
        guess,
        method="Nelder-Mead",
        bounds=[(-bounds_m, bounds_m), (-bounds_m, bounds_m)],
        options={"maxiter": 800, "xatol": 1e-4, "fatol": 1e-10},
    )
    if not np.all(np.isfinite(result.x)):
        return None
    return float(result.x[0]), float(result.x[1]), float(result.fun)


@dataclass
class NodeEvent:
    node: str
    event_id: str
    utc: datetime
    utc_ts: float
    samples: np.ndarray
    trigger_index: int
    sample_period_us: float
    p2p: float | None
    rms: float | None
    peak: float | None
    unit: str
    received_at: float

    @property
    def key(self) -> tuple:
        return (self.node, self.event_id, round(self.utc_ts, 6))


@dataclass
class TdoaConfig:
    broker: str
    port: int
    username: str
    password: str
    topic_event: str
    topic_gps: str
    topic_data: str
    tls: bool
    positions: dict[str, np.ndarray]
    wave_speed: float
    pair_tolerance_s: float
    event_ttl_s: float
    buffer_per_node: int
    window_samples: int
    preroll_samples: int
    target_fs: float
    max_lag_s: float
    min_quality: float
    max_residual: float
    bounds_m: float

    @classmethod
    def from_env(cls) -> "TdoaConfig":
        raw_positions = os.getenv("GEOPHONE_POS_JSON", "").strip()
        if raw_positions:
            parsed = json.loads(raw_positions)
            positions = {
                str(key): np.array(value, dtype=float)
                for key, value in parsed.items()
            }
        else:
            positions = {
                key: np.array(value, dtype=float)
                for key, value in DEFAULT_POSITIONS.items()
            }
        for node in NODES:
            if node not in positions:
                raise ValueError(f"GEOPHONE_POS_JSON tidak punya {node}")

        wave_speed = _float_env("WAVE_SPEED", 300.0)
        max_distance = max(
            float(np.linalg.norm(positions[a] - positions[b]))
            for i, a in enumerate(NODES)
            for b in NODES[i + 1 :]
        )
        default_lag = min(0.5, max(0.2, 2.0 * max_distance / wave_speed))

        return cls(
            broker=os.getenv("MQTT_BROKER", "").strip(),
            port=_int_env("MQTT_PORT", 8883),
            username=os.getenv("MQTT_USERNAME", "").strip(),
            password=os.getenv("MQTT_PASSWORD", ""),
            topic_event=os.getenv("MQTT_TOPIC_RAW", "rawdata").strip(),
            topic_gps=os.getenv("MQTT_TOPIC_GPS", "gpsdata").strip(),
            topic_data=os.getenv("MQTT_TOPIC_DATA", "geosense/data").strip(),
            tls=bool(_int_env("MQTT_TLS", 1)),
            positions=positions,
            wave_speed=wave_speed,
            pair_tolerance_s=_float_env("TDOA_PAIR_TOLERANCE_S", 2.0),
            event_ttl_s=_float_env("TDOA_EVENT_TTL_S", 60.0),
            buffer_per_node=_int_env("TDOA_BUFFER_PER_NODE", 8),
            window_samples=_int_env("TDOA_WINDOW_SAMPLES", 512),
            preroll_samples=_int_env("TDOA_PREROLL_SAMPLES", 180),
            target_fs=_float_env("TDOA_TARGET_FS", 90.0),
            max_lag_s=_float_env("TDOA_MAX_LAG_S", default_lag),
            min_quality=_float_env("TDOA_MIN_QUALITY", 0.1),
            max_residual=_float_env("TDOA_MAX_RESIDUAL", 1e-3),
            bounds_m=_float_env("TDOA_BOUNDS_M", 50.0),
        )


class TdoaEngine:
    """Buffer event per node + pairing + kalkulasi TDOA."""

    def __init__(self, config: TdoaConfig):
        self.cfg = config
        self.buffers = {
            node: deque(maxlen=config.buffer_per_node) for node in NODES
        }
        self.used: dict[tuple, float] = {}
        self.gps: dict[str, dict] = {}

    def add_gps(self, payload: dict) -> None:
        node = canonical_node(payload)
        if node is None:
            return
        try:
            latitude = float(payload.get("lat", payload.get("latitude")))
            longitude = float(payload.get("lon", payload.get("longitude")))
        except (TypeError, ValueError):
            return
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return
        self.gps[node] = {
            "latitude": latitude,
            "longitude": longitude,
            "updated": time.monotonic(),
        }

    def parse_event(self, payload: dict) -> NodeEvent | None:
        if not isinstance(payload, dict):
            return None
        marker = str(payload.get("type") or "").strip().lower()
        samples = payload.get("samples")
        if marker != "event" or not isinstance(samples, list):
            return None
        node = canonical_node(payload)
        if node is None:
            return None
        utc = parse_utc(payload.get("utc"))
        utc_valid = payload.get("utc_valid")
        if utc is None or utc_valid is False:
            return None

        values = []
        for item in samples:
            try:
                values.append(float(item))
            except (TypeError, ValueError):
                values.append(0.0)
        if len(values) < 64:
            return None

        try:
            trigger_index = int(payload.get("trigger_index") or 0)
        except (TypeError, ValueError):
            trigger_index = 0
        trigger_index = min(max(trigger_index, 0), len(values) - 1)

        period = payload.get("sample_period_us")
        if period is None:
            rate = payload.get("sampling_rate")
            period = 1_000_000.0 / float(rate) if rate else 11_111.0
        try:
            period = float(period)
        except (TypeError, ValueError):
            period = 11_111.0
        if period <= 0:
            return None

        array = np.asarray(values, dtype=float)
        return NodeEvent(
            node=node,
            event_id=str(payload.get("event_id")),
            utc=utc,
            utc_ts=utc.timestamp(),
            samples=array,
            trigger_index=trigger_index,
            sample_period_us=period,
            p2p=_optional_float(payload.get("p2p")),
            rms=_optional_float(payload.get("rms")),
            peak=_optional_float(payload.get("peak")),
            unit=str(payload.get("unit") or "adc_counts"),
            received_at=time.monotonic(),
        )

    def add_event(self, event: NodeEvent) -> dict | None:
        self._cleanup()
        if event.key in self.used:
            return None
        if any(item.key == event.key for item in self.buffers[event.node]):
            return None

        self.buffers[event.node].append(event)
        groups = self._candidate_groups(event)
        if not groups:
            return None

        best: tuple[tuple[float, float], dict[str, NodeEvent], dict] | None = None
        for span, group in groups:
            result = self._compute(group)
            if result is None:
                continue
            quality = min(result["q12"], result["q13"])
            score = (quality, -span)
            if best is None or score > best[0]:
                best = (score, group, result)

        if best is None:
            # Jangan buang event: kombinasi lain mungkin muncul belakangan.
            log(
                "TDOA belum cocok untuk event "
                + ", ".join(f"{n}={e.event_id}" for n, e in groups[0][1].items())
            )
            return None

        _score, group, result = best
        for item in group.values():
            self.used[item.key] = time.monotonic()
            try:
                self.buffers[item.node].remove(item)
            except ValueError:
                pass
        return self._payload(group, result)

    def _cleanup(self) -> None:
        now = time.monotonic()
        for key in [key for key, seen in self.used.items() if now - seen > self.cfg.event_ttl_s]:
            self.used.pop(key, None)
        for node, buffer in self.buffers.items():
            while buffer and (now - buffer[0].received_at) > self.cfg.event_ttl_s:
                buffer.popleft()
        for node in list(self.gps):
            if now - self.gps[node]["updated"] > 300.0:
                self.gps.pop(node, None)

    def _candidate_groups(
        self, new_event: NodeEvent
    ) -> list[tuple[float, dict[str, NodeEvent]]]:
        others = [node for node in NODES if node != new_event.node]
        tolerance = self.cfg.pair_tolerance_s

        options = []
        for node in others:
            candidates = [
                item
                for item in self.buffers[node]
                if abs(item.utc_ts - new_event.utc_ts) <= tolerance
            ]
            if not candidates:
                return []
            options.append((node, candidates))

        groups = []
        for first in options[0][1]:
            for second in options[1][1]:
                span = max(
                    abs(first.utc_ts - new_event.utc_ts),
                    abs(second.utc_ts - new_event.utc_ts),
                )
                groups.append(
                    (
                        span,
                        {
                            new_event.node: new_event,
                            options[0][0]: first,
                            options[1][0]: second,
                        },
                    )
                )
        groups.sort(key=lambda item: item[0])
        return groups

    def _window(self, event: NodeEvent, times: np.ndarray) -> np.ndarray | None:
        fs_native = 1_000_000.0 / event.sample_period_us
        indices = event.trigger_index + (times - event.utc_ts) * fs_native
        if indices.min() < 0 or indices.max() > len(event.samples) - 1:
            return None
        return np.interp(
            indices, np.arange(len(event.samples)), event.samples
        )

    def _gps_positions(self) -> dict[str, np.ndarray] | None:
        """Derive local x/y from GPS when all three nodes have a fix.

        ``GEOPHONE_POS_JSON`` is still the fallback. GPS positions are used by
        default so TDOA is not computed with a 10 m demo triangle on a real
        site that is hundreds of metres wide. Set
        ``TDOA_USE_GPS_POSITIONS=0`` to force the configured positions.
        """
        use_gps = os.getenv("TDOA_USE_GPS_POSITIONS", "1").strip().lower()
        if use_gps in {"0", "false", "no", "off"}:
            return None
        if any(node not in self.gps for node in NODES):
            return None

        origin = self.gps["geo_1"]
        lat0 = float(origin["latitude"])
        lon0 = float(origin["longitude"])
        cos_lat = max(0.01, math.cos(math.radians(lat0)))
        positions = {}
        for node in NODES:
            gps = self.gps[node]
            east = (float(gps["longitude"]) - lon0) * 111_320.0 * cos_lat
            north = (float(gps["latitude"]) - lat0) * 111_320.0
            positions[node] = np.array([east, north], dtype=float)

        span = max(
            float(np.linalg.norm(positions[a] - positions[b]))
            for index, a in enumerate(NODES)
            for b in NODES[index + 1:]
        )
        return positions if span >= 1.0 else None

    def _compute(self, group: dict[str, NodeEvent]) -> dict | None:
        if any(node not in group for node in NODES):
            return None
        positions = self._gps_positions() or self.cfg.positions
        max_distance = max(
            float(np.linalg.norm(positions[a] - positions[b]))
            for index, a in enumerate(NODES)
            for b in NODES[index + 1:]
        )
        max_lag = max(self.cfg.max_lag_s, 2.0 * max_distance / self.cfg.wave_speed)
        bounds = max(self.cfg.bounds_m, max_distance * 2.0)
        periods = {
            node: event.sample_period_us / 1_000_000.0
            for node, event in group.items()
        }
        earliest = {
            node: event.utc_ts - event.trigger_index * periods[node]
            for node, event in group.items()
        }
        latest = {
            node: event.utc_ts
            + (len(event.samples) - 1 - event.trigger_index) * periods[node]
            for node, event in group.items()
        }

        desired_start = min(event.utc_ts for event in group.values()) - (
            self.cfg.preroll_samples / self.cfg.target_fs
        )
        # Jangan mulai sebelum sampel pertama node mana pun.
        start = max(desired_start, max(earliest.values()))
        end = start + (self.cfg.window_samples - 1) / self.cfg.target_fs
        if end > min(latest.values()):
            log("window TDOA melewati akhir waveform salah satu node")
            return None
        times = start + np.arange(self.cfg.window_samples) / self.cfg.target_fs

        windows = {}
        for node, event in group.items():
            window = self._window(event, times)
            if window is None:
                log(f"window {node} keluar dari panjang event")
                return None
            windows[node] = window

        dt12, q12 = time_delay(
            windows["geo_1"], windows["geo_2"], self.cfg.target_fs, max_lag
        )
        dt13, q13 = time_delay(
            windows["geo_1"], windows["geo_3"], self.cfg.target_fs, max_lag
        )
        if min(q12, q13) < self.cfg.min_quality:
            log(f"kualitas korelasi rendah: q12={q12:.3f} q13={q13:.3f}")
            return None

        location = estimate_location(
            dt12,
            dt13,
            positions,
            self.cfg.wave_speed,
            bounds,
        )
        if location is None:
            return None
        x, y, residual = location
        if residual > self.cfg.max_residual:
            log(f"residual TDOA terlalu besar: {residual:.6f}")
            return None
        return {
            "x": x,
            "y": y,
            "dt12": dt12,
            "dt13": dt13,
            "q12": q12,
            "q13": q13,
            "residual": residual,
        }

    def _payload(self, group: dict[str, NodeEvent], result: dict) -> dict:
        reference = group["geo_1"]
        nodes_payload = {}
        for node in NODES:
            event = group[node]
            samples = event.samples
            p2p = event.p2p if event.p2p is not None else float(np.ptp(samples))
            rms = event.rms
            if rms is None:
                rms = float(np.sqrt(np.mean(samples**2)))
            peak = event.peak
            if peak is None:
                peak = float(np.max(np.abs(samples)))
            nodes_payload[node] = {
                "online": True,
                "p2p": round(p2p, 3),
                "rms": round(rms, 3),
                "peak": round(peak, 3),
                "unit": event.unit,
                "event_id": event.event_id,
                "trigger_index": event.trigger_index,
                "sample_count": int(len(samples)),
                "utc": event.utc.isoformat(),
            }
            gps = self.gps.get(node)
            if gps:
                nodes_payload[node]["gps"] = {
                    "latitude": gps["latitude"],
                    "longitude": gps["longitude"],
                    "fix": True,
                }

        origin = "geo_1"
        gps = self.gps.get("geo_1")
        if gps:
            origin = {
                "latitude": gps["latitude"],
                "longitude": gps["longitude"],
            }

        min_ts = min(event.utc_ts for event in group.values())
        max_ts = max(event.utc_ts for event in group.values())
        location = {
            "x": round(result["x"], 3),
            "y": round(result["y"], 3),
            "origin": origin,
            "tdoa": {
                "dt12_ms": round(result["dt12"] * 1000.0, 2),
                "dt13_ms": round(result["dt13"] * 1000.0, 2),
                "quality_12": round(result["q12"], 4),
                "quality_13": round(result["q13"], 4),
                "residual": result["residual"],
                "pair_span_ms": round((max_ts - min_ts) * 1000.0, 2),
                "event_ids": {node: group[node].event_id for node in NODES},
                "wave_speed": self.cfg.wave_speed,
            },
        }

        p2p_values = [nodes_payload[node]["p2p"] for node in NODES]
        rms_values = [nodes_payload[node]["rms"] for node in NODES]
        return {
            "timestamp": int(min_ts),
            "source": "tdoa_event",
            "tdoa_ready": True,
            "aktivitas": "EVENT",
            "p2p_amplitude": max(p2p_values),
            "rms": max(rms_values),
            "sampling_rate": self.cfg.target_fs,
            "nodes": nodes_payload,
            "location": location,
            "event_id": reference.event_id,
            "utc": reference.utc.isoformat(),
        }


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class TdoaService:
    def __init__(self, config: TdoaConfig):
        if mqtt is None:
            raise RuntimeError("paho-mqtt belum terpasang")
        self.cfg = config
        self.engine = TdoaEngine(config)
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"geosense_tdoa_{int(time.time())}",
        )
        if config.username:
            self.client.username_pw_set(config.username, config.password)
        if config.tls:
            self.client.tls_set(
                cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2
            )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=20)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        code = getattr(reason_code, "value", reason_code)
        log(f"MQTT connect rc={reason_code} ({self.cfg.broker}:{self.cfg.port})")
        if code != 0:
            return
        client.subscribe(self.cfg.topic_event, qos=1)
        client.subscribe(self.cfg.topic_gps, qos=1)
        log(
            f"subscribe '{self.cfg.topic_event}' + '{self.cfg.topic_gps}', "
            f"publish '{self.cfg.topic_data}'"
        )

    def _on_message(self, client, userdata, message):
        topic = message.topic
        try:
            payload = json.loads(message.payload.decode("utf-8", errors="replace"))
        except ValueError:
            return
        if not isinstance(payload, dict):
            return

        if topic == self.cfg.topic_gps:
            self.engine.add_gps(payload)
            return

        if topic != self.cfg.topic_event:
            return

        event = self.engine.parse_event(payload)
        if event is None:
            return
        result = self.engine.add_event(event)
        if result is None:
            return

        data = json.dumps(result, separators=(",", ":")).encode("utf-8")
        info = client.publish(self.cfg.topic_data, data, qos=1)
        location = result["location"]
        log(
            f"TDOA event {result['event_id']} -> x={location['x']} "
            f"y={location['y']} dt12={location['tdoa']['dt12_ms']}ms "
            f"dt13={location['tdoa']['dt13_ms']}ms "
            f"q=({location['tdoa']['quality_12']},{location['tdoa']['quality_13']}) "
            f"pub_rc={info.rc}"
        )

    def run(self) -> None:
        self.client.connect(self.cfg.broker, self.cfg.port, keepalive=60)
        self.client.loop_forever()


def main() -> None:
    if load_dotenv is not None:
        load_dotenv(ENV_PATH)

    config = TdoaConfig.from_env()

    parser = argparse.ArgumentParser(description="TDOA event mode GeoSense")
    parser.add_argument("--broker", default=config.broker)
    parser.add_argument("--port", type=int, default=config.port)
    parser.add_argument("--username", default=config.username)
    parser.add_argument("--password", default=config.password)
    parser.add_argument("--event-topic", default=config.topic_event)
    parser.add_argument("--gps-topic", default=config.topic_gps)
    parser.add_argument("--data-topic", default=config.topic_data)
    parser.add_argument("--pair-tolerance", type=float, default=config.pair_tolerance_s)
    parser.add_argument("--min-quality", type=float, default=config.min_quality)
    parser.add_argument("--window", type=int, default=config.window_samples)
    parser.add_argument("--preroll", type=int, default=config.preroll_samples)
    args = parser.parse_args()

    config.broker = args.broker
    config.port = args.port
    config.username = args.username
    config.password = args.password
    config.topic_event = args.event_topic
    config.topic_gps = args.gps_topic
    config.topic_data = args.data_topic
    config.pair_tolerance_s = args.pair_tolerance
    config.min_quality = args.min_quality
    config.window_samples = args.window
    config.preroll_samples = args.preroll

    if not config.broker:
        sys.exit("MQTT_BROKER belum diisi (env/.env)")

    print("=" * 72)
    print(" GeoSense TDOA event mode")
    print(f" MQTT   : {config.broker}:{config.port}")
    print(f" Input  : {config.topic_event} + {config.topic_gps}")
    print(f" Output : {config.topic_data}")
    print(
        " Node   : "
        + ", ".join(
            f"{node}=({pos[0]:.2f},{pos[1]:.2f})"
            for node, pos in config.positions.items()
        )
    )
    print(
        f" v={config.wave_speed} m/s | pair tol={config.pair_tolerance_s}s | "
        f"window={config.window_samples} sampel @ {config.target_fs} Hz"
    )
    print("=" * 72)

    service = TdoaService(config)
    try:
        service.run()
    except KeyboardInterrupt:
        print("\nDihentikan.")


if __name__ == "__main__":
    main()
