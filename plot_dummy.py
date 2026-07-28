# plot_dummy.py
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# 1. Baca data CSV
df = pd.read_csv('dummy_dataset.csv')

sampling_rate = 90
window_size = 256
t = np.linspace(0, window_size / sampling_rate, window_size)
fft_freqs = np.fft.rfftfreq(window_size, d=1.0 / sampling_rate)

# 2. Buat grid 3x2 (Sisi Kiri: Waktu, Sisi Kanan: FFT Frekuensi)
fig, axes = plt.subplots(3, 2, figsize=(12, 8))
fig.suptitle("Analisis Sinyal Seismik Geofon (Time Domain & FFT Spektrum)", fontsize=14, fontweight='bold')

labels_list = ['diem', 'orang_jalan', 'kendaraan']
colors = ['#1f77b4', '#ff7f0e', '#2ca02c']

for i, label in enumerate(labels_list):
    # .astype(float) ditambahkan di sini agar datanya pasti bertipe desimal
    sample_signal = df[df['label'] == label].iloc[0].drop('label').values.astype(float)
    
    # --- GRAFIK KIRI: DOMAIN WAKTU ---
    axes[i, 0].plot(t, sample_signal, color=colors[i], lw=1.2)
    axes[i, 0].set_title(f"Sinyal Waktu ({label})")
    axes[i, 0].set_ylabel("Amplitudo (mV)")
    axes[i, 0].grid(True, linestyle=':', alpha=0.7)
    
    # --- GRAFIK KANAN: DOMAIN FREKUENSI (FFT) ---
    detrended = sample_signal - np.mean(sample_signal)
    fft_values = np.abs(np.fft.rfft(detrended)) / window_size
    
    axes[i, 1].plot(fft_freqs, fft_values, color='#d62728', lw=1.5)
    axes[i, 1].set_title(f"Spektrum FFT ({label})")
    axes[i, 1].set_ylabel("Magnitudo")
    axes[i, 1].grid(True, linestyle=':', alpha=0.7)
    axes[i, 1].set_xlim(0, sampling_rate / 2)

axes[2, 0].set_xlabel("Waktu (Detik)")
axes[2, 1].set_xlabel("Frekuensi (Hz)")

plt.tight_layout()
plt.savefig('dummy_signals_fft.png')
print("✅ Grafik Sinyal & FFT berhasil disimpan sebagai 'dummy_signals_fft.png'!")