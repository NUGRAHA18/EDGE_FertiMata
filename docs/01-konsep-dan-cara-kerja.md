# Konsep dan Cara Kerja Edge Agent FertiMata

> Dokumen ini menjelaskan **apa** edge agent itu dan **bagaimana** ia bekerja, sebelum satu baris
> kode ditulis. Acuan: [`referensi/Arsitektur-Sistem.md`](referensi/Arsitektur-Sistem.md) (Revisi A,
> 21 Okt 2026), [`referensi/Protokol-Data.md`](referensi/Protokol-Data.md), dan kode backend
> (`BE-melonSmart`, paket `mqtt/`). Kontrak pesan rinci ada di [02-kontrak-mqtt.md](02-kontrak-mqtt.md).

---

## 1. Posisi edge agent dalam sistem

Semua berjalan di **satu Jetson Orin Nano**. Backend Java **tidak pernah** menyentuh port serial,
GPIO, atau kamera. Itu tugas edge agent.

```
┌──────────────────────────── JETSON ORIN NANO ─────────────────────────────┐
│                                                                           │
│  Frontend (Next.js) ⇄ Backend (Spring) ⇄ PostgreSQL                       │
│                          ⇅  MQTT  (Mosquitto, 127.0.0.1:1883)            │
│                    ┌─────┴──────────────────────────────┐                 │
│                    │        EDGE AGENT (Python)          │  ← repo ini    │
│                    │  satu proses, layanan systemd       │                 │
│                    └──┬───────────┬──────────────┬──────┘                 │
│                       │           │              │                        │
│                 /dev/rs485    gpiochip     /dev/cam-trolley                │
└───────────────────────┼───────────┼──────────────┼────────────────────────┘
                        │           │              │
          bus RS485 Modbus RTU   papan GPIO     kamera USB (UVC)
          ├ relay 20 (panel)     ├ reed stasiun   di trolley
          ├ PZEM-016 (30)        ├ limit HOME
          ├ relay 21 (tangki)    └ limit END
          ├ ADC 10 (EC A1, pH A2)
          ├ DS18B20 (6)
          ├ SHT40 (3), cahaya (4)
          └ tanah 7-in-1 (1)
```

Pembagian peran yang dipegang ketat:

| Lapisan | Memutuskan | Tidak boleh |
|---|---|---|
| **Backend** | Siapa boleh memerintah, mencatat, menampilkan, interlock lapis kedua, jadwal dan keputusan tingkat tinggi | Membuka serial/GPIO, menafsirkan register |
| **Edge agent** | Cara menjalankan perintah di hardware, **keselamatan lapis pertama**, deteksi gangguan (padam, SSR short, pompa kering) | Menyimpan riwayat jangka panjang, punya UI sendiri |

Interlock di backend hanya berlaku selama backend hidup. **Edge agent wajib menegakkan aturan yang
sama secara lokal**, karena ia yang terakhir menyentuh relay ([04-aturan-keselamatan.md](04-aturan-keselamatan.md)).

---

## 2. Analogi singkat

- **Backend** = kantor. Ia menerima permintaan petani ("siram 10 menit"), memeriksa izin, mencatat.
- **MQTT** = kotak pos di dalam Jetson.
- **Edge agent** = mandor lapangan. Ia mengambil surat perintah dari kotak pos, memeriksa apakah
  aman ("listrik PLN mati? air drum habis? trolley sedang jalan?"), menekan saklar, lalu mengirim
  surat balasan ("sudah menyala" atau "gagal, alasannya ini"). Setiap beberapa detik ia juga keliling
  membaca semua meteran dan mengirim laporannya.

---

## 3. Isi edge agent (modul)

Satu proses Python `asyncio`. Setiap modul punya satu tanggung jawab.

| Modul | Tugas | Acuan |
|---|---|---|
| `config` | Membaca dan memvalidasi `fertimata.yaml` (peta alamat, register, batas waktu, interval). Nilai yang belum terverifikasi hanya ada di sini, bukan di kode | Arsitektur §11 |
| `bus` (bus manager) | **Satu-satunya** pemilik `/dev/rs485`. Antrean berprioritas: STOP > perintah > polling. Jeda ≥ 10 ms, timeout 300 ms (tanah 1000 ms), ulang 2×. Menunda polling saat trolley bergerak | §4.1, §4.6 |
| `profiles` | Driver per tipe perangkat: `pzem016`, `xy_md02`, `soil_7in1`, `lux_rs485`, `ds18b20_mb`, `adc_7ch`, `relay_mb` (profil A coil / B holding). Peta register dibaca dari config | §4.5 |
| `poller` | Jadwal baca per kelompok (PZEM 2–5 s, relay 5 s, udara/cahaya 60 s, tanah 5–10 menit, tangki 10 menit) → menyusun telemetri | §4.6 |
| `outputs` | Model semua output (pompa, aduk, trolley, pan, daya EC). Tulis relay lalu **baca balik**. Watchdog `max_on_s` per output yang mematikan sendiri | §5, §5.4 |
| `safety` | Penjaga lokal sebelum relay ditulis: padam, float LOW, eksklusivitas, DIR-sebelum-RUN, limit trolley | §5.3, §12 |
| `power` | Menentukan `MAINS` / `BATTERY` / `UNKNOWN` dari PZEM + respons relay 20. Deteksi SSR short dan pompa kering dari arus | §7.4, §7.5 |
| `analog` | Urutan baca pH → EC → suhu larutan (R5 OFF/ON, median, kompensasi suhu) | §6.3 |
| `gpio` | Reed, HOME, END lewat libgpiod dengan debounce dan deteksi tepi | §6.1 |
| `trolley` | `homing()`, `ke_stasiun(n)`, `patroli()`, beserta pemulihan galat | §9 |
| `camera` | Ambil foto dengan exposure/WB/fokus terkunci, simpan berkas + metadata | §9.5, Protokol §4 |
| `link` (MQTT) | Terima perintah, kirim telemetri/status/ACK/alert. Membangun topik dari template yang sama dengan backend | [02-kontrak-mqtt.md](02-kontrak-mqtt.md) |
| `commands` | Memetakan `actuatorCode` → output, cek `expiresAt`, jalankan, balas ACK. `CommandRouter` meneruskan aktuator virtual ke `virtual` | 02 §5 |
| `virtual` | Aktuator virtual backend: `TROLLEY-GOTO` (HOME/GOTO_STATION/STOP) dan `TROLLEY-PATROL` (START/STOP) sebagai tugas latar; ACK saat mulai, status ON/OFF, hasil lewat alert INFO | 02 §5.1 |
| `hal` (fake) | Pengganti bus/GPIO/kamera untuk pengembangan di laptop tanpa hardware | — |

Semua akses hardware lewat antarmuka `hal`. Implementasi asli (pymodbus, gpiod, OpenCV/V4L2) dan
implementasi palsu (simulator) bisa ditukar lewat config. Dengan begitu hampir semua logika bisa
dikembangkan dan dites di laptop Windows.

---

## 4. Alur kerja

### 4.1 Saat menyala (boot)

```
1. Baca & validasi fertimata.yaml           → gagal? keluar dengan pesan jelas (systemd restart)
2. Buka bus /dev/rs485
3. Tulis OFF ke SEMUA kanal relay 20 & 21    → baca balik. Tidak cocok? alert CRITICAL, tetap OFF
4. Baca PZEM + relay 20 sekali               → tentukan powerSource awal
5. Siapkan GPIO; baca HOME/END               → posisi trolley "tidak diketahui" sampai homing
6. Sambung ke MQTT, subscribe topik command
7. Kirim status awal per device logis (state aktuator = OFF semua)
8. Mulai poller, watchdog, penjadwal
```

Aturan §12.1: **tidak ada logika lain yang jalan sebelum langkah 3 selesai.**

### 4.2 Loop telemetri

```
poller (tiap kelompok, sesuai interval)
   → bus manager (antre) → profile.decode(register) → nilai bersatuan
   → kelompokkan per device logis (GREENHOUSE-01, RIO-TANK-01, PZEM-01, JETSON-01)
   → publish  fertimata/{device}/telemetry   {"timestamp": ..., "data": {"air.temperature": 29.8, ...}}
```

Perangkat yang tidak menjawab tidak diisi angka palsu. Metrik itu **tidak dikirim**, dan setelah
beberapa kali gagal edge mengirim alert `MODBUS_TIMEOUT`. Device logis yang semua perangkatnya diam
juga berhenti mendapat heartbeat, sehingga backend menandainya OFFLINE setelah 120 s. Itu
perilaku yang diinginkan.

### 4.3 Perintah dari dashboard sampai relay

```
Petani tekan "Nyalakan pompa N 5 detik"
  → Backend: cek role, interlock lapis 2 → simpan command (SENT) → publish
       fertimata/RIO-TANK-01/command  {commandUid, actuatorCode:"DOSING-N", command:"ON",
                                       parameters:{durationSeconds:5}, expiresAt}
  → Edge `link` menerima → `commands`:
       a. sekarang > expiresAt?           → tolak, ACK success=false "EXPIRED"
       b. actuatorCode dikenal?           → tidak: ACK gagal
       c. `safety` lokal mengizinkan?     → tidak: ACK gagal + alasan (mis. "PLN padam")
       d. bus manager: tulis relay 21 R0 ON → baca balik
       e. pasang timer mati = min(durationSeconds, max_on_s)
       f. publish .../command/ack {commandUid, success:true, actuatorState:"ON", executedAt}
  → Backend: command → EXECUTED, dashboard ikut berubah lewat WebSocket
  → 5 s kemudian timer edge: relay OFF → baca balik → status
       fertimata/RIO-TANK-01/status {"actuators": {"DOSING-N": "OFF"}}
```

ACK adalah **satu-satunya** cara command di backend berpindah dari SENT ke EXECUTED/FAILED.

### 4.4 Heartbeat dan status

Setiap ±30 s (usulan, lihat keputusan D4) edge mengirim pesan status untuk **setiap device logis
yang masih menjawab**:

- `JETSON-01`: `powerSource`, suhu SoC (di telemetri), versi agent di `metadata`
- `PANEL-01`, `RIO-TANK-01`: map `actuators` berisi state hasil baca balik relay
- `PZEM-01`, `GREENHOUSE-01`: heartbeat kosong cukup (telemetri juga dihitung tanda hidup)

### 4.5 PLN padam

```
PZEM: tegangan < 180 V, atau PZEM diam tapi relay 20 menjawab  → BATTERY
PZEM diam DAN relay 20 diam                                    → UNKNOWN (gangguan bus)
   → hentikan trolley, dosing, aduk, pompa distribusi (lokal, tanpa menunggu backend)
   → status JETSON-01 {"powerSource": "BATTERY"}   (backend: alert CRITICAL + blokir perintah)
   → semua perintah aktivasi tipe terlarang ditolak lokal selama belum MAINS
PLN kembali → MAINS → izin dibuka lagi (dosing setelah tegangan bus normal)
```

Jetson **tidak pernah di-shutdown oleh software** (§7.4).

### 4.6 Patroli kamera (malam hari)

```
homing → untuk stasiun 1..10: ke_stasiun(n) → tunggu 2 s → pan timur → 2 s → foto
                                             → pan barat → 2 s → foto
→ pan kembali timur → homing
Selama trolley bergerak: polling sensor ditunda, STOP prioritas tertinggi,
dosing/pompa dilarang. END/HOME tak terduga, reed diam 10 s, total > 60 s → berhenti + alert.
```

Pemicu: jadwal lokal `camera.patrol_schedule` (WIB, D3), jalan walau backend mati. Pemicu manual
dari backend menunggu BE-1. Modul: `patrol.py` (urutan, §9.6, jadwal) dan `captures.py` (berkas).

Hasil foto disimpan di disk Jetson (D1):

```
data/citra/2026-10-22/
  20261022-1903_S01_timur.png    ← nama tanpa ekstensi = captureId (Kontrak §4.5), waktu WIB
  20261022-1903_S01_timur.json   ← sidecar: stationCode, panDirection, capturedAt (UTC Z), patrolId,
                                   setelan kamera, sharpness/blurry, environment (suhu, RH, lux)
  patroli_20261022-1900.json     ← ringkasan: status OK/PARTIAL/FAILED/SKIPPED, foto tersimpan/gagal
```

Folder memakai tanggal mulai patroli. Foto dan sidecar ditulis atomik (berkas `.tmp` lalu rename),
sidecar **sesudah** foto. Layanan AI (D6) cukup memproses foto yang `.json`-nya sudah ada. Frame blur
(`sharpness` < `camera.blur_threshold`) diambil ulang sekali; bila tetap blur tetap disimpan dengan
`blurry: true`, karena data riset tidak dibuang edge. Hasil inferensi AI dikirim ke
`fertimata/JETSON-01/ai`. Model AI tidak termasuk lingkup awal repo ini (lihat TASKS F12).

### 4.7 Loop dosing (tahap akhir)

Loop tertutup berbasis EC (§8.2): aduk → baca pH/EC/suhu → hitung ΔEC → dosis 30–50% per iterasi
N → P → K satu per satu → aduk → ulang (maks 6). Karena butuh pembacaan cepat dan berurutan, loop ini
paling aman dijalankan **di edge**, dipicu satu perintah tingkat tinggi dari backend. Kontrak perintah
ini belum ada di backend (keputusan D2).

---

## 5. Pengembangan tanpa hardware

Mode `hal: fake` menjalankan agent penuh di laptop:

```
docker compose up mosquitto        (di BE-melonSmart)
backend: SPRING_PROFILES_ACTIVE=dev MQTT_ENABLED=true DEV_SIMULATOR_ENABLED=false SERVER_PORT=8090
edge   : python -m fertimata_edge --config config/fertimata.dev.yaml   (hal: fake)
```

Simulator backend **harus dimatikan** supaya tidak ada dua sumber data untuk device yang sama.
Bus palsu mensimulasikan register (nilai sensor berubah pelan, relay menyimpan state, PZEM bisa
"dipadamkan"). GPIO palsu memancarkan pulsa reed sesuai kecepatan trolley. Kamera palsu menulis
gambar uji.

---

## 6. Teknologi

| Kebutuhan | Pilihan | Catatan |
|---|---|---|
| Bahasa | Python 3.10 (bawaan Ubuntu 22.04 / JetPack 6) | Jangan pakai fitur > 3.10 |
| Modbus | `pymodbus` 3.x (async serial client) | Ditetapkan di Arsitektur §4.1 |
| MQTT | `paho-mqtt` 2.x (dibungkus `aiomqtt` atau thread sendiri) | MQTT v5, QoS 1 |
| GPIO | `gpiod` (libgpiod) | Versi v1/v2 dicek di Jetson (P-S5) |
| Kamera | OpenCV + V4L2 (`v4l2-ctl` untuk kunci exposure/WB) | Format dicek (§15 no. 3) |
| Config | PyYAML + `pydantic` v2 | Validasi saat startup |
| Test | `pytest`, `pytest-asyncio` | Semua logika dites dengan `hal` palsu |
| Layanan | systemd `Type=notify`, `Restart=always`, `WatchdogSec` (`deploy/`, ping dari `sdnotify.py`) | Bukan Docker: butuh /dev dan GPIO langsung |
