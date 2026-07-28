import serial
import serial.tools.list_ports
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import numpy as np
from collections import deque
import sys

# --- KONFIGURASI PROYEK GEOPHONE 10HZ ---
SERIAL_PORT = 'COM5'       # Ganti dengan nomor port USB Arduino Nano Anda
BAUD_RATE = 115200
SAMPLING_RATE = 90         # Sesuai dengan konfigurasi 90 SPS pada ADS1220
MAX_DATA_POINTS = 256      # Menggunakan angka biner kelipatan 2 agar perhitungan FFT cepat

# Penyimpanan data dinamis
x_time = np.linspace(0, MAX_DATA_POINTS / SAMPLING_RATE, MAX_DATA_POINTS)
y_signal = deque([0.0] * MAX_DATA_POINTS, maxlen=MAX_DATA_POINTS)

# Hubungkan ke Port Serial Arduino
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"Terhubung ke Geophone pada port {SERIAL_PORT}")
except serial.SerialException:
    print(f"Gagal membuka port {SERIAL_PORT}. Port aktif saat ini:")
    for port in serial.tools.list_ports.comports():
        print(f"- {port.device}")
    sys.exit()

# --- SETUP MATPLOTLIB DUAL LAYOUT ---
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7))
fig.suptitle("Penganalisis Sinyal Seismik Geophone 10Hz", fontsize=14, fontweight='bold')

# Grafik 1: Gelombang Sinyal Waktu (Time Domain)
line_time, = ax1.plot(x_time, list(y_signal), lw=1.5, color='#2ca02c')
ax1.set_title("Bentuk Gelombang Getaran Real-Time")
ax1.set_xlabel("Waktu (Detik)")
ax1.set_ylabel("Amplitudo (mV)")
ax1.grid(True, linestyle=':', alpha=0.6)
ax1.set_ylim(-0.1, 0.1)

# Grafik 2: Analisis Frekuensi (Frequency Domain / FFT)
fft_freqs = np.fft.rfftfreq(MAX_DATA_POINTS, d=1.0/SAMPLING_RATE)
line_fft, = ax2.plot(fft_freqs, np.zeros(len(fft_freqs)), lw=2, color='#d62728')
ax2.set_title("Spektrum Frekuensi (FFT Analysis)")
ax2.set_xlabel("Frekuensi (Hz)")
ax2.set_ylabel("Magnitudo Daya")
ax2.set_xlim(0, SAMPLING_RATE / 2) # Batas Nyquist (45 Hz)
ax2.set_ylim(0, 0.05)
ax2.grid(True, linestyle=':', alpha=0.6)

# --- FUNGSI UPDATE DATA DAN KALKULASI FFT ---
def update_plots(frame):
    while ser.in_waiting > 0:
        try:
            raw_line = ser.readline().decode('utf-8').strip()
            val = float(raw_line)
            y_signal.append(val)
        except (ValueError, UnicodeDecodeError):
            continue

    signal_array = np.array(y_signal)
    
    # 1. Update Grafik Waktu
    line_time.set_ydata(signal_array)
    current_min, current_max = signal_array.min(), signal_array.max()
    padding = max(0.01, (current_max - current_min) * 0.1)
    ax1.set_ylim(current_min - padding, current_max + padding)

    # 2. Hitung Fast Fourier Transform (FFT) untuk mendeteksi resonansi 10Hz
    # Hilangkan komponen DC offset (nilai rata-rata) agar grafik FFT bersih dari noise awal
    detrended_signal = signal_array - np.mean(signal_array)
    fft_values = np.abs(np.fft.rfft(detrended_signal)) / MAX_DATA_POINTS
    
    line_fft.set_ydata(fft_values)
    if len(fft_values) > 0:
        ax2.set_ylim(0, max(0.01, np.max(fft_values) * 1.2))

    return line_time, line_fft

# Jalankan animasi grafik dengan interval pembaruan 30ms
ani = animation.FuncAnimation(fig, update_plots, blit=False, interval=30, cache_frame_data=False)

try:
    plt.tight_layout()
    plt.show()
finally:
    ser.close()
    print("Koneksi Geophone aman ditutup.")