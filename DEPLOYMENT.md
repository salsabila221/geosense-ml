# Deploy di Jetson (rencana)

Sementara ini laptop dipakai sebagai "Jetson" untuk uji coba. Arsitektur yang
dituju:

```
Node STM32 --LoRa biner--> EBYTE radio (USB-UART di Jetson)
                                |
                          lora_receiver.py
                          |-- ACK --> node
                          |-- event JSON --> MQTT rawdata
                          |-- GPS JSON   --> MQTT gpsdata
                                              |
                                        event_tdoa.py
                                        (pairing 3 node + TDOA)
                                              |
                                        MQTT geosense/data
                                              |
                                        backend FastAPI
                                        (Supabase + Socket.IO)
                                              |
                                        dashboard browser
```

## Dua service di Jetson

### 1. `geosense-lora` (receiver)

- Membaca frame biner dari radio EBYTE lewat USB-UART.
- Memvalidasi CRC-16/CCITT-FALSE.
- Mengirim ACK untuk `EVENT_META`, setiap `DATA`, dan `EVENT_END`.
- Menyusun event berdasarkan `first_sample_index`.
- Meneruskan event ke MQTT `rawdata` dan GPS JSON ke `gpsdata`.

Contoh jalan manual:

```bash
cd /opt/geosense/geosense-processing
python3 lora_receiver.py --port /dev/ttyUSB0 --baud 9600
```

Di Linux, port USB sebaiknya dipakai lewat path stabil:

```bash
ls -l /dev/serial/by-id/
python3 lora_receiver.py --port /dev/serial/by-id/usb-... --baud 9600
```

Baud 9600 adalah UART radio, bukan kecepatan udara LoRa. Kalau memakai ESP32
receiver sebagai jembatan yang sudah meng-ACK, flash environment
`esp32dev_bridge` dari `GeoSense_Dummy_Jetson_ESP32`, lalu jalankan:

```bash
python3 lora_receiver.py --port /dev/ttyUSB0 --baud 115200 --no-ack
```

`--no-ack` wajib karena ACK sudah dikirim ESP32.

### 2. `geosense-tdoa` (processing event)

- Berlangganan `rawdata` + `gpsdata`.
- Memasangkan event 3 node berdasarkan UTC.
- Menghitung TDOA dari waveform event.
- Publish hasil ke `geosense/data`.

```bash
cd /opt/geosense/geosense-processing
python3 event_tdoa.py
```

### 3. `geosense-backend` (FastAPI)

```bash
cd /opt/geosense/geosense-backend
uvicorn main:app --host 0.0.0.0 --port 8000
```

Konfigurasi lewat `.env` (jangan hardcode credential):

```
MQTT_BROKER=<host-cluster>
MQTT_PORT=8883
MQTT_TOPIC=rawdata,gpsdata,geosense/data
MQTT_USERNAME=<user>
MQTT_PASSWORD=<password>
DATABASE_URL=<supabase>
```

## Contoh systemd (template)

`/etc/systemd/system/geosense-backend.service`:

```ini
[Unit]
Description=GeoSense backend
After=network-online.target

[Service]
User=YOUR_USER
WorkingDirectory=/opt/geosense/geosense-backend
EnvironmentFile=/opt/geosense/geosense-backend/.env
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/geosense-lora.service`:

```ini
[Unit]
Description=GeoSense LoRa receiver
After=network-online.target

[Service]
User=YOUR_USER
WorkingDirectory=/opt/geosense/geosense-processing
EnvironmentFile=/opt/geosense/geosense-processing/.env
ExecStart=/usr/bin/python3 lora_receiver.py --port /dev/serial/by-id/USB-SERIAL --baud 9600
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/geosense-tdoa.service`:

```ini
[Unit]
Description=GeoSense TDOA event processing
After=network-online.target

[Service]
User=YOUR_USER
WorkingDirectory=/opt/geosense/geosense-processing
EnvironmentFile=/opt/geosense/geosense-processing/.env
ExecStart=/usr/bin/python3 event_tdoa.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Aktifkan:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now geosense-backend geosense-lora geosense-tdoa
sudo journalctl -u geosense-backend -f
sudo journalctl -u geosense-lora -f
sudo journalctl -u geosense-tdoa -f
```

## Catatan

- Password HiveMQ sebaiknya diganti karena pernah muncul di chat.
- Jangan menaruh password di kode; pakai `.env` dengan permission `600`.
- Backend subscribe `rawdata,gpsdata,geosense/data` supaya hasil TDOA masuk
  dashboard.
- Uji komunikasi LoRa/ACK tidak membutuhkan MQTT; MQTT baru diperlukan saat
  event sudah berhasil dirangkai.
