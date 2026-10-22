import glob
import os
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
base_path = BASE_DIR / "dataset_raw"
output_path = BASE_DIR / "data" / "dataset_geophone_gabungan.csv"
all_data = []

if not base_path.exists():
    print(f"❌ Folder '{base_path}' tidak ditemukan!")
    raise SystemExit(1)

print("🔍 Memulai proses penggabungan dataset...\n")

# Iterasi ke setiap subfolder (tanah_basah, tanah_kering)
for kondisi in os.listdir(base_path):
    folder_path = base_path / kondisi

    if folder_path.is_dir():
        csv_files = glob.glob(str(folder_path / "*.csv"))

        for file in csv_files:
            # 1. Cek ukuran file dulu, jika 0 KB langsung lewati
            if os.path.getsize(file) == 0:
                print(f"⚠️ Melewati file kosong (0 KB): {file}")
                continue

            try:
                # 2. Coba baca file CSV
                df = pd.read_csv(file)

                if df.empty:
                    print(f"⚠️ Melewati file tanpa data: {file}")
                    continue

                # Tambahkan kolom kondisi tanah
                df["kondisi_tanah"] = kondisi

                # Tentukan aktivitas dari nama file
                nama_file = os.path.basename(file).lower()
                if "diem" in nama_file:
                    df["aktivitas"] = "diem"
                elif "jalan" in nama_file:
                    df["aktivitas"] = "jalan"
                elif "kendaraan" in nama_file:
                    df["aktivitas"] = "kendaraan"
                else:
                    df["aktivitas"] = "unknown"

                all_data.append(df)
                print(f"✅ Berhasil membaca: {file} ({len(df)} baris)")

            except pd.errors.EmptyDataError:
                print(f"⚠️ File kosong/corrupt dilewati: {file}")

# Gabungkan seluruh DataFrame jika ada data valid
if all_data:
    gabungan_df = pd.concat(all_data, ignore_index=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gabungan_df.to_csv(output_path, index=False)

    print("\n" + "=" * 55)
    print(f"🎉 BERHASIL! Semua data valid digabung ke '{output_path}'")
    print(f"📊 Total Data Terkumpul: {len(gabungan_df)} baris")
    print("=" * 55)
else:
    print("\n❌ Tidak ada data CSV valid yang ditemukan untuk digabung.")