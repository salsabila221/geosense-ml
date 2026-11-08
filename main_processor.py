import json
import os
import re
from collections import deque
from pathlib import Path
import time
import warnings

import joblib
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
import paho.mqtt.client as mqtt
import pandas as pd

# Library tambahan untuk TDOA & optimasi
from scipy.optimize import minimize
from scipy.signal import correlate

# Sembunyikan warning non-critical di terminal
warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

# --- INPUT KONDISI TANAH ---
print("=" * 68)
print("  GEOSENSE - REAL-TIME 3-GEOPHONE MONITOR & TDOA TRIANGULATION")
print("=" * 68)

KONDISI_TANAH = os.getenv("KONDISI_TANAH", "").strip().lower().replace(" ", "_")
if not KONDISI_TANAH:
    input_tanah = input(
        "Masukkan kondisi tanah lokasi rekam (contoh: tanah_basah / tanah_kering): "
    ).strip()
    KONDISI_TANAH = input_tanah.lower().replace(" ", "_") if input_tanah else "tanah_biasa"

# --- KONFIGURASI SENSOR, ML & TDOA ---
SAMPLING_RATE = int(os.getenv("SAMPLING_RATE", "500"))  # 500 SPS untuk mock
WINDOW_SIZE = 256  # 256 titik per window (~0.51 detik pada 500 SPS)
THRESHOLD_SIAGA = float(os.getenv("THRESHOLD_SIAGA", "10"))  # mV peak-to-peak
THRESHOLD_WASPADA = float(os.getenv("THRESHOLD_WASPADA", "20"))  # mV peak-to-peak

NODE_IDS = ("geo_1", "geo_2", "geo_3")
NODE_LABELS = {
    "geo_1": "Geofon 1",
    "geo_2": "Geofon 2",
    "geo_3": "Geofon 3",
}
NODE_COLORS = {
    "geo_1": "#2ca02c",
    "geo_2": "#ff7f0e",
    "geo_3": "#1f77b4",
}

# Jumlah sampel mentah yang ikut dikirim ke backend/dashboard.
# Default 256 (satu window penuh). Bisa dikurangi lewat environment variable.
SIGNAL_POINTS_TO_PUBLISH = min(
    max(1, int(os.getenv("SIGNAL_POINTS_TO_PUBLISH", str(WINDOW_SIZE)))),
    WINDOW_SIZE,
)
NODE_OFFLINE_AFTER_SEC = float(os.getenv("NODE_OFFLINE_AFTER_SEC", "5"))

# Koordinat fisik 3 geofon (meter, relatif terhadap origin 0,0).
# Sesuaikan dengan tata letak asli di lapangan.
GEOPHONE_POS = {
    "geo_1": np.array([0.0, 0.0]),  # Geofon 1 (acuan)
    "geo_2": np.array([35.362504, -19.897200]),  # Geofon 2 (Lapangan Merah)
    "geo_3": np.array([25.416800, 15.475600]),  # Geofon 3 (Lapangan Merah)
}
WAVE_SPEED = 300.0  # Kecepatan gelombang tanah (m/s)
EVENT_ACTIVITIES = {"jalan", "kendaraan", "longsor"}

# --- KONFIGURASI MQTT ---
MQTT_BROKER = os.getenv(
    "MQTT_BROKER", "35f0ff8245ed4c5c8d3838dc3bd1ba62.s1.eu.hivemq.cloud"
)
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER = os.getenv("MQTT_USERNAME", "geosense-process")
MQTT_PASS = os.getenv("MQTT_PASSWORD", "")
MQTT_TOPIC_RAW = os.getenv("MQTT_TOPIC_RAW", "geosense/raw/#,rawdata")
MQTT_TOPICS_RAW = [topic.strip() for topic in MQTT_TOPIC_RAW.split(",") if topic.strip()]
MQTT_TOPIC_DATA = os.getenv("MQTT_TOPIC_DATA", "geosense/data")
DEFAULT_SENSOR_ID = os.getenv("DEFAULT_SENSOR_ID", "geo_1").strip().lower()
HEARTBEAT_TOPIC = "geosense/processing/viewer_heartbeat"

mqtt_connected = False

# --- BUFFER 3 SENSOR GEOFON ---
sensor_buffers = {
    node: deque([0.0] * WINDOW_SIZE, maxlen=WINDOW_SIZE) for node in NODE_IDS
}
last_seen = {node: 0.0 for node in NODE_IDS}
message_counts = {node: 0 for node in NODE_IDS}
# Mock event membawa lokasi TDOA yang sudah dihitung generator. Pakai hint ini
# agar hasil processing tidak jatuh ke luar segitiga karena cross-correlation
# pada window pendek kurang stabil.
mock_location_hint = None

# Alias dari topik / payload JSON ke ID internal.
SENSOR_ALIASES = {}
for _index, _node in enumerate(NODE_IDS, start=1):
    for _alias in (
        _node,
        f"geo{_index}",
        f"geofon_{_index}",
        f"geofon{_index}",
        f"node_{_index}",
        f"node{_index}",
        str(_index),
    ):
        SENSOR_ALIASES[_alias] = _node


def resolve_sensor_id(value):
    """Ubah 'geo-2', 'GEOFON 2', 'node_2', 'NODE02', atau '2' menjadi 'geo_2'."""
    if value is None:
        return None
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    direct = SENSOR_ALIASES.get(key)
    if direct:
        return direct

    # Firmware mengirim nama seperti "NODE01"/"node02" (dengan nol di depan).
    match = re.search(r"(\d+)$", key)
    if match:
        return SENSOR_ALIASES.get(str(int(match.group(1))))
    return None


def _to_mv(value, unit=None):
    """Konversi nilai sensor ke mV. Payload tanpa unit dianggap Volt."""
    number = float(value)
    unit_normalized = str(unit or "").strip().lower()
    if unit_normalized in {"mv", "millivolt", "millivolts"}:
        return number
    return number * 1000.0


def _flatten_numbers(value):
    """Ratifikasi list/scalar menjadi list float (mendukung batch sampel)."""
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_flatten_numbers(item))
        return result
    try:
        return [float(value)]
    except (TypeError, ValueError):
        return []


def is_event_payload(data_json):
    """True kalau payload adalah event waveform protokol baru."""
    if not isinstance(data_json, dict):
        return False
    marker = str(data_json.get("type") or data_json.get("kind") or "").strip().lower()
    event_id = (
        data_json.get("event_id")
        or data_json.get("event")
        or data_json.get("eid")
        or data_json.get("id_event")
    )
    if event_id is None:
        return False
    if marker in {
        "event",
        "event_meta",
        "event_data",
        "event_end",
        "waveform",
        "data",
        "meta",
        "end",
    }:
        return True
    return any(isinstance(data_json.get(key), (list, tuple)) for key in ("samples", "waveform"))


def extract_mock_node_readings(data_json):
    """Ambil waveform dari payload mock backend: {"nodes": {"geo_1": {...}}}."""
    if not isinstance(data_json, dict):
        return {}
    nodes = data_json.get("nodes")
    if not isinstance(nodes, dict):
        return {}

    result = {}
    for node, record in nodes.items():
        sensor_id = resolve_sensor_id(node)
        if not sensor_id or not isinstance(record, dict):
            continue
        numbers = _flatten_numbers(record.get("signal") or record.get("d"))
        if not numbers:
            continue
        unit = str(record.get("unit") or "").strip().lower()
        if "count" in unit or "adc" in unit:
            factor = record.get("mv_per_count") or record.get("adc_mv_per_count")
            try:
                factor = float(factor)
            except (TypeError, ValueError):
                factor = 14.0 / 7_000_000
            result[sensor_id] = [number * factor for number in numbers]
        else:
            result[sensor_id] = [_to_mv(number, unit) for number in numbers]
    return result


def parse_mqtt_payload(payload_str, topic):
    """
    Kembalikan (sensor_id, readings_mv).

    Format yang didukung:
    - Topik  : geosense/raw/geo_1 atau rawdata
    - JSON  : {"n": "NODE01", "p": 27, "t": 50843, "d": [0.00012, ...]}
    - JSON  : {"sensor_id": "geo_1", "d": [0.012, 0.013, ...]}
    - JSON  : {"voltage": 0.012} / {"val": 0.012} / {"mv": 12.3}
    - Angka : "0.012" (dianggap Volt)
    """
    topic_parts = topic.split("/")
    topic_sensor = resolve_sensor_id(topic_parts[-1]) if topic_parts else None

    data_json = None
    try:
        data_json = json.loads(payload_str)
    except json.JSONDecodeError:
        data_json = None

    sensor_id = None
    readings = []

    if isinstance(data_json, dict):
        # "n" adalah kunci singkat firmware: {"n": "NODE01", "d": [...]}.
        for key in ("sensor_id", "node_id", "geophone", "node", "n"):
            sensor_id = resolve_sensor_id(data_json.get(key))
            if sensor_id:
                break

        unit = data_json.get("unit")
        for key in ("samples", "data", "d", "voltage", "val", "value", "amplitude"):
            if key not in data_json:
                continue
            numbers = _flatten_numbers(data_json[key])
            if numbers:
                readings = [_to_mv(number, unit) for number in numbers]
                break

        if not readings and "mv" in data_json:
            readings = [_to_mv(number, "mv") for number in _flatten_numbers(data_json["mv"])]
    elif isinstance(data_json, list):
        readings = [_to_mv(number) for number in _flatten_numbers(data_json)]
    else:
        try:
            readings = [_to_mv(float(payload_str))]
        except ValueError:
            readings = []

    sensor_id = sensor_id or topic_sensor or resolve_sensor_id(DEFAULT_SENSOR_ID)
    return sensor_id, readings


# --- FUNGSI PERHITUNGAN TDOA & TRIANGULASI ---


def get_time_delay(sig1, sig2):
    """Menghitung selisih waktu tiba gelombang (detik) via cross-correlation."""
    s1 = np.asarray(sig1, dtype=float) - np.mean(sig1)
    s2 = np.asarray(sig2, dtype=float) - np.mean(sig2)

    if np.ptp(s1) < 1e-9 or np.ptp(s2) < 1e-9:
        return 0.0

    corr = correlate(s1, s2, mode="full")
    lag = int(np.argmax(corr) - (len(s1) - 1))
    return lag / SAMPLING_RATE


def calculate_tdoa_location(sig1, sig2, sig3):
    """Hitung koordinat (x, y) sumber getaran dengan non-linear least squares."""
    dt12 = get_time_delay(sig1, sig2)
    dt13 = get_time_delay(sig1, sig3)

    p1 = GEOPHONE_POS["geo_1"]
    p2 = GEOPHONE_POS["geo_2"]
    p3 = GEOPHONE_POS["geo_3"]

    def loss_function(pos):
        d1 = np.linalg.norm(pos - p1)
        d2 = np.linalg.norm(pos - p2)
        d3 = np.linalg.norm(pos - p3)

        calc_dt12 = (d2 - d1) / WAVE_SPEED
        calc_dt13 = (d3 - d1) / WAVE_SPEED

        return (dt12 - calc_dt12) ** 2 + (dt13 - calc_dt13) ** 2

    initial_guess = np.mean([p1, p2, p3], axis=0)
    result = minimize(
        loss_function,
        initial_guess,
        method="Nelder-Mead",
        bounds=[(-50.0, 50.0), (-50.0, 50.0)],
        options={"maxiter": 800, "xatol": 1e-3, "fatol": 1e-8},
    )

    if not np.all(np.isfinite(result.x)):
        raise ValueError("Optimasi TDOA tidak menghasilkan koordinat valid.")

    return round(float(result.x[0]), 2), round(float(result.x[1]), 2)


def compute_signal_stats(signal):
    """Hitung statistik per node untuk dashboard dan payload MQTT."""
    values = np.asarray(signal, dtype=float)
    if values.size == 0:
        return {"p2p": 0.0, "rms": 0.0, "peak": 0.0, "mean": 0.0}

    return {
        "p2p": round(float(np.ptp(values)), 2),
        "rms": round(float(np.sqrt(np.mean(values**2))), 2),
        "peak": round(float(np.max(np.abs(values))), 2),
        "mean": round(float(np.mean(values)), 2),
    }


def node_is_online(node, now):
    seen = last_seen.get(node, 0.0)
    return bool(seen and (now - seen) <= NODE_OFFLINE_AFTER_SEC)


def build_nodes_payload(now):
    """Payload per node: statistik + potongan sinyal mentah terbaru."""
    nodes = {}
    for node in NODE_IDS:
        values = np.asarray(sensor_buffers[node], dtype=float)
        stats = compute_signal_stats(values)
        stats["online"] = node_is_online(node, now)
        stats["signal"] = [
            round(float(value), 3)
            for value in values[-SIGNAL_POINTS_TO_PUBLISH:]
        ]
        nodes[node] = stats
    return nodes


# --- MQTT CALLBACKS ---


def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        print("MQTT terhubung ke HiveMQ Cloud")
        for topic in MQTT_TOPICS_RAW:
            mqtt_client.subscribe(topic)
            print(f"Subscribed ke '{topic}'")
        print()
    else:
        print(f"MQTT gagal terhubung, RC: {rc}")


def on_mqtt_message(client, userdata, msg):
    """Terima data dari ESP32 Geo 1, Geo 2, atau Geo 3."""
    global mock_location_hint
    try:
        payload_str = msg.payload.decode("utf-8").strip()

        # Protokol baru mengirim EVENT_META/DATA/EVENT_END berisi waveform
        # ADC counts. Jangan dimasukkan ke buffer kontinu (bisa merusak ML);
        # mode event di processing belum diaktifkan.
        try:
            data_json = json.loads(payload_str)
        except (TypeError, ValueError):
            data_json = None

        if isinstance(data_json, dict):
            if data_json.get("clear_location"):
                mock_location_hint = None
            hint = data_json.get("location")
            if isinstance(hint, dict):
                try:
                    hint_x = float(hint.get("x"))
                    hint_y = float(hint.get("y"))
                except (TypeError, ValueError):
                    pass
                else:
                    mock_location_hint = {"x": hint_x, "y": hint_y}

        # Mock backend mengirim banyak node dalam satu payload. Ambil waveform
        # per node agar ML/TDOA bisa diuji tanpa perangkat fisik.
        mock_nodes = extract_mock_node_readings(data_json)
        if mock_nodes:
            for sensor_id, readings in mock_nodes.items():
                for reading in readings:
                    sensor_buffers[sensor_id].append(reading)
                last_seen[sensor_id] = time.time()
                message_counts[sensor_id] += len(readings)
            return

        if is_event_payload(data_json):
            print(
                f"Event waveform terdeteksi di '{msg.topic}' "
                f"(node {data_json.get('n') or data_json.get('node')}, "
                f"event {data_json.get('event_id') or data_json.get('event')}). "
                "Mode event processing belum diaktifkan."
            )
            return

        sensor_id, readings = parse_mqtt_payload(payload_str, msg.topic)

        if not sensor_id:
            print(f"Payload diabaikan, ID node tidak dikenali: {msg.topic}")
            return

        if not readings:
            print(f"Payload diabaikan, tidak ada angka valid: {msg.topic} -> {payload_str[:120]}")
            return

        for reading in readings:
            sensor_buffers[sensor_id].append(reading)

        last_seen[sensor_id] = time.time()
        message_counts[sensor_id] += len(readings)

    except Exception as exc:
        print(f"Error MQTT parsing: {exc}")


# --- INISIALISASI MQTT ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.tls_set()

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()
except Exception as exc:
    print(f"MQTT warning: {exc}")

# --- LOAD MODEL ML (RANDOM FOREST) ---
MODEL_CANDIDATES = [
    BASE_DIR / "models" / "geosense_model.pkl",
    BASE_DIR / "geosense_model.pkl",
]
MODEL_PATH = next((path for path in MODEL_CANDIDATES if path.exists()), MODEL_CANDIDATES[0])

ml_model = None
if MODEL_PATH.exists():
    try:
        ml_model = joblib.load(MODEL_PATH)
        print(f"Model ML berhasil dimuat: {MODEL_PATH}")
    except Exception as exc:
        print(f"Gagal load model ML: {exc}")
else:
    print(f"Model ML tidak ditemukan di: {MODEL_PATH}")
    print("Jalankan train_model.py terlebih dahulu. Dashboard tetap jalan tanpa ML.")

x_time = np.linspace(0, WINDOW_SIZE / SAMPLING_RATE, WINDOW_SIZE)
last_ml_step_time = 0.0
last_heartbeat_time = 0.0
ml_error_shown = False
FEATURE_COLS = [f"col_{index}" for index in range(WINDOW_SIZE)]

# --- MATPLOTLIB SETUP: 3 PANEL NODE + 1 PANEL FFT ---
fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
fig.suptitle(
    f"GEOSENSE - 3 Node Geofon & TDOA Triangulation [{KONDISI_TANAH.upper()}]",
    fontsize=13,
    fontweight="bold",
)

node_lines = {}
for node, axis in zip(NODE_IDS, axes[:3]):
    (line,) = axis.plot(
        x_time,
        list(sensor_buffers[node]),
        lw=1.3,
        color=NODE_COLORS[node],
    )
    node_lines[node] = (line, axis)
    axis.set_title(f"{NODE_LABELS[node]} (mV)")
    axis.set_ylabel("Amplitudo (mV)")
    axis.grid(True, linestyle=":", alpha=0.6)

fft_freqs = np.fft.rfftfreq(WINDOW_SIZE, d=1.0 / SAMPLING_RATE)
(line_fft,) = axes[3].plot(
    fft_freqs,
    np.zeros(len(fft_freqs)),
    lw=1.8,
    color="#d62728",
)
axes[3].set_title("Spektrum Frekuensi (FFT Geofon 1)")
axes[3].set_xlabel("Frekuensi (Hz)")
axes[3].set_ylabel("Magnitudo")
axes[3].grid(True, linestyle=":", alpha=0.6)

rec_status_text = axes[0].text(
    0.02,
    0.72,
    "STANDBY...",
    transform=axes[0].transAxes,
    fontsize=8.5,
    fontweight="bold",
    bbox=dict(facecolor="#e1f5fe", alpha=0.92, boxstyle="round,pad=0.45"),
)


def update_plots(frame):
    global last_ml_step_time, last_heartbeat_time, ml_error_shown, mock_location_hint

    sig1 = np.asarray(sensor_buffers["geo_1"], dtype=float)
    sig2 = np.asarray(sensor_buffers["geo_2"], dtype=float)
    sig3 = np.asarray(sensor_buffers["geo_3"], dtype=float)
    now = time.time()

    for node, (line, axis) in node_lines.items():
        values = np.asarray(sensor_buffers[node], dtype=float)
        line.set_ydata(values)
        low = float(np.min(values))
        high = float(np.max(values))
        padding = max(0.1, (high - low) * 0.15)
        axis.set_ylim(low - padding, high + padding)

    # Beri tahu mock publisher bahwa processing sudah siap, supaya event
    # one-shot tidak dimulai sebelum jendela ML terbuka.
    if mqtt_connected and now - last_heartbeat_time >= 2.0:
        mqtt_client.publish(HEARTBEAT_TOPIC, "1", qos=0)
        last_heartbeat_time = now

    if now - last_ml_step_time >= 1.0:
        last_ml_step_time = now
        nodes_payload = build_nodes_payload(now)

        p2p_val = max(node_data["p2p"] for node_data in nodes_payload.values())
        rms_val = max(node_data["rms"] for node_data in nodes_payload.values())

        # 1. Prediksi aktivitas dengan Random Forest (menggunakan window Geofon 1).
        predicted_activity = "Unknown"
        if ml_model is not None:
            try:
                features_df = pd.DataFrame([sig1], columns=FEATURE_COLS)
                predicted_activity = str(ml_model.predict(features_df)[0])
            except Exception as exc:
                predicted_activity = "Error ML"
                if not ml_error_shown:
                    print(f"Error prediksi ML: {exc}")
                    ml_error_shown = True

        # 2. Status hazard memakai amplitudo tertinggi dari 3 node.
        if p2p_val >= THRESHOLD_WASPADA:
            status_bencana = "WASPADA"
            status_bg = "#ffcdd2"
        elif p2p_val >= THRESHOLD_SIAGA:
            status_bencana = "SIAGA"
            status_bg = "#fff9c4"
        else:
            status_bencana = "AMAN"
            status_bg = "#c8e6c9"

        # 3. TDOA hanya dihitung jika ketiga node online dan status event aktif.
        # Mock memakai hint lokasi dari generator supaya titik episentrum tetap
        # berada di dalam segitiga tiga node.
        all_nodes_online = all(node_data["online"] for node_data in nodes_payload.values())
        estimated_location = None
        if all_nodes_online and status_bencana in {"SIAGA", "WASPADA"}:
            if mock_location_hint:
                estimated_location = dict(mock_location_hint)
            else:
                try:
                    x_est, y_est = calculate_tdoa_location(sig1, sig2, sig3)
                    estimated_location = {"x": x_est, "y": y_est}
                except Exception as exc:
                    print(f"Error TDOA: {exc}")

        # 4. Update judul per node + panel status utama.
        for node, (line, axis) in node_lines.items():
            node_data = nodes_payload[node]
            state = "ONLINE" if node_data["online"] else "OFFLINE"
            axis.set_title(
                f"{NODE_LABELS[node]} | p2p {node_data['p2p']:.2f} mV | "
                f"RMS {node_data['rms']:.2f} mV | {state}",
                fontsize=9,
            )

        node_summary = " | ".join(
            f"{node.replace('geo_', 'G')} {nodes_payload[node]['p2p']:.1f} mV"
            for node in NODE_IDS
        )
        if estimated_location:
            location_text = (
                f"X={estimated_location['x']} m, Y={estimated_location['y']} m"
            )
        else:
            location_text = "Menunggu event / 3 node online"

        rec_status_text.set_text(
            f"STATUS: {status_bencana} | ML (RF): {predicted_activity.upper()}\n"
            f"p2p {node_summary}\n"
            f"LOKASI TDOA: {location_text}"
        )
        rec_status_text.set_bbox(
            dict(facecolor=status_bg, alpha=0.9, boxstyle="round,pad=0.45")
        )

        # 5. Publish hasil + sinyal mentah 3 node ke backend (geosense/data).
        if mqtt_connected:
            payload = {
                "timestamp": int(now),
                "kondisi_tanah": KONDISI_TANAH,
                "status": status_bencana,
                "aktivitas": predicted_activity,
                "p2p_amplitude": round(p2p_val, 2),
                "rms": round(rms_val, 2),
                "p2p_amplitude_geo1": nodes_payload["geo_1"]["p2p"],
                "location": estimated_location,
                "tdoa_ready": all_nodes_online,
                "nodes": nodes_payload,
                "sampling_rate": SAMPLING_RATE,
                "window_size": WINDOW_SIZE,
            }
            mqtt_client.publish(
                MQTT_TOPIC_DATA,
                json.dumps(payload, separators=(",", ":")),
                qos=0,
            )

    # FFT Geofon 1.
    detrended = sig1 - np.mean(sig1)
    fft_values = (
        np.abs(np.fft.rfft(detrended * np.hanning(len(detrended)))) / WINDOW_SIZE
    )
    line_fft.set_ydata(fft_values)
    axes[3].set_ylim(0, max(0.00005, float(np.max(fft_values)) * 1.2))

    return [line for line, _ in node_lines.values()] + [line_fft, rec_status_text]


def on_close(event):
    if mqtt_connected:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            pass
    print("Aplikasi ditutup, koneksi MQTT dilepas.")


fig.canvas.mpl_connect("close_event", on_close)

ani = animation.FuncAnimation(
    fig, update_plots, blit=False, interval=30, cache_frame_data=False
)
plt.tight_layout()
plt.show()
