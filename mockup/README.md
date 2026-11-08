# GeoSense ML Mockup Lokal

Mockup ini hanya menjalankan **model ML** di laptop memakai data statis.
Tidak mengirim MQTT, tidak memanggil backend, dan tidak mengubah frontend.

Ekstrak folder `mockup/` ke:

```text
C:\geosense-processing\mockup
```

supaya script bisa memakai `event_tdoa.py` dan `models/geosense_model.pkl`
dari repo processing.

## Hasil

- Kondisi 1: getaran rendah, ML memprediksi `diem`, status `AMAN`.
- Kondisi 2: getaran di area antara Gedung PPBS dan Lapangan Merah, ML
  memprediksi `jalan`, status `SIAGA`, dan TDOA menghasilkan estimasi lokasi.

## File

| File | Fungsi |
|---|---|
| `make_mock_data.py` | Membuat `mock_ml_data.json` dari dataset dan sinyal sintetis |
| `mock_ml_data.json` | Data statis: 3 node, 2 skenario |
| `mock_ml_windows.csv` | 6 window 256 sampel siap dipakai model |
| `run_mock.py` | Menjalankan model ML dan TDOA lokal |
| `plot_mock.py` | Menampilkan waveform mock 3 node dengan matplotlib |
| `mock_waveforms.png` | Hasil plot waveform normal dan event |
| `publish_mock_mqtt.py` | Kirim mock ke MQTT dan cek backend |
| `loop_normal.bat` | Looping getaran normal |
| `loop_event.bat` | Looping event tanpa ubah status |
| `mock_ml_output.json` | Hasil prediksi dalam JSON |
| `mock_ml_report.html` | Laporan visual lokal |

## Tampilkan grafik di processing

```bash
cd C:\geosense-processing\mockup
python plot_mock.py
```

Hasilnya:

```text
mock_waveforms.png
```

Grafik berisi 3 node Geofon untuk kondisi normal dan event. Script ini tidak
memakai MQTT, backend, atau frontend.

## Jalankan

Dari folder ini:

```bash
python run_mock.py
```

Atau langsung buka laporannya:

```bash
python run_mock.py --open
```

Di Windows bisa juga klik `run_mock.bat`.

## Node dan lokasi

| Node | Lokasi | Latitude | Longitude |
|---|---|---|---|
| `geo_1` | Gedung PPBS | -6.9233875 | 107.7731538 |
| `geo_2` | Parking Jembatan Cincin | -6.9244170 | 107.7738624 |
| `geo_3` | Lapangan Merah | -6.9253700 | 107.7742400 |

Event simulasi berada di:

```text
-6.9242921, 107.7736967
```

Area antara Gedung PPBS dan Lapangan Merah, sekitar 23 meter dari node
`geo_2`.

Sumber koordinat:

- Gedung PPBS: OpenStreetMap, `PPBS (Gedung A)`
- Lapangan Merah: GeoView, `Lapangan Merah`
- Parking Jembatan Cincin: OpenStreetMap reverse geocoding

`mock_ml_windows.csv` berisi 6 baris:

```text
normal/geo_1, normal/geo_2, normal/geo_3
event/geo_1,  event/geo_2,  event/geo_3
```

Setiap baris punya 256 kolom `col_0` sampai `col_255`, plus kolom
`scenario`, `node`, `expected_activity`, dan `expected_status`.

## Cara kerja

### Kondisi normal

- Memakai 3 baris nyata dari dataset `diem` yang punya confidence tinggi.
- Setiap node mendapat window 256 sampel.
- Model ML memprediksi `diem` dengan confidence tinggi.
- p2p maksimum sekitar `0.0031 mV`, jadi status `AMAN`.

### Kondisi event

- Membuat waveform 1024 sampel per node.
- Sumber getaran berada di area antara PPBS dan Lapangan Merah.
- Setiap node mendapat delay sesuai jarak dan kecepatan gelombang 300 m/s.
- ML memakai window 256 sampel di sekitar trigger.
- TDOA memakai 3 event yang sama dan menghasilkan estimasi `x, y`.
- Estimasi dikonversi kembali ke latitude/longitude.

## Spike event 500 Hz 24-bit

Mock event baru memakai 24-bit ADC counts, 500 Hz, 15 detik:

```text
0-5 s   baseline
5-15 s  spike/burst
>15 s   stop (tidak loop)
```

Generate file saja:

```bash
python spike_event_500hz.py --generate-only
```

Kirim streaming dinamis (default, 0.5 detik per chunk):

```bash
python spike_event_500hz.py --publish
```

Snapshot event dipersist tanpa menimpa chart; setelah event, stream kembali
ke baseline idle dan berhenti tanpa looping.

Kalau hanya ingin satu snapshot statis:

```bash
python spike_event_500hz.py --publish --snapshot
```

File hasil:

```text
spike_event_500hz.json
spike_event_500hz_windows.csv
```

Frontend mengonversi `signal` counts ke mV memakai `mv_per_count`
(2e-6 mV/count, sehingga +/-7.000.000 counts = +/-14 mV).

## Kirim ke MQTT dan backend

Setelah `run_mock.py`, event mock bisa dikirim ke HiveMQ dan backend lokal:

```bash
python publish_mock_mqtt.py
```

Mode default aman:

- publish ke topik `rawdata`
- backend menyimpan telemetry
- `last_updated` backend berubah
- status sistem tidak diubah
- WhatsApp/Web Push tidak dipicu

Cek hasilnya:

```bash
python publish_mock_mqtt.py --wait 5
```

Looping kondisi normal:

```bash
python publish_mock_mqtt.py --condition normal --loop --interval 10
```

Looping kondisi event tanpa mengubah status:

```bash
python publish_mock_mqtt.py --condition event --loop --interval 10
```

Looping event dengan status SIAGA:

```bash
python publish_mock_mqtt.py --condition event --status SIAGA --loop --interval 10
```

Perintah `--status SIAGA` **dapat memicu notifikasi WhatsApp dan Web Push**
ke user terdaftar. Jangan dipakai tanpa sengaja.

Untuk berhenti, tekan `Ctrl+C`.

## Catatan

- Model yang dipakai: `../models/geosense_model.pkl`.
- Model memakai 256 fitur `col_0` sampai `col_255`.
- Kelas model saat ini: `diem` dan `jalan`.
- Mockup ini tidak memakai feature JSON 7 nilai.
- Jika path model berbeda:

```bash
python run_mock.py --model "D:\path\ke\geosense_model.pkl"
```

- Jika dataset berubah, buat ulang data statis:

```bash
python make_mock_data.py
python run_mock.py
```
