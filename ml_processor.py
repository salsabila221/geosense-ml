import os
import json
import joblib
import numpy as np
import paho.mqtt.client as mqtt

# ==========================================
# 1. PATH & LOAD MODEL MACHINE LEARNING
# ==========================================
# Ambil path folder tempat file ml_processor.py berada
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# GANTI NAMA FILE INI SESUAI FILE MODEL ML KAMU (misal: model.pkl / model_geosense.joblib)
MODEL_NAME = 'model_geosense.joblib' 
MODEL_PATH = os.path.join(BASE_DIR, MODEL_NAME)

try:
    model_ml = joblib.load(MODEL_PATH)
    print(f"✅ Model ML '{MODEL_NAME}' berhasil di-load!")
except FileNotFoundError:
    print(f"❌ KETEMU ERROR: File '{MODEL_NAME}' tidak ada di folder:")
    print(f"   📂 {BASE_DIR}")
    print("   👉 Tolong copy/pindahkan file model ML kamu ke folder di atas ya!\n")
    exit(1)
except Exception as e:
    print(f"❌ Gagal me-load model ML: {e}\n")
    exit(1)

# Pemetaan output angka model (0, 1, 2) ke Status Text
STATUS_MAP = {
    0: "AMAN",
    1: "SIAGA",
    2: "WARNING"
}

# ==========================================
# 2. KONFIGURASI MOSQUITTO MQTT
# ==========================================
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
TOPIC_RAW = "sensor/gemastik/raw"
TOPIC_DATA = "sensor/gemastik/data"

# ==========================================
# 3. HANDLER MQTT
# ==========================================
def on_connect(client, userdata, flags, rc, properties=None):
    """Dipanggil saat terhubung ke Mosquitto"""
    if rc == 0:
        print("✅ [ML Processor] Terhubung ke Mosquitto Broker Lokal!")
        client.subscribe(TOPIC_RAW)
        print(f"📡 Menunggu data masuk di topic: '{TOPIC_RAW}'...\n")
    else:
        print(f"❌ Gagal konek ke Mosquitto. Code: {rc}")

def on_message(client, userdata, msg):
    """Dipanggil saat ada payload masuk dari ESP32"""
    try:
        payload_str = msg.payload.decode('utf-8')
        data = json.loads(payload_str)
        
        node_id = data.get("n", "NODE-UNKNOWN")
        features = data.get("d", [])
        timestamp = data.get("t", 0)

        if not features:
            print("⚠️ Data array 'd' kosong, skip...")
            return

        # Prediksi ML
        input_array = np.array(features).reshape(1, -1)
        pred_code = model_ml.predict(input_array)[0]
        predicted_status = STATUS_MAP.get(pred_code, "AMAN")

        # Ambil nilai amplitudo getaran
        amplitude_val = round(float(features[3]), 5) if len(features) > 3 else round(float(features[0]), 5)

        processed_payload = {
            "node": node_id,
            "status": predicted_status,
            "p2p_amplitude": amplitude_val,
            "aktivitas": predicted_status,
            "kondisi_tanah": f"Node {node_id}",
            "timestamp": timestamp
        }

        # Send to Mosquitto topic data
        client.publish(TOPIC_DATA, json.dumps(processed_payload))
        print(f"⚡ [ML RESULT] Status: {predicted_status} | Vibration: {amplitude_val} ➔ Published!")

    except Exception as e:
        print(f"⚠️ Error memproses data: {e}")

# ==========================================
# 4. RUN SERVICE (Paho MQTT v2.x Compatible)
# ==========================================
# Menggunakan CallbackAPIVersion.VERSION1 agar kompatibel & tanpa warning
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_forever()