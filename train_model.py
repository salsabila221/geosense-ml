# 04_machine_learning/train_model.py
import os
import pandas as pd
import numpy as np
import joblib
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score

def train_geophone_ml(csv_path):
    print(f"📂 Membaca dataset dari: {csv_path} ...")
    
    # Cek apakah file CSV ada
    if not os.path.exists(csv_path):
        print(f"❌ File '{csv_path}' tidak ditemukan! Pastikan file CSV sudah dibuat.")
        return

    df = pd.read_csv(csv_path)

    # 1. Pisahkan Fitur (X) dan Label/Target (y)
    # df.drop otomatis mengambil seluruh 256 kolom fitur angka raw signal
    X = df.drop(columns=['label'])
    y = df['label']

    # 2. Bagi Data: 80% Training, 20% Testing
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # 3. Inisialisasi & Latih Model Random Forest
    print("🌲 Melatih model Random Forest...")
    clf = RandomForestClassifier(n_estimators=100, random_state=42)
    clf.fit(X_train, y_train)

    # 4. Evaluasi Model
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"\n🎯 Akurasi Model: {acc * 100:.2f}%\n")

    print("📊 Laporan Klasifikasi:")
    print(classification_report(y_test, y_pred))

    # 5. Buat Visualisasi Confusion Matrix
    cm = confusion_matrix(y_test, y_pred)
    plt.figure(figsize=(6, 5))
    sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', 
                xticklabels=clf.classes_, yticklabels=clf.classes_)
    plt.title('Confusion Matrix - Geophone Vibration ML')
    plt.xlabel('Prediksi Model')
    plt.ylabel('Label Asli')
    plt.tight_layout()
    plt.savefig('confusion_matrix.png')
    print("🖼️ Grafik Confusion Matrix disimpan sebagai 'confusion_matrix.png'")

    # 6. Simpan Model Jadi File .pkl
    # Otomatis buat folder 'saved_models' jika belum ada
    os.makedirs('saved_models', exist_ok=True)
    
    model_filename = 'saved_models/geophone_rf_model.pkl'
    joblib.dump(clf, model_filename)
    print(f"💾 Model berhasil disimpan di: {model_filename}\n")

if __name__ == "__main__":
    # Jalankan pengujian awal menggunakan file dummy_dataset.csv
    train_geophone_ml('dummy_dataset.csv')