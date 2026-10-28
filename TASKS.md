# TASKS — Breakdown Pengembangan Edge Agent FertiMata

> **Dokumen hidup.** Setiap selesai mengerjakan sesuatu: centang tugasnya, isi kolom bukti, dan tambah
> satu baris di [Log perkembangan](#log-perkembangan). Tugas yang terhambat diberi tanda ⛔ dan
> rujukan ke ID persiapan/keputusan di [docs/03-persiapan.md](docs/03-persiapan.md).
>
> Status: `[ ]` belum · `[~]` sedang · `[x]` selesai · ⛔ terhambat

Urutan fase sengaja dibuat supaya **semua yang bisa dikerjakan tanpa hardware selesai lebih dulu**
(F0–F2, sebagian F3–F9 dengan HAL palsu). Pekerjaan yang butuh barang menunggu hasil cek P-H*.

---

## Ringkasan fase

| Fase | Isi | Butuh hardware? | Status |
|---|---|---|---|
| F0 | Persiapan & keputusan | — | `[~]` D7 belum dijawab; API tim AI (D6) ditunggu |
| F1 | Kerangka proyek, config, HAL palsu, test | Tidak | `[x]` |
| F2 | Link MQTT + kontrak + heartbeat, uji dengan backend | Tidak | `[x]` termasuk uji end-to-end F2.7 dengan backend |
| F3 | Bus manager Modbus + profil sensor + polling telemetri | Sebagian (verifikasi register) | `[~]` kode selesai; register tanah/cahaya ⛔P-H5/P-H6, uji Jetson F3.9 |
| F4 | Output relay, perintah, ACK, watchdog, interlock lokal | Sebagian (P-H4) | `[~]` kode + test selesai dengan HAL palsu; profil relay ⛔P-H4, S7 menunggu F5, uji Jetson F4.8 |
| F5 | Daya: MAINS/BATTERY/UNKNOWN, SSR short, pompa kering | Sebagian (P-H11) | `[~]` kode + test selesai dengan HAL palsu; ambang arus ⛔P-H11, uji PLN nyata di Jetson |
| F6 | Analog EC/pH/suhu larutan + kalibrasi | Ya (P-H7, P-H8, P-H13) | `[~]` urutan baca, konversi, validasi selesai dengan simulator; register ⛔P-H7/P-H8, kalibrasi ⛔P-H13 |
| F7 | GPIO + trolley (homing, stasiun, pemulihan) | Ya (P-H9) | `[~]` logika trolley + perintah tingkat tinggi `TROLLEY-GOTO` selesai dengan GPIO palsu; HAL GPIO asli ⛔P-H9, uji rel F7.6 |
| F8 | Kamera + patroli malam | Ya (P-H10) | `[~]` patroli, penyimpanan, gerbang blur, disk selesai dengan kamera palsu; HAL kamera asli ⛔P-H10. Pemicu manual `TROLLEY-PATROL` selesai |
| F9 | Loop dosing tertutup | Ya (P-H12) + D2 | `[~]` algoritme §8.2 + `DOSING-LOOP` + telemetri `dosing.*` selesai dengan simulator; angka pompa/drum ⛔P-H12, uji kering F9.5 |
| F10 | Deployment Jetson (systemd, udev, watchdog, log) | Ya | `[~]` berkas siap, belum dicoba di Jetson |
| F11 | Komisioning lapangan | Ya | `[ ]` |
| F12 | Integrasi AI (layanan systemd terpisah, D6) | Ya | `[ ]` menunggu dokumentasi API tim AI |

---

## F0 — Persiapan & keputusan

- [x] F0.1 Dokumen konsep, kontrak MQTT, persiapan, aturan keselamatan, breakdown tugas
- [x] F0.2 Salin referensi (Arsitektur Rev A, Protokol Data, katalog backend) ke `docs/referensi/`
- [x] F0.3 Contoh `config/fertimata.example.yaml`
- [~] F0.4 Lingkungan laptop siap (P-S1 … P-S5). P-S1 ✓ 3.10.11 (venv dibuat ulang), P-S2 ✓, P-S3 ✓, P-S5 ✓.
      P-S4 (backend + MQTT sungguhan) belum dicoba: langkahnya di [docs/07-uji-end-to-end.md](docs/07-uji-end-to-end.md)
- [~] F0.5 Keputusan D1–D9: dijawab 23 Okt (03-persiapan.md §C): D1 malam tanpa LED + PNG, D6 layanan terpisah. Sisa: D7
- [~] F0.6 Repo GitHub `EDGE_FertiMata` dibuat, remote `origin` terpasang. Commit awal belum (menunggu permintaan)

## F1 — Kerangka proyek

- [x] F1.1 Struktur paket `src/fertimata_edge/` + `pyproject.toml` (Python 3.10, dependency terkunci)
- [x] F1.2 `config`: model pydantic untuk seluruh `fertimata.yaml`, validasi saat startup, pesan galat jelas.
      Selesai bila: config contoh lolos, config rusak ditolak dengan pesan yang menyebut kuncinya
- [x] F1.3 Logging terstruktur (stdout → journald), level dari config
- [x] F1.4 Antarmuka HAL: `ModbusBus`, `GpioLines`, `Camera`, `Clock` + implementasi **fake** (register
      in-memory, relay menyimpan state, PZEM bisa "dipadamkan", reed berdetak sesuai kecepatan trolley).
      Catatan: relay palsu menyimpan state di tabel coil (mirip profil A); mengikuti profil dari config di F4.1.
      Sensor dengan register belum terverifikasi (tanah, cahaya, DS18B20, ADC) di simulator masih memori kosong
- [x] F1.5 `pytest` + `pytest-asyncio`, `ruff`, skrip `make`/`tasks` sederhana; test berjalan di Windows
- [x] F1.6 Entry point `python -m fertimata_edge --config ...` yang start/stop bersih (SIGINT/SIGTERM)

## F2 — Link MQTT

- [x] F2.1 Klien MQTT (QoS 1, sesi persisten, client-id `fertimata-edge`, reconnect otomatis, tanpa LWT/retain)
- [x] F2.2 Model payload (telemetri, status, ACK, alert, AI, command) sesuai [02-kontrak-mqtt.md](docs/02-kontrak-mqtt.md); waktu UTC `Z`
- [x] F2.3 Topik dari template config (sama dengan `MQTT_TOPIC_*` backend)
- [x] F2.4 Heartbeat per device logis (usulan 30 s, D4), hanya untuk device yang sehat
- [x] F2.5 Penerima perintah: parse, dedup `commandUid`, cek `expiresAt`, teruskan ke `commands`
- [x] F2.6 Antrean keluar saat broker putus (batas ukuran, buang telemetri tertua, **jangan** buang ACK/alert)
- [x] F2.7 Uji end-to-end di laptop: Mosquitto + backend `dev` (`MQTT_ENABLED=true`, `DEV_SIMULATOR_ENABLED=false`) + edge `hal: fake`.
      Lulus 26 Okt: 5 device ONLINE, angka sensor palsu tampil, perintah dari API → ACK → EXECUTED (lihat log)

## F3 — Bus manager & sensor

- [x] F3.1 `tools/modbus_probe.py`: baca/tulis register manual untuk verifikasi P-H3 … P-H8.
      Panduan per cek di 03-persiapan.md "Cara cek register dengan modbus_probe"
- [x] F3.2 Bus manager: satu pemilik port, antrean prioritas (STOP > perintah > polling), jeda 10 ms,
      timeout per perangkat, retry 2×, status "tidak menjawab" per alamat, mode jeda saat trolley jalan
- [x] F3.3 Profil `pzem016` (terverifikasi di manual) + test decode 32-bit low/high
- [x] F3.4 Profil `xy_md02` (SHT40) + test int16 bertanda
- [~] F3.5 ⛔P-H5 Profil `soil_7in1`: kode generik siap, peta register hanya dari config (contoh config sengaja kosong,
      jadi tidak di-poll). Buka setelah P-H5: isi `devices.soil.registers` + hapus pengecekan di `test_example_has_no_unverified_register_maps`
- [~] F3.6 ⛔P-H6 Profil `lux_rs485`: sama seperti F3.5 (`devices.light.registers`, u32 + `word_order` wajib)
- [x] F3.7 Poller terjadwal per kelompok (§4.6) → telemetri per device logis; metrik gagal dihilangkan
- [x] F3.8 Alert `MODBUS_TIMEOUT` dan `BUS_DOWN` (dengan jeda ulang)
- [ ] F3.9 Uji di Jetson dengan bus nyata: semua alamat menjawab, angka masuk akal

## F4 — Output, perintah, keselamatan

- [~] F4.1 ⛔P-H4 Profil `relay_mb` (A coil / B holding, dipilih di config), baca IN1. Kode generik siap
      (`profiles/relay.py`, `devices.*.relay` wajib diisi). Contoh config memakai pola profil A dari Arsitektur §4.5
      yang **belum terverifikasi**; buka setelah P-H4: isi `devices.relay_p.relay` dan `devices.relay_t.relay`
- [x] F4.2 S1: OFF-semua + baca balik saat boot (semua kanal, termasuk cadangan). Relay yang diam saat boot di-OFF-kan
      saat pertama menjawab, sebelum boleh dipakai
- [x] F4.3 Model output + baca balik setiap tulis + `RELAY_MISMATCH` (S19). Kanal ON tanpa perintah dimatikan saat polling
- [~] F4.4 Watchdog durasi per output (S2) selesai. Mode nyala-berwaktu relay (S20) ⛔P-H4
- [~] F4.5 `safety`: S3–S6, S8, S12, S14, S17 + S13/S16 dasar (tolak jalan ke limit aktif, berhenti saat limit aktif).
      S7 (daya cadangan) menyusul di F5.2
- [x] F4.6 `commands`: peta `actuatorCode` → output, semua aturan kontrak §5, ACK sukses/gagal (D5)
- [x] F4.7 Laporan state aktuator di status saat berubah tanpa ACK dan berkala
- [ ] F4.8 Uji di Jetson: setiap relay ON/OFF dari dashboard, durasi berhenti sendiri, interlock menolak

## F5 — Daya

- [x] F5.1 Penentu `powerSource` (MAINS / BATTERY / UNKNOWN) dengan histeresis; status `JETSON-01`.
      Dibaca sekali saat boot sebelum MQTT (Konsep §4.1 langkah 4); status dikirim saat berubah
- [x] F5.2 Respons padam: stop darurat kelompok terlarang (S7), blokir sampai MAINS (juga saat belum diketahui).
      Stop yang gagal (relay diam) diulang tiap siklus PZEM
- [~] F5.3 ⛔P-H11 `SSR_SHORT` (S10) dan `PUMP_DRY_RUN` (S11) dari arus PZEM. Kode selesai (margin Q4, `dry_run_ratio`,
      `current_settle_s`, `anomaly_reads`); `pump_baseline_a`/`pump_nominal_a` masih angka Arsitektur §7.5 yang belum diukur
- [~] F5.4 Suhu SoC Jetson (`jetson.soc_temperature`) + `SOC_HOT`. HAL `SocSensor` (sysfs, zona terpanas dari
      `power.soc_thermal_glob`); belum dicoba di Jetson
- [~] F5.5 Uji: cabut PLN saat pompa jalan → pompa mati, alert muncul, kembali normal saat PLN pulih.
      Lulus dengan simulator (`test_f5_5_outage_stops_pump_and_recovers`); uji PLN nyata menunggu Jetson

## F6 — Analog EC/pH

- [~] F6.1 ⛔P-H7 Profil `ds18b20_mb`: profil generik F3 (`devices.water_t.registers`), sekarang dibaca di siklus analog (§6.3 langkah 3), bukan poller; tinggal isi register setelah P-H7
- [~] F6.2 ⛔P-H8 Profil `adc_7ch`: kanal `a1`…`a7` (mV) dari `devices.adc.registers`; contoh config sengaja kosong
      sehingga EC/pH tidak dibaca dan R5 tidak disentuh. Buka setelah P-H8
- [x] F6.3 Urutan baca §6.3 (R5 OFF → pH, R5 ON → EC, R5 OFF, suhu), median (minimal separuh sampel berhasil),
      `EC-POWER` dikunci edge selama siklus (ACK `BUSY`)
- [x] F6.4 Konversi tegangan → EC (pustaka DFR0300: K rendah/tinggi + perpindahan rentang berhisteresis, kompensasi 25 °C)
      dan → pH (2/3 titik kuadrat terkecil, slope dikoreksi suhu), parameter di config. Nilai kalibrasi nyata ⛔P-H13
- [x] F6.5 Validasi rentang (S9), `EC_PH_INVALID`; EC tanpa suhu larutan tidak dikirim (katalog: sudah terkompensasi)
- [~] F6.6 ⛔P-H13 Kalibrasi nyata. Alat bantu `tools/calibrate_ec_ph.py` selesai: satu larutan per perintah
      (`--point ec1413|ec12880|ph401|ph686|ph918`), urutan R5 §6.3, cek K (`analog.ec.cal_k_range`) dan urutan buffer pH,
      `--write` mengganti nilai itu saja di YAML (komentar utuh, `.bak`, divalidasi dulu). Tinggal dipakai saat P-H13

## F7 — GPIO & trolley

- [ ] F7.1 ⛔P-H9 HAL GPIO asli (libgpiod), debounce, deteksi tepi
- [x] F7.2 `homing()`, `goto_station(n)` dengan urutan DIR/RUN (S12), limit (S13), timeout (S15), kabel putus (S16).
      Stasiun selalu didekati dari selatan; END tak terduga → berhenti, homing, gagal (`TROLLEY_LIMIT_HIT`, §9.6)
- [x] F7.3 Telemetri `trolley.station`, `trolley.home_limit`, `trolley.end_limit` (saat berubah + tiap `polling.trolley_s`);
      `trolley.station` hanya saat diam di stasiun/HOME yang diketahui
- [x] F7.4 Perilaku `TROLLEY-RUN`/`TROLLEY-DIR`/`CAMERA-PAN` dari perintah mentah tetap aman: gerak mentah membuat posisi
      tidak diketahui dan diawasi S15; selama gerak otomatis DIR/pan/RUN ON mentah ditolak `BUSY`, RUN OFF selalu boleh
- [x] F7.5 (D2) Perintah tingkat tinggi `GOTO_STATION`/`HOME` setelah katalog backend ditambah: `JETSON-01/TROLLEY-GOTO` (BE-1) di `virtual.py` (kontrak §5.1). Uji di rel tetap di F7.6
- [ ] F7.6 Uji di rel: homing berulang, berhenti tepat di tiap stasiun, cabut kabel limit → terkunci

## F8 — Kamera & patroli

- [ ] F8.1 ⛔P-H10 HAL kamera asli (V4L2/OpenCV), exposure/WB/fokus terkunci dari config
- [x] F8.2 Simpan berkas sesuai D1 + metadata (stasiun, arah, eksposur, suhu, RH) ke JSON sidecar.
      `captures.py`: waktu lokal `agent.utc_offset_h` (WIB), folder = tanggal mulai patroli, tulis atomik, sidecar
      sesudah foto (penanda "utuh" untuk layanan AI), tidak pernah menimpa (akhiran `-2`), ringkasan `patroli_*.json`
- [~] F8.3 Gerbang kualitas blur (variance of Laplacian), frame blur diulang sekali. Logika selesai (`Frame.sharpness`
      dari HAL, ambil ulang sekali, simpan yang lebih tajam + `blurry`). Hitungan Laplacian asli (cv2) masuk F8.1;
      `camera.blur_threshold` ⛔P-H10 (contoh config `null` = gerbang mati)
- [x] F8.4 `patroli()` §9.4 + pemulihan §9.6 + jadwal lokal (D3) selesai (`patrol.py`, `camera.alternate_pan`,
      `patrol_catchup_min`). Pemicu manual (D2): `JETSON-01/TROLLEY-PATROL` START/STOP (BE-1, `virtual.py`)
- [x] F8.5 Pembersihan disk: peringatan bila ruang < ambang (jangan hapus data riset otomatis). `DISK_LOW` di bawah
      `camera.disk_warn_free_gb`, `DISK_FULL` + foto tidak disimpan di bawah `disk_stop_free_gb` (usulan 20/5 GB)

## F9 — Loop dosing

- [x] F9.1 Kontrak perintah `DOSE_TO_TARGET` (target EC, rasio fase) disepakati dan ditambah ke backend: dari BE-1 `RIO-TANK-01/DOSING-LOOP` `{targetEc 0.5..4.0, phase}` (kontrak §5.1). Rasio N:P:K per fase di `dosing.ratio` (kunci = `phase` backend), rentang target `dosing.target_ec_ms_cm`, `virtual_actuators.dosing_loop`
- [x] F9.2 Algoritme §8.2: prasyarat, ΔEC, dosis 30–50 %, N → P → K, aduk, tunggu stabil, maks iterasi (`dosing.py`, lewat `outputs`/`safety`/`analog`)
- [~] F9.3 ⛔P-H12 Parameter debit, volume, konsentrasi stok dari config. Kunci config siap (`drum_volume_l`, `pump_flow_ml_s`, `ec_gain_ms_cm_per_ml_l`, `stock_g_l`, `stir_s`, `min_pump_s`, `stable_*`, `guard_s`); contoh config `null` → `NOT_CONFIGURED`, dev config berisi angka simulator. Angka nyata menunggu P-H12/F11.3
- [x] F9.4 Log tiap iterasi (telemetri `dosing.*` satu pesan per iterasi) + `DOSING_NOT_CONVERGING`, `DOSING_DONE`, `DOSING_FAILED`
- [ ] F9.5 Uji kering (air saja) sebelum pakai pupuk

## F10 — Deployment Jetson

- [~] F10.1 Udev rules `/dev/rs485`, `/dev/cam-trolley` (`deploy/udev/`): `/dev/rs485` + gpiochip aktif; rule kamera masih dikomentari ⛔P-H10 (VID:PID). Belum dicoba di Jetson
- [~] F10.2 Unit systemd `fertimata-edge.service` (`Restart=always`, `WatchdogSec`, user non-root di grup `dialout`/`gpio`/`video`): unit + `sdnotify.py` (READY/WATCHDOG/STOPPING) selesai dan dites; `systemd-analyze verify` dan uji kill di Jetson belum (P-H1)
- [~] F10.3 Skrip instal/upgrade (`deploy/install.sh`), venv di `/opt/fertimata-edge` atau `~/FertiMata/EDGE_FertiMata`: venv `/opt/fertimata-edge/venv`, repo tetap di `~/FertiMata/EDGE_FertiMata`; `--dry-run` dites, belum dijalankan sungguhan di Jetson
- [ ] F10.4 Hardware watchdog Jetson aktif (P-H15)
- [x] F10.5 Rotasi log journald, config produksi di luar repo (`/etc/fertimata/fertimata.yaml`): `deploy/journald/fertimata.conf`; install.sh menyalin config contoh hanya bila belum ada
- [ ] F10.6 Dokumentasi operasi singkat: start/stop, lihat log, ganti register tanpa ubah kode

## F11 — Komisioning

- [ ] F11.1 Daftar cek per perangkat (alamat menjawab, angka masuk akal, relay klik & baca balik)
- [ ] F11.2 Uji semua aturan S1–S20 di hardware nyata, catat hasilnya di `docs/komisioning.md`
- [ ] F11.3 Ukur dan isi: baseline arus, volume drum, debit pompa, kalibrasi EC/pH
- [ ] F11.4 Uji ketahanan 72 jam: kelengkapan telemetri, tanpa restart tak terduga

## F12 — AI (di luar lingkup awal)

- [ ] F12.1 (D6) Layanan inferensi terpisah membaca folder citra → publish `fertimata/JETSON-01/ai`

---

## Pertanyaan terbuka (dari orientasi 2026-10-23)

Q7 ✓ (F5): sebelum bacaan PZEM pertama, status `JETSON-01` **tidak** memuat `powerSource`. Mengirim
`UNKNOWN` akan dianggap daya cadangan oleh backend dan memblokir semua perintah aktivasi. Backend
membiarkan nilai lama bila field kosong.

| ID | Hal | Usulan sementara | Terkait |
|---|---|---|---|
| Q1 ✓ dipakai di F4.4 | `OUTPUT_TIMEOUT`: S2 menyebut alert saat timer habis, kontrak §4.4 hanya saat watchdog `max_on_s` yang mematikan. Perintah "dosing 5 s" yang selesai normal seharusnya tidak jadi WARNING | Alert hanya bila `max_on_s` yang memotong (durasi diminta > batas, atau output tanpa durasi). Selesai normal → cukup status | F4.4 |
| Q2 ✓ dipakai di F4 | `tank.float_low_ok` wajib dikirim ≤ 5 menit, tapi `polling.tank_s` = 600 s. IN1 dibaca bersama relay tiap `relays_s` | Kirim `tank.float_low_ok` di telemetri RIO-TANK-01 setiap siklus baca relay 21, terpisah dari EC/pH/suhu | F3.7, F4.1 |
| Q3 ✓ dipakai di F2 | Heartbeat device logis dengan beberapa sumber (RIO-TANK-01 = relay 21 + ADC + DS18B20; GREENHOUSE-01 = 3 sensor): "tidak menjawab" berarti semua atau salah satu? | Heartbeat selama **minimal satu** sumber menjawab; sumber yang diam dilaporkan lewat `MODBUS_TIMEOUT` dan metriknya dihilangkan | F2.4, F3.8 |
| Q4 ✓ dipakai di F5.3 (`power.ssr_short_margin_a`) | `SSR_SHORT` = "arus di atas baseline" tanpa margin; noise PZEM akan memicu palsu | Tambah ambang di config (mis. `power.ssr_short_margin_a`) saat F5.3 | F5.3 |
| Q5 ✓ dipakai di F4.5 (`safety.blocked_while_on`) | Backend memblokir `CAMERA-PAN` saat `TROLLEY_MOTOR` aktif (`blocked-while-active`), di config edge belum ada daftarnya (hanya S14 di dokumen) | Tambah `safety.blocked_while_active` di config saat F4.5 | F4.5 |
| Q6 ✓ PNG dipakai | D1: nama berkas Protokol §4 (`rgb_YYYYmmdd_HHMMSS_st<NN>.png`, folder per stasiun) beda dengan usulan D1 (`YYYYMMDD-HHMM_S{nn}_{timur|barat}.png`) dan contoh `captureId` kontrak §4.5 | Ikuti D1 (cocok dengan `captureId` kontrak). Perlu konfirmasi tim riset | F8.2 |

## Perubahan yang dibutuhkan di backend (dicatat di sini, dikerjakan di BE-melonSmart)

| ID | Perubahan | Terkait |
|---|---|---|
| BE-1 ✓ 26 Okt (branch BE `feat/edge-alignment-be5-be7`, belum di-commit; kontrak §5.1) | Aktuator virtual + `command` tingkat tinggi untuk trolley/patroli/dosing | D2, F7.5, F8.4, F9.1 |
| BE-2 ✓ 26 Okt (`/captures/**`, kontrak §4.5) | Sajikan berkas citra (`imageUrl`) | D7, F8 |
| BE-3 ✓ 26 Okt (interval per device, kontrak §3; RIO-TANK-01 sudah diperbaiki BE ke [5 s, 600 s]) | Sesuaikan `DEVICE_OFFLINE_TIMEOUT_SECONDS`, `METRICS_EXPECTED_TELEMETRY_INTERVAL` | D4 |
| BE-4 ✓ 26 Okt (`dosing.iteration/ec_target/ec_measured/n_ml/p_ml/k_ml`) | Tambah metrik `dosing.*` ke katalog bila log iterasi lewat telemetri | F9.4 |
| BE-5 ✓ 26 Okt (branch BE `feat/edge-alignment-be5-be7`, belum di-merge) | `docker-compose.yml` meneruskan `DEV_SIMULATOR_ENABLED` dan `DEV_SIMULATOR_INTERVAL` | F2.7 |
| BE-6 ✓ 26 Okt (branch BE `feat/edge-alignment-be5-be7`, belum di-merge) | Penamaan dev `smartmelon` → `fertimata` (D8): topik MQTT, `MQTT_CLIENT_ID`, `JWT_ISSUER`, lalu database dan role diganti nama di tempat (data utuh). Container memakai `jdbc:postgresql://postgres:5432/fertimata` | F2.7 |
| BE-7 ✓ 26 Okt (branch BE `feat/edge-alignment-be5-be7`, belum di-merge) | Kolom `actuator_command.failure_code` (Flyway V8) dari awalan `KODE:` ACK gagal; `errorMessage` tetap utuh. Kode yang dikenal edge: `INTERLOCK_EXCLUSIVE`, `INTERLOCK_FLOAT`, `INTERLOCK_MOVING`, `INTERLOCK_LIMIT`, `INTERLOCK_POWER`, `BUSY`, `OUTPUT_FAULT`, `DEVICE_OFFLINE`, `RELAY_MISMATCH` | F4.6 |

Rincian semua butir: [docs/06-perubahan-backend.md](docs/06-perubahan-backend.md).

---

## Log perkembangan

| Tanggal | Fase/Tugas | Ringkasan | Bukti (test/commit/catatan) |
|---|---|---|---|
| 2026-10-22 | F0.1–F0.3 | Repo dibuat; dokumen konsep, kontrak MQTT (diturunkan dari kode backend), persiapan, aturan keselamatan, breakdown, config contoh | `docs/`, `config/fertimata.example.yaml` |
| 2026-10-23 | F1.1–F1.6 | Kerangka paket `src/fertimata_edge/` (config pydantic, HAL + fake world, logging key=value UTC, entry point `python -m fertimata_edge`, `tasks.py`). Config contoh ditambah `safety.output_groups` (kelompok "dosing" dijadikan eksplisit) dan dibuat `config/fertimata.dev.yaml`. Uji S18 (grep shutdown) dan larangan impor pymodbus/gpiod/cv2 di logika sudah otomatis | `python tasks.py check`: ruff bersih, pytest 81 lulus, 1 dilewati (SIGTERM subprocess hanya POSIX). Dijalankan di Python 3.12 Windows; belum diuji di 3.10 |
| 2026-10-23 | F0.4–F0.6 | Venv dibuat ulang dengan Python 3.10.11; remote GitHub `EDGE_FertiMata`; jawaban D1–D9 dicatat di 03-persiapan §C; dokumen pekerjaan backend `docs/06-perubahan-backend.md` (BE-1…BE-6) | `python tasks.py check` di 3.10.11: 81 lulus sebelum F2 |
| 2026-10-23 | F2.1–F2.6 | Paket `link/`: payload sesuai record Java backend, topik dari template, transport paho-mqtt 2.1 (MQTT v5, QoS 1, `clean_start=false` + Session Expiry, tanpa LWT/retain, reconnect otomatis), outbox (telemetri/status tertua dibuang, ACK/alert/AI tidak), heartbeat 30 s hanya untuk device sehat (`health.py`), penerima perintah (dedup 200 UID, `EXPIRED`, `UNSUPPORTED`, `INVALID`, diproses berurutan), alert dengan jeda ulang per tipe. Eksekutor sementara `NotReadyHandler` menjawab ACK gagal `NOT_READY` sampai F4.6. `parse_iso` menerima pecahan detik 1–9 digit dari `Instant` Java | `python tasks.py check`: ruff bersih, 129 lulus, 1 dilewati; `tests/test_mqtt_broker.py` lulus melawan Mosquitto `fertimata-mosquitto`; agent manual: heartbeat JETSON-01 dan ACK `NOT_READY` terlihat di `mosquitto_sub` |
| 2026-10-23 | F2.7 | Sebagian: langkah uji dengan backend ditulis di `docs/07-uji-end-to-end.md`; kriteria penuh menunggu F3.7 + F4.6 | — |
| 2026-10-23 | D1, D6 | D1: PNG. D6: AI sebagai layanan systemd terpisah, menunggu dokumentasi API tim AI | 03-persiapan.md §C |
| 2026-10-23 | F3.1–F3.8 | `tools/modbus_probe.py` (baca FC1–4, tulis FC5/6 dengan `--yes` + baca balik, `--scan`, tafsiran u16/s16/32-bit, `--fake`); HAL Modbus asli `hal/real_modbus.py` (pymodbus 3.16.1, retry internal mati, timeout per transaksi) + pengganti GPIO yang mengunci trolley; `bus.py` (prioritas STOP>perintah>poll, jeda, retry, `fail_threshold`, jeda polling mode trolley, buka ulang port); profil peta register generik (`profiles/`) dengan bawaan PZEM-016/XY-MD02, lainnya hanya dari config; config `devices.*.registers/label/metric_prefix`; `poller.py` per kelompok §4.6 + alert `MODBUS_TIMEOUT`/`BUS_DOWN`; health device logis mengikuti bus | `python tasks.py check`: ruff bersih, 176 lulus, 1 dilewati. Uji S3 (`test_s3_stop_goes_before_queued_polling`) dan S14 (`test_s14_polling_paused_commands_still_run`) di tingkat bus. Agent manual ke Mosquitto: telemetri PZEM-01 tiap 3 s, GREENHOUSE-01, RIO-TANK-01 terlihat di `mosquitto_sub` |
| 2026-10-26 | F4.1–F4.7 | Profil `relay_mb` generik dari config (`devices.*.relay`: FC5/FC6, nilai ON/OFF, FC baca balik, FC + alamat IN1), relay palsu mengikuti profil (profil salah → exception seperti modul asli). `outputs.py`: S1 di boot sebelum link, tulis + baca balik + S19, watchdog S2 (alert `OUTPUT_TIMEOUT` hanya bila `max_on_s` memotong, Q1), polling relay `relays_s` (kanal ON tanpa perintah → OFF), IN1 → telemetri `tank.float_low_ok` 1/0 + alert `FLOAT_LOW` (Q2), float terbuka/basi → pompa dimatikan, jeda polling saat RUN ON (S14). `safety.py`: eksklusif S4–S6 (state tak diketahui = mungkin ON), float S8, `blocked_while_on` S12/S14 (Q5; satu-satunya pengecualian "OFF tidak diblokir", karena ubah arah bukan berhenti), jeda S12 100/300 ms, limit S13/S16 dasar + penjaga GPIO. `OutputCommandHandler` menggantikan `NotReadyHandler`. Config baru: `devices.*.relay`, `outputs.*.label`, `safety.blocked_while_on`, `safety.travel` | `python tasks.py check`: ruff bersih, 237 lulus, 1 dilewati (`test_outputs.py`, `test_safety.py`, test F4 di `test_app.py`, `test_config.py`). Uji mutasi: mematikan cek eksklusif/float membuat 8 test gagal. Agent manual ke Mosquitto: S1 dua relay, `STIRRER` ON 3 s → ACK sukses → status OFF oleh timer; `DIST-PUMP` tanpa durasi → ACK `INVALID` |
| 2026-10-26 | F5.1–F5.5 | `power.py`: penentu sumber daya §7.4 (PZEM diam + relay acuan menjawab = BATTERY, keduanya diam/port hilang = UNKNOWN, histeresis 180/200 V, tegangan lama dibuang saat PZEM diam), powerSource awal dibaca sebelum MQTT, status `JETSON-01` saat berubah; S7 stop lokal + `INTERLOCK_POWER` di `safety` (belum diketahui juga diblokir); SSR short/pompa kering dari arus total panel dengan settle + N bacaan berturut-turut. `soc.py` + HAL `SocSensor` (sysfs asli, palsu di simulator). Poller punya `read_listeners`. Config baru: `power.pzem_device/reference_device/pump_output/ssr_short_margin_a/dry_run_ratio/current_settle_s/anomaly_reads/soc_thermal_glob`, `polling.soc_s`, `fake.soc_temperature_c`. Catatan: sesi backend sempat menimpa `PowerConfig` di `config.py` (±15:10), sudah diperiksa dan dipulihkan | `python tasks.py check`: ruff bersih, 256 lulus, 1 dilewati (`test_power.py`, test F5 di `test_app.py`). Uji mutasi: mematikan cek/stop S7 membuat 4 test gagal. Agent manual ke Mosquitto: status `JETSON-01` `powerSource: MAINS` + telemetri `jetson.soc_temperature` |
| 2026-10-26 | F6.1–F6.5 | `analog.py`: siklus §6.3 tiap `polling.tank_s` (R5 OFF → settle → median pH → R5 ON → settle → median EC → R5 OFF → DS18B20), EC-POWER dikunci lewat `safety.managed` (ACK `BUSY`), konversi EC DFR0300 dua rentang + kompensasi `temp_coef`, pH kuadrat terkecil + koreksi Nernst, S9 (`analog.valid`) → dibuang + `EC_PH_INVALID` + `valid_for_dosing` untuk F9. `RegisterMapProfile.read_fields`; DS18B20 pindah dari poller ke siklus analog. Simulator: model larutan (EC hanya saat R5 ON, pH tergeser 150 mV saat R5 ON). Config baru: `analog.power_output/temperature_device/valid`, `analog.ec.mv_per_ms_cm/range_switch_ms_cm`, `analog.ph.cal_temperature_c`, `devices.adc.metric_prefix`, `fake.tank_*`/`ph_*`/`ec_*`. Dev config berisi register ADC dan kalibrasi khusus simulator | `python tasks.py check`: ruff bersih, 284 lulus, 1 dilewati (`test_analog.py`, test baru di `test_config.py`/`test_app.py`). Uji mutasi: baca pH saat R5 ON → 4 test gagal; validasi rentang dimatikan → 2 test gagal. Agent manual ke Mosquitto: `RIO-TANK-01` telemetri `tank.ph 6.2`, `tank.ec 1.801`, `tank.water_temperature 26.3` |
| 2026-10-26 | BE-5, BE-6, BE-7 | Laporan sesi backend: BE-5 dan BE-7 selesai, BE-6 sebagian (topik `.env` BE sebelumnya `smartmelon/...`, sehingga backend `mvnw` tidak mendengar edge; sudah `fertimata/...`). Edge tidak berubah: kode alasan ACK sudah berformat `KODE: teks` | Dicek baca-saja di BE-melonSmart: `docker-compose.yml` baris 73–74, `V8__command_failure_code.sql`, `.env` `MQTT_TOPIC_*=fertimata/...` |
| 2026-10-26 | F2.7 | Uji end-to-end dengan container backend (`DEV_SIMULATOR_ENABLED=false MQTT_ENABLED=true docker compose up -d --build backend`, image memuat BE-6/BE-7) + Mosquitto + edge `hal: fake`. Hasil: 5 device ONLINE, `JETSON-01` `powerSource` MAINS; 22 metrik edge tersimpan dengan waktu hari ini (udara, cahaya, tanah, PZEM, larutan, float, SoC). `STIRRER` ON 10 s → command EXECUTED < 1 s, state ON → OFF oleh timer edge tanpa ACK baru. `DOSING-N` ON lalu `DOSING-P` ON: backend meneruskan, edge menolak → FAILED, `failureCode` `INTERLOCK_EXCLUSIVE` (BE-7 bekerja). `DIST-PUMP` saat dosing ditolak backend sendiri (409 `SAFETY_INTERLOCK`). Catatan: `trolley.*` belum dikirim edge (F7.3); container backend sempat dibuat ulang sesi BE untuk BE-6 di tengah uji, edge tidak perlu restart | Log edge tanpa ERROR; jejak MQTT di `mosquitto_sub`; riwayat command `/api/actuators/{id}/commands` (id 5–8) |
| 2026-10-26 | F6.6 | `tools/calibrate_ec_ph.py` + `AnalogReader.read_raw()`. EC disimpan setara 25 °C, K dicek terhadap `analog.ec.cal_k_range` (jendela pustaka DFRobot_EC), pH ditolak bila urutan buffer tidak monoton (validator `PhCalibration`), `cal_temperature_c` ikut diisi. Penulisan YAML per kunci (regex, kunci harus unik), divalidasi sebelum menimpa, salinan `.bak` | `tests/test_calibrate.py` 8 lulus (termasuk buffer tertukar, larutan salah, suhu manual); dicoba CLI ke config dev tanpa `--write` |
| 2026-10-26 | F7.2–F7.4 | `trolley.py`: `homing()`, `goto_station(n)` (§9.3), S12 lewat `safety` (`check(..., internal=True)`), S13 END tak terduga → homing + gagal, S15 segmen/total (alert `TROLLEY_NO_REED`, `TROLLEY_TIMEOUT` baru), S16 dua limit aktif atau limit tertinggal aktif → kunci (`safety.travel_locked`, alert `TROLLEY_LIMIT_WIRE`), pengawas gerak mentah, telemetri `trolley.*`. `safety.managed_on` (tolak ON saja), `GpioLines.available` (pengganti GPIO Jetson tidak memicu alarm palsu). Config baru: `analog.ec.cal_k_range`, `polling.trolley_s` | `python tasks.py check`: ruff bersih, 311 lulus, 1 dilewati (`tests/test_trolley.py` 18). Uji mutasi: mematikan S15 segmen, S16 limit tertinggal, pendekatan dari selatan, atau reset posisi gerak mentah → masing-masing 1 test gagal |
| 2026-10-26 | F8.2–F8.5 | `patrol.py`: urutan §9.4 (stasiun genap barat dulu bila `alternate_pan`), jeda `settle_s` setelah berhenti dan `pan_settle_s` setelah pan, pan lewat `safety.check(internal=True)` + baca balik; §9.6: kamera gagal → lewati + `CAMERA_FAIL` di akhir, kesalahan trolley/PLN padam → batal di tempat tanpa homing tambahan, alert `PATROL_FAILED`; jadwal lokal WIB tiap ≤ 60 s (tahan lompatan NTP, tidak dobel), galat patroli tidak mematikan agent. Kunci perintah mentah trolley dibuat berhitung (`hold_raw_commands`/`release_raw_commands`) supaya DIR/pan/RUN ON dari backend tetap `BUSY` di antara dua perjalanan patroli. `captures.py` (berkas + sidecar + ringkasan), `Frame.sharpness`, kamera palsu dengan `sharpness_queue`. Config baru: `agent.utc_offset_h`, `camera.pan_output/alternate_pan/blur_threshold/patrol_catchup_min/disk_warn_free_gb/disk_stop_free_gb`, `fake.image_sharpness`. Kontrak §4.4: `PATROL_FAILED`, `DISK_LOW`, `DISK_FULL`. Usulan dipakai: nama berkas dan folder D1 memakai waktu WIB | `python tasks.py check`: ruff bersih, 341 lulus, 2 dilewati (`tests/test_patrol.py` 30, test sambungan di `test_app.py`). Uji mutasi: hapus jeda settle, kunci perintah mentah, ambil ulang blur, pan bergantian, cek PLN per stasiun, atau cek disk penuh → masing-masing ≥ 1 test gagal. Agent dev jalan: `jadwal patroli aktif times=19:00` |
| 2026-10-26 | F10.1–F10.3, F10.5 | `deploy/udev/99-fertimata.rules` (`/dev/rs485` FT232 0403:6001 grup dialout + `ID_MM_DEVICE_IGNORE`, gpiochip grup gpio, rule `/dev/cam-trolley` dikomentari sampai P-H10). `deploy/systemd/fertimata-edge.service`: `Type=notify`, `WatchdogSec=30`, `Restart=always`, `StartLimitIntervalSec=0` (S20: restart tidak pernah menyerah), user sistem `fertimata` + grup dialout/gpio/video, tidak menunggu jaringan/broker (S1 tetap jalan saat LAN mati), config `/etc/fertimata/fertimata.yaml`, data `/var/lib/fertimata` (foto di `data/citra`). `src/fertimata_edge/sdnotify.py` tanpa dependensi: READY setelah "agent siap", ping tiap WatchdogSec/2 dari event loop (mulai sebelum S1), STOPPING saat berhenti; diam bila bukan di bawah systemd. `deploy/journald/fertimata.conf` (persisten, maks 1 GB, sisakan 5 GB). `deploy/install.sh` (aman diulang, `--dry-run`, `--no-restart`): venv `/opt/fertimata-edge/venv` berisi salinan paket (bukan editable), config contoh hanya disalin bila belum ada dan agent tidak dijalankan otomatis dengan config contoh, restart hanya bila `--check-config` lolos sebagai user layanan, peringatan brltty dan perbedaan kunci config. `.gitattributes` memaksa LF untuk `deploy/**` dan `*.sh` (core.autocrlf=true di laptop). Perbaikan test F8: `test_patrol.py` kadang gagal/menggantung (±1 dari 3 run penuh) karena `CaptureStore` menulis lewat thread sungguhan sementara jam palsu dimajukan; `CaptureStore(offload=...)` sekarang bisa diganti dan rig test menulis langsung | `python tasks.py check`: ruff bersih, 364 lulus, 3 dilewati (`tests/test_deploy.py` 16, `tests/test_sdnotify.py` 8; test socket Unix asli dilewati di Windows). `bash deploy/install.sh --dry-run` di Git Bash. Uji mutasi: tanpa READY, tanpa ping, tanpa STOPPING, `StartLimitIntervalSec` ≠ 0, start dengan config contoh, timpa config → masing-masing ≥ 1 test gagal. `test_patrol.py` 15× berturut-turut dan suite penuh 5× lulus. Belum: `systemd-analyze verify`, instal sungguhan, dan uji `kill -STOP` (watchdog) di Jetson |
| 2026-10-26 | BE-1…BE-4 | Pesan dari sesi backend: aktuator virtual `TROLLEY-PATROL` (START/STOP), `TROLLEY-GOTO` (HOME/GOTO_STATION/STOP) di JETSON-01, `DOSING-LOOP` (DOSE_TO_TARGET/STOP) di RIO-TANK-01, backend menolak (400) command di luar kosakata, `/captures/**`, interval telemetri per device, metrik `dosing.*`. Kontrak edge diperbarui (§3 tabel interval, §4.2 state aktuator virtual, §4.3 ACK saat urutan mulai, §4.4 usulan `PATROL_DONE`/`TROLLEY_ARRIVED`/`DOSING_DONE`, §4.5 `imageUrl`, §5.1). Kode edge belum berubah: aktuator virtual dijawab `UNSUPPORTED` | `docs/02-kontrak-mqtt.md`, `docs/06-perubahan-backend.md`. Selisih BE-3 untuk RIO-TANK-01 dilaporkan ke sesi backend |
| 2026-10-26 | F2.6 (alert) | Backend kini mencatat setiap alert INFO (duplikat hanya ditahan untuk WARNING/CRITICAL); edge menyesuaikan: `Link.publish_alert` tidak menjeda INFO. Tipe hasil `PATROL_DONE`/`TROLLEY_ARRIVED`/`DOSING_DONE` disetujui backend (teks bebas ≤ 64 karakter). BE-3 RIO-TANK-01 diperbaiki BE ke [5 s, 600 s] | `tests/test_link.py::test_info_alerts_are_never_rate_limited`; kontrak §4.4 |
| 2026-10-26 | F7.5, F8.4 (pemicu manual) | Aktuator virtual Kontrak §5.1 di edge. Config baru `virtual_actuators` (`trolley_goto` → `TROLLEY-GOTO`, `trolley_patrol` → `TROLLEY-PATROL`, device `JETSON-01`); `CommandRouter` meneruskan perintah virtual, link memeriksa kosakata per jenis (relay hanya ON/OFF). `virtual.py`: ACK `ON` saat urutan mulai; prasyarat ditolak di depan (`INVALID` stasiun bukan integer 1…N, `TROLLEY_LOCKED`, `BUSY` bila patroli/goto/gerak mentah berjalan, `INTERLOCK_POWER`/`INTERLOCK_EXCLUSIVE`/`OUTPUT_FAULT` lewat `Safety.check_activation`, `DISK_FULL`); urutan berjalan di latar dengan kunci perintah mentah; status `JETSON-01` memuat `TROLLEY-GOTO`/`TROLLEY-PATROL` ON/OFF (patroli terjadwal ikut ON); hasil `TROLLEY_ARRIVED`/`PATROL_DONE` (INFO), `TROLLEY_GOTO_FAILED` (WARNING, tanpa dobel dengan alert trolley). STOP tidak pernah ditolak: RUN OFF prioritas STOP; STOP patroli → `request_stop()` + `trolley.halt()`, patroli berstatus `STOPPED` (bukan gagal), trolley berhenti di tempat, ACK `OFF` setelah patroli benar-benar diam. `DOSING-LOOP` tetap `UNSUPPORTED` (F9). **Perbaikan keselamatan** ditemukan test: `Outputs.set` yang dibatalkan setelah relay ditulis tapi sebelum baca balik dulu menyimpan state lama (OFF) sehingga trolley tidak dimatikan saat agent berhenti; sekarang state menjadi tidak diketahui (= mungkin ON) dan `finally` trolley mematikan RUN | `python tasks.py check`: ruff bersih, 396 lulus, 3 dilewati (`tests/test_virtual.py` 24, `test_link.py` routing + kosakata, `test_app.py::test_agent_executes_virtual_trolley_command` lewat MQTT palsu, `test_outputs.py::test_cancelled_write_leaves_state_unknown`); suite penuh 3× lulus. Uji mutasi (STOP tidak menghentikan, tanpa cek daya/eksklusif, STOP dianggap gagal, patroli terjadwal tak dilaporkan, alert dobel, gerak mentah tak dicek, STOP saat memotret, STOP patroli = gagal, state batal tulis) → masing-masing ≥ 1 test gagal; mutasi "STOP saat memotret" awalnya lolos, test diperketat. Belum: uji dengan backend sungguhan (Docker tidak jalan) |
| 2026-10-26 | F8.4 | Masukan sesi backend: STOP `TROLLEY-PATROL` tetap SENT di backend sampai ACK tertunda tiba (TTL 30 s), jadi jeda patroli (`settle_s`, `pan_settle_s`, ambil ulang blur) kini diputus begitu STOP diminta (`Patrol._pause`). Backend: kode gagal `KODE: teks` apa pun disimpan ke `failure_code`; `TROLLEY_GOTO_FAILED` WARNING diduplikasi per device+tipe selama belum ditangani; BE-1…BE-4 + perbaikan INFO sudah di-merge ke main BE lokal (belum di-push) | `tests/test_virtual.py::test_stop_ack_is_quick_during_waits` (ACK < 1 s saat jeda getaran dan jeda pan); mutasi jeda tak terputus → 2 test gagal. `python tasks.py check`: 398 lulus, 3 dilewati |
| 2026-10-28 | F9.1, F9.2, F9.4 (F9.3 sebagian) | Loop dosing §8.2 di edge (`dosing.py`) dipicu `RIO-TANK-01/DOSING-LOOP`. Iterasi: aduk `stir_s` → aduk OFF → baca larutan §6.3 berulang tiap `stable_interval_s` sampai dua bacaan berbeda ≤ `stable_delta_ms_cm` (maks `stable_max_reads`, lalu `EC_UNSTABLE`) → ΔEC = target − EC25; ΔEC ≤ `ec_tolerance_ms_cm` (termasuk EC sudah di atas target) → `DOSING_DONE` tanpa dosis; selain itu `fraction_per_iteration` (0,3–0,5, divalidasi) × ΔEC dibagi rasio fase, `ml = ΔEC_pompa × V / ec_gain`, `t = ml / debit`, dipotong `max_on_s` pompa, dosis < `min_pump_s` dilewati, N → P → K satu per satu dengan watchdog relay; iterasi ke-`max_iterations` hanya memeriksa → `DOSING_NOT_CONVERGING`. Keselamatan: prasyarat lewat `safety.check(internal=True)` sebelum mulai, sebelum tiap pompa, dan tiap `guard_s` selama menunggu (S7, S8, S4–S5, S19); S9 `valid_for_dosing`; gagal → semua pompa + aduk OFF + `DOSING_FAILED` (WARNING, tipe baru, kontrak §4.4). `Safety.held` baru: selama loop trolley dan pompa siram ditolak `INTERLOCK_EXCLUSIVE` juga untuk modul edge sendiri (patroli terjadwal/goto), di antara dua dosis pun; ON mentah pompa pupuk/aduk `BUSY`, OFF mentah tetap boleh. STOP tidak pernah ditolak: batalkan tugas, semua output dosing OFF prioritas STOP (juga yang dinyalakan mentah), tanpa alert. `virtual.py`: `DOSE_TO_TARGET` (ACK `ON`; `INVALID` targetEc/phase, `BUSY`, `NOT_CONFIGURED`, `INTERLOCK_EXCLUSIVE` trolley dipakai, `INTERLOCK_POWER`/`INTERLOCK_FLOAT`/`OUTPUT_FAULT`), status `DOSING-LOOP` ON/OFF. Telemetri `dosing.iteration/ec_target/ec_measured/n_ml/p_ml/k_ml` satu pesan per iterasi (ml = dosis iterasi itu). Config baru `dosing.pumps/stirrer_output/target_ec_ms_cm/ec_gain_ms_cm_per_ml_l/min_pump_s/stable_*/guard_s`, `virtual_actuators.dosing_loop`, `fake.mix_tau_s/mix_idle_tau_s`; `max_iterations` 6 → 8 (§8.2: 5–8; dengan 40 % per iterasi 6 iterasi hanya menutup selisih ±0,6 mS/cm); angka P-H12 di contoh config tetap `null` (⛔P-H12 → `NOT_CONFIGURED`), dev config berisi angka simulator. Simulator: pompa pupuk menambah EC drum (debit × kenaikan/volume) yang baru terbaca setelah tercampur (cepat saat aduk, lambat saat diam). **Perbaikan keselamatan** ditemukan test: baca polling relay yang dijalankan bus di antara tulis dan baca balik `Outputs.set` menganggap kanal "ON tanpa perintah" → S19 palsu (output dikunci); sekarang hasil polling dibuang selama tulis berjalan (`Outputs._writing`) | `python tasks.py check`: ruff bersih, 440 lulus, 3 dilewati (`tests/test_dosing.py` 40, `test_app.py::test_agent_runs_and_stops_dosing_loop` lewat MQTT palsu, `test_link.py` kosakata DOSING-LOOP). Uji mutasi di `test_dosing.py`: prasyarat tidak dicek saat menunggu → 2 gagal; STOP tidak membatalkan → test menggantung; readiness tanpa `safety` → 2 gagal; tanpa tahanan trolley → 1; dosis penuh ΔEC → 2 (melewati target); dosis di iterasi terakhir → 1; tanpa potong `max_on_s` → 1; tanpa cek S9 → 1; polling tanpa penanda tulis → 14. Belum: angka P-H12, uji kering F9.5, uji dengan backend sungguhan |
