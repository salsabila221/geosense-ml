import csv
from collections import deque
import json
import os
from pathlib import Path
import sys
import time
import warnings

import joblib
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
import paho.mqtt.client as mqtt
import pandas as pd

# Sembunyikan warning non-critical di terminal
warnings.filterwarnings("ignore")

# --- INPUT KONDISI TANAH ---
print("=" * 60)
print("  GEOSENSE - REAL-TIME MONITOR, ML & MQTT PUBLISHER (HIVEMQ CLOUD)")
print("=" * 60)
input_tanah = input(
    "Masukkan kondisi tanah lokasi rekam (contoh: tanah_basah / tanah_kering): "
).strip()
KONDISI_TANAH = (
    input_tanah.lower().replace(" ", "_") if input_tanah else "tanah_biasa"
)

# --- KONFIGURASI SENSOR & ML ---
SAMPLING_RATE = 90  # 90 SPS dari ADS1220
WINDOW_SIZE = 256  # 256 poin data per window untuk ML
STEP_SIZE = 90  # Sliding window pergeseran 1 detik

# THRESHOLD AMPLITUDO PEAK-TO-PEAK (mV) UNTUK LOGIKA BENCANA
THRESHOLD_SIAGA = 150.0  # mV Peak-to-Peak
THRESHOLD_WASPADA = 400.0  # mV Peak-to-Peak

# --- KONFIGURASI MQTT (HIVEMQ CLOUD) ---
MQTT_BROKER = "3cd0f777a8ec4a20af722dd1214f7eb3.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "Kelom2"
MQTT_PASS = "TCPRule1"

MQTT_TOPIC_RAW = "geosense/raw"  # Data mentah dari ESP32
MQTT_TOPIC_DATA = "geosense/data"  # Mengirim hasil ke backend Node.js

mqtt_connected = False


# --- MQTT CALLBACKS (DENGAN PRINT DEBUG & FALLBACK PARSER) ---
def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        print(f"📡 MQTT: Terhubung ke HiveMQ Cloud '{MQTT_BROKER}:{MQTT_PORT}'")
        mqtt_client.subscribe(MQTT_TOPIC_RAW)
        print(
            f"📥 Subscribed ke topik '{MQTT_TOPIC_RAW}' (Menunggu data ESP32...)\n"
        )
    else:
        print(f"⚠️ MQTT: Gagal terhubung, Kode RC: {rc}")


def on_mqtt_message(client, userdata, msg):
    """Menerima dan membedah data dari ESP32"""
    try:
        payload_str = msg.payload.decode("utf-8").strip()

        # 🔍 LOG DEBUG: Cetak apa saja teks yang dikirim oleh ESP32 ke terminal
        print(f"📩 [MQTT IN] Topik: {msg.topic} | Payload: {payload_str}")

        raw_val_mv = None

        # Try Parse Opsi 1: JSON Format
        try:
            data_json = json.loads(payload_str)

            # Format {"d": [0.0123]}
            if (
                "d" in data_json
                and isinstance(data_json["d"], list)
                and len(data_json["d"]) > 0
            ):
                raw_val_mv = float(data_json["d"][0]) * 1000.0
            # Format {"voltage": 0.0123} atau {"val": 0.0123}
            elif "voltage" in data_json:
                raw_val_mv = float(data_json["voltage"]) * 1000.0
            elif "val" in data_json:
                raw_val_mv = float(data_json["val"]) * 1000.0
            else:
                print(
                    f"⚠️ Format JSON tidak dikenali! Key yang masuk: {list(data_json.keys())}"
                )

        except json.JSONDecodeError:
            # Try Parse Opsi 2: Jika ESP32 cuma kirim angka float biasa (misal: "0.0123")
            try:
                raw_val_mv = float(payload_str) * 1000.0
            except ValueError:
                print(f"⚠️ Payload bukan format angka/JSON yang valid!")

        # Jika nilai berhasil diparse, tambahkan ke buffer sinyal
        if raw_val_mv is not None:
            y_signal.append(raw_val_mv)
            if is_recording:
                recording_buffer.append(raw_val_mv)

    except Exception as e:
        print(f"⚠️ Error parsing data MQTT: {e}")


# --- INISIALISASI CLIENT MQTT ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message

mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.tls_set()

try:
    print(f"🔄 Menghubungkan ke HiveMQ Cloud ({MQTT_BROKER})...")
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()
except Exception as e:
    print(f"⚠️ MQTT Warning: Gagal terhubung ke HiveMQ Cloud ({e}).")

# --- LOAD MODEL MACHINE LEARNING ---
BASE_DIR = Path(__file__).parent if "__file__" in locals() else Path.cwd()
MODEL_PATH = BASE_DIR / "geosense_model.pkl"

ml_model = None
if MODEL_PATH.exists():
    try:
        ml_model = joblib.load(MODEL_PATH)
        print("🤖 Model ML 'geosense_model.pkl' berhasil dimuat!")
    except Exception as e:
        print(f"⚠️ Gagal load model ML: {e}")
else:
    print("ℹ️ File 'geosense_model.pkl' tidak ditemukan. Berjalan tanpa ML.")

RAW_DATA_DIR = BASE_DIR / "dataset_raw" / KONDISI_TANAH
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Buffer Tampilan Grafik (FIFO 256 Data)
x_time = np.linspace(0, WINDOW_SIZE / SAMPLING_RATE, WINDOW_SIZE)
y_signal = deque([0.0] * WINDOW_SIZE, maxlen=WINDOW_SIZE)

is_recording = False
current_label = ""
recording_buffer = []
start_rec_time = 0
total_samples_saved = 0
last_ml_step_time = 0

FEATURE_COLS = [f"col_{i}" for i in range(WINDOW_SIZE)]


def get_next_filepath(label):
    existing_files = list(RAW_DATA_DIR.glob(f"{label}*.csv"))
    next_index = len(existing_files) + 1
    return RAW_DATA_DIR / f"{label}{next_index:02d}.csv"


def save_recording_data():
    global recording_buffer, current_label, total_samples_saved
    total_points = len(recording_buffer)

    if total_points < WINDOW_SIZE:
        print(f"\n⚠️ Data rekam terlalu pendek ({total_points} poin)!")
        recording_buffer = []
        return

    target_filepath = get_next_filepath(current_label)
    rows_added = 0

    with open(target_filepath, mode="w", newline="") as f:
        writer = csv.writer(f)
        header = FEATURE_COLS + ["label"]
        writer.writerow(header)

        for i in range(0, total_points - WINDOW_SIZE + 1, STEP_SIZE):
            window = recording_buffer[i : i + WINDOW_SIZE]
            writer.writerow(window + [current_label])
            rows_added += 1

    total_samples_saved += rows_added
    print(
        f"✅ Tersimpan {rows_added} baris window '{current_label}' -> {target_filepath.name}"
    )
    recording_buffer = []


# --- SETUP GRAFIK MATPLOTLIB ---
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7))
fig.suptitle(
    f"GEOSENSE - Seismic Monitor [{KONDISI_TANAH.upper()}] (HiveMQ Cloud)",
    fontsize=13,
    fontweight="bold",
)

(line_time,) = ax1.plot(x_time, list(y_signal), lw=1.5, color="#2ca02c")
ax1.set_title("Sinyal Amplitudo Geofon (mV)")
ax1.set_xlabel("Waktu (Detik)")
ax1.set_ylabel("Amplitudo (mV)")
ax1.grid(True, linestyle=":", alpha=0.6)

fft_freqs = np.fft.rfftfreq(WINDOW_SIZE, d=1.0 / SAMPLING_RATE)
(line_fft,) = ax2.plot(
    fft_freqs, np.zeros(len(fft_freqs)), lw=2, color="#d62728"
)
ax2.set_title("Spektrum Frekuensi (FFT Analysis)")
ax2.set_xlabel("Frekuensi (Hz)")
ax2.set_ylabel("Magnitudo Daya")
ax2.set_xlim(0, SAMPLING_RATE / 2)
ax2.grid(True, linestyle=":", alpha=0.6)

status_init_msg = (
    f"[OK] KONDISI: {KONDISI_TANAH} | STATUS: AMAN\n"
    "Tekan Keyboard: [1] Diem | [2] Jalan | [3] Kendaraan"
)
rec_status_text = ax1.text(
    0.02,
    0.75,
    status_init_msg,
    transform=ax1.transAxes,
    color="black",
    fontweight="bold",
    bbox=dict(facecolor="#e1f5fe", alpha=0.9, boxstyle="round,pad=0.5"),
)

peak_text = ax2.text(
    0.02,
    0.82,
    "",
    transform=ax2.transAxes,
    color="black",
    fontweight="bold",
    bbox=dict(facecolor="white", alpha=0.8, boxstyle="round,pad=0.5"),
)


def target_label_key(label):
    mapping = {"diem": "1", "jalan": "2", "kendaraan": "3"}
    return mapping.get(label, "0")


def toggle_record(target_label):
    global is_recording, current_label, start_rec_time, recording_buffer
    if is_recording and current_label == target_label:
        is_recording = False
        print(f"\n⏹️ PEREKAMAN DIHENTIKAN ({current_label.upper()})")
        save_recording_data()
        rec_status_text.set_text(status_init_msg)
        rec_status_text.set_bbox(
            dict(facecolor="#e1f5fe", alpha=0.9, boxstyle="round,pad=0.5")
        )
    else:
        if is_recording:
            save_recording_data()
        is_recording = True
        current_label = target_label
        recording_buffer = []
        start_rec_time = time.time()
        print(f"\n🔴 MULAI MEREKAM KELAS: '{current_label.upper()}'...")


def on_key_press(event):
    if event.key == "1":
        toggle_record("diem")
    elif event.key == "2":
        toggle_record("jalan")
    elif event.key == "3":
        toggle_record("kendaraan")
    elif event.key in ["space", "0"] and is_recording:
        toggle_record(current_label)


fig.canvas.mpl_connect("key_press_event", on_key_press)


def on_close(event):
    if is_recording:
        save_recording_data()
    if mqtt_connected:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    print("🔌 Aplikasi ditutup & MQTT HiveMQ Cloud dilepas.")


fig.canvas.mpl_connect("close_event", on_close)


def update_plots(frame):
    global last_ml_step_time

    signal_array = np.array(y_signal)

    line_time.set_ydata(signal_array)
    c_min, c_max = signal_array.min(), signal_array.max()
    diff = c_max - c_min
    padding = max(0.0001, diff * 0.15)
    ax1.set_ylim(c_min - padding, c_max + padding)

    current_time = time.time()
    if current_time - last_ml_step_time >= 1.0:
        last_ml_step_time = current_time

        p2p_val = c_max - c_min
        rms_val = float(np.sqrt(np.mean(signal_array**2)))

        predicted_activity = "Unknown"
        if ml_model is not None:
            try:
                features_df = pd.DataFrame([signal_array], columns=FEATURE_COLS)
                predicted_activity = str(ml_model.predict(features_df)[0])
            except Exception:
                predicted_activity = "Error ML"

        if p2p_val >= THRESHOLD_WASPADA:
            status_bencana = "WASPADA"
            status_bg = "#ffcdd2"
        elif p2p_val >= THRESHOLD_SIAGA:
            status_bencana = "SIAGA"
            status_bg = "#fff9c4"
        else:
            status_bencana = "AMAN"
            status_bg = "#c8e6c9"

        if not is_recording:
            rec_status_text.set_text(
                f"STATUS: {status_bencana} | AKTIVITAS ML: {predicted_activity.upper()}\n"
                f"Peak-to-Peak: {p2p_val:.2f} mV | RMS: {rms_val:.2f} mV"
            )
            rec_status_text.set_bbox(
                dict(
                    facecolor=status_bg, alpha=0.9, boxstyle="round,pad=0.5"
                )
            )

        if mqtt_connected:
            payload = {
                "timestamp": int(time.time()),
                "kondisi_tanah": KONDISI_TANAH,
                "status": status_bencana,
                "aktivitas": predicted_activity,
                "p2p_amplitude": round(p2p_val, 2),
                "rms": round(rms_val, 2),
            }
            mqtt_client.publish(MQTT_TOPIC_DATA, json.dumps(payload))

    if is_recording:
        durasi = time.time() - start_rec_time
        pts = len(recording_buffer)
        rec_status_text.set_text(
            f"RECORDING [{KONDISI_TANAH}]: '{current_label.upper()}' | Durasi: {durasi:.1f}s ({pts} pts)\n"
            f"👉 Tekan [{target_label_key(current_label)}] atau [SPACE] untuk STOP & SIMPAN"
        )
        rec_status_text.set_bbox(
            dict(facecolor="#ffcdd2", alpha=0.9, boxstyle="round,pad=0.5")
        )

    detrended = signal_array - np.mean(signal_array)
    windowed = detrended * np.hanning(len(detrended))
    fft_values = np.abs(np.fft.rfft(windowed)) / WINDOW_SIZE
    line_fft.set_ydata(fft_values)

    if len(fft_values) > 0:
        max_fft = np.max(fft_values)
        ax2.set_ylim(0, max(0.00005, max_fft * 1.2))
        peak_index = np.argmax(fft_values[1:]) + 1
        peak_freq = fft_freqs[peak_index]

        if max_fft > 0.00001:
            peak_text.set_text(f"Frekuensi Dominan: {peak_freq:.2f} Hz")
        else:
            peak_text.set_text("Status: Tanah Tenang (Noise Background)")

    return line_time, line_fft, rec_status_text, peak_text


ani = animation.FuncAnimation(
    fig, update_plots, blit=False, interval=30, cache_frame_data=False
)

plt.tight_layout()
plt.show()