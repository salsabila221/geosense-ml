import glob
import os
import pandas as pd

base_path = "dataset_raw"
all_data = []

if not os.path.exists(base_path):
    print(f"❌ Folder '{base_path}' tidak ditemukan!")
    exit()

print("🔍 Memulai proses penggabungan dataset...\n")

# Iterasi ke setiap subfolder (tanah_basah, tanah_kering)
for kondisi in os.listdir(base_path):
    folder_path = os.path.join(base_path, kondisi)

    if os.path.isdir(folder_path):
        csv_files = glob.glob(os.path.join(folder_path, "*.csv"))

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
    gabungan_df.to_csv("dataset_geophone_gabungan.csv", index=False)

    print("\n" + "=" * 55)
    print(
        "🎉 BERHASIL! Semua data valid digabung ke 'dataset_geophone_gabungan.csv'"
    )
    print(f"📊 Total Data Terkumpul: {len(gabungan_df)} baris")
    print("=" * 55)
else:
    print("\n❌ Tidak ada data CSV valid yang ditemukan untuk digabung.")