# Uji ML dan TDOA dengan ESP Dummy

Dokumen ini untuk skenario:

```text
ESP dummy (NODE01/NODE02/NODE03)
  -> LoRa E220
  -> gateway E15 di Jetson
  -> lora_receiver.py
  -> MQTT rawdata
  -> ML/TDOA
  -> backend
```

## Aturan utama

- Untuk ML dan TDOA, kirim **event waveform**, bukan feature JSON 7 nilai.
- Event waveform bisa berupa frame biner V2 atau event JSON.
- GPS JSON boleh dikirim, tetapi hanya untuk posisi node.
- Heartbeat tidak dipakai ML.
- Feature JSON 7 nilai diabaikan oleh pipeline ini.

## Event JSON yang diterima gateway

Gateway versi terbaru mendukung event JSON dengan format:

```json
{
  "type": "event",
  "n": "NODE01",
  "event_id": "dummy-001",
  "utc": "2026-10-27T09:15:42.000Z",
  "samples": [0.0, 0.01, 0.02, "..."],
  "trigger_index": 512,
  "sample_period_us": 11111.111,
  "p2p": 0.5,
  "rms": 0.12,
  "peak": 0.4,
  "unit": "adc_counts"
}
```

Field penting:

| Field | Wajib | Keterangan |
|---|---|---|
| `type` | ya | Harus `event` |
| `n` | ya | `NODE01`, `NODE02`, atau `NODE03` |
| `event_id` | ya | ID unik per event |
| `utc` | ya | Waktu event, format ISO 8601 dengan `Z` |
| `samples` | ya | Minimal 64, ideal 1024 untuk TDOA default (window 512 + preroll 180) |
| `trigger_index` | ya | Indeks sampel saat trigger, mis. 512 |
| `sample_period_us` | ya | `11111.111` untuk 90 SPS |
| `p2p`, `rms`, `peak` | tidak | Statistik opsional |
| `unit` | tidak | Default `adc_counts` |

Contoh untuk 3 node:

```text
NODE01 -> node index 0 -> geo_1
NODE02 -> node index 1 -> geo_2
NODE03 -> node index 2 -> geo_3
```

Kirim ketiganya dengan UTC berdekatan. Untuk TDOA, beri delay kecil antar node,
misalnya 0 ms, 1.5 ms, dan 3 ms.

Jika hanya menguji ML dan service ML meminta tepat 256 sampel, gunakan
`--samples 256`. Untuk TDOA, gunakan 1024 sampel.

Payload harus diakhiri newline `\n` sebelum `lora.transmit()`.

## Routing di gateway

`lora_receiver.py` sekarang memilah JSON berdasarkan `type`:

| `type` | Tujuan |
|---|---|
| `event` | `rawdata` |
| `gps` atau `gnss` | `gpsdata` |
| `hb` atau `heartbeat` | log saja |
| `feature` | diabaikan dengan peringatan |
| tidak dikenal | log peringatan |

Jika memakai frame biner V2, routing lama tetap berlaku dan tidak berubah.

## Tes tanpa LoRa

Untuk memastikan ML/TDOA/backend siap sebelum ESP dikirim:

```bash
cd /opt/geosense/geosense-processing
/opt/geosense/venv/bin/python tools/publish_dummy_events.py \
  --host 127.0.0.1 --port 1883 --count 1
```

Script mengirim 3 event sintetis ke `rawdata`. Untuk mencetak payload utuh
tanpa MQTT:

```bash
/opt/geosense/venv/bin/python tools/publish_dummy_events.py \
  --dry-run --count 1 > events.jsonl
```

Cek MQTT:

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t rawdata -t gpsdata -t geosense/data -v
```

Cek log:

```bash
sudo journalctl -u geosense-tdoa -f
sudo journalctl -u geosense-backend -f
```

## Tes lewat LoRa

1. Pastikan ESP mengirim JSON satu baris dan newline.
2. Pastikan node ID benar.
3. Jalankan gateway:

```bash
sudo journalctl -u geosense-receiver -f
```

Yang harus muncul:

```text
EVENT JSON node=NODE01 event=dummy-001 samples=1024
```

Lalu cek `rawdata`. Jika event masuk `rawdata`, ML/TDOA dapat memprosesnya.

## Catatan model

Model yang ada di repo ini memakai 256 sampel mentah:

```text
col_0 ... col_255
```

Jika ML di Jetson memakai model 7 fitur, model itu harus dilatih ulang dan
service ML harus membaca payload `type=feature`. Jangan mencampur model 256
sampel dengan payload 7 fitur.
