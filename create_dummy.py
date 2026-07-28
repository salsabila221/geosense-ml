# 04_machine_learning/create_dummy.py
import pandas as pd
import numpy as np

# 1. Samakan window_size dengan MAX_DATA_POINTS temanmu (256 poin)
window_size = 256
num_samples = 100

data = []
labels = []

for label in ['diem', 'orang_jalan', 'kendaraan']:
    for _ in range(num_samples):
        # 2. Pakai angka float mV (misal rentang -0.1 s/d 0.1 mV sesuai grafik temanmu)
        fake_signal = np.random.uniform(-0.1, 0.1, size=window_size)
        data.append(fake_signal)
        labels.append(label)

# Simpan ke CSV dummy
df = pd.DataFrame(data)
df['label'] = labels
df.to_csv('dummy_dataset.csv', index=False)
print("✅ Data dummy 256 poin (mV) berhasil dibuat!")