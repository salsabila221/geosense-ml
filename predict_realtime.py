from collections import deque
from pathlib import Path
import sys
import joblib
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import serial
import serial.tools.list_ports

# --- KONFIGURASI HARDWARE & ML ---
SERIAL_PORT = "COM7"  # Sesuaikan dengan Port Arduino kamu
BAUD_RATE = 230400  # Sesuai Serial.begin(230400)
SAMPLING_RATE = 90  # 90 SPS
WINDOW_SIZE = 256  # 256 poin data

MODEL_FILE = "geosense_model.pkl"

# 1. LOAD MODEL ML YANG SUDAH DITRAINING
print("=" * 60)
print("  GEOSENSE - REAL-TIME AI PREDICTION MONITOR")
print("=" * 60)

try:
    model = joblib.load(MODEL_FILE)
    print(f"✅ Model AI '{MODEL_FILE}' berhasil dimuat!")
except FileNotFoundError:
    print(
        f"❌ File model '{MODEL_FILE}' tidak ditemukan! Jalankan 'train_model.py' dulu."
    )
    sys.exit()

# 2. HUBUGKAN KE PORT SERIAL ARDUINO
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.05)
    print(f"✅ Terhubung ke Geophone pada {SERIAL_PORT}")
except serial.SerialException:
    print(f"❌ Gagal membuka port {SERIAL_PORT}. Port aktif saat ini:")
    for port in serial.tools.list_ports.comports():
        print(f"- {port.device}")
    sys.exit()

# Buffer Data (256 poin)
x_time = np.linspace(0, WINDOW_SIZE / SAMPLING_RATE, WINDOW_SIZE)
y_signal = deque([0.0] * WINDOW_SIZE, maxlen=WINDOW_SIZE)
sensor_cols = [f"col_{i}" for i in range(WINDOW_SIZE)]

# --- SETUP MATPLOTLIB ---
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7))
fig.suptitle(
    "GEOSENSE - Real-Time Seismic AI Detection", fontsize=14, fontweight="bold"
)

# Grafik 1: Sinyal Geofon
(line_time,) = ax1.plot(x_time, list(y_signal), lw=1.5, color="#1f77b4")
ax1.set_title("Sinyal Amplitudo Geofon (mV)")
ax1.set_xlabel("Waktu (Detik)")
ax1.set_ylabel("Amplitudo (mV)")
ax1.grid(True, linestyle=":", alpha=0.6)

# Teks Hasil Prediksi AI (Kotak Besar di Atas Grafik)
pred_box = ax1.text(
    0.02,
    0.78,
    "🤖 MENGANALISIS SINYAL...",
    transform=ax1.transAxes,
    fontsize=12,
    fontweight="bold",
    bbox=dict(facecolor="#fff59d", alpha=0.9, boxstyle="round,pad=0.6"),
)

# Grafik 2: FFT (Frekuensi)
fft_freqs = np.fft.rfftfreq(WINDOW_SIZE, d=1.0 / SAMPLING_RATE)
(line_fft,) = ax2.plot(
    fft_freqs, np.zeros(len(fft_freqs)), lw=2, color="#d62728"
)
ax2.set_title("Spektrum Frekuensi (FFT Analysis)")
ax2.set_xlabel("Frekuensi (Hz)")
ax2.set_ylabel("Magnitudo Daya")
ax2.set_xlim(0, SAMPLING_RATE / 2)
ax2.grid(True, linestyle=":", alpha=0.6)


# --- FUNGSI UPDATE REAL-TIME ---
def update_plots(frame):
    reads = 0
    while ser.in_waiting > 0 and reads < 50:
        reads += 1
        try:
            raw_line = ser.readline().decode("utf-8", errors="ignore").strip()
            if raw_line:
                val = float(raw_line)
                y_signal.append(val)
        except ValueError:
            continue

    signal_array = np.array(y_signal)

    # 1. Update Grafik Sinyal
    line_time.set_ydata(signal_array)
    c_min, c_max = signal_array.min(), signal_array.max()
    diff = c_max - c_min
    padding = max(0.0001, diff * 0.15)
    ax1.set_ylim(c_min - padding, c_max + padding)

    # 2. PREDIKSI MACHINE LEARNING REAL-TIME
    # Buat dataframe 1 baris x 256 kolom sesuai format training
    df_input = pd.DataFrame([signal_array], columns=sensor_cols)

    # Tebak kelas aktivitas
    pred_class = model.predict(df_input)[0]

    # Ambil nilai probabilitas/keyakinan model (jika ada)
    try:
        probs = model.predict_proba(df_input)[0]
        max_prob = np.max(probs) * 100
        prob_str = f" ({max_prob:.1f}%)"
    except Exception:
        prob_str = ""

    # Update Tampilan Prediksi
    if pred_class.lower() == "jalan":
        pred_box.set_text(f"🚶 PREDIKSI AI: ADA ORANG JALAN{prob_str}")
        pred_box.set_bbox(
            dict(facecolor="#ffcdd2", alpha=0.9, boxstyle="round,pad=0.6")
        )  # Merah/Warning
    else:
        pred_box.set_text(f"🟢 PREDIKSI AI: KONDISI DIEM / NOISE{prob_str}")
        pred_box.set_bbox(
            dict(facecolor="#c8e6c9", alpha=0.9, boxstyle="round,pad=0.6")
        )  # Hijau/Aman

    # 3. Update Grafik FFT
    detrended = signal_array - np.mean(signal_array)
    windowed = detrended * np.hanning(len(detrended))
    fft_values = np.abs(np.fft.rfft(windowed)) / WINDOW_SIZE
    line_fft.set_ydata(fft_values)

    if len(fft_values) > 0:
        max_fft = np.max(fft_values)
        ax2.set_ylim(0, max(0.00005, max_fft * 1.2))

    return line_time, line_fft, pred_box


def on_close(event):
    if ser.is_open:
        ser.close()
    print("\n🔌 Port Serial dilepas. Aplikasi ditutup.")


fig.canvas.mpl_connect("close_event", on_close)

ani = animation.FuncAnimation(
    fig, update_plots, blit=False, interval=30, cache_frame_data=False
)

plt.tight_layout()
plt.show()