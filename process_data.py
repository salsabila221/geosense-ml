import csv
import os
from pathlib import Path
import sys
import time
from collections import deque
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
import serial
import serial.tools.list_ports

# --- INPUT KONDISI TANAH ---
print("=" * 60)
print("  GEOSENSE - REAL-TIME MONITOR & DATASET RECORDER")
print("=" * 60)
input_tanah = input(
    "Masukkan kondisi tanah lokasi rekam (contoh: tanah_basah / tanah_kering): "
).strip()
KONDISI_TANAH = (
    input_tanah.lower().replace(" ", "_") if input_tanah else "tanah_biasa"
)

# --- KONFIGURASI HARDWARE & ML ---
SERIAL_PORT = "COM3"  # Port USB Arduino
BAUD_RATE = 230400  # Sesuai Serial.begin(230400)
SAMPLING_RATE = 90  # 90 SPS dari ADS1220
WINDOW_SIZE = 256  # 256 poin data per window untuk ML
STEP_SIZE = 90  # Sliding window pergeseran 1 detik (90 poin)

BASE_DIR = Path(__file__).parent if "__file__" in locals() else Path.cwd()
RAW_DATA_DIR = BASE_DIR / "dataset_raw" / KONDISI_TANAH

# Otomatis buat folder kondisi tanah jika belum ada
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
print(f"📁 Folder Output Dataset: {RAW_DATA_DIR.relative_to(BASE_DIR)}\n")

# Buffer Tampilan Grafik (FIFO 256 Data)
x_time = np.linspace(0, WINDOW_SIZE / SAMPLING_RATE, WINDOW_SIZE)
y_signal = deque([0.0] * WINDOW_SIZE, maxlen=WINDOW_SIZE)

# Variabel Status Recording
is_recording = False
current_label = ""
recording_buffer = []
start_rec_time = 0
total_samples_saved = 0

# Hubungkan ke Port Serial Arduino
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.05)
    print(
        f"✅ Terhubung ke Geophone pada {SERIAL_PORT} (Baud Rate: {BAUD_RATE})"
    )
except serial.SerialException:
    print(f"❌ Gagal membuka port {SERIAL_PORT}. Port aktif saat ini:")
    for port in serial.tools.list_ports.comports():
        print(f"- {port.device}")
    sys.exit()


# --- FUNGSI GENERATE NAMA FILE INCREMENTAL ---
def get_next_filepath(label):
    """Mencari nomor nomor take berikutnya, misal: diem01.csv, diem02.csv"""
    existing_files = list(RAW_DATA_DIR.glob(f"{label}*.csv"))
    next_index = len(existing_files) + 1
    filename = f"{label}{next_index:02d}.csv"
    return RAW_DATA_DIR / filename


# --- FUNGSI PEMROSESAN RECORDING KE CSV ---
def save_recording_data():
    global recording_buffer, current_label, total_samples_saved
    total_points = len(recording_buffer)

    if total_points < WINDOW_SIZE:
        print(
            f"\n⚠️ Data rekam terlalu pendek ({total_points} poin). Butuh minimal {WINDOW_SIZE} poin (~2.8 detik)!"
        )
        recording_buffer = []
        return

    target_filepath = get_next_filepath(current_label)
    rows_added = 0

    with open(target_filepath, mode="w", newline="") as f:
        writer = csv.writer(f)

        # Header CSV
        header = [f"col_{i}" for i in range(WINDOW_SIZE)] + ["label"]
        writer.writerow(header)

        # Potong buffer sinyal mentah menggunakan Sliding Window (256 window, 90 step)
        for i in range(0, total_points - WINDOW_SIZE + 1, STEP_SIZE):
            window = recording_buffer[i : i + WINDOW_SIZE]
            writer.writerow(window + [current_label])
            rows_added += 1

    total_samples_saved += rows_added
    print(
        f"✅ BINGO! Tersimpan {rows_added} baris window kelas '{current_label}' ke file: {target_filepath.name}"
    )
    print(f"📍 Lokasi: {target_filepath.relative_to(BASE_DIR)}")
    recording_buffer = []


# --- SETUP STRUKTUR GRAFIK MATPLOTLIB ---
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7))
fig.suptitle(
    f"GEOSENSE - Seismic Monitor [{KONDISI_TANAH.upper()}]",
    fontsize=13,
    fontweight="bold",
)

# Grafik 1: Waktu
(line_time,) = ax1.plot(x_time, list(y_signal), lw=1.5, color="#2ca02c")
ax1.set_title("Sinyal Amplitudo Geofon (mV)")
ax1.set_xlabel("Waktu (Detik)")
ax1.set_ylabel("Amplitudo (mV)")
ax1.grid(True, linestyle=":", alpha=0.6)

# Grafik 2: FFT
fft_freqs = np.fft.rfftfreq(WINDOW_SIZE, d=1.0 / SAMPLING_RATE)
(line_fft,) = ax2.plot(
    fft_freqs, np.zeros(len(fft_freqs)), lw=2, color="#d62728"
)
ax2.set_title("Spektrum Frekuensi (FFT Analysis)")
ax2.set_xlabel("Frekuensi (Hz)")
ax2.set_ylabel("Magnitudo Daya")
ax2.set_xlim(0, SAMPLING_RATE / 2)
ax2.grid(True, linestyle=":", alpha=0.6)

# Text Indicators
status_init_msg = (
    f"🟢 KONDISI: {KONDISI_TANAH} | STATUS: MONITORING (Siap Rekam)\n"
    "Tekan Keyboard: [1] Diem | [2] Jalan | [3] Kendaraan"
)
rec_status_text = ax1.text(
    0.02,
    0.80,
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


# --- TANGANI SHORTCUT KEYBOARD ---
def target_label_key(label):
    if label == "diem":
        return "1"
    if label == "jalan":
        return "2"
    if label == "kendaraan":
        return "3"
    return "0"


def toggle_record(target_label):
    global is_recording, current_label, start_rec_time, recording_buffer

    # Jika sedang merekam kelas yang sama -> STOP
    if is_recording and current_label == target_label:
        is_recording = False
        print(f"\n⏹️ PEREKAMAN DIHENTIKAN ({current_label.upper()})")
        save_recording_data()
        rec_status_text.set_text(status_init_msg)
        rec_status_text.set_bbox(
            dict(facecolor="#e1f5fe", alpha=0.9, boxstyle="round,pad=0.5")
        )
    else:
        # Jika sedang merekam kelas lain -> Simpan dulu data lama
        if is_recording:
            save_recording_data()

        # Mulai rekam kelas baru
        is_recording = True
        current_label = target_label
        recording_buffer = []
        start_rec_time = time.time()
        print(
            f"\n🔴 MULAI MEREKAM KELAS: '{current_label.upper()}' di '{KONDISI_TANAH}'... "
            f"(Tekan '{target_label_key(target_label)}' atau [SPACE] untuk stop)"
        )


def on_key_press(event):
    global is_recording
    if event.key == "1":
        toggle_record("diem")
    elif event.key == "2":
        toggle_record("jalan")
    elif event.key == "3":
        toggle_record("kendaraan")
    elif event.key in ["space", "0"]:
        if is_recording:
            toggle_record(current_label)


fig.canvas.mpl_connect("key_press_event", on_key_press)


def on_close(event):
    global is_recording
    if is_recording:
        save_recording_data()
    if ser.is_open:
        ser.close()
    print("🔌 Aplikasi ditutup & Port Serial dilepas dengan aman.")


fig.canvas.mpl_connect("close_event", on_close)


# --- FUNGSI UPDATE ANIMASI REAL-TIME ---
def update_plots(frame):
    reads = 0
    while ser.in_waiting > 0 and reads < 50:
        reads += 1
        try:
            raw_line = ser.readline().decode("utf-8", errors="ignore").strip()
            if raw_line:
                val = float(raw_line)
                y_signal.append(val)

                # Jika status REKAM aktif, tampung ke buffer rekam
                if is_recording:
                    recording_buffer.append(val)
        except ValueError:
            continue

    signal_array = np.array(y_signal)

    # 1. Update Grafik Sinyal
    line_time.set_ydata(signal_array)
    c_min, c_max = signal_array.min(), signal_array.max()
    diff = c_max - c_min
    padding = max(0.0001, diff * 0.15)
    ax1.set_ylim(c_min - padding, c_max + padding)

    # Update Indikator Status Perekaman di Layar
    if is_recording:
        durasi = time.time() - start_rec_time
        pts = len(recording_buffer)
        rec_status_text.set_text(
            f"🔴 RECORDING [{KONDISI_TANAH}]: '{current_label.upper()}' | Durasi: {durasi:.1f}s ({pts} pts)\n"
            f"👉 Tekan tombol [{target_label_key(current_label)}] atau [SPACE] untuk STOP & SIMPAN"
        )
        rec_status_text.set_bbox(
            dict(facecolor="#ffcdd2", alpha=0.9, boxstyle="round,pad=0.5")
        )

    # 2. Update Grafik FFT
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