# FertiMata Edge Agent (`EDGE_FertiMata`)

Program Python di **Jetson Orin Nano** yang menghubungkan backend FertiMata dengan hardware
greenhouse melon (FERTIMATA Revisi A). Tugasnya membaca sensor lewat RS485/Modbus, menjalankan relay
(pompa, trolley, pan kamera, dosing), menjaga keselamatan secara lokal, mengambil foto patroli, dan
berkomunikasi dengan backend lewat MQTT.

```
Backend ⇄ MQTT (127.0.0.1) ⇄ EDGE AGENT ⇄ RS485 / GPIO / kamera ⇄ hardware
```

**Status:** F1 selesai; F2 (MQTT) dan F3 (bus manager, sensor, polling) selesai kecuali yang menunggu cek hardware. Lihat [TASKS.md](TASKS.md).

## Mulai cepat (laptop, tanpa hardware)

```bash
python tasks.py setup     # .venv + paket (sebaiknya dengan Python 3.10, sama seperti Jetson)
python tasks.py check     # ruff + pytest
python tasks.py run       # agent dengan config/fertimata.dev.yaml (agent.hal: fake)
python -m fertimata_edge --config config/fertimata.example.yaml --check-config
```

Cek register perangkat Modbus (di Jetson, agent dihentikan dulu):

```bash
python tools/modbus_probe.py --port /dev/rs485 --unit 30 --fc 4 --address 0 --count 10
python tools/modbus_probe.py --fake --scan 1-31 --fc 1      # latihan dengan simulator
```

## Pasang di Jetson

```bash
cd ~/FertiMata/EDGE_FertiMata && git pull
sudo bash deploy/install.sh            # instal pertama / upgrade (aman diulang)
sudo nano /etc/fertimata/fertimata.yaml   # instal pertama: isi nilai lapangan dulu
sudo systemctl start fertimata-edge
journalctl -u fertimata-edge -f
```

Config produksi ada di `/etc/fertimata/fertimata.yaml` dan tidak pernah ditimpa skrip. Foto patroli ada di
`/var/lib/fertimata/data/citra`. Berkas udev, unit systemd, dan journald ada di `deploy/`.

## Dokumen

| Berkas | Isi |
|---|---|
| [docs/01-konsep-dan-cara-kerja.md](docs/01-konsep-dan-cara-kerja.md) | Konsep, modul, alur kerja, teknologi |
| [docs/02-kontrak-mqtt.md](docs/02-kontrak-mqtt.md) | Topik dan payload dengan backend |
| [docs/03-persiapan.md](docs/03-persiapan.md) | Yang harus disiapkan, dicek, dan diputuskan |
| [docs/04-aturan-keselamatan.md](docs/04-aturan-keselamatan.md) | Aturan S1–S20 yang tidak boleh dilanggar |
| [docs/05-prompt-siap-pakai.md](docs/05-prompt-siap-pakai.md) | Prompt untuk Claude per fase |
| [docs/06-perubahan-backend.md](docs/06-perubahan-backend.md) | Pekerjaan yang dibutuhkan di backend (BE-1…) |
| [docs/07-uji-end-to-end.md](docs/07-uji-end-to-end.md) | Langkah uji edge + Mosquitto + backend di laptop |
| [TASKS.md](TASKS.md) | Breakdown tugas dan log perkembangan |
| [CLAUDE.md](CLAUDE.md) | Aturan kerja untuk Claude/developer |
| [config/fertimata.example.yaml](config/fertimata.example.yaml) | Contoh konfigurasi lengkap |
| `docs/referensi/` | Salinan Arsitektur Rev A, Protokol Data, katalog backend |

## Repo terkait

- `BE-melonSmart`: backend Spring Boot (pemilik kontrak MQTT dan katalog hardware)
- `FE-melonSmart`: frontend Next.js
