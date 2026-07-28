# record_manual.py
import serial
import time
import csv
import os
import msvcrt  # Module bawaan Windows untuk deteksi tombol keyboard

# --- KONFIGURASI UTAMA ---
SERIAL_PORT = 'COM7'       # Port USB Arduino
BAUD_RATE = 230400         # Sesuai Serial.begin(230400)
SAMPLING_RATE = 90         # 90 SPS
WINDOW_SIZE = 256          # 256 poin per sampel ML
STEP_SIZE = 90             # Sliding window pergeseran 1 detik (90 poin)
CSV_FILENAME = 'dataset_real.csv'

# Buat file CSV + Header jika belum ada
if not os.path.exists(CSV_FILENAME):
    with open(CSV_FILENAME, mode='w', newline='') as f:
        writer = csv.writer(f)
        header = [f"col_{i}" for i in range(WINDOW_SIZE)] + ['label']
        writer.writerow(header)

def process_and_save(raw_buffer, label_name):
    """Memotong data mentah menjadi window 256 poin & disimpan ke CSV"""
    total_points = len(raw_buffer)
    if total_points < WINDOW_SIZE:
        print(f"\n⚠️ Data terlalu singkat ({total_points} poin). Butuh minimal 256 poin (~2.8 detik)!")
        return 0

    rows_saved = 0
    with open(CSV_FILENAME, mode='a', newline='') as f:
        writer = csv.writer(f)
        # Potong buffer sinyal mentah pakai sliding window
        for i in range(0, total_points - WINDOW_SIZE + 1, STEP_SIZE):
            window = raw_buffer[i : i + WINDOW_SIZE]
            writer.writerow(window + [label_name])
            rows_saved += 1

    return rows_saved

def main():
    print("=" * 65)
    print("      GEOSENSE MANUAL DATASET RECORDER (START / STOP MODE)     ")
    print("=" * 65)

    print(f"\n🔌 Menghubungkan ke {SERIAL_PORT} (Baud Rate: {BAUD_RATE})...")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.05)
        time.sleep(1)
        ser.reset_input_buffer()
        print("✅ Terhubung ke Geophone!")
    except Exception as e:
        print(f"❌ Gagal membuka {SERIAL_PORT}: {e}")
        return

    try:
        while True:
            print("\n-------------------------------------------------------------")
            label_input = input("📌 Masukkan nama label kelas (contoh: diem / orang_jalan / kendaraan) \n   [atau ketik 'exit' untuk keluar]: ").strip().lower()
            
            if label_input == 'exit':
                break
            if not label_input:
                print("❌ Label tidak boleh kosong!")
                continue

            print(f"\n🎯 Kelas Aktif: '{label_input.upper()}'")
            print("👉 Tekan [ENTER] untuk MULAILAH PEREKAMAN...")
            
            # Tunggu tombol ENTER ditekan
            input()
            
            # Clear buffer serial lama sebelum mulai rekam
            ser.reset_input_buffer()
            raw_buffer = []
            start_time = time.time()
            
            print("🔴 === SEMENTARA MEREKAM... ===")
            print("👉 Tekan [ENTER] kapan saja untuk STOP / BERHENTI PEREKAMAN!")
            
            # Flush pencetan keyboard terdahulu
            while msvcrt.kbhit():
                msvcrt.getch()

            # Loop Perekaman Berjalan
            recording = True
            while recording:
                # 1. Baca data dari Serial USB
                while ser.in_waiting > 0:
                    try:
                        raw_line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if raw_line:
                            val = float(raw_line)
                            raw_buffer.append(val)
                    except ValueError:
                        continue

                # 2. Tampilkan durasi real-time di terminal
                durasi = time.time() - start_time
                pts = len(raw_buffer)
                print(f"\r⏱️  Durasi: {durasi:5.1f}s | Data Terkumpul: {pts} poin", end="")

                # 3. Cek apakah tombol keyboard ditekan (ENTER untuk Stop)
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key in [b'\r', b'\n']:  # Tombol ENTER
                        recording = False

                time.sleep(0.02)

            # Setelah Tombol Stop Ditekan
            print("\n\n⏹️  PEREKAMAN DIHENTIKAN!")
            print("🔄 Memproses data mentah ke format ML 256-kolom...")
            
            saved_rows = process_and_save(raw_buffer, label_input)
            if saved_rows > 0:
                print(f"✅ BINGO! Berhasil menambahkan {saved_rows} baris sampel kelas '{label_input}' ke '{CSV_FILENAME}'!")

    except KeyboardInterrupt:
        print("\n\n⚠️ Program dihentikan.")
    finally:
        ser.close()
        print("🔌 Port Serial ditutup dengan aman. Terima kasih!")

if __name__ == "__main__":
    main()