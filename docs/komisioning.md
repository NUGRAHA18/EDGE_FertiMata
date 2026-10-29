# Komisioning Edge Agent di Greenhouse (F11)

Lembar kerja uji lapangan. Isi kolom **Hasil** dengan `✓`, `✗` + catatan, atau angka yang terbaca, lalu
tanggal dan inisial penguji. Perintah servis (start/stop, log, ganti register) ada di
[08-operasi.md](08-operasi.md); perintah `modbus_probe` per perangkat ada di
[03-persiapan.md](03-persiapan.md#cara-cek-register-dengan-modbus_probe-p-h3--p-h8).

**Sebelum mulai**

- [ ] Agent terpasang (`deploy/install.sh`), config produksi di `/etc/fertimata/fertimata.yaml`
- [ ] Register P-H4 … P-H8 sudah diisi di config dan `--check-config` lolos
- [ ] Backend + Mosquitto jalan, dashboard bisa dibuka, ada akun operator
- [ ] Terminal log terbuka: `journalctl -u fertimata-edge -f`
- [ ] Terminal MQTT terbuka: `mosquitto_sub -t 'fertimata/#' -v`
- [ ] Untuk uji relay pertama: RCBO pompa siram OFF dan motor trolley dicabut sampai baca balik terbukti benar
- [ ] Ada orang di dekat panel yang bisa memutus daya bila sesuatu tidak berhenti

| Tanggal | Penguji | Versi agent (`systemctl status` / status `agentVersion`) | Commit repo |
|---|---|---|---|
| | | | |

---

## F11.1 Daftar cek per perangkat

### Bus RS485 dan modul Modbus

Agent dihentikan saat memakai `modbus_probe`; nyalakan lagi untuk kolom "di dashboard".

| Perangkat (config) | Alamat | Cek | Harapan | Hasil | Tgl/Paraf |
|---|---|---|---|---|---|
| Adaptor RS485 | — | `ls -l /dev/rs485` | Symlink ke `ttyUSB*`, grup `dialout` | | |
| Seluruh bus | — | `modbus_probe --scan 1-31` dengan `--fc 3`, `--fc 4`, `--fc 1` | Hanya 1, 3, 4, 6, 10, 20, 21, 30 yang muncul | | |
| `soil` sensor tanah | 1 | Baca register; siram sedikit di dekat probe | Kelembapan naik, suhu 20–35 °C, pH 4–8 | | |
| `air` XY-MD02 | 3 | Baca; tiup/genggam sensor | Suhu dan RH berubah, cocok dengan termometer ±1 °C / ±5 % | | |
| `light` sensor cahaya | 4 | Baca terbuka vs ditutup | Siang 10.000–100.000 lux, ditutup turun drastis | | |
| `water_t` DS18B20 | 6 | Celup air hangat lalu dingin | Mengikuti termometer ±0,5 °C | | |
| `adc` pembaca EC/pH | 10 | Tegangan diketahui ke A1 | Satuan mV sesuai `devices.adc.registers` | | |
| `relay_p` relay panel 4 kanal | 20 | Lihat tabel relay di bawah | | | |
| `relay_t` relay kotak tangki 8 kanal | 21 | Lihat tabel relay di bawah + IN1 | | | |
| `pzem` meteran listrik | 30 | Baca FC4 0–9 | Tegangan 200–240 V, frekuensi ~50 Hz | | |
| Semua | — | Agent jalan: `journalctl … \| grep "perangkat tidak menjawab"` selama 30 menit | Tidak ada | | |
| Semua | — | Dashboard: tiap device logis ONLINE dan angkanya masuk akal | GREENHOUSE-01, PZEM-01, PANEL-01, RIO-TANK-01, JETSON-01 | | |

### Relay: klik, baca balik, dan arti kanal

Dari dashboard: ON dengan durasi pendek (5 s untuk pompa, 2 s untuk trolley), lalu biarkan berhenti
sendiri. Cocokkan LED/klik relay, beban yang bergerak, dan state di dashboard setelah ACK.

| Relay | Kanal | Aktuator | Beban | ON → klik + beban jalan | Baca balik = dashboard | Berhenti sendiri | Tgl/Paraf |
|---|---|---|---|---|---|---|---|
| 20 | R0 | `DIST-PUMP` | Pompa siram (SSR) | | | | |
| 20 | R1 | `CAMERA-PAN` | Pan kamera (ON = barat) | | | — | |
| 20 | R2 | `TROLLEY-RUN` | Motor trolley | | | | |
| 20 | R3 | `TROLLEY-DIR` | Arah (ON = utara/END) | | | — | |
| 21 | R0 | `DOSING-N` | Peristaltik N | | | | |
| 21 | R1 | `DOSING-P` | Peristaltik P | | | | |
| 21 | R2 | `DOSING-K` | Peristaltik K | | | | |
| 21 | R3 | (cadangan) | — | Tetap OFF | | — | |
| 21 | R4 | `STIRRER` | Pompa aduk | | | | |
| 21 | R5 | `EC-POWER` | Daya papan EC | | | — | |
| 21 | R6–R7 | (cadangan) | — | Tetap OFF | | — | |
| 21 | IN1 | `tank.float_low_ok` | Pelampung drum | Angkat → `true`, turunkan → `false` di telemetri | | — | |

Profil relay yang terpasang (P-H4): A coil / B holding: ______ · Mode nyala-berwaktu (S20) ada / tidak: ______

### GPIO trolley, kamera, Jetson

| Bagian | Cek | Harapan | Hasil | Tgl/Paraf |
|---|---|---|---|---|
| Reed | Dorong trolley pelan melewati satu magnet (motor dicabut) | Satu tepi per magnet, tanpa pantulan ganda (`debounce_ms`) | | |
| HOME | Tekan sakelar HOME | `trolley.home_limit` berubah | | |
| END | Tekan sakelar END | `trolley.end_limit` berubah | | |
| Homing | `TROLLEY-GOTO` → `HOME` dari tengah rel | Berhenti di HOME, alert INFO `TROLLEY_ARRIVED` station 0 | | |
| Stasiun | `GOTO_STATION` 1, 5, 10 | Berhenti di depan tanaman yang benar, `trolley.station` sesuai | | |
| Kamera | `ls -l /dev/cam-trolley`; patroli manual `TROLLEY-PATROL` START | 20 foto PNG tajam (10 stasiun × timur/barat) + JSON di `data/citra/<tanggal>/` | | |
| Kamera | Bandingkan foto dua malam berbeda | Exposure dan warna sama (terkunci) | | |
| SoC | Telemetri `jetson.soc_temperature` | Masuk akal (30–70 °C) | | |
| Jam | `timedatectl` | `System clock synchronized: yes`, zona tidak memengaruhi payload (UTC `Z`) | | |
| Watchdog | `ls /dev/watchdog*` (P-H15, F10.4) | Ada | | |
| Disk | `df -h /var/lib/fertimata` | Ruang bebas > `camera.disk_warn_free_gb` | | |

---

## F11.2 Uji aturan keselamatan S1–S20 di hardware nyata

Aturan lengkap: [04-aturan-keselamatan.md](04-aturan-keselamatan.md). Semua aturan sudah lulus test otomatis
dengan HAL palsu; di sini dibuktikan di barang aslinya. Kode penolakan muncul di awal `errorMessage` ACK
(`KODE: …`). Uji yang memakai "trik config" mengubah config sementara: **simpan cadangan dan kembalikan**
sesudahnya (prosedur di [08-operasi.md §4](08-operasi.md#4-mengubah-config)).

| # | Langkah uji | Harapan | Hasil | Tgl/Paraf |
|---|---|---|---|---|
| S1 | Nyalakan beberapa relay lewat `modbus_probe` (agent berhenti), lalu `systemctl start` | Semua relay OFF dan terbaca OFF sebelum `agent siap`; log `relay dimatikan semua` | | |
| S2 | Trik config: `outputs.pump_dist.max_on_s: 20`. Kirim `DIST-PUMP` ON langsung ke MQTT (backend membatasi durasi) dengan `durationSeconds` 60; lalu ulangi dengan 5 | 60 s: pompa mati di detik ke-20 + alert `OUTPUT_TIMEOUT`, ACK sukses. 5 s: mati tepat waktu tanpa alert. | | |
| S3 | Saat patroli berjalan (bus sibuk), `TROLLEY-RUN` OFF dari dashboard | Trolley berhenti segera (< 1 s), ACK sukses | | |
| S4 | `TROLLEY-RUN` ON, lalu `DOSING-N` ON | ACK gagal `INTERLOCK_EXCLUSIVE`, pompa tidak klik | | |
| S5 | `DIST-PUMP` ON, lalu `STIRRER` ON | ACK gagal `INTERLOCK_EXCLUSIVE` | | |
| S6 | `DOSING-N` ON, lalu `DOSING-P` ON | ACK gagal `INTERLOCK_EXCLUSIVE` | | |
| S7 | `DIST-PUMP` ON lalu cabut PLN (Jetson tetap di aki) | Pompa mati, status `powerSource` = `BATTERY`, perintah aktivasi ditolak `INTERLOCK_POWER`; normal lagi saat PLN pulih (F5.5) | | |
| S8 | Turunkan pelampung (air rendah) lalu `DIST-PUMP` ON; ulangi dengan kabel IN1 dicabut | Ditolak `INTERLOCK_FLOAT`, alert `FLOAT_LOW`; pompa yang sedang ON dimatikan | | |
| S9 | Trik config: persempit `analog.valid.ph` sehingga bacaan nyata di luar rentang | pH tidak dikirim di telemetri, alert `EC_PH_INVALID`. (Penolakan dosis dijaga layanan tim AI, Q8) | | |
| S10 | Trik config: `pump_baseline_a` di bawah arus diam panel yang terukur, pompa OFF | Alert CRITICAL `SSR_SHORT` setelah `anomaly_reads` bacaan | | |
| S11 | Trik config: `pump_nominal_a` jauh di atas arus pompa nyata, `DIST-PUMP` ON | Pompa dimatikan, alert CRITICAL `PUMP_DRY_RUN` | | |
| S12 | `TROLLEY-RUN` ON, lalu `TROLLEY-DIR` ON/OFF | Ditolak `INTERLOCK_MOVING`, DIR tidak klik selama RUN ON. Saat GOTO berbalik arah: RUN OFF → jeda → DIR → RUN ON (dengar urutan klik) | | |
| S13 | Trolley ke arah HOME, tekan HOME dengan tangan sebelum sampai; lalu RUN ke arah limit yang aktif | Berhenti seketika; jalan ke arah limit aktif ditolak `INTERLOCK_LIMIT` | | |
| S14 | Saat trolley bergerak: `CAMERA-PAN` ON; amati log | Pan ditolak `INTERLOCK_MOVING`; log `polling dijeda (trolley bergerak)` → `polling dilanjutkan` | | |
| S15 | Cabut kabel reed, `GOTO_STATION` 5 | Berhenti ≤ `segment_timeout_s`, alert CRITICAL `TROLLEY_NO_REED` | | |
| S16 | Cabut konektor limit HOME (atau jepit HOME dan END bersamaan) | Trolley dikunci, alert CRITICAL `TROLLEY_LIMIT_WIRE`, perintah gerak ditolak sampai agent di-restart | | |
| S17 | Kirim perintah ke MQTT dengan `expiresAt` 1 menit lalu | Tidak dijalankan, ACK gagal `EXPIRED` | | |
| S18 | `grep -rnE "shutdown|poweroff|reboot" /opt/fertimata-edge/venv/lib/python3.10/site-packages/fertimata_edge` | Tidak ada pemanggilan (komentar boleh) | | |
| S19 | Saat relay ON, cabut kabel data relay 21 atau matikan modulnya sebentar lalu hidupkan; atau ganti `devices.relay_t.relay.read_base` sementara | Alert `RELAY_MISMATCH`, output dikunci (`OUTPUT_FAULT`) sampai restart | | |
| S20 | (a) `DIST-PUMP` ON 120 s, lalu `sudo systemctl stop fertimata-edge`. (b) Ulangi, tapi `sudo systemctl kill -s KILL fertimata-edge` | (a) Pompa OFF sebelum agent keluar, log `relay dimatikan semua … cause=stop` (Q9); `systemctl start` lagi. (b) Agent hidup lagi ≤ 10 s dan pompa OFF (S1). Bila relay mendukung mode nyala-berwaktu: pompa mati sendiri walau agent tidak hidup | | |

Contoh perintah langsung ke MQTT (S2, S17), dari Jetson:

```bash
mosquitto_pub -q 1 -t fertimata/PANEL-01/command -m '{"commandUid":"uji-s2-1","deviceId":"PANEL-01",
  "actuatorCode":"DIST-PUMP","command":"ON","parameters":{"durationSeconds":5},
  "issuedAt":"2026-11-02T12:00:00Z","expiresAt":"2026-11-02T12:05:00Z"}'
```

Ganti `commandUid` setiap kirim (yang sama diabaikan) dan sesuaikan `issuedAt`/`expiresAt` dengan jam
sekarang (UTC). Untuk S17 isi `expiresAt` dengan waktu yang sudah lewat.

Catatan risiko yang ditemukan saat uji (tulis juga di laporan):

-

---

## F11.3 Pengukuran lapangan (diisi saat komisioning)

| Besaran | Cara | Nilai | Masuk config / ke |
|---|---|---|---|
| Arus panel saat pompa OFF (P-H11) | Rata-rata `mains.current` 5 menit | | `power.pump_baseline_a` |
| Arus pompa siram ON (P-H11) | Rata-rata setelah 10 s | | `power.pump_nominal_a` |
| Noise arus | Selisih maks–min saat OFF | | `power.ssr_short_margin_a` |
| Volume kerja drum (P-H12) | Gelas ukur | | Layanan tim AI |
| Debit DOSING-N/P/K (P-H12, 3 ulangan) | Air saja, 30 s, gelas ukur (F9.5) | | Layanan tim AI |
| Kalibrasi EC 1413 / 12,88 (P-H13) | `tools/calibrate_ec_ph.py --write` | | `analog.ec.cal` |
| Kalibrasi pH 6,86 / 4,01 / 9,18 (P-H13) | idem | | `analog.ph.cal` |
| Ambang blur kamera (P-H10) | `sharpness` foto tajam vs goyang | | `camera.blur_threshold` |

## F11.4 Uji ketahanan 72 jam

Mulai: ______ Selesai: ______

| Cek | Cara | Hasil |
|---|---|---|
| Tidak ada restart tak terduga | `systemctl show fertimata-edge -p NRestarts` di awal dan akhir | |
| Kelengkapan telemetri | Jumlah pesan per device dibanding yang diharapkan dari `polling.*` | |
| Patroli malam | 3 patroli `PATROL_DONE` status `OK` | |
| Alert palsu | Daftar alert selama 72 jam dan sebabnya | |
| Ruang disk dan log | `df -h`, `journalctl --disk-usage` di awal dan akhir | |
