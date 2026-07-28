import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# 1. Tentukan lokasi file CSV yang mau dilihat
# Ubah sesuai dengan nama file yang kamu punya
FILE_CSV = 'dataset_raw/tanah_kering/diem02.csv' 

# Baca file CSV
df = pd.read_csv(FILE_CSV)

# 2. Pisahkan kolom data sensor (col_0 sampai col_255) dengan kolom label
# Kita cuma mau ambil kolom yang berawalan "col_"
sensor_columns = [col for col in df.columns if col.startswith('col_')]
data_sensor = df[sensor_columns]

# 3. Pilih baris ke-berapa yang mau di-plot (misal baris index 10)
baris_ke = 10 
sinyal_mentah = data_sensor.iloc[baris_ke].values

# Ambil labelnya untuk judul grafik (menyesuaikan nama kolom label kamu)
# Kalau dari raw data biasanya namanya 'label'
label_kelas = df['label'].iloc[baris_ke] if 'label' in df.columns else "Tidak diketahui"

# 4. Bikin Sumbu X (Waktu dalam Detik)
SAMPLING_RATE = 90
WINDOW_SIZE = 256
x_waktu = np.linspace(0, WINDOW_SIZE / SAMPLING_RATE, WINDOW_SIZE)

# 5. Tampilkan Grafiknya
plt.figure(figsize=(10, 4))
plt.plot(x_waktu, sinyal_mentah, color='#1f77b4', linewidth=1.5)

plt.title(f"Visualisasi Sinyal Geofon\nFile: {FILE_CSV} | Baris: {baris_ke} | Kelas: '{label_kelas.upper()}'", fontweight='bold')
plt.xlabel("Waktu (Detik)")
plt.ylabel("Amplitudo (mV)")
plt.grid(True, linestyle='--', alpha=0.6)

# Biar grafiknya rapi
plt.tight_layout()
plt.show()