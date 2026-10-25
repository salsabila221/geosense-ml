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
| Masuk | `geosense/raw/#`, `rawdata` | Data mentah per node |
| Keluar | `geosense/data` | Statistik, sinyal 3 node, aktivitas ML, lokasi TDOA |

Node yang dikenali: `geo_1`, `geo_2`, `geo_3`. Topik/payload dapat memakai
`geo_1`, `geo-1`, `geofon_1`, `node_1`, `NODE01`, atau `1`.

## Format payload mentah yang didukung

Format firmware saat ini (satu node per pesan):

```json
{"n": "NODE01", "p": 27, "t": 50843, "d": [0.00012, 0.00143, 0.03797]}
```

`n` = ID node, `p` = nomor paket, `t` = uptime device (ms), `d` = sampel.

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

## Protokol event waveform (baru)

Firmware baru tidak mengirim stream kontinu, tetapi satu **event** yang terdiri
dari `EVENT_META` → `DATA` (beberapa frame) → `EVENT_END`, berisi waveform hasil
denoising Haar level 5 dalam **ADC counts** (bukan volt). Event hanya dikirim
saat STA/LTA mendeteksi getaran.

Processing saat ini masih memakai buffer kontinu 90 SPS dan model ML lama,
sehingga payload event sengaja **tidak** dimasukkan ke buffer (lihat
`is_event_payload`). Supaya processing bisa memproses event, perlu:

1. pengelompokan event per node berdasarkan `event_id`,
2. pemasangan event antar-node memakai UTC/trigger (UTC GPS hanya perkiraan),
3. TDOA dihitung dari waveform event, bukan buffer kontinu,
4. model ML dilatih ulang dengan data ADC counts + denoising wavelet.

Kontrak MQTT dan penjelasan lengkap ada di
`C:\geosense-backend\EVENT_WAVEFORM.md`.

### Receiver LoRa + MQTT

Node mengirim frame **biner** v2 lewat LoRa; GPS dikirim sebagai JSON di link
yang sama. Receiver membaca radio EBYTE lewat USB-UART, mengirim ACK, menyusun
event, lalu publish ke MQTT. Detail protokol ada di `LORA_PROTOCOL.md`.

```powershell
# Radio EBYTE langsung ke laptop (UART radio 9600)
python lora_receiver.py --port COM5 --baud 9600

# Uji LoRa/ACK tanpa MQTT
python lora_receiver.py --port COM5 --baud 9600 --no-mqtt

# Hanya capture byte mentah
python binary_capture.py --port COM5 --baud 9600 --seconds 30
```

`lora_receiver.py` menyimpan event lengkap ke `lora_events.jsonl`, publish
event ke `rawdata` dan GPS ke `gpsdata`.

Firmware node V2 masih memakai protokol biner yang sama. Dua opsi receiver:

```powershell
# Radio EBYTE langsung ke USB-UART (receiver mengirim ACK)
python lora_receiver.py --port COM5 --baud 9600

# ESP32 receiver mode bridge (ESP32 yang mengirim ACK)
python lora_receiver.py --port COM5 --baud 115200 --no-ack
```

### TDOA event mode

```powershell
python event_tdoa.py
```

Service ini berlangganan `rawdata` + `gpsdata`, memasangkan event dari 3 node
berdasarkan UTC, menghitung TDOA dengan cross-correlation, lalu publish hasil
ke `geosense/data`. Backend harus subscribe `geosense/data` (sudah default di
`.env` contoh).

Konfigurasi env penting:

| Variable | Default | Fungsi |
|---|---|---|
| `WAVE_SPEED` | `300` | Kecepatan gelombang tanah (m/s) |
| `GEOPHONE_POS_JSON` | posisi segitiga 10 m | Posisi node (meter, relatif geo_1) |
| `TDOA_PAIR_TOLERANCE_S` | `2.0` | Toleransi pairing UTC antar-node |
| `TDOA_WINDOW_SAMPLES` | `512` | Panjang window cross-correlation |
| `TDOA_PREROLL_SAMPLES` | `180` | Pre-roll window |
| `TDOA_MIN_QUALITY` | `0.1` | Ambang normalized correlation |
| `TDOA_MAX_RESIDUAL` | `1e-3` | Ambang residual least-squares |

Catatan: UTC dari GPS **bukan PPS**, jadi selisih jam antar-node masih masuk ke
TDOA. Anggap hasilnya perkiraan; kalibrasi `WAVE_SPEED`, posisi node, dan
`TDOA_MAX_LAG_S` bila delay terlihat tidak masuk akal.

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
| `MQTT_BROKER` | `35f0ff8245ed4c5c8d3838dc3bd1ba62.s1.eu.hivemq.cloud` | Host broker |
| `MQTT_PORT` | `8883` | Port TLS |
| `MQTT_USERNAME` | `geosense-process` | Username MQTT |
| `MQTT_PASSWORD` | (kosong) | Password MQTT — wajib diisi lewat env |
| `MQTT_TOPIC_RAW` | `geosense/raw/#,rawdata` | Topik data mentah (pisahkan dengan koma) |
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
