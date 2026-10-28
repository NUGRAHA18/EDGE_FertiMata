# CLAUDE.md — Edge Agent FertiMata

Panduan untuk Claude (dan developer) yang bekerja di repo ini. Baca seluruhnya sebelum mengubah apa pun.

## Apa repo ini

Program Python yang berjalan di **Jetson Orin Nano** dan menjadi jembatan antara backend FertiMata
(lewat MQTT di `127.0.0.1`) dan hardware greenhouse melon: bus RS485/Modbus RTU (`/dev/rs485`), GPIO
trolley, dan kamera USB. Backend dan frontend ada di repo terpisah (`BE-melonSmart`, `FE-melonSmart`).

## Sumber kebenaran (urut prioritas)

1. [`docs/04-aturan-keselamatan.md`](docs/04-aturan-keselamatan.md): tidak boleh dilanggar
2. [`docs/02-kontrak-mqtt.md`](docs/02-kontrak-mqtt.md): bentuk pesan dengan backend
3. [`docs/referensi/Arsitektur-Sistem.md`](docs/referensi/Arsitektur-Sistem.md): hardware Rev A (alamat, register, urutan switching)
4. [`docs/01-konsep-dan-cara-kerja.md`](docs/01-konsep-dan-cara-kerja.md): arsitektur agent
5. [`docs/referensi/Protokol-Data.md`](docs/referensi/Protokol-Data.md): kebutuhan riset (citra, kalibrasi)
6. [`TASKS.md`](TASKS.md): apa yang dikerjakan dan urutannya

Berkas di `docs/referensi/` adalah **salinan** dari `BE-melonSmart/docs` per 22 Okt 2026. Kalau ada
konflik dengan versi backend yang lebih baru, tanyakan dulu sebelum mengikuti salah satunya.

## Alur kerja wajib

1. **Sebelum mulai:** baca `TASKS.md`, pilih tugas berikutnya yang tidak terhambat (⛔), dan sebutkan
   ID tugas yang akan dikerjakan. Kalau tugas terlalu besar, pecah dulu di `TASKS.md`.
2. **Saat mengerjakan:** tulis test lebih dulu atau bersamaan. Semua logika harus bisa dites dengan
   HAL palsu di Windows/laptop, tanpa hardware.
3. **Setelah selesai:** perbarui `TASKS.md` (centang, tandai `[~]` kalau parsial, tambah baris di
   **Log perkembangan** berisi tanggal, ID, ringkasan, dan bukti seperti hasil test atau nama berkas).
   Tugas yang selesai tanpa test dianggap belum selesai.
4. Kalau menemukan sesuatu yang butuh perubahan backend, catat di tabel "Perubahan yang dibutuhkan di
   backend" pada `TASKS.md`. **Jangan** mengubah repo backend dari sini.
5. Kalau sebuah keputusan (D1–D9 di `docs/03-persiapan.md`) belum dijawab dan menghambat, pakai
   usulannya lalu tulis bahwa usulan itu dipakai. Kalau keputusan menyangkut keselamatan, tanya dulu.
6. Jangan commit kecuali diminta.

## Aturan teknis

- **Python 3.10.** Jangan pakai fitur yang lebih baru (Jetson memakai Ubuntu 22.04).
- `asyncio`, satu proses. Hanya **bus manager** yang boleh membuka `/dev/rs485`.
- **Tidak ada angka hardware di kode.** Alamat, register, kanal, pin, batas waktu, interval, ambang,
  dan kalibrasi semuanya ada di `fertimata.yaml`. Nilai "belum terverifikasi" tidak boleh menjadi
  konstanta. Pengecualian: konstanta yang terverifikasi di manual (peta register PZEM-016 §4.5) boleh
  menjadi default profil, tapi tetap bisa ditimpa config.
- Semua akses hardware lewat antarmuka `hal`. Logika tidak boleh mengimpor `pymodbus`, `gpiod`, atau
  `cv2` secara langsung.
- `OFF`/STOP tidak pernah diblokir dan selalu didahulukan. Setiap tulis relay diikuti baca balik.
- Tidak boleh ada kode yang mematikan/me-reboot Jetson (`shutdown`, `poweroff`, `reboot`).
- Waktu di payload: UTC ISO-8601 dengan `Z`. Jadwal untuk manusia (patroli) memakai WIB.
- Nilai yang gagal dibaca **dihilangkan** dari telemetri, tidak pernah diisi 0 atau nilai palsu.
- YAML: selalu kutip `"ON"`/`"OFF"`.

## Gaya

- Identifier (nama variabel, fungsi, kelas) dalam bahasa Inggris. Komentar dan dokumen dalam bahasa
  Indonesia.
- Teks yang bisa sampai ke petani (`title`/`message` alert, `errorMessage` ACK) memakai bahasa
  Indonesia sederhana tanpa istilah teknis, mis. "Air di drum hampir habis", bukan "Float LOW open".
  Detail teknis ditaruh di `metadata`.
- Komentar menjelaskan **kenapa** (rujuk § dokumen), bukan apa.
- Format & lint: `ruff`. Test: `pytest`.

## Menjalankan (setelah F1/F2 selesai)

```bash
# Laptop, tanpa hardware
python -m fertimata_edge --config config/fertimata.dev.yaml      # agent.hal: fake
pytest

# Backend pasangan untuk uji end-to-end (di BE-melonSmart)
docker compose up -d mosquitto
SPRING_PROFILES_ACTIVE=dev MQTT_ENABLED=true DEV_SIMULATOR_ENABLED=false SERVER_PORT=8090 ./mvnw spring-boot:run
```

Port 8080 di laptop dipakai Docker. Pakai 8090 untuk backend dari `mvnw`, lalu matikan proses
`java.exe` sesudahnya.
