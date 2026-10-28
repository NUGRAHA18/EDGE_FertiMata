# Perubahan yang Dibutuhkan di Backend

> Daftar pekerjaan untuk repo backend FertiMata, yang ditemukan saat mengembangkan edge agent.
> **Edge tidak mengubah repo backend.** Berkas ini diserahkan ke pengembang backend; setelah
> dikerjakan, kontrak di [02-kontrak-mqtt.md](02-kontrak-mqtt.md) diperbarui lebih dulu, baru kode edge.
> Ringkasannya juga ada di tabel "Perubahan yang dibutuhkan di backend" di [TASKS.md](../TASKS.md).

Status per 26 Okt 2026 (laporan sesi backend; di-commit di branch BE `feat/edge-alignment-be5-be7` 7899118 + b03fbb4, belum di-merge ke main dan belum di-push): **BE-5 selesai**, **BE-7 selesai**
(`actuator_command.failure_code`, Flyway V8), **BE-6 selesai** (topik MQTT/`MQTT_CLIENT_ID`/`JWT_ISSUER` di
`.env`, lalu database dan role diganti nama ke `fertimata` dengan data utuh). BE-1 … BE-4 dilaporkan selesai 26 Okt oleh sesi backend (branch `feat/edge-alignment-be5-be7`,
belum di-commit); kontrak hasilnya ada di [02-kontrak-mqtt.md](02-kontrak-mqtt.md) §3, §4.5, §5.1. Sisi
edge: `TROLLEY-GOTO` dan `TROLLEY-PATROL` sudah berjalan (F7.5, F8.4, 26 Okt); `DOSING-LOOP` sejak 28 Okt (F9,
dengan simulator; angka P-H12 belum ada). Alert hasil baru `DOSING_FAILED` (WARNING) memakai tipe bebas backend.

| ID | Ringkas | Prioritas | Edge yang menunggu |
|---|---|---|---|
| BE-1 | Aktuator virtual + perintah tingkat tinggi (trolley, patroli, dosing) | Tinggi (D2, D3) | F7.5, F8.4, F9.1 |
| BE-2 | Sajikan berkas citra (`imageUrl`) | Sedang (D7) | F8, F12 |
| BE-3 | Sesuaikan timeout offline dan interval telemetri | Rendah (D4) | — |
| BE-4 | Metrik `dosing.*` di katalog | Sedang | F9.4 |
| BE-5 | `docker-compose.yml` meneruskan `DEV_SIMULATOR_ENABLED` | Rendah | uji end-to-end |
| BE-6 | Penamaan `smartmelon` → `fertimata` di database dev | Rendah (D8) | — |
| BE-7 | (Opsional) kenali kode alasan ACK gagal dari interlock edge | Rendah | — |

---

## BE-1 — Aktuator virtual dan perintah tingkat tinggi (D2, D3)

**Masalah.** Katalog Rev A hanya mengenal relay mentah (`TROLLEY-RUN`, `TROLLEY-DIR`, `DOSING-N`, …).
Patroli kamera (§9.4) dan loop dosing (§8.2) butuh puluhan langkah berurutan yang cepat dan harus tetap
aman bila backend mati, jadi dijalankan edge. Backend cukup memicu dan memantau.

**Usulan.** Tambah aktuator virtual di `fertimata-rev-a.yml`. Relay mentah tetap ada untuk servis manual.

| Aktuator | Device | Tipe usulan | `command` | `parameters` |
|---|---|---|---|---|
| `TROLLEY-PATROL` | `JETSON-01` | `TROLLEY_PATROL` | `START`, `STOP` | — |
| `TROLLEY-GOTO` | `JETSON-01` | `TROLLEY_POSITION` | `HOME`, `GOTO_STATION`, `STOP` | `{"station": 1..10}` untuk `GOTO_STATION` |
| `DOSING-LOOP` | `RIO-TANK-01` | `DOSING_LOOP` | `DOSE_TO_TARGET`, `STOP` | `{"targetEc": 2.2, "phase": "vegetatif"}` (mS/cm, nama fase sesuai config edge) |

Yang perlu disesuaikan di backend:

1. **Validasi command.** Saat ini aktuator dianggap ON/OFF. Aktuator virtual menerima daftar `command`
   di atas; tolak yang lain dengan 400.
2. **Interlock lapis 2.** `activating-commands` sekarang `["ON"]`. Tambahkan `START`, `HOME`,
   `GOTO_STATION`, `DOSE_TO_TARGET` sebagai perintah aktivasi, dan masukkan tipe baru ke
   `blocked-on-backup-power-types` serta `exclusive-type-groups`:
   - `TROLLEY_PATROL`, `TROLLEY_POSITION` ekuivalen `TROLLEY_MOTOR` (tidak bersamaan dengan dosing/aduk).
   - `DOSING_LOOP` ekuivalen `DOSING_PUMP` (diblokir float LOW, PLN padam, pompa distribusi).
   - `STOP` diperlakukan seperti `OFF`: **tidak pernah diblokir**.
3. **Tanpa `durationSeconds`.** Tipe virtual tidak masuk `timed-types`; batas waktunya dijaga edge
   (patroli ±3 menit, dosing `max_iterations`).
4. **Arti ACK.** Edge mengirim ACK sukses saat proses **dimulai** (`actuatorState: "ON"`), lalu
   `actuators: {"TROLLEY-PATROL": "OFF"}` di pesan status saat selesai/batal. Hasil dilaporkan lewat
   alert `INFO` (mis. "Patroli selesai, 20 foto") atau `WARNING` (mis. `DOSING_NOT_CONVERGING`).
   Command di backend cukup EXECUTED saat ACK; tidak perlu status "selesai" baru.
5. **Pemicu manual patroli (D3).** Tombol "Patroli sekarang" di dashboard = `TROLLEY-PATROL START`.
   Jadwal malam tetap di edge (`camera.patrol_schedule`), jalan walau backend mati.

## BE-2 — Menyajikan berkas citra (D7)

Edge menyimpan foto di `data/citra/YYYY-MM-DD/` di Jetson dan mengirim `imageUrl` relatif, mis.
`/captures/2026-10-22/20261022-1905_S03_timur.png`. Usulan: backend memetakan `/captures/**` ke folder
itu (read-only, mount volume bila backend di container), dengan otorisasi sama seperti API lain.

## BE-3 — Timeout offline dan interval telemetri (D4)

Heartbeat edge 30 s per device. `DEVICE_OFFLINE_TIMEOUT_SECONDS=120` sudah cocok (toleran 3 heartbeat
hilang). `METRICS_EXPECTED_TELEMETRY_INTERVAL` (default 30s) sebaiknya per device mengikuti kelompok
polling tercepat: PZEM 3 s, relay/tangki float 5 s, udara/cahaya 60 s, tanah 300 s, EC/pH 600 s.
Bila backend hanya mendukung satu nilai global, cukup dicatat bahwa metrik kelengkapan untuk
GREENHOUSE-01 dan EC/pH akan terlihat rendah padahal normal.

## BE-4 — Metrik `dosing.*`

Untuk catatan tiap iterasi loop dosing (Protokol Data, laporan riset). Usulan metrik di `RIO-TANK-01`:
`dosing.iteration`, `dosing.ec_target`, `dosing.ec_measured`, `dosing.n_ml`, `dosing.p_ml`, `dosing.k_ml`.
Kontrak §3: metrik baru wajib ditambahkan ke katalog agar tidak menjadi sensor placeholder nonaktif.

## BE-5 — `DEV_SIMULATOR_ENABLED` di docker compose

Layanan `backend` di `docker-compose.yml` tidak meneruskan `DEV_SIMULATOR_ENABLED`, sehingga container
dev selalu menjalankan simulator (default `true`). Untuk uji dengan edge sungguhan simulator harus mati,
jadi sekarang terpaksa memakai `mvnw` di port 8090. Usulan: tambahkan
`DEV_SIMULATOR_ENABLED: ${DEV_SIMULATOR_ENABLED:-true}` di blok `environment`.

## BE-6 — Penamaan `smartmelon`

Keputusan D8: nama yang dipakai **FertiMata**. Container backend dev masih memakai database dan user
`smartmelon` (dari `.env`), sementara default di `application.yml` dan compose sudah `fertimata`.
Usulan: samakan `.env` ke `fertimata`. Mengganti nama database berarti data dev lama tidak terbawa;
lakukan saat data dev boleh dibuang. Folder lokal `BE-melonSmart`/`FE-melonSmart`/`EDGE-melonSmart`
bisa di-rename kapan saja; repo edge sudah `EDGE_FertiMata` di GitHub.

## BE-7 — Kode alasan di ACK gagal (opsional)

Sejak F4.6 edge menolak perintah dengan `success=false` dan `errorMessage` berformat
`"KODE: teks untuk petani"` (Kontrak §4.3). Kode yang dipakai:

| Kode | Arti |
|---|---|
| `EXPIRED` | Lewat `expiresAt` |
| `UNSUPPORTED` | Device/aktuator/perintah tidak dikenal edge |
| `INVALID` | Isi perintah tidak lengkap, mis. `durationSeconds` wajib tapi kosong atau ≤ 0 |
| `INTERLOCK_EXCLUSIVE` | Bentrok dengan output lain yang menyala (S4–S6) |
| `INTERLOCK_FLOAT` | Air drum rendah atau pelampung tidak terbaca (S8) |
| `INTERLOCK_MOVING` | Arah trolley/pan diubah saat trolley jalan (S12, S14) |
| `INTERLOCK_LIMIT` | Trolley sudah di ujung rel, atau kabel limit bermasalah (S13, S16) |
| `INTERLOCK_POWER` | Listrik bukan dari PLN, atau belum/tidak terbaca (S7) |
| `BUSY` | EC-POWER sedang dipakai siklus baca pH/EC otomatis |
| `OUTPUT_FAULT` | Output dikunci karena relay pernah tidak menurut (S19) |
| `DEVICE_OFFLINE` | Relay tidak menjawab |
| `RELAY_MISMATCH` | Baca balik relay tidak sesuai perintah (S19) |

Backend **tidak wajib** berubah: teks setelah titik dua sudah bisa ditampilkan apa adanya. Usulan opsional:
simpan kode di kolom terpisah supaya bisa difilter atau diberi ikon di dashboard.
