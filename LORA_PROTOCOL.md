# Protokol LoRa GeoSense v2 (frame biner)

Implementasi Python:

- `lora_protocol.py` — CRC, header, payload, ACK, demux biner/JSON.
- `lora_events.py` — penyusunan event (META + DATA + END).
- `lora_receiver.py` — receiver serial → ACK → MQTT.

## Struktur frame

```
[ header 25 byte ][ payload N byte ][ CRC-16 2 byte ]
```

Semua nilai multibyte **little-endian**. CRC-16/CCITT-FALSE (poly `0x1021`,
init `0xFFFF`, tanpa reflect/xorout), dihitung atas header + payload, dikirim
little-endian. Batas frame firmware node 96 byte (receiver toleran sampai 128).

### Layout header

| Offset | Field | Byte |
|---|---|---:|
| 0 | Sync `47 53` (`GS`) | 2 |
| 2 | Versi protokol (`2`) | 1 |
| 3 | Tipe: DATA=1, ACK=2, EVENT_END=3, EVENT_META=4 | 1 |
| 4 | Node index (zero-based: NODE01=0, NODE02=1, NODE03=2) | 1 |
| 5 | Flags: bit0 denoised, bit1 GPS UTC valid | 1 |
| 6 | Event ID | 4 |
| 10 | Packet sequence (zero-based) | 2 |
| 12 | Jumlah paket DATA dalam event | 2 |
| 14 | Indeks sampel pertama (DATA) | 2 |
| 16 | Jumlah sampel di frame (DATA) | 1 |
| 17 | Periode sampel nominal (µs) | 2 |
| 19 | Timestamp MCU sampel pertama (µs) | 4 |
| 23 | Encoding: 1 = int32 + zigzag LEB128 | 1 |
| 24 | Panjang payload | 1 |

## EVENT_META

- `packet_sequence = 0`, payload 12 byte.
- UTC detik `u32`, milidetik `u16`, total sampel `u16`, indeks trigger `u16`,
  ID wavelet `u8` (1 = Haar), level wavelet `u8`.
- UTC adalah perkiraan dari GPS + offset MCU, **bukan PPS**. Kalau GPS invalid,
  UTC = 0 dan flag bit1 clear.

## DATA

- `sample_count > 0`, payload minimal 4 byte.
- Sampel pertama `int32` little-endian.
- Sampel berikutnya: delta bertanda → zigzag → LEB128.
- Sampel diletakkan berdasarkan `first_sample_index` (boleh out-of-order).
- Nilai = **ADC counts** hasil denoising, bukan volt.

## EVENT_END

- `packet_sequence == total_packets`.
- Tidak punya payload.
- Event dianggap lengkap hanya jika semua paket DATA diterima **dan** END
  diterima, tanpa sampel bolong.

## ACK

- 27 byte: header 25 byte + CRC 2 byte, tanpa payload.
- Menyalin node ID, event ID, packet sequence, dan total packets dari frame
  yang di-ACK.
- `flags` ACK = tipe frame yang diakui (1/3/4).
- `sample_count`, `encoding`, dan `payload_length` = 0.
- Receiver meng-ACK META, setiap DATA (termasuk duplikat), dan END.
- Kalau ACK untuk END hilang dan node retry, receiver meng-ACK lagi tanpa
  mem-publish event dua kali (`event-duplicate`).

## Paket GPS (JSON legacy, link yang sama)

Node juga mengirim baris JSON newline-terminated setiap ±10 detik:

```json
{"n":"NODE01","type":"gps","lat":-6.9,"lon":107.6,"sat":8,"hdop":1.2,
 "utc":"2026-10-23T10:15:30Z","uptime_ms":60000}
```

Receiver memisahkan ini dari frame biner dan meneruskannya ke MQTT `gpsdata`.
Tidak ada ACK untuk JSON.

## Alur data

```
Node STM32 --LoRa--> EBYTE (USB-UART di Jetson)
                          |
                    lora_receiver.py
                    |-- ACK --> node
                    |-- event JSON --> MQTT rawdata
                    |-- GPS JSON   --> MQTT gpsdata
                                        |
                                  backend FastAPI
```

Untuk uji sekarang, laptop berperan sebagai Jetson. Radio EBYTE dihubungkan ke
laptop lewat USB-UART, UART radio **9600 8N1** (bukan kecepatan udara LoRa).

Catatan: firmware ESP32 dummy receiver yang ada hanya menampilkan log/plot ke
USB; ia tidak meneruskan frame biner ke Jetson. Untuk jalur Jetson, pakai radio
EBYTE + USB-UART (atau tambahkan mode bridge di firmware ESP32).

## Menjalankan

> Konfigurasi radio terpusat di `lora/config.env`. Tanpa argumen,
> `lora_receiver.py` membaca port/baud/ACK/log dari file tersebut. Contoh di
> bawah adalah override manual.

```powershell
# Radio EBYTE langsung ke laptop
python lora_receiver.py --port COM5 --baud 9600

# Tanpa MQTT (uji LoRa/ACK saja)
python lora_receiver.py --port COM5 --baud 9600 --no-mqtt

# Kalau ada jembatan lain yang sudah meng-ACK
python lora_receiver.py --port COM5 --baud 9600 --no-ack
```

Output:

- log ACK/event di terminal,
- `lora_events.jsonl` berisi event lengkap,
- MQTT `rawdata` (event) dan `gpsdata` (GPS).

## Catatan

- Kalibrasi ADC belum tersedia; dashboard menampilkan `unit: adc_counts`.
- Varian EBYTE (E220/E32), frekuensi, air rate, dan baud harus cocok antara
  node dan receiver. Firmware memakai library E220.
- Implementasi ACK mengandalkan UART radio; AUX tidak dikontrol lewat adapter
  USB. Kalau ACK sering gagal, cek mode/AUX dan timing radio.

## TDOA

`event_tdoa.py` mengonsumsi event dari `rawdata`, memasangkan 3 node, lalu
mempublish `geosense/data` (lihat `README.md`). Backend subscribe topik itu
juga.

## Firmware node V2

Folder `GeoSense Node_V2` memakai **protokol biner yang sama** dengan V1
(header 25 byte, CRC, META/DATA/END/ACK). Perubahan V2 yang relevan:

- TX frame biner memakai UART langsung + handshake AUX (bukan
  `E220.sendMessage`), supaya byte ACK yang masuk saat turnaround tidak hilang.
- Timeout ACK event 5000 ms.
- STA/LTA threshold 2.5; event hanya dikirim saat trigger (bukan stream
  fitur terus-menerus).
- GPS JSON tetap dikirim tiap 10 detik di link yang sama.
- Node index tetap 0-based: NODE01=0, NODE02=1, NODE03=2.

### Dua cara menerima di laptop/Jetson

1. **Radio EBYTE langsung ke USB-UART** (deployed path):

   ```powershell
   python lora_receiver.py --port COM5 --baud 9600
   ```

   Receiver mengirim ACK sendiri.

2. **Lewat ESP32 receiver mode bridge** (untuk hardware uji yang ada):

   Flash environment `esp32dev_bridge` dari project
   `GeoSense_Dummy_Jetson_ESP32`. ESP32 tetap meng-ACK node dan meneruskan
   frame biner + JSON GPS ke USB. Di Jetson:

   ```powershell
   python lora_receiver.py --port COM5 --baud 115200 --no-ack
   ```

   Gunakan `--no-ack` karena ACK sudah dikirim ESP32.

## Mode uji event JSON (ESP dummy)

`lora_receiver.py` juga menerima event JSON dari ESP dummy. Format dan contoh
payload ada di `LORA_JSON_TEST.md`. Fitur JSON 7 nilai bukan input model 256
sampel dan akan diabaikan dengan peringatan.
