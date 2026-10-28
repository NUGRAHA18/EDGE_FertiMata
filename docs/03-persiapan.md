# Persiapan Sebelum Mengembangkan Edge Agent

Dibagi tiga: yang **bisa dikerjakan sekarang tanpa hardware**, yang **harus dicek ke barang**, dan
**keputusan** yang perlu dijawab tim. Kode boleh mulai ditulis setelah bagian A selesai. Bagian B dan C
menentukan kapan fase tertentu boleh dimulai (lihat kolom "Menghambat" dan [TASKS.md](../TASKS.md)).

---

## A. Lingkungan pengembangan (laptop)

| ID | Siapkan | Cara / catatan |
|---|---|---|
| P-S1 | Python **3.10** | Samakan dengan Jetson (Ubuntu 22.04). `py -3.10 -m venv .venv` |
| P-S2 | Git + repo ini | Sebaiknya dibuat juga di GitHub, seperti BE/FE |
| P-S3 | Docker Desktop | Untuk Mosquitto dari `BE-melonSmart/docker-compose.yml` |
| P-S4 | Backend bisa jalan dengan MQTT sungguhan | `SPRING_PROFILES_ACTIVE=dev MQTT_ENABLED=true DEV_SIMULATOR_ENABLED=false`, port 8090 (8080 dipakai Docker) |
| P-S5 | Klien MQTT untuk mengintip | `mosquitto_sub -t 'fertimata/#' -v` (dari container) atau MQTT Explorer |

**Status 23 Okt 2026:** P-S1 Python 3.10.11 terpasang (`py -3.10`), venv dibuat ulang dengan 3.10 · P-S2
repo `https://github.com/NUGRAHA18/EDGE_FertiMata.git` (remote `origin` sudah dipasang) · P-S3 Mosquitto
`fertimata-mosquitto` jalan di `0.0.0.0:1883` · P-S4 belum dicoba (lihat [07-uji-end-to-end.md](07-uji-end-to-end.md))
· P-S5 `docker exec -it fertimata-mosquitto mosquitto_sub -t 'fertimata/#' -v`.

## B. Di Jetson dan hardware (cek fisik)

Diambil dari Arsitektur §15. Setiap hasil ditulis ke `config/fertimata.yaml`, **bukan** ke kode.

| ID | Yang dicek | Cara | Menghambat |
|---|---|---|---|
| P-H1 | Akses SSH ke Jetson, user, Python 3.10, `pip`, `venv` | `ssh`, `python3 --version` | F10 |
| P-H2 | Adaptor RS485 muncul sebagai `/dev/rs485` | Pasang udev rule §2, `ls -l /dev/rs485`; hapus `brltty` bila perlu | F3 |
| P-H3 | Semua modul sudah diberi alamat unik (1, 3, 4, 6, 10, 20, 21, 30) dan baud 9600 | Set satu per satu terpisah dari bus (§4.7), tempel label | F3 |
| P-H4 | **Profil relay 4-ch & 8-ch**: coil (A) atau holding (B), register IN1, ada/tidaknya mode nyala-berwaktu | Manual penjual + uji baca `pymodbus` | **F4** |
| P-H5 | Register sensor tanah 7-in-1 (urutan, skala), baud bawaan unit SimonKori | Manual + uji baca | F3 (tanah) |
| P-H6 | Register sensor cahaya (alamat, urutan word 32-bit) | idem | F3 (cahaya) |
| P-H7 | Register DS18B20 Modbus dan skala | idem | F6 |
| P-H8 | Register ADC kanal A1–A7 dan satuannya (mV / 0,01 V) | idem | F6 |
| P-H9 | Nomor pin GPIO reed, HOME, END dari papan GPIO; versi libgpiod di Jetson | Dari pembuat papan; `gpioinfo`, `gpiodetect` | F7 |
| P-H10 | Kamera: VID:PID, format/resolusi, kontrol exposure/WB yang tersedia | `lsusb`, `v4l2-ctl --list-formats-ext`, `v4l2-ctl -l` | F8 |
| P-H11 | Baseline arus panel saat pompa OFF, arus nominal pompa | Baca PZEM saat komisioning. Isi `power.pump_baseline_a`, `power.pump_nominal_a`, lalu sesuaikan `ssr_short_margin_a` dengan noise bacaan | F5 (alert arus) |
| P-H12 | Volume kerja drum, debit tiap pompa peristaltik (3 ulangan), konsentrasi stok | Gelas ukur + stopwatch, Protokol §8 | F9 |
| P-H13 | Kalibrasi EC (1413 µS/cm, 12,88 mS/cm) dan pH (4,01 · 6,86 · 9,18) | Kit DFR0300 + buffer. Pakai `tools/calibrate_ec_ph.py --point ec1413|ec12880|ph401|ph686|ph918 --write` (agent dihentikan dulu); hasilnya masuk `analog.ec.cal` (mV, setara 25 °C) dan `analog.ph.cal` | F6 (nilai akhir) |
| P-H14 | Jam Jetson tersinkron NTP; baterai RTC | `timedatectl` | F2 (expiresAt) |
| P-H15 | Hardware watchdog Jetson bisa dipakai systemd | `ls /dev/watchdog*` | F10 |

Catatan P-H1: Jetson Python **3.10.12**, laptop **3.10.11**. Beda hanya di angka patch (perbaikan bug dan
keamanan), bahasa dan pustaka standarnya sama, jadi tidak berpengaruh. Yang penting: di Jetson pakai
`python3.10 -m venv` dan versi paket yang sama persis dengan `pyproject.toml` (sudah dikunci).

### Cara cek register dengan `modbus_probe` (P-H3 … P-H8)

Alat: `tools/modbus_probe.py` (TASKS F3.1). Jalankan di Jetson dari folder repo dengan venv aktif.
**Hentikan agent dulu** (`sudo systemctl stop fertimata-edge`): hanya satu program boleh memakai port.
Latihan di laptop tanpa hardware: tambahkan `--fake` (memakai simulator `config/fertimata.dev.yaml`).

Opsi umum: `--port /dev/rs485` (bawaan), `--unit <alamat>`, `--fc <1..6>`, `--address <register>`
(boleh `0x..`), `--count <jumlah>`, `--timeout <detik>`, `--repeat N --interval S`. Setiap register
ditampilkan sebagai `u16`, `s16`, dan `hex`; bila `--count ≥ 2` juga pasangan 32 bit dalam dua urutan
word (`lo_hi` dan `hi_lo`), sehingga skala dan urutan word bisa ditebak dari angka yang masuk akal.

| Cek | Perintah | Yang dicari → tulis ke config |
|---|---|---|
| **P-H3** alamat & baud | Bus lengkap: `python tools/modbus_probe.py --scan 1-31 --fc 3 --address 0 --timeout 0.3`, ulangi dengan `--fc 4` dan `--fc 1` (relay) | Alamat 1, 3, 4, 6, 10, 20, 21, 30 muncul ("menjawab" atau "ada, exception" — keduanya berarti perangkat ada). Yang tidak muncul: cek label alamat, A/B, baud (`--baud 4800`), daya |
| **P-H4** profil relay | Profil A: `--unit 20 --fc 5 --address 1 --value on --yes`, lalu `--fc 1 --address 0 --count 4`, lalu `--value off`. Profil B: `--unit 20 --fc 6 --address 1 --value 0x0100 --yes`, lalu `--value 0x0200`. IN1: `--unit 21 --fc 2 --address 0 --count 4` sambil float diangkat/diturunkan; kalau exception, coba `--fc 3`/`--fc 4` | **Matikan RCBO pompa dan cabut motor trolley sebelum uji ini.** Di profil B nomor kanal bisa mulai dari 1, sehingga register 1 ternyata R0 (pompa). Pakai R1 (pan kamera) sebagai target. Catat profil mana yang membuat relay klik dan baca baliknya benar, register IN1 yang berubah saat float digerakkan, dan apakah manual menyebut mode flash/delay (S20). Tulis hasilnya di `devices.relay_p.relay` dan `devices.relay_t.relay` (`write_fc`, `write_base`, `on_value`/`off_value` untuk FC6, `read_fc`, `read_base`, `input_fc`, `input_base`) |
| **P-H5** tanah 7-in-1 | `--unit 1 --fc 3 --address 0 --count 7 --timeout 1.0` (lalu `--fc 4` bila exception) | Urutan dan skala: kelembapan ~0–100 %, suhu ~20–35 °C, pH ~4–8. Mis. 345 → 34,5 % berarti scale 0,1. Isi `devices.soil.registers` |
| **P-H6** cahaya | `--unit 4 --fc 3 --address 0 --count 6` (dan `--fc 4`), bandingkan terang/tutup sensor | Register yang berubah besar; urutan word dari kolom 32 bit yang masuk akal (siang 10.000–100.000 lux). Isi `illuminance: {fc, address, type: u32, word_order}` |
| **P-H7** DS18B20 | `--unit 6 --fc 3 --address 0 --count 4`, celup probe ke air hangat/dingin | Register yang mengikuti suhu dan skalanya (mis. 263 → 26,3 °C = scale 0,1). Isi `water_temperature` |
| **P-H8** ADC | `--unit 10 --fc 3 --address 0 --count 8`, beri tegangan diketahui (mis. 1,5 V dari baterai) ke A1 | Register tiap kanal A1–A7 dan satuannya (1500 → mV, 150 → 0,01 V). Isi `devices.adc.registers` dengan kunci `a1`…`a7` dalam mV, mis. `a1: {fc: 3, address: 0, scale: 1}` (atau `scale: 10` bila 0,01 V) |

Setelah config diisi, jalankan `python -m fertimata_edge --config /etc/fertimata/fertimata.yaml
--check-config`, lalu nyalakan lagi agent dan pastikan log `perangkat menjawab` untuk setiap alamat.

## C. Keputusan yang perlu dijawab

Setiap keputusan sudah diberi **usulan**. Kalau tidak ada keberatan, usulan itu yang dipakai.

| ID | Pertanyaan | Usulan | Menghambat |
|---|---|---|---|
| D1 | Format dan jadwal foto bertabrakan: Protokol §4 meminta **PNG**, 10.00 & 15.00, 12 stasiun, LED. Arsitektur Rev A (lebih baru) menyebut malam hari, tanpa LED, 10 stasiun, nama berkas `.jpg` | Ikuti Rev A untuk jadwal/stasiun (malam, 10 stasiun, 2 arah), tapi simpan **PNG** sesuai Protokol (JPEG merusak sinyal warna untuk riset). Nama: `YYYYMMDD-HHMM_S{nn}_{timur|barat}.png` di `data/citra/YYYY-MM-DD/` | F8 |
| D2 | Trolley & dosing dikendalikan sebagai relay mentah dari backend. Perlu perintah tingkat tinggi (`PATROL`, `GOTO_STATION`, `CAPTURE`, `DOSE_TO_TARGET`)? | Ya. Tambah satu aktuator virtual per fungsi di katalog backend (mis. `TROLLEY-PATROL`, `DOSING-LOOP`) dengan `command` baru. Relay mentah tetap ada untuk servis manual. Butuh perubahan kecil di backend | F7 (sebagian), F9 |
| D3 | Siapa memicu patroli malam: jadwal di edge atau backend? | Edge punya jadwal lokal (jalan walau backend mati), backend bisa memicu manual | F8 |
| D4 | Interval heartbeat & telemetri | Status 30 s per device; telemetri sesuai §4.6. Lalu set `DEVICE_OFFLINE_TIMEOUT_SECONDS=120` (tetap) dan `METRICS_EXPECTED_TELEMETRY_INTERVAL` mengikuti kelompok tercepat per device | F2 |
| D5 | Perintah kedaluwarsa: diam saja atau ACK gagal? | ACK gagal `EXPIRED`, supaya command tidak menggantung SENT dan operator tahu | F4 |
| D6 | Inferensi AI di proses edge atau layanan terpisah? | Layanan terpisah (berat, GPU, bisa restart sendiri). Edge hanya mengambil foto dan menulis berkas | F12 |
| D7 | Penyajian berkas gambar ke frontend | Backend menyajikan folder `data/citra` read-only (mount volume) — pekerjaan backend | F8 |
| D8 | Nama repo GitHub | `EDGE_FertiMata` (GitHub dan di Jetson `~/FertiMata/EDGE_FertiMata`) | F10 |
| D9 | Saat ±2 jam di aki: siapa menghentikan DB/backend? | Edge mengirim alert dan menulis status. Penghentian layanan dilakukan skrip systemd terpisah, bukan edge | F10 |

### Jawaban tim (23 Okt 2026)

| ID | Jawaban | Catatan |
|---|---|---|
| D1 | Ikuti dokumen terbaru (Arsitektur Rev A): patroli **malam hari** saat lampu greenhouse menyala, **tanpa LED** (LED 24 V tidak dipakai), 10 stasiun, 2 arah pan. Format berkas **PNG** (dijawab 23 Okt) | Nama berkas usulan D1: `YYYYMMDD-HHMM_S{nn}_{timur\|barat}.png` di `data/citra/YYYY-MM-DD/` |
| D2 | Ya, perintah tingkat tinggi lewat aktuator virtual | Rincian pekerjaan backend: [06-perubahan-backend.md](06-perubahan-backend.md). Edge tidak mengubah repo backend |
| D3 | Usulan diterima: jadwal lokal di edge, backend bisa memicu manual | Pemicu manual butuh BE-1 |
| D4 | Usulan diterima: status 30 s, telemetri sesuai §4.6 | `mqtt.heartbeat_s: 30` |
| D5 | Usulan diterima: ACK gagal `EXPIRED` | Sudah diterapkan di F2 |
| D6 | **Layanan systemd terpisah** di Jetson (dijawab 23 Okt). Menunggu dokumentasi API dari tim AI | Edge menyediakan foto + metadata; kontrak hasil ke backend tetap topik `fertimata/JETSON-01/ai` (Kontrak §4.5). Saran: kode AI tetap di Jetson (boleh satu repo ini, paket `fertimata_ai`), tapi **jalan sebagai layanan systemd terpisah**. Alasan: inferensi GPU berat dan bisa macet/kehabisan memori; kalau satu proses dengan agent, crash-nya ikut mematikan watchdog relay dan memicu S1 (semua relay OFF) di tengah irigasi. Komunikasi cukup lewat folder citra + topik `fertimata/JETSON-01/ai` |
| D7 | Belum dijawab | Usulan tetap: backend menyajikan `data/citra` read-only (BE-2) |
| D8 | Repo GitHub `EDGE_FertiMata`; penamaan memakai **FertiMata**, bukan melonSmart | Folder lokal laptop masih `EDGE-melonSmart`; boleh di-rename kapan saja, kode tidak bergantung pada nama folder |
| D9 | Usulan diterima | Edge hanya mengirim alert + status |

## D. Bahan bacaan wajib sebelum menulis kode

1. [`referensi/Arsitektur-Sistem.md`](referensi/Arsitektur-Sistem.md): §3–§9, §11, §12
2. [02-kontrak-mqtt.md](02-kontrak-mqtt.md)
3. [04-aturan-keselamatan.md](04-aturan-keselamatan.md)
4. [`referensi/Protokol-Data.md`](referensi/Protokol-Data.md): §4 (citra) dan §7–§8 (kalibrasi)
