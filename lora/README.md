# LoRa GeoSense — Konfigurasi Radio + Reader Teks

Folder ini adalah **satu-satunya sumber konfigurasi radio LoRa** untuk
GeoSense di Jetson. Folder ini dipakai oleh dua program:

| Program | Fungsi |
|---|---|
| `../lora_receiver.py` | **Pipeline produksi**: frame biner V2 → ACK → MQTT |
| `lora_read.py` | **Diagnostik**: membaca baris teks/CSV mentah dari radio |

> Pipeline dashboard memakai `lora_receiver.py`, bukan `lora_read.py`.
> `lora_read.py` hanya untuk cek cepat apakah radio menerima data.

## File

| File | Fungsi |
|---|---|
| `config.env` | Port, baud, ACK, log, dan dokumentasi setting radio |
| `config.env.example` | Template; disalin installer menjadi `config.env` |
| `lora_config.py` | Loader konfigurasi untuk kedua program |
| `lora_read.py` | Reader teks/CSV (diagnostik) |
| `run.sh` | Launcher `lora_read.py` |
| `README.md` | Dokumen ini |
| `AGENTS.md` | Instruksi tambahan untuk AI/engineer |

## Setting radio yang wajib sama di semua node

- Channel: **18** (`0x12`)
- UART: **9600 8N1**
- Air data rate: **2.4k**
- Mode: **transparent**
- Board E15-USB-T2: jumper **M0 & M1 terpasang**, jumper **RX/TX dicabut**
- Baud script: **9600** (bukan 115200), kecuali memakai ESP32 bridge.

Setting channel/air rate dilakukan di modul E220/firmware, bukan oleh script
Python. `config.env` menyimpannya sebagai dokumentasi/validasi agar tidak
tertukar saat migrasi.

## Port serial

Cari path stabil:

```bash
ls -l /dev/serial/by-id/
```

Edit `config.env`:

```dotenv
LORA_PORT=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
LORA_BAUD=9600
LORA_NO_ACK=0
```

`LORA_NO_ACK=1` hanya untuk ESP32 bridge yang sudah mengirim ACK. Jangan
menjalankan dua pihak yang sama-sama ACK.

## Menjalankan reader teks (diagnostik)

```bash
cd /opt/geosense/geosense-processing
/opt/geosense/venv/bin/python lora/lora_read.py
```

Atau:

```bash
lora/run.sh
```

Reader akan memakai `LORA_PORT`, `LORA_BAUD`, dan `LORA_TEXT_LOG` dari
`config.env`. Untuk override manual:

```bash
lora/run.sh /dev/ttyUSB0 9600 --no-timestamp
lora/run.sh --log /tmp/lora-text.log
```

Output normal misalnya:

```text
[2026-10-15 14:29:36] Hello from ESP32 #110
```

Kalau data kosong: cek channel/air rate, jumper RX/TX, baud, dan apakah
Serial Monitor lain sedang memegang port.

## Menjalankan pipeline produksi (biner)

```bash
cd /opt/geosense/geosense-processing
/opt/geosense/venv/bin/python lora_receiver.py
```

Atau via systemd:

```bash
sudo systemctl restart geosense-receiver
sudo journalctl -u geosense-receiver -f
```

`lora_receiver.py` memakai konfigurasi yang sama (`config.env`) untuk port,
baud, ACK, dan path log event biner.

## Troubleshooting

| Gejala | Penyebab / solusi |
|---|---|
| Tidak ada `/dev/ttyUSB*` | board belum tercolok, kabel rusak, cek `dmesg | tail` |
| `Permission denied` | user `geosense` belum masuk grup `dialout` |
| Port terbuka tapi data kosong | jumper RX/TX masih terpasang, channel beda, atau port dipegang program lain |
| Data acak | baud/air rate tidak sama |
| Port echo balik | jumper RX/TX masih terpasang; cabut |
| Reader teks jalan tapi dashboard kosong | normal: teks bukan frame biner; cek `lora_receiver.py` dan MQTT |

## Catatan banyak node

Transparent mode membuat semua node di channel yang sama saling mendengar dan
bisa bertabrakan. Untuk skala besar, gunakan ID node di payload + checksum,
atau fixed transmission/address di E220. Pipeline biner GeoSense sudah
memakai `node_index` 0-based (`NODE01=0`, `NODE02=1`, `NODE03=2`).
