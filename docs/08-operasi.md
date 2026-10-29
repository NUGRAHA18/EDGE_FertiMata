# Operasi Harian Edge Agent di Jetson (F10.6)

Panduan singkat untuk siapa pun yang memegang Jetson: menyalakan/menghentikan agent, membaca log, dan
mengganti register atau nilai lapangan **tanpa mengubah kode**. Pemasangan pertama ada di
`deploy/install.sh`; uji lapangan ada di [komisioning.md](komisioning.md).

## 1. Letak berkas

| Apa | Lokasi | Catatan |
|---|---|---|
| Repo (sumber kode) | `~/FertiMata/EDGE_FertiMata` | Mengedit repo **tidak** mengubah agent yang berjalan sampai `install.sh` dijalankan lagi |
| Program terpasang (venv) | `/opt/fertimata-edge/venv` | Dibuat ulang/diperbarui oleh `install.sh` |
| Config produksi | `/etc/fertimata/fertimata.yaml` | Satu-satunya tempat alamat, register, pin, ambang, kalibrasi. Tidak pernah ditimpa `install.sh` |
| Data (foto patroli) | `/var/lib/fertimata/data/citra/YYYY-MM-DD/` | PNG + JSON sidecar + `patroli_*.json`. Tidak pernah dihapus otomatis |
| Unit systemd | `/etc/systemd/system/fertimata-edge.service` | Nama layanan: `fertimata-edge` |
| Log | journald (`journalctl -u fertimata-edge`) | Permanen, maks. 1 GB, 3 bulan (`deploy/journald/fertimata.conf`) |
| Port RS485 | `/dev/rs485` | Symlink udev ke adaptor FT232; jangan pakai `ttyUSB0` |

## 2. Start, stop, restart, status

```bash
systemctl status fertimata-edge            # jalan? sejak kapan? 10 baris log terakhir
sudo systemctl start fertimata-edge
sudo systemctl restart fertimata-edge      # = berhenti lalu boot ulang agent (S1: semua relay OFF dulu)
sudo systemctl stop fertimata-edge         # semua relay dimatikan dulu, lalu agent berhenti
```

- Agent menyala sendiri setiap Jetson hidup (`enable`) dan di-restart otomatis tanpa batas bila mati
  atau macet (`Restart=always`, `WatchdogSec=30`). Setiap start selalu diawali **S1**: semua relay
  ditulis OFF dan dibaca balik sebelum perintah apa pun diterima.
- Agent **tidak menunggu** broker MQTT atau jaringan. Kalau backend/Mosquitto mati, agent tetap
  menjaga relay dan menyambung ulang sendiri (log `MQTT terputus` → `menyambung ke broker`).

- **Berhenti = semua relay OFF (Q9).** `stop` (dan `restart`) lebih dulu mematikan semua kanal semua
  relay lalu membaca baliknya, baru agent keluar. Log: `relay dimatikan semua … cause=stop`. Batasnya
  `agent.stop_off_timeout_s` (15 s); kalau bus macet sehingga tidak selesai, log
  `relay tidak sempat dimatikan sebelum berhenti` muncul dan relay baru dimatikan saat agent start lagi
  (S1). Dalam kasus itu cek panel sebelum meninggalkan Jetson dengan agent mati.
- Yang **tidak** tercakup: agent yang mati mendadak (crash, `kill -9`, Jetson hang). Di situ yang
  menjaga adalah restart otomatis systemd → S1, dan mode nyala-berwaktu relay bila ada (S20, P-H4).

Arti kode keluar di `systemctl status` (`code=exited, status=N`):

| Kode | Arti | Tindakan |
|---|---|---|
| 0 | Berhenti normal (`stop`) | — |
| 2 | Config ditolak | Baca pesan `Config ditolak: …` di log, perbaiki config (bagian 4) |
| 3 | Hardware tidak bisa dibuka (HAL), mis. `/dev/rs485` tidak ada | Bagian 7 |
| lain / `signal=ABRT` | Agent macet dan dibunuh watchdog systemd | Simpan log (bagian 3), laporkan ke pengembang |

## 3. Melihat log

Satu baris per kejadian: `waktu(UTC) LEVEL modul pesan kunci=nilai`, mis.
`2026-11-02T12:00:03.120Z WARNING fertimata_edge.bus perangkat tidak menjawab device=soil address=1`.
Waktu log selalu **UTC** (WIB = UTC + 7).

```bash
journalctl -u fertimata-edge -f                         # ikuti langsung (Ctrl+C untuk keluar)
journalctl -u fertimata-edge -b                         # sejak Jetson terakhir menyala
journalctl -u fertimata-edge --since "1 hour ago"
journalctl -u fertimata-edge --since "2026-11-02 19:00" --until "2026-11-02 20:00"   # jam lokal Jetson
journalctl -u fertimata-edge -p warning                 # hanya WARNING ke atas
journalctl -u fertimata-edge --since today | grep "device=relay_t"
journalctl -u fertimata-edge --since today > ~/log-agent.txt    # untuk dikirim ke pengembang
journalctl --disk-usage
```

Log lebih rinci sementara (jangan dibiarkan, log membengkak): `sudo systemctl edit fertimata-edge`,
isi

```ini
[Service]
ExecStart=
ExecStart=/opt/fertimata-edge/venv/bin/python -m fertimata_edge --config /etc/fertimata/fertimata.yaml --log-level DEBUG
```

lalu `sudo systemctl restart fertimata-edge`. Kembalikan dengan `sudo systemctl revert fertimata-edge`
dan restart. Untuk perubahan permanen cukup ubah `agent.log_level` di config.

Pesan yang paling sering dicari:

| Pesan | Arti |
|---|---|
| `relay dimatikan semua` | S1 selesai saat boot |
| `S1: tidak semua relay terbukti OFF` | Ada relay yang tidak menjawab/tidak cocok saat boot. Cek panel dan alert `RELAY_MISMATCH` |
| `sumber daya awal power=MAINS` | PZEM terbaca. `power=None` = PZEM belum menjawab |
| `agent siap` | Boot selesai; perintah diterima |
| `perangkat tidak menjawab device=… address=…` / `perangkat menjawab` | Status tiap alamat Modbus berubah |
| `port RS485 tidak bisa dibuka` | `/dev/rs485` hilang atau dipakai program lain |
| `relay ditulis` | Setiap ON/OFF yang dijalankan (dengan sebabnya) |
| `output mati sendiri` | Durasi/watchdog habis (S2) |
| `kanal ON tanpa perintah dimatikan` | Relay ditemukan ON tanpa perintah, dimatikan |
| `float LOW berubah` | Pelampung drum berubah |
| `trolley dikunci: kabel limit` | S16, lihat bagian 6 |
| `alert` | Setiap alert yang dikirim ke backend (`type=…`) |
| `MQTT terputus` | Broker tidak terjangkau; pesan ditahan di antrean |
| `… belum punya register … (P-H8)`, `… (P-H11)`, `GPIO asli belum tersedia (F7.1)` | Fitur sengaja nonaktif karena config/hardware belum diverifikasi |

## 4. Mengubah config

Semua angka hardware ada di `/etc/fertimata/fertimata.yaml`. Prosedur yang sama berlaku untuk register,
alamat, ambang arus, interval polling, jadwal patroli, dan seterusnya.

```bash
sudo cp /etc/fertimata/fertimata.yaml /etc/fertimata/fertimata.yaml.$(date +%Y%m%d-%H%M)   # cadangan
sudoedit /etc/fertimata/fertimata.yaml
sudo -u fertimata /opt/fertimata-edge/venv/bin/python -m fertimata_edge \
    --config /etc/fertimata/fertimata.yaml --check-config      # harus: "Config valid: …"
sudo systemctl restart fertimata-edge
journalctl -u fertimata-edge -f                                # tunggu "agent siap"
```

- `--check-config` dijalankan sebagai user `fertimata` supaya sekaligus memastikan agent boleh membaca
  berkasnya. Config yang ditolak menyebut kunci yang salah, mis. `devices.soil.registers.ph.fc`.
- Agent hanya membaca config saat start, jadi perubahan baru berlaku setelah `restart`.
- Selalu kutip `"ON"`/`"OFF"` di YAML. Alamat boleh desimal (`30`) atau heksadesimal (`0x1E`).
- Jangan menyimpan config produksi di repo; repo hanya punya contoh `config/fertimata.example.yaml`.
  Kalau contoh di repo berubah (versi baru), `install.sh` memberi peringatan bila susunan bagian
  config berbeda. Bandingkan dengan `diff` dan salin bagian baru secara manual.

## 5. Mengganti register sensor/relay tanpa mengubah kode

Untuk sensor baru, sensor pengganti dengan peta register lain, atau pengisian P-H4 … P-H8.

1. **Hentikan agent** (baca peringatan di bagian 2): hanya satu program boleh memakai `/dev/rs485`.
   ```bash
   sudo systemctl stop fertimata-edge
   ```
2. **Cari register dengan `modbus_probe`** dari folder repo (perintah lengkap per perangkat:
   [03-persiapan.md "Cara cek register dengan modbus_probe"](03-persiapan.md#cara-cek-register-dengan-modbus_probe-p-h3--p-h8)):
   ```bash
   cd ~/FertiMata/EDGE_FertiMata
   /opt/fertimata-edge/venv/bin/python tools/modbus_probe.py --unit 1 --fc 3 --address 0 --count 7 --timeout 1.0
   ```
   Pakai `sudo -u fertimata …` bila user Anda tidak di grup `dialout`.
3. **Tulis hasilnya di config**, di bawah perangkatnya:
   ```yaml
   devices:
     soil:
       {address: 1, profile: soil_7in1, timeout_s: 1.0, label: sensor tanah, metric_prefix: soil_a,
        registers: {
          moisture:    {fc: 3, address: 0x0000, scale: 0.1},
          temperature: {fc: 3, address: 0x0001, type: s16, scale: 0.1},
          ph:          {fc: 3, address: 0x0003, scale: 0.1}}}
   ```
   Format satu register: `{fc: 3|4, address, type: u16|s16|u32|s32, word_order: lo_hi|hi_lo, scale}`;
   nilai = mentah × `scale`. `word_order` wajib untuk `u32`/`s32`. Hanya metrik yang ditulis yang dibaca;
   metrik yang tidak ada di peta tidak dikirim (bukan 0). Relay memakai blok `relay:` (lihat komentar di
   config contoh), bukan `registers`.
4. **Cek, start, lihat log** seperti bagian 4. Pastikan `perangkat menjawab device=…` dan angka di
   dashboard masuk akal.
5. **Catat** hasilnya di [komisioning.md](komisioning.md) (dan beri tahu pengembang supaya config contoh
   di repo ikut diperbarui).

Alamat Modbus baru untuk modul pengganti: set alamatnya **terpisah dari bus** (satu modul saja di kabel),
lalu ubah `devices.<nama>.address`. Dua modul beralamat sama di satu bus membuat keduanya tidak terbaca.

## 6. Kalibrasi EC/pH

Hentikan agent, lalu satu perintah per larutan (detail di docstring `tools/calibrate_ec_ph.py`):

```bash
sudo systemctl stop fertimata-edge
cd ~/FertiMata/EDGE_FertiMata
sudo -u fertimata /opt/fertimata-edge/venv/bin/python tools/calibrate_ec_ph.py \
    --config /etc/fertimata/fertimata.yaml --point ec1413            # lihat dulu tanpa menyimpan
sudo /opt/fertimata-edge/venv/bin/python tools/calibrate_ec_ph.py \
    --config /etc/fertimata/fertimata.yaml --point ec1413 --write    # simpan (butuh hak tulis /etc)
# ulangi: ec12880, ph686, ph401 dan/atau ph918
sudo systemctl start fertimata-edge
```

`--write` hanya mengganti nilai titik itu, menyimpan salinan `.bak`, dan menolak hasil yang janggal.

## 7. Membuka kunci dan masalah umum

**Output terkunci (S19, `OUTPUT_FAULT`).** Relay yang baca baliknya pernah tidak cocok dikunci: ON
ditolak, OFF tetap boleh. Periksa panel (relay macet, kabel A/B, modul mati), lalu
`sudo systemctl restart fertimata-edge` untuk membuka kunci.

**Trolley terkunci (S16, `TROLLEY_LIMIT_WIRE`).** HOME dan END terbaca aktif bersamaan atau terus-menerus
(kabel limit putus/lepas). Periksa kabel dan sakelar ujung rel, lalu restart agent. Kunci tidak dibuka
otomatis.

| Gejala | Kemungkinan sebab | Cek |
|---|---|---|
| `/dev/rs485` tidak ada, kode keluar 3 | Adaptor lepas; `brltty` merebut port | `ls -l /dev/rs485 /dev/ttyUSB*`; `sudo apt remove brltty`; cabut-pasang adaptor |
| Alert `BUS_DOWN` | Semua perangkat diam: kabel A/B, daya 24 V modul, adaptor | Lampu modul; `modbus_probe --scan 1-31` (agent dihentikan) |
| `MODBUS_TIMEOUT` satu perangkat | Alamat/baud berubah, kabel cabang putus, modul mati | `modbus_probe --unit <alamat>` |
| Semua perintah aktivasi ditolak `INTERLOCK_POWER` | PLN padam (benar) atau PZEM tidak terbaca | Log `sumber daya awal`, metrik `mains.voltage` |
| Pompa siram/pupuk ditolak `INTERLOCK_FLOAT` | Air drum rendah atau kabel pelampung putus | Metrik `tank.float_low_ok`, kabel IN1 relay 21 |
| Perangkat ONLINE tapi angka tidak ada di dashboard | Register belum diisi (sengaja tidak di-poll) atau bacaan di luar rentang | Bagian 5; log `EC_PH_INVALID` |
| Dashboard OFFLINE semua, agent jalan | Mosquitto/backend mati, atau topik/kredensial MQTT beda | Log `MQTT terputus`/`broker menolak`; `docker ps` |
| `DISK_LOW` / `DISK_FULL` | Foto patroli memenuhi SSD | `df -h /var/lib/fertimata`; pindahkan folder tanggal lama ke penyimpanan lain (jangan dihapus tanpa izin tim riset) |
| Jam di payload salah | NTP tidak sinkron (perintah bisa ditolak `EXPIRED`) | `timedatectl` |

## 8. Upgrade dan kembali ke versi lama

```bash
cd ~/FertiMata/EDGE_FertiMata
git pull
sudo bash deploy/install.sh                 # pasang + cek config + restart (S1)
sudo bash deploy/install.sh --no-restart    # pasang saja; restart nanti saat tidak ada pompa/trolley jalan
bash deploy/install.sh --dry-run            # lihat langkahnya tanpa mengubah apa pun
```

Kalau config ditolak versi baru, `install.sh` **tidak** me-restart agent; versi lama terus berjalan
sampai config diperbaiki. Kembali ke versi sebelumnya:

```bash
git log --oneline -5
git checkout <commit-lama>
sudo bash deploy/install.sh
git checkout main                           # setelah masalah versi baru diperbaiki
```

## 9. Jangan

- Jangan menjalankan dua program yang membuka `/dev/rs485` bersamaan (agent + `modbus_probe`).
- Jangan mematikan Jetson dari software atau memasang skrip yang melakukannya (S18). Jetson menyala
  sendiri saat daya masuk; di aki, layanan lain dihentikan oleh skrip terpisah (D9), bukan agent.
- Jangan menulis angka hardware ke kode. Semua lewat config.
- Jangan menghapus foto di `data/citra` tanpa persetujuan tim riset.
