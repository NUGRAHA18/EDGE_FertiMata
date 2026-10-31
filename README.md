# FertiMata — Edge Agent

Agent Python di Jetson Orin Nano: membaca sensor (RS485/Modbus), menjalankan relay (pompa, trolley, kamera, dosing),
menjaga keselamatan secara lokal, mengambil foto patroli, dan berkomunikasi dengan backend lewat MQTT.

```
Backend ⇄ MQTT (127.0.0.1) ⇄ edge agent ⇄ RS485 / GPIO / kamera ⇄ hardware
```

## Laptop (tanpa hardware)

```bash
python tasks.py setup   # .venv + paket (Python 3.10, sama dengan Jetson)
python tasks.py check   # ruff + pytest
python tasks.py run     # config/fertimata.dev.yaml (hal: fake)
```

Cek register Modbus: `python tools/modbus_probe.py --port /dev/rs485 --unit 30 --fc 4 --address 0 --count 10` (`--fake` untuk simulator).

## Jetson

```bash
sudo bash deploy/install.sh               # instal/upgrade, aman diulang
sudo nano /etc/fertimata/fertimata.yaml   # nilai lapangan (tidak pernah ditimpa skrip)
sudo systemctl start fertimata-edge && journalctl -u fertimata-edge -f
```

Contoh konfigurasi lengkap: `config/fertimata.example.yaml`. Foto patroli: `/var/lib/fertimata/data/citra`.

Repo terkait: `BE-melonSmart` (backend, pemilik kontrak MQTT), `FE-melonSmart` (frontend).
