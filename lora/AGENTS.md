# AGENTS.md — LoRa GeoSense di Jetson

Folder ini adalah **sumber konfigurasi radio LoRa** untuk GeoSense.

- Pipeline produksi: `../lora_receiver.py` (frame biner V2 → ACK → MQTT).
- Reader teks diagnostik: `lora_read.py` (bukan pipeline dashboard).
- Keduanya membaca `config.env` di folder ini.
- Panduan migrasi lengkap: `../../AI-IMPLEMENTATION-GUIDE.md`.

## Hardware / setting yang tidak boleh diubah

- Channel **18** (`0x12`), UART **9600 8N1**, air rate **2.4k**,
  mode **transparent**.
- Board E15-USB-T2: jumper **M0 & M1 terpasang**, jumper **RX/TX dicabut**.
- Baud script **9600** untuk radio langsung; **115200** hanya untuk ESP32
  bridge yang sudah meng-ACK.
- Hanya satu pihak yang boleh mengirim ACK.

## Cek cepat di Jetson

```bash
ls -l /dev/serial/by-id/
cd /opt/geosense/geosense-processing

# reader teks (diagnostik)
/opt/geosense/venv/bin/python lora/lora_read.py

# pipeline biner (produksi)
/opt/geosense/venv/bin/python lora_receiver.py
```

Kalau `LORA_PORT` masih `CHANGE-ME`, edit `lora/config.env` terlebih dahulu.

## Troubleshooting

| Gejala | Solusi |
|---|---|
| `Permission denied` | `sudo usermod -aG dialout geosense`, lalu reboot |
| Tidak ada `/dev/ttyUSB*` | cek kabel/port, `dmesg | tail` |
| Data kosong | cek channel/air rate, jumper RX/TX, baud, port dipakai program lain |
| Data acak | baud/air rate tidak sama |
| Port echo | jumper RX/TX masih terpasang; cabut |
| Dashboard kosong | pastikan memakai `lora_receiver.py`, bukan `lora_read.py` |
