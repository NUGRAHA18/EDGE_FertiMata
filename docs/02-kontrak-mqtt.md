# Kontrak MQTT Edge Agent ⇄ Backend

> Diturunkan dari kode backend `BE-melonSmart` per **22 Oktober 2026**:
> `mqtt/payload/*.java`, `mqtt/TopicKind.java`, `application.yml` (`app.mqtt`), dan katalog
> `src/main/resources/hardware/fertimata-rev-a.yml` (salinan: [`referensi/katalog-backend-rev-a.yml`](referensi/katalog-backend-rev-a.yml)).
> Kalau backend berubah, **dokumen ini yang diperbarui dulu**, baru kode edge.
>
> **Pembaruan 26 Oktober 2026:** BE-1…BE-4 (aktuator virtual, `/captures/**`, interval telemetri per
> device, metrik `dosing.*`) dari sesi backend, branch `feat/edge-alignment-be5-be7` (belum di-commit).
> Edge menjalankan `TROLLEY-GOTO` dan `TROLLEY-PATROL` sejak 26 Okt (F7.5, F8.4, `virtual.py`).
>
> **Pembaruan 28 Oktober 2026 (keputusan user):** campur pupuk otomatis dan penjadwalan
> penyiraman/dosing **bukan** tugas edge maupun backend. Keduanya menjadi layanan terpisah milik tim
> AI yang memanggil API backend (`POST /api/actuators/{id}/commands`) dengan perintah relay mentah
> `DOSING-N/P/K`, `STIRRER`, `DIST-PUMP` (`ON` + `durationSeconds`, `OFF`). Aktuator virtual
> `DOSING-LOOP`, metrik `dosing.*`, dan alert `DOSING_*` dihapus dari katalog (migrasi backend V9)
> dan dari edge (loop F9 ada di commit `9f0990e` bila dibutuhkan lagi). Interlock backend dan edge
> (S2, S4–S8, S19) tetap berlaku untuk perintah mentah itu.

---

## 1. Koneksi

| Hal | Nilai |
|---|---|
| Broker | Mosquitto `tcp://127.0.0.1:1883` (Jetson; Docker publish hanya di loopback) |
| Auth | Anonim saat ini (`allow_anonymous true`); siapkan `username`/`password` di config |
| Client ID edge | `fertimata-edge` (backend memakai `fertimata-backend`, jangan bentrok) |
| QoS | 1 untuk semua topik |
| Sesi | Persisten (`clean_start=false`) **dan** Session Expiry Interval > 0 (`mqtt.session_expiry_s`, 3600 s). Di MQTT v5, `clean_start=false` dengan expiry 0 tetap menghapus sesi saat putus, jadi perintah bisa hilang. Perintah basi disaring lewat `expiresAt` |
| Retain | **Jangan** pakai retain |
| Last Will | **Jangan** pakai LWT di topik status. Backend menganggap *setiap* pesan status sebagai tanda hidup, jadi LWT malah membuat device terlihat online |
| Format | JSON UTF-8. Waktu ISO-8601 **UTC** dengan `Z`, mis. `2026-10-22T08:00:00Z` |

## 2. Topik

Template dari backend (bisa diganti lewat env `MQTT_TOPIC_*`, jadi edge juga membacanya dari config):

| Kind | Template | Arah |
|---|---|---|
| telemetry | `fertimata/{deviceId}/telemetry` | edge → backend |
| status | `fertimata/{deviceId}/status` | edge → backend |
| command | `fertimata/{deviceId}/command` | **backend → edge** (edge subscribe `fertimata/+/command`) |
| command-ack | `fertimata/{deviceId}/command/ack` | edge → backend |
| alert | `fertimata/{deviceId}/alert` | edge → backend |
| ai | `fertimata/{deviceId}/ai` | edge → backend |

`{deviceId}` = **kode device katalog**. Device dari topik yang dipercaya backend, bukan field
`deviceId` di payload.

## 3. Device logis dan isinya

Satu Jetson melapor atas nama 5 device logis. Ini mengikuti *fault domain*, supaya kabel tangki putus
terlihat beda dengan Jetson mati.

| Device | Sumber fisik | Metrik telemetri | Aktuator |
|---|---|---|---|
| `JETSON-01` | GPIO, SoC | `trolley.station`, `trolley.home_limit`, `trolley.end_limit`, `jetson.soc_temperature` | Virtual: `TROLLEY-PATROL`, `TROLLEY-GOTO` (§5.1) |
| `PZEM-01` | Modbus 30 | `mains.voltage`, `mains.frequency`, `mains.current`, `mains.power`, `mains.energy` (kWh), `mains.power_factor` | — |
| `PANEL-01` | Modbus 20 (relay 4-ch) | — | `DIST-PUMP` R0, `CAMERA-PAN` R1, `TROLLEY-RUN` R2, `TROLLEY-DIR` R3 |
| `GREENHOUSE-01` | Modbus 1, 3, 4 | `soil_a.moisture`, `soil_a.temperature`, `soil_a.ec` (**mS/cm**; register sensor µS/cm × `scale: 0.001` di config, sejak 29 Okt mengikuti fuzzy irigasi tim AI 0–4 mS/cm), `soil_a.ph`, `soil_a.nitrogen`, `soil_a.phosphorus`, `soil_a.potassium` (mg/kg), `air.temperature`, `air.humidity`, `light.illuminance` | — |
| `RIO-TANK-01` | Modbus 6, 10, 21 | `tank.ec` (mS/cm, sudah dikompensasi 25 °C), `tank.ph`, `tank.water_temperature`, `tank.float_low_ok` | `DOSING-N` R0, `DOSING-P` R1, `DOSING-K` R2, `STIRRER` R4, `EC-POWER` R5 |

Interval kirim telemetri edge, satu pesan per kelompok polling (`polling.*` edge). Backend tidak lagi
menyimpan salinan interval ini (modul metrik sistem dan `app.metrics.device-intervals` dihapus 29 Okt),
jadi tabel ini hanya acuan. Yang tetap bergantung pada interval: interlock `FLOAT_LOW` backend memeriksa
kesegaran `tank.float_low_ok` (≤ 5 menit), dan device dianggap OFFLINE dari heartbeat status (§4.2). PZEM dibaca tiap `pzem_s` untuk deteksi lokal (PLN padam,
SSR short, pompa kering), tapi telemetrinya dikirim tiap `pzem_publish_s` (bacaan terakhir; disepakati
29 Okt untuk menekan volume database). Perubahan MAINS ↔ BATTERY tetap dikirim **segera** lewat
status `powerSource` (§4.2), tidak menunggu telemetri:

| Device | Kelompok edge | Interval |
|---|---|---|
| `JETSON-01` | `trolley_s`, `soc_s` | 30 s, 60 s |
| `PZEM-01` | `pzem_publish_s` (dibaca tiap `pzem_s` 3 s) | 30 s |
| `GREENHOUSE-01` | `air_light_s` (udara + cahaya satu pesan), `soil_s` | 60 s, 300 s |
| `RIO-TANK-01` | `relays_s` (`tank.float_low_ok`), `tank_s` (pH + EC + suhu air **satu** pesan) | 5 s, 600 s |
| `PANEL-01` | — (hanya status) | — |

Kalau `polling.relays_s` diubah, pastikan `tank.float_low_ok` tetap terkirim jauh di bawah batas
kesegaran 5 menit backend. Interval lain bebas diubah tanpa mengubah backend.

Satuan mengikuti katalog: suhu °C, kelembapan %, energi **kWh** (PZEM memberi Wh → bagi 1000).

Nilai metrik: angka (disimpan numerik), boolean/string (disimpan teks). Untuk limit dan float
**kirim angka `1`/`0`**: interlock backend `FLOAT_LOW` menerima `"1"`, `"CLOSED"`, `"TRUE"`, dan bacaan
basi lebih dari 10 menit dianggap tidak aman. Jadi `tank.float_low_ok` harus dikirim **minimal tiap
5 menit**.

Metrik yang tidak dikenal backend tidak ditolak (dibuat sensor placeholder nonaktif). Meski begitu,
jangan menambah metrik baru tanpa menambahkannya juga ke katalog backend.

## 4. Payload edge → backend

### 4.1 Telemetri

```json
{ "timestamp": "2026-10-22T08:00:00Z",
  "data": { "air.temperature": 29.8, "air.humidity": 71.2, "light.illuminance": 18450 } }
```

Satu pesan per device per siklus baca. Metrik yang gagal dibaca **dihilangkan**, tidak diisi `null`
atau 0.

### 4.2 Status / heartbeat

```json
{ "timestamp": "2026-10-22T08:00:00Z",
  "powerSource": "MAINS",
  "actuators": { "DIST-PUMP": "OFF", "CAMERA-PAN": "OFF", "TROLLEY-RUN": "OFF", "TROLLEY-DIR": "OFF" },
  "metadata": { "agentVersion": "0.1.0" } }
```

| Field | Aturan |
|---|---|
| `powerSource` | Dikirim di `JETSON-01` saja. `MAINS` / `BATTERY` / `UNKNOWN`. Selain `MAINS` dianggap daya cadangan oleh backend. Field dihilangkan selama edge belum punya data daya (sebelum bacaan PZEM pertama); backend lalu mempertahankan nilai terakhir. Dikirim juga setiap kali berubah |
| `actuators` | State **hasil baca balik relay**, `"ON"`/`"OFF"`. Wajib dikirim setiap kali state berubah tanpa ACK (timer habis, watchdog, darurat) dan berkala. Aktuator virtual (§5.1) ikut dilaporkan: `TROLLEY-PATROL`/`TROLLEY-GOTO` di `JETSON-01`; `"ON"` selama urutannya berjalan. Backend memakai state ini untuk interlock |
| `status`, `metadata` | Opsional |

Interval: usulan 30 s per device (backend `DEVICE_OFFLINE_TIMEOUT_SECONDS=120`). Device yang perangkat
Modbus-nya tidak menjawab **tidak** diberi heartbeat.

### 4.3 ACK perintah

```json
{ "commandUid": "e71802f2-…", "success": true, "actuatorState": "ON",
  "executedAt": "2026-10-22T08:00:01Z", "errorMessage": null }
```

- Topik: `fertimata/{device yang sama dengan command}/command/ack`.
- `success=true` hanya setelah relay ditulis **dan** dibaca balik sesuai.
- Gagal: `success=false`, `errorMessage` singkat dalam bahasa Indonesia yang bisa dibaca petani
  (backend menampilkannya sebagai peringatan). Kode alasan ditaruh di awal, mis.
  `"EXPIRED: perintah sudah kedaluwarsa"`, `"INTERLOCK_POWER: listrik PLN padam"`.
- ACK ganda untuk `commandUid` yang sama diabaikan backend, tapi tetap jangan dikirim.
- Aktuator virtual (§5.1): ACK sukses dengan `actuatorState: "ON"` begitu urutan **dimulai** (command
  menjadi EXECUTED saat ACK). Selesai atau batal → status `actuators: {"<kode>": "OFF"}` tanpa ACK
  baru; hasilnya lewat alert INFO/WARNING (§4.4). ACK gagal bila prasyarat tidak terpenuhi (mis.
  `BUSY`, `INTERLOCK_POWER`, `TROLLEY_LOCKED`).

### 4.4 Alert

```json
{ "type": "SSR_SHORT", "severity": "CRITICAL",
  "title": "Saklar pompa rusak (SSR short)",
  "message": "Arus 1,4 A padahal pompa dimatikan. Matikan RCBO di panel.",
  "actuatorCode": "DIST-PUMP", "sensorCode": null,
  "timestamp": "2026-10-22T08:00:00Z", "metadata": { "current": 1.4 } }
```

`severity`: `INFO` / `WARNING` / `CRITICAL`. Untuk WARNING/CRITICAL backend menahan duplikat (tipe sama,
device sama) selama alert belum ditandai ditangani, jadi edge boleh mengulang alert tanpa membanjiri
dashboard. Meski begitu, edge memberi jeda `mqtt.alert_repeat_s` per tipe. **INFO selalu dicatat**
backend (sejak 26 Okt, BE-1) dan tidak dijeda edge: dipakai untuk hasil satu kejadian (`PATROL_DONE`,
`TROLLEY_ARRIVED`), bukan kondisi yang berulang.

Tipe yang direncanakan:

| Tipe | Device | Severity | Pemicu |
|---|---|---|---|
| `SSR_SHORT` | PANEL-01 | CRITICAL | Arus di atas baseline saat R0 OFF (§7.5) |
| `PUMP_DRY_RUN` | PANEL-01 | CRITICAL | Kenaikan arus jauh di bawah nominal saat R0 ON |
| `RELAY_MISMATCH` | PANEL-01 / RIO-TANK-01 | CRITICAL | Baca balik tidak sesuai tulisan |
| `OUTPUT_TIMEOUT` | sesuai output | WARNING | Watchdog `max_on_s` mematikan output |
| `MODBUS_TIMEOUT` | device terkait | WARNING | Perangkat tidak menjawab N kali berturut-turut |
| `BUS_DOWN` | JETSON-01 | CRITICAL | Tidak ada perangkat yang menjawab / `/dev/rs485` hilang |
| `FLOAT_LOW` | RIO-TANK-01 | WARNING | IN1 terbuka (air rendah atau kabel putus) |
| `TROLLEY_LIMIT_HIT` | JETSON-01 | WARNING | END/HOME aktif tak terduga |
| `TROLLEY_NO_REED` | JETSON-01 | CRITICAL | Tidak ada pulsa reed dalam 10 s |
| `TROLLEY_LIMIT_WIRE` | JETSON-01 | CRITICAL | HOME dan END aktif bersamaan / aktif terus (kabel putus) |
| `TROLLEY_TIMEOUT` | JETSON-01 | WARNING | Perjalanan melewati `trolley.travel_timeout_s` (S15). `type` alert bebas di backend, tidak perlu perubahan katalog |
| `CAMERA_FAIL` | JETSON-01 | WARNING | Foto gagal diambil saat patroli (satu alert per patroli, `metadata.missed`) |
| `PATROL_FAILED` | JETSON-01 | WARNING | Patroli tidak dijalankan atau berhenti di tengah (`metadata.reason`: kode trolley, `INTERLOCK_POWER`, `DISK_FULL`, …). Tipe bebas di backend |
| `DISK_LOW` | JETSON-01 | WARNING | Ruang disk folder foto < `camera.disk_warn_free_gb` |
| `DISK_FULL` | JETSON-01 | CRITICAL | Ruang disk < `camera.disk_stop_free_gb`: foto tidak disimpan. Data lama tidak dihapus otomatis |
| `SOC_HOT` | JETSON-01 | WARNING | Suhu SoC mendekati batas |
| `EC_PH_INVALID` | RIO-TANK-01 | WARNING | Bacaan di luar rentang fisik |
| `PATROL_DONE` | JETSON-01 | INFO | Patroli selesai atau dihentikan dari aplikasi (`metadata.status` `OK`/`PARTIAL`/`STOPPED`, `saved`, `missed`, `blurry`, `patrolId`, `trigger` `schedule`/`manual`). Tipe bebas di backend (≤ 64 karakter), disetujui sesi backend |
| `TROLLEY_ARRIVED` | JETSON-01 | INFO | `HOME`/`GOTO_STATION` selesai (`metadata.station`, 0 = HOME) |
| `TROLLEY_GOTO_FAILED` | JETSON-01 | WARNING | `HOME`/`GOTO_STATION` tidak sampai karena sebab yang belum punya alert sendiri (mis. RUN dimatikan lewat relay mentah, ditolak interlock saat jalan). `TROLLEY_NO_REED`/`TIMEOUT`/`LIMIT_HIT`/`LIMIT_WIRE` tidak diulang. `STOP` dari aplikasi tidak memicu alert |

### 4.5 Hasil AI (tahap lanjut)

```json
{ "detectedAt": "2026-10-22T19:05:00Z", "detectionType": "NUTRIENT_DEFICIENCY",
  "stationCode": "ST-03", "panDirection": "TIMUR", "captureId": "20261022-1905_S03_timur",
  "scores": { "Leaf_N_stress": 0.71, "Leaf_P_stress": 0.12, "Leaf_K_stress": 0.64 },
  "imageUrl": "/captures/2026-10-22/20261022-1905_S03_timur.png" }
```

Wajib ada `label` atau `scores`. `imageUrl` = `/captures/` + jalur relatif terhadap `camera.root`:
backend menyajikan folder itu read-only di `/captures/**` (BE-2, env `CAPTURES_DIR`, di Jetson
`/var/lib/fertimata/data/citra`), dengan autentikasi JWT yang sama dengan API.

## 5. Payload backend → edge: perintah

```json
{ "commandUid": "e71802f2-…", "deviceId": "RIO-TANK-01", "actuatorCode": "DOSING-N",
  "command": "ON", "parameters": { "durationSeconds": 5 },
  "issuedAt": "2026-10-22T08:00:00Z", "expiresAt": "2026-10-22T08:00:30Z" }
```

| Aktuator | `command` | Arti di edge | `durationSeconds` |
|---|---|---|---|
| `DIST-PUMP` | `ON` / `OFF` | R0 relay 20 | wajib, ≤ 900 |
| `CAMERA-PAN` | `ON` / `OFF` | ON = barat, OFF = timur. Ditolak saat trolley bergerak | — |
| `TROLLEY-RUN` | `ON` / `OFF` | Jalan ke arah DIR saat ini, **tetap** berhenti di limit/timeout | wajib, ≤ 60 |
| `TROLLEY-DIR` | `ON` / `OFF` | ON = utara/END. Hanya boleh saat RUN OFF | — |
| `DOSING-N/P/K` | `ON` / `OFF` | Peristaltik; satu pompa dalam satu waktu | wajib, ≤ 150 (fuzzy pupuk tim AI 0–150 s; 29 Okt, semula 120) |
| `STIRRER` | `ON` / `OFF` | Pompa aduk | wajib, ≤ 600 |
| `EC-POWER` | `ON` / `OFF` | Daya papan EC. **Dikelola edge** saat siklus baca analog; perintah manual ditolak saat siklus berjalan | — |

Backend menolak (400) `command` di luar kosakata per tipe aktuator: relay mentah hanya `ON`/`OFF`.

### 5.1 Aktuator virtual (BE-1, keputusan D2)

Tidak memakai `durationSeconds`: batas waktu dipegang edge (S2, S15).

| Aktuator (device) | Tipe katalog | `command` | `parameters` | Arti di edge |
|---|---|---|---|---|
| `TROLLEY-PATROL` (`JETSON-01`) | `TROLLEY_PATROL` | `START` / `STOP` | — | `START` = patroli manual (`Patrol.run(trigger="manual")`, F8.4). `STOP` = batalkan, trolley berhenti di tempat (tidak pulang ke HOME), ACK `OFF` setelah patroli benar-benar berhenti (jeda getaran/pan diputus saat STOP, jadi biasanya < 1 s; paling lama satu pengambilan foto yang sedang berjalan + pan kembali ke timur). State `ON` juga selama patroli **terjadwal** |
| `TROLLEY-GOTO` (`JETSON-01`) | `TROLLEY_POSITION` | `HOME` / `GOTO_STATION` / `STOP` | `GOTO_STATION`: `{"station": 1..10}` (integer JSON, wajib) | `homing()` / `goto_station(n)` (F7.5). `STOP` = RUN OFF |

Pemeriksaan edge sebelum ACK sukses (gagal → ACK `success=false`, state tetap `OFF`):

| Kode | Kapan |
|---|---|
| `INVALID` | `GOTO_STATION` tanpa `station` integer 1…`trolley.stations` |
| `TROLLEY_LOCKED` | GPIO trolley belum tersedia atau trolley dikunci S16 |
| `BUSY` | Patroli (manual/terjadwal) atau `TROLLEY-GOTO` lain sedang jalan, atau trolley sedang digerakkan relay mentah |
| `INTERLOCK_POWER`, `INTERLOCK_EXCLUSIVE`, `OUTPUT_FAULT` | Sama dengan aturan `TROLLEY-RUN` ON (S7, S4–S6, S19) |
| `DISK_FULL` | `START` saat ruang foto di bawah `camera.disk_stop_free_gb` |

`STOP` tidak pernah ditolak dan selalu mematikan `TROLLEY-RUN` (prioritas STOP), juga bila tidak ada
urutan virtual yang berjalan. Selama urutan berjalan, `TROLLEY-DIR`, `CAMERA-PAN`, dan `TROLLEY-RUN` ON
mentah dijawab `BUSY`.

Interlock di backend (edge tetap memeriksa ulang dengan aturannya sendiri):

- Perintah pengaktif: `ON`, `START`, `HOME`, `GOTO_STATION`. `STOP`/`OFF` tidak pernah diblokir.
- Semua tipe virtual diblokir saat daya cadangan.
- Eksklusif: `TROLLEY_PATROL`/`TROLLEY_POSITION` ⟷ `DOSING_PUMP`, `STIRRER`, `TROLLEY_MOTOR`, dan satu
  sama lain.
- `CAMERA_PAN` diblokir selama `TROLLEY_MOTOR`/`TROLLEY_PATROL`/`TROLLEY_POSITION` ON.

Aturan edge:

1. `expiresAt` tidak null dan sekarang > `expiresAt` → **jangan jalankan**, ACK `EXPIRED`.
   (TTL Jetson 30 s, jadi jam Jetson harus sinkron NTP.)
2. `OFF` **selalu** dijalankan, tidak pernah diblokir interlock.
3. `ON` dengan durasi: matikan sendiri setelah `min(durationSeconds, max_on_s)`.
4. `ON` tanpa durasi untuk output yang wajib berdurasi → tolak (backend juga menolak, ini lapis kedua).
5. Perintah ke device/aktuator yang tidak dikenal, atau `command` lain → ACK gagal `UNSUPPORTED`.
6. Jika `commandUid` sama datang dua kali (QoS 1 bisa mengirim ulang) → jangan eksekusi ulang;
   simpan ±200 UID terakhir.

## 6. Cara menguji kontrak tanpa edge

Backend profil `dev` punya `GET /api/dev/outbox` (melihat command yang dipublish) dan endpoint peniru
edge: `POST /api/dev/telemetry`, `/api/dev/devices/{code}/heartbeat|status|alerts|command-ack|ai-detections`.
Contoh payload di dokumen ini harus lolos di endpoint-endpoint itu. Uji akhir tetap lewat Mosquitto
sungguhan (`MQTT_ENABLED=true`).
