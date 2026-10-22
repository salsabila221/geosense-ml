# GeoSense Processing

Aplikasi desktop Python untuk menerima data mentah 3 geofon lewat MQTT,
menampilkan sinyal tiap node, menjalankan klasifikasi aktivitas dengan Random
Forest, dan menghitung estimasi lokasi sumber getaran dengan TDOA.

## Alur data

```
ESP32 / geofon
  -> MQTT geosense/raw/<node>
  -> main_processor.py
  -> MQTT geosense/data
  -> backend FastAPI
  -> Socket.IO geosense_update
  -> dashboard admin
```

Topik yang dipakai:

| Arah | Topik | Keterangan |
|---|---|---|
| Masuk | `geosense/raw/#` | Data mentah per node |
| Keluar | `geosense/data` | Statistik, sinyal 3 node, aktivitas ML, lokasi TDOA |

Node yang dikenali: `geo_1`, `geo_2`, `geo_3`. Topik dapat memakai akhiran
`geo_1`, `geo-1`, `geofon_1`, `node_1`, atau `1`.

## Format payload mentah yang didukung

```json
{"sensor_id": "geo_2", "d": [0.0123, 0.0125, 0.0121]}
```

```json
{"voltage": 0.0123}
```

```json
{"node": "geo_3", "mv": 12.3}
```

Payload angka tunggal, misalnya `0.0123`, juga diterima dan dianggap Volt.
Payload tanpa unit akan dikalikan 1000 menjadi mV, sedangkan key `mv` dipakai
langsung sebagai mV.

## Menjalankan

```powershell
cd C:\geosense-processing
pip install -r requirements.txt

# Sekali saja saat dataset mentah berubah
python gabungan_csv.py
python train_model.py

python main_processor.py
```

Dataset gabungan ditulis ke `data/dataset_geophone_gabungan.csv` dan model
disimpan ke `models/geosense_model.pkl`.

## Konfigurasi

Nilai berikut bisa diatur lewat environment variable:

| Variable | Default | Fungsi |
|---|---|---|
| `KONDISI_TANAH` | ditanyakan saat start | Contoh `tanah_basah` |
| `MQTT_BROKER` | HiveMQ proyek | Host broker |
| `MQTT_PORT` | `8883` | Port TLS |
| `MQTT_USERNAME` | `Kelom2` | Username MQTT |
| `MQTT_PASSWORD` | `TCPRule1` | Password MQTT |
| `MQTT_TOPIC_RAW` | `geosense/raw/#` | Topik data mentah |
| `MQTT_TOPIC_DATA` | `geosense/data` | Topik hasil processing |
| `DEFAULT_SENSOR_ID` | `geo_1` | Fallback jika topik tanpa ID |
| `SIGNAL_POINTS_TO_PUBLISH` | `256` | Jumlah sampel per node yang dikirim |
| `NODE_OFFLINE_AFTER_SEC` | `5` | Batas waktu node dianggap offline |

Contoh PowerShell:

```powershell
$env:KONDISI_TANAH = "tanah_basah"
$env:SIGNAL_POINTS_TO_PUBLISH = "128"
python main_processor.py
```

## Kalibrasi TDOA

Koordinat geofon dan kecepatan gelombang diatur di `main_processor.py`:

```python
GEOPHONE_POS = {
    "geo_1": np.array([0.0, 0.0]),
    "geo_2": np.array([10.0, 0.0]),
    "geo_3": np.array([5.0, 8.66]),
}
WAVE_SPEED = 300.0  # m/s
```

Ganti nilai tersebut dengan jarak asli di lapangan. Hasil `location` memakai
koordinat lokal dalam meter relatif terhadap Geofon 1, bukan latitude/longitude,
sehingga tidak langsung digambar sebagai marker Leaflet.

## Catatan model

Random Forest saat ini memprediksi aktivitas (`diem` / `jalan`) dari window
Geofon 1. Estimasi lokasi memakai TDOA dan optimasi non-linear, bukan Random
Forest, karena dataset yang ada belum memiliki label koordinat sumber getaran.
