# mqtt_listener.py
import json
import numpy as np
import joblib
import paho.mqtt.client as mqtt

# 1. Load Model Machine Learning yang sudah kamu latih
MODEL_PATH = 'saved_models/geophone_rf_model.pkl'
print(f"Memuat model ML dari '{MODEL_PATH}'...")

try:
    model = joblib.load(MODEL_PATH)
    print("Model ML berhasil dimuat!")
except Exception as e:
    print(f"Gagal memuat model: {e}")
    exit()

# 2. Callback saat laptop berhasil terhubung ke MQTT Broker
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("\nTerhubung ke Local MQTT Broker!")
        # Menangkap data dari SEMUA geofon (misal: geosense/geophone_1, geosense/geophone_2, dst)
        topic = "geosense/#"
        client.subscribe(topic)
        print(f"Mendengarkan data pada topik: '{topic}'...\n")
        print("--------------------------------------------------")
    else:
        print(f"❌ Gagal terhubung, return code: {rc}")

# 3. Callback saat ada data baru masuk dari mikrokontroler temanmu
def on_message(client, userdata, msg):
    try:
        topic_name = msg.topic
        payload_str = msg.payload.decode('utf-8')
        
        # Kiriman dari temanmu bisa berupa format JSON berisi array 256 data desimal
        # Contoh format JSON: {"data": [-0.01, 0.05, ... 256 data]}
        data_json = json.loads(payload_str)
        
        if "data" in data_json:
            raw_signal = data_json["data"]
        else:
            # Jika temanmu ngirim array murni tanpa key "data"
            raw_signal = data_json

        # Pastikan jumlah data persis 256 poin
        if len(raw_signal) == 256:
            # Ubah data ke bentuk 2D array untuk prediksi ML
            input_data = np.array(raw_signal).reshape(1, -1)
            
            # Lakukan Prediksi Menggunakan Model ML Kamu!
            prediction = model.predict(input_data)[0]
            
            print(f"[DATA MASUK] Topik: {topic_name}")
            print(f"Hasil Prediksi ML: >>> {prediction.upper()} <<<\n")
        else:
            print(f"Warning: Data dari {topic_name} berjumlah {len(raw_signal)} poin (harus 256 poin)!")

    except Exception as e:
        print(f"❌ Error saat memproses pesan dari {msg.topic}: {e}")

# 4. Inisialisasi Klien MQTT
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message

# Connect ke Mosquitto Broker di localhost (laptop sendiri)
BROKER_IP = "localhost" 
PORT = 1883

print(f"🔌 Menghubungkan ke MQTT Broker ({BROKER_IP}:{PORT})...")
client.connect(BROKER_IP, PORT, 60)

# Jalankan loop penerimaan data terus menerus
client.loop_forever()