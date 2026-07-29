import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score, confusion_matrix
import joblib

# 1. BACA DATASET GABUNGAN
DATASET_FILE = "dataset_geophone_gabungan.csv"
print(f"📂 Memuat data dari '{DATASET_FILE}'...")

try:
    df = pd.read_csv(DATASET_FILE)
except FileNotFoundError:
    print(f"❌ File '{DATASET_FILE}' belum ada! Jalankan 'python gabungan_csv.py' dulu.")
    exit()

# 2. PISAHKAN FITUR (X) DAN LABEL (y)
# Ambil kolom data sinyal col_0 sampai col_255
sensor_columns = [col for col in df.columns if col.startswith('col_')]
X = df[sensor_columns]

# Tentukan kolom target label (menggunakan 'aktivitas' atau 'label')
if 'aktivitas' in df.columns:
    y = df['aktivitas']
elif 'label' in df.columns:
    y = df['label']
else:
    print("❌ Kolom label/aktivitas tidak ditemukan di CSV!")
    exit()

print(f"📊 Total sampel data: {len(df)} baris")
print(" Distribusi kelas:\n", y.value_counts(), "\n")

# 3. SPLIT DATA (TRAINING & TESTING)
# 80% data untuk latihan, 20% untuk ujian/pengujian
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

print(f" Data Latih (Train): {len(X_train)} baris")
print(f" Data Uji (Test)   : {len(X_test)} baris")

# 4. TRAINING MODEL (Random Forest)
print("\n🤖 Sedang melatih model Random Forest...")
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
print("✅ Training selesai!")

# 5. EVALUASI HASIL UJIAN MODEL
y_pred = model.predict(X_test)
akurasi = accuracy_score(y_test, y_pred)

print("\n" + "="*40)
print(f"🎯 AKURASI MODEL: {akurasi * 100:.2f}%")
print("="*40)
print("\nDetail Laporan Klasifikasi:")
print(classification_report(y_test, y_pred))

# 6. SIMPAN MODEL KEDALAM FILE .PKL
MODEL_FILE = "geosense_model.pkl"
joblib.dump(model, MODEL_FILE)
print(f"💾 Model berhasil disimpan sebagai '{MODEL_FILE}'")