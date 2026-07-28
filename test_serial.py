# test_serial.py
import serial
import serial.tools.list_ports
import time

print("🔍 DETEKSI PORT USB YANG AKTIF:")
ports = serial.tools.list_ports.comports()
for p in ports:
    print(f" -> {p.device}: {p.description}")

PORT_TEST = 'COM7'
BAUD_TEST = 115200  # Sesuaikan jika di Arduino kamu 9600

print(f"\n📡 Mencoba membaca data RAW dari {PORT_TEST}...")

try:
    ser = serial.Serial(PORT_TEST, BAUD_TEST, timeout=2)
    time.sleep(1) # Buka jeda koneksi
    
    print("Membaca 10 paket data mentah:")
    for i in range(10):
        # Baca byte langsung tanpa nunggu newline
        raw_data = ser.read(ser.in_waiting or 1)
        print(f"Deteksi {i+1}: {raw_data}")
        time.sleep(0.2)

    ser.close()
    print("\n🔌 Selesai.")
except Exception as e:
    print(f"❌ Gagal: {e}")