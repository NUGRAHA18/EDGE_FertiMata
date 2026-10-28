# Arsitektur Sistem — FERTIMATA Revisi A

> Sistem fertigasi presisi terpandu citra daun untuk greenhouse melon di Banyuraden,
> Gamping, Sleman. Diperbarui **21 Oktober 2026**, menggantikan versi 17 September 2026.
>
> Hardware design: **Radipta Basri Wijaya** · Software: **Agung Nugraha**
>
> Gambar kerja: [FERTIMATA-Blueprint-Revisi-A.pdf](../FERTIMATA-Blueprint-Revisi-A.pdf) (panduan rakit) ·
> [FERTIMATA-Blueprint-Formal-Revisi-A.pdf](../FERTIMATA-Blueprint-Formal-Revisi-A.pdf) (gambar teknik) ·
> Belanja: [Shopee.md](../Shopee.md)

Dokumen ini adalah kontrak antara hardware dan software. Semua alamat, kanal, pin, dan
waktu di sini adalah nilai final Revisi A, kecuali yang ditandai **belum terverifikasi**.
Nilai yang belum terverifikasi jangan ditulis sebagai konstanta di kode; taruh di berkas
konfigurasi ([§11](#11-berkas-konfigurasi)) supaya bisa dikoreksi setelah barang dicek.

---

## 0. Yang berubah sejak versi 17 September

Bagian ini yang paling penting untuk menyesuaikan software yang sudah ada.

| Aspek | Versi 17 Sep | Revisi A (final) | Dampak ke software |
|---|---|---|---|
| PZEM-016 | Port USB sendiri `/dev/pzem`, alamat 1 | **Di bus RS485 bersama, alamat 30**, lewat `/dev/rs485` | Hanya satu port serial. Udev `/dev/pzem` dihapus |
| Deteksi PLN padam | PZEM tidak menjawab | PZEM tidak menjawab **sementara perangkat lain di bus tetap menjawab** | Bedakan padam vs bus putus ([§7.4](#74-perilaku-saat-pln-padam)) |
| Sensor tanah 7-in-1 | 2 unit, alamat 1 dan 2 | **1 unit, alamat 1**, diambil dari SimonKori | Alamat 2 kosong |
| Suhu & RH udara | SHT20 | **SHT40 XY-MD02**, alamat 3 | Register sama dengan keluarga XY-MD02 |
| Analog input | Analog In 8-ch | **ADC 7 kanal** (A1–A4 0–5 V, A5–A7 0–10 V), alamat 10. **A1 = EC, A2 = pH** | Peta register dicek ulang ([§4.5](#45-peta-register)) |
| Suhu larutan | — | **Modul Modbus DS18B20**, alamat 6 | Untuk kompensasi suhu EC dan pH |
| Relay panel (20) | 8-ch: R0 pompa, R1 LED, R2 RUN, R3 DIR | **4-ch**: R0 pompa, **R1 pan kamera**, R2 RUN trolley, R3 DIR trolley | LED dihapus. R1 sekarang pan |
| Relay tangki (21) | R3 solenoid, R4 pompa sampling | R3 cadangan, **R4 pompa aduk, R5 daya papan EC**, **IN1 = float LOW** | Solenoid pengisian sudah otomatis, tidak dikendalikan |
| Probe EC/pH | Flow cell + pompa sampling | **Rakit styrofoam** di permukaan drum, tanpa flow cell | Tidak ada pompa sampling |
| Iluminasi | LED 24 V di trolley | **Dihapus.** Foto saat lampu greenhouse menyala (malam) + color patch + ArUco | Jadwal capture malam |
| Pendingin panel | Fan + termostat | **Tanpa fan** (sudah diuji pengguna) | Pantau suhu Jetson, turunkan mode daya bila panas |
| Batas keras pompa | Timer relay mekanis | **Dihapus.** Batas durasi di software + mode nyala-berwaktu relay (bila didukung) + PZEM | Watchdog per output wajib ([§5.4](#54-batas-waktu-nyala)) |
| Suplai Jetson | Buck 15 V | **Buck 18 V** + fuse 4 A + TVS + elco | — |
| Catu DC | 3 rel | **4 buck: 18 V, 12 V, 5 V, 24 V** | — |
| Motor trolley | JGB37 24 V di 24 V | **JGB37-31ZY 24 V 89 rpm di 12 V** (±45 rpm, ±0,15 m/s) | Timeout perjalanan ikut kecepatan ini |
| Pan kamera | — | **Baru:** motor JGY-370 12 V, timur/barat lewat R1, berhenti sendiri di limit | Tunggu ±2 s setelah pan |
| Rel | 7,8 m sambungan, 12 stasiun | **6 m satu batang, 10 stasiun tiap 600 mm** | `stations: 10` |
| Input trolley | Reed + HOME + END ke GPIO | Sama, lewat **papan GPIO** (dibuat Koko) | Nomor pin dari papan GPIO |

---

## 1. Gambaran sistem

```
                         PLN 220 V (L + N, tanpa PE dari PLN)
                                     │
┌─ PANEL 400 × 500 × 200 IP55 ──────┼────────────────────────────────────────────────────┐
│                                    │                                                    │
│  RCBO 1P+N 6 A 30 mA ─(CT PZEM)─ terminal UKK5 T1–T4 ─┬─ SPD T2 ── earth bar ── arde    │
│                                                       ├─ PZEM-016 (alamat 30)          │
│                                                       ├─ SSR-40DA ──────────► pompa 220 V
│                                                       └─ UPS DC 24 V 150 W ─ MCB DC 10 A ─ 2× VRLA 12 V 9 Ah seri
│                                                                │ 27,2 V
│                                                         LVD XH-M609 (putus 21 V · sambung 26 V)
│                                                                │
│   ═══════════════════ BUS DC 27,2 V ═══════════════════════════╪═══════════════        │
│      │ F1 4 A          │                 │                     │                        │
│   buck 18 V        buck 12 V          buck 5 V              buck 24 V                   │
│   TVS + elco        │                  │                     │                          │
│      │           relay 20 · F3 ─►trolley/tangki  PZEM · ─►tangki   F2 ─►tangki · F4 ─►sensor · koil MY4N
│      ▼                                                                                  │
│  JETSON ORIN NANO 8GB ── USB ── Waveshare USB-RS485 (FTDI) [120 Ω] ═══ bus RS485 ════════╪══► tangki, greenhouse
│      │ LAN       │ GPIO 40-pin ── papan GPIO ◄── reed · HOME · END                      │
│      │           │ USB ◄── kabel USB 2.0 aktif 15 m ◄── kamera                          │
└──────┼───────────┼───────────────────────────────────────────────────────────────────────┘
       │           │            drag chain: USB · NYMHY 2×1,5 (motor) · Cat6 (sinyal + motor pan)
   internet        ▼
 (Cloudflare)   TROLLEY: IMX477 + Arducam B0278 (UVC) · motor JGB37 · motor pan JGY-370
                         reed MC-38 · limit HOME/END/pan timur/pan barat
```

Prinsip yang tidak berubah: **satu komputer** (Jetson), **satu bus lapangan**
(RS485/Modbus RTU), **nol PCB custom** selain papan GPIO, dan **Jetson tidak pernah ikut
bergerak** — hanya kamera yang ada di trolley.

---

## 2. Jetson Orin Nano (otak tunggal)

Menjalankan frontend, backend, database, dan inferensi sekaligus. JetPack 6.x
(Ubuntu 22.04). Boot dan database di **SSD NVMe**, bukan microSD.

| Batasan Jetson | Konsekuensi desain |
|---|---|
| Suhu kerja 0–35 °C, panel tanpa fan | Jalankan di **mode 15 W** (`nvpmodel`). Pantau suhu SoC; kalau mendekati batas, turunkan beban inferensi |
| DC jack 9–20 V, tanpa OVP, tanpa proteksi polaritas | Buck disetel **18,0 V**, fuse 4 A di input buck, TVS SMBJ18A ×2 + elco 1000 µF + 100 nF di output |
| Barrel 5,5/2,5 mm, maks 3,5 A | Jack male "LB", center-positive |
| Tanpa ADC | Semua sinyal analog lewat modul ADC Modbus (alamat 10) |
| Kernel tanpa RS485 (`TIOCSRS485` tidak ada) | Arah RS485 diurus hardware adaptor FTDI (auto-direction) |
| Tanpa Wake-on-LAN | Dev kit menyala sendiri saat tegangan DC masuk. **Jangan pernah shutdown dari software** ([§7.4](#74-perilaku-saat-pln-padam)) |

### Pemakaian port

| Port Jetson | Tersambung ke | Nama di Linux |
|---|---|---|
| USB-A | Waveshare USB-RS485 P03005 (FT232RL, `0403:6001`) | `/dev/rs485` (udev) |
| USB-A | Kabel USB 2.0 aktif 15 m → Arducam B0278 (UVC) | `/dev/cam-trolley` (udev, VID:PID **belum terverifikasi**) |
| RJ45 Gigabit | LAN eksisting (ujung kabel **belum terverifikasi**) | `eth0`/`enP8p1s0` |
| Header 40-pin | Kabel flat 40-pin → papan GPIO | `gpiochip` via libgpiod |
| M.2 Key M 2280 | SSD NVMe (boot + database) | `/dev/nvme0n1` |
| M.2 Key E | WiFi RTL8822CE bawaan | opsional, akses servis lokal |
| CSI ×2 | **Tidak dipakai** — kamera lewat USB | — |

```
# /etc/udev/rules.d/99-fertimata.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", SYMLINK+="rs485"
# Kamera: isi VID:PID dari `lsusb` setelah B0278 tersambung (belum terverifikasi)
SUBSYSTEM=="video4linux", ATTRS{idVendor}=="XXXX", ATTRS{idProduct}=="YYYY", ATTR{index}=="0", SYMLINK+="cam-trolley"
```

Software selalu membuka `/dev/rs485` dan `/dev/cam-trolley`, bukan `ttyUSB0` atau
`video0`. Kalau `/dev/ttyUSB*` muncul lalu hilang, hapus `brltty` (`sudo apt remove brltty`).

---

## 3. Ringkasan antarmuka untuk software

| Fungsi | Antarmuka | Alamat / kanal | Arah |
|---|---|---|---|
| Sensor tanah 7-in-1 | Modbus | alamat 1 | baca |
| Suhu & RH udara (SHT40 XY-MD02) | Modbus | alamat 3 | baca |
| Cahaya 0–200.000 lux | Modbus | alamat 4 | baca |
| Suhu larutan (DS18B20) | Modbus | alamat 6 | baca |
| EC larutan | Modbus ADC | alamat 10, A1 | baca |
| pH larutan | Modbus ADC | alamat 10, A2 | baca |
| Pompa distribusi 220 V | Modbus relay | alamat 20, R0 | tulis |
| Pan kamera timur/barat | Modbus relay | alamat 20, R1 | tulis |
| Trolley jalan (RUN) | Modbus relay | alamat 20, R2 | tulis |
| Trolley arah (DIR) | Modbus relay | alamat 20, R3 | tulis |
| Pompa peristaltik N / P / K | Modbus relay | alamat 21, R0 / R1 / R2 | tulis |
| Pompa aduk drum | Modbus relay | alamat 21, R4 | tulis |
| Daya papan EC | Modbus relay | alamat 21, R5 | tulis |
| Float LOW drum | Modbus relay (input) | alamat 21, IN1 | baca |
| Tegangan PLN, arus, daya, energi | Modbus | alamat 30 (PZEM-016) | baca |
| Reed stasiun | GPIO | papan GPIO | baca (tepi) |
| Limit HOME (selatan) / END (utara) | GPIO | papan GPIO | baca |
| Kamera | USB UVC | `/dev/cam-trolley` | baca |

Relay 21 R3, R6, R7 cadangan. Input relay 20 tidak dipakai.

---

## 4. Bus RS485 / Modbus RTU

### 4.1 Parameter

| Parameter | Nilai |
|---|---|
| Mode | Modbus RTU, satu master (Jetson), half-duplex |
| Format | **9600 baud, 8N1**, seragam untuk semua perangkat |
| Adaptor | Waveshare USB to RS485 P03005, FT232RL, arah otomatis, resistor 120 Ω onboard |
| Terminasi | 120 Ω di **dua ujung saja**: adaptor (panel) dan perangkat terjauh (resistor dari SCIT) |
| Topologi | **Daisy chain**, satu jalur tanpa cabang. Stub ke tiap perangkat sependek mungkin (≤ 0,5 m) |
| Library | `pymodbus` versi stabil terbaru. Lewat chip FTDI, jadi 8N1 aman (catatan NVIDIA soal 2 stop bit hanya untuk UART Tegra) |

### 4.2 Urutan fisik

```
Jetson ─USB─ adaptor [120 Ω] ─┬─ relay 20 (panel) ─ PZEM 30 (panel)
                              │
                     Cat6 bus │  ───►  KOTAK TANGKI: relay 21 · ADC 10 · DS18B20 6
                              │
                              └────────►  GREENHOUSE: SHT40 3 ─ cahaya 4 ─ tanah 1 [120 Ω]
```

Urutan di atas logis, satu garis tanpa percabangan. Urutan fisik akhir mengikuti lokasi
panel dan tangki (**lokasi panel belum terverifikasi**). Syaratnya tetap: satu jalur,
120 Ω hanya di dua ujung.

### 4.3 Kabel bus (Cat6 UTP Vascolink)

| Pasangan | Fungsi |
|---|---|
| Biru | **A+** |
| Putih-biru | **B−** |
| Coklat + putih-coklat | **GND referensi** (0 V) |
| Oranye + putih-oranye | **+24 V sensor greenhouse** (fuse F4 2 A di panel) |
| Hijau + putih-hijau | cadangan |

Nama A/B tidak seragam antar vendor. Kalau satu perangkat tidak menjawab, tukar A/B di
perangkat itu sebelum mencurigai hal lain. Jauhkan kabel bus dari kabel 220 V; kalau
harus bersilangan, silangkan tegak lurus.

### 4.4 Peta alamat

| Alamat | Perangkat | Lokasi | Suplai | Status |
|---|---|---|---|---|
| 1 | Sensor tanah 7-in-1 RS485 (dari SimonKori) | pot di bench | 24 V (Cat6 oranye) | baud bawaan **belum terverifikasi** — banyak unit 7-in-1 bawaan 4800, wajib diubah ke 9600 |
| 2 | — | — | — | kosong (dulu sensor tanah kedua) |
| 3 | SHT40 XY-MD02 | greenhouse, terlindung | 24 V | — |
| 4 | Sensor cahaya RS485 0–200.000 lux | greenhouse, menghadap atas | 24 V | — |
| 5 | — | — | — | cadangan |
| 6 | Modul Modbus DS18B20 + probe | kotak tangki | 12 V | — |
| 7–9 | — | — | — | cadangan sensor |
| 10 | Modul ADC Acquisition (A1–A4 0–5 V, A5–A7 0–10 V) | kotak tangki | 12 V | — |
| 20 | Relay Modbus 4-ch | panel | 12 V | — |
| 21 | Relay Modbus 8-ch | kotak tangki | 12 V | — |
| 30 | PZEM-016 (CT 100 A ring) | panel | 5 V (rel 5 V panel) | — |

Tempel tabel ini di pintu panel. Tempel label alamat di badan setiap modul.

### 4.5 Peta register

**PZEM-016** — terverifikasi dari manual Peacefair PZEM-014/016.

| Fungsi | Register | Isi | Satuan |
|---|---|---|---|
| 0x04 | 0x0000 | Tegangan | 0,1 V |
| 0x04 | 0x0001 (low) + 0x0002 (high) | Arus, 32 bit | 0,001 A |
| 0x04 | 0x0003 (low) + 0x0004 (high) | Daya aktif, 32 bit | 0,1 W |
| 0x04 | 0x0005 (low) + 0x0006 (high) | Energi, 32 bit | 1 Wh |
| 0x04 | 0x0007 | Frekuensi | 0,1 Hz |
| 0x04 | 0x0008 | Faktor daya | 0,01 |
| 0x04 | 0x0009 | Status alarm | 0xFFFF alarm |
| 0x03 / 0x06 | 0x0001 | Ambang alarm daya | 1 W |
| 0x03 / 0x06 | 0x0002 | Alamat Modbus (1–247) | — |
| 0x42 | — | Reset energi | — |

**SHT40 XY-MD02** — pola keluarga XY-MD02. Cocokkan dengan lembar manual dari penjual.

| Fungsi | Register | Isi |
|---|---|---|
| 0x04 | 0x0001 | Suhu, int16 bertanda, 0,1 °C |
| 0x04 | 0x0002 | Kelembapan, 0,1 %RH |
| 0x03 / 0x06 | 0x0101 | Alamat perangkat |
| 0x03 / 0x06 | 0x0102 | Baud (0 = 9600) |

**Belum terverifikasi — wajib dicek dari manual atau uji baca sebelum ditulis ke kode:**

| Perangkat | Yang diketahui | Yang harus dicek |
|---|---|---|
| Sensor tanah 7-in-1 (alamat 1) | Pola umum: FC 0x03, 0x0000 kelembapan · 0x0001 suhu · 0x0002 EC · 0x0003 pH · 0x0004 N · 0x0005 P · 0x0006 K | Urutan register, skala, baud bawaan unit SimonKori |
| Cahaya (alamat 4) | FC 0x03, nilai lux 32 bit | Alamat register dan urutan word |
| DS18B20 (alamat 6) | FC 0x03 | Register suhu dan skala |
| ADC (alamat 10) | Listing: mendukung FC 0x03 dan 0x06 | Register tiap kanal A1–A7, satuan (mV atau 0,01 V) |
| Relay 4-ch (20) dan 8-ch (21) | Dua profil umum: **A** — FC 0x05 coil 0x0000–0x0007 (0xFF00 ON, 0x0000 OFF), FC 0x01 baca coil, FC 0x02 baca input; **B** — FC 0x06 register = nomor kanal, 0x0100 ON, 0x0200 OFF | Profil mana yang dipakai, register alamat, register input IN1, dan **apakah ada mode nyala-berwaktu (flash/delay)** |

Buat driver per tipe perangkat (device profile) yang membaca peta register dari
`fertimata.yaml`, sehingga koreksi register tidak perlu mengubah kode.

### 4.6 Pola polling

| Kelompok | Interval usulan | Catatan |
|---|---|---|
| PZEM 30 | 2–5 s | Deteksi PLN padam dan arus pompa |
| Relay 20/21 (baca balik status + IN1) | 5 s, dan setelah setiap tulis | Konfirmasi perintah benar-benar dijalankan |
| SHT40 3, cahaya 4 | 60 s | — |
| Tanah 1 | 5–10 menit | Respons sensor tanah lambat, timeout lebih panjang |
| DS18B20 6, EC/pH (ADC 10) | 10 menit, dan terus-menerus selama dosing | pH dibaca dengan urutan R5 ([§6.3](#63-analog-ec-dan-ph)) |

Aturan bus:

1. **Satu proses saja yang memegang `/dev/rs485`** (bus manager dengan antrean
   perintah). Proses lain mengirim permintaan ke bus manager, tidak membuka port sendiri.
2. Jeda antar transaksi ≥ 10 ms. Timeout jawaban 300 ms (sensor tanah 1000 ms).
   Ulangi maksimal 2 kali, lalu tandai perangkat **tidak menjawab**.
3. **Selama trolley bergerak, polling lain ditunda.** Perintah STOP ke relay 20 harus
   langsung keluar tanpa antre di belakang pembacaan sensor
   ([§9.2](#92-kendali-motor-trolley)).

### 4.7 Set alamat

Semua modul keluar pabrik dengan alamat 1 (sebagian sensor juga 9600, sebagian 4800).
Set **satu per satu, terpisah dari bus**, dengan adaptor yang sama: sambungkan satu
perangkat saja, ubah alamat (dan baud bila perlu), cabut, tempel label, lanjut ke
perangkat berikutnya. Baru setelah semuanya ber-alamat unik, gabungkan ke bus.

### 4.8 Diagnosa cepat

| Gejala | Kemungkinan |
|---|---|
| Tidak ada perangkat yang menjawab | Adaptor tidak terbaca (`/dev/rs485` hilang), A/B tertukar di adaptor, kabel bus putus dekat panel |
| Semua perangkat di tangki/greenhouse diam, panel menjawab | Kabel Cat6 bus putus atau sambungan di kotak tangki lepas |
| Hanya PZEM 30 diam, yang lain menjawab | **PLN padam** (sisi ukur PZEM ditenagai PLN), atau PZEM rusak |
| Dua perangkat menjawab bersamaan, CRC error | Alamat bentrok |
| Error sporadis | Terminator ganda/hilang, stub terlalu panjang, kabel bus sejajar kabel 220 V |

---

## 5. Aktuator

Semua relay **normally OFF**. Setelah boot, sebelum logika apa pun berjalan, software
menulis OFF ke semua kanal (keadaan aman), lalu membaca balik.

### 5.1 Relay 20 — panel (4-ch, koil modul 12 V)

| Kanal | Kontak | Beban | ON berarti | Keadaan aman |
|---|---|---|---|---|
| R0 | COM = +24 V, NO → SSR 3(+) | SSR-40DA → pompa distribusi 220 V | Pompa menyala | OFF |
| R1 | COM = +24 V, NO → koil MY4N #2 | Pembalik polaritas motor pan 12 V | Kamera menghadap **barat** (OFF = **timur**) | OFF (timur) |
| R2 | COM = +12 V (F3), NO → common MY4N #1 | Daya motor trolley | Trolley **jalan** | OFF |
| R3 | COM = +24 V, NO → koil MY4N #1 | Arah motor trolley | Menuju **utara/END** (OFF = **selatan/HOME**) | OFF |

Arah sebenarnya bergantung pada polaritas kabel motor. Saat komisioning, kalau R3 OFF
ternyata bergerak ke utara, **tukar kabel motor di terminal**, bukan logika software.

### 5.2 Relay 21 — kotak tangki (8-ch, koil modul 12 V)

| Kanal | Beban | Catatan |
|---|---|---|
| R0 | Pompa peristaltik **N** (Calnit) 24 V | dioda 1N4007 terbalik di pompa |
| R1 | Pompa peristaltik **P** (MKP) 24 V | idem |
| R2 | Pompa peristaltik **K** (SOP) 24 V | idem |
| R3 | — | cadangan (solenoid pengisian sudah otomatis, tidak dikendalikan) |
| R4 | Pompa aduk BLDC 24 V 22 W | menyala selama dosing + waktu aduk |
| R5 | Daya 5 V papan EC (DFR0300) | **OFF saat membaca pH** |
| R6, R7 | — | cadangan (mis. pompa pH-down kelak) |
| IN1 | Float LOW drum | **Tertutup = air cukup.** Terbuka = air rendah **atau kabel putus** |

### 5.3 Aturan switching

1. **DIR dulu, baru RUN.** Ubah R3 hanya saat R2 OFF. Urutan jalan: R2 OFF → tulis R3 →
   tunggu ≥ 100 ms → R2 ON. Urutan berhenti: R2 OFF → tunggu ≥ 300 ms sebelum
   mengubah R3.
2. **Pan hanya saat trolley diam.** Ubah R1, lalu tunggu **±2 s** sebelum memotret.
   Motor pan berhenti sendiri di limit timur/barat (rangkaian dioda), jadi tidak ada
   kanal "RUN" untuk pan.
3. **Dosing dan perjalanan trolley tidak boleh bersamaan** (anggaran arus UPS 4 A).
4. **Satu pompa peristaltik menyala dalam satu waktu** (N → P → K berurutan).
5. Pompa distribusi (R0) **tidak menyala** selama dosing/aduk, dan **tidak menyala** kalau
   IN1 terbuka (float LOW).

### 5.4 Batas waktu nyala

Tidak ada timer mekanis. Setiap output wajib punya batas durasi di software, dan bus
manager mematikannya sendiri kalau perintah OFF tidak pernah datang (misalnya service
lain crash). Kalau modul relay mendukung mode nyala-berwaktu, pakai itu juga sebagai
lapisan kedua (**belum terverifikasi**).

| Output | Batas usulan per perintah | Catatan |
|---|---|---|
| R0 pompa distribusi | 15 menit | sesuaikan jadwal irigasi |
| R2 trolley RUN | 60 s per segmen | perjalanan penuh ±40 s |
| R0–R2 relay 21 peristaltik | 120 s | dosis per iterasi biasanya jauh lebih pendek |
| R4 pompa aduk | 10 menit | — |

Tambahan: hardware watchdog Jetson aktif, semua service sebagai `systemd` dengan
`Restart=always`.

---

## 6. Input

### 6.1 GPIO — reed dan limit trolley

Rangkaian di papan GPIO, per kanal:

```
3,3 V (pin 1) ── 10 kΩ ──┬── 1 kΩ ──┬── GPIO Jetson
                          │          │
                    kontak ke 0 V   100 nF ke 0 V
```

| Sinyal | Kontak | GPIO normal | GPIO aktif | Debounce software |
|---|---|---|---|---|
| Reed stasiun (MC-38) | menutup saat magnet dekat | HIGH | **LOW** = di atas magnet | 5 ms |
| HOME (selatan) | KW12, COM–NC | LOW | **HIGH** = tertekan **atau kabel putus** | 20 ms |
| END (utara) | KW12, COM–NC | LOW | **HIGH** = tertekan **atau kabel putus** | 20 ms |

Limit dipasang di kontak NC, sehingga kabel putus terbaca sama dengan limit tertekan dan
trolley tetap berhenti. Limit pan timur/barat **tidak** masuk GPIO; keduanya memutus
motor pan langsung lewat rangkaian dioda.

Nomor pin header 40-pin ditentukan oleh papan GPIO (**belum terverifikasi**) dan ditulis
di `fertimata.yaml`. Akses lewat libgpiod (`gpiod`), dengan edge detection.

### 6.2 Float LOW

Dibaca dari IN1 relay 21 (lihat profil register di [§4.5](#45-peta-register)). Terbuka =
air di bawah batas **atau** kabel putus — keduanya diperlakukan sebagai air rendah:
pompa distribusi dan dosing diblokir, alarm dikirim.

### 6.3 Analog: EC dan pH

| Kanal ADC | Sumber | Rentang keluaran | Catatan |
|---|---|---|---|
| A1 (0–5 V) | DFRobot DFR0300 EC V2, K = 1 | 0–3,4 V | rentang ukur 0–20 mS/cm; target melon 0–4 mS/cm |
| A2 (0–5 V) | PH-4502C | 0–5 V | offset dan slope dari kalibrasi |
| A3–A7 | — | — | cadangan |

**Urutan baca** (probe EC dan pH berada di larutan yang sama, eksitasi EC mengganggu pH):

```
1. R5 OFF (papan EC mati)  → tunggu 5 s  → baca A2 beberapa kali, ambil median → pH
2. R5 ON                   → tunggu 5 s  → baca A1 beberapa kali, ambil median → EC
3. Baca DS18B20 (alamat 6) → kompensasi suhu
```

Waktu tunggu di atas nilai awal, sesuaikan saat komisioning.

Kompensasi suhu EC ke 25 °C: `EC25 = EC_ukur / (1 + 0,02 × (T − 25))`.
Konversi tegangan → EC mengikuti pustaka DFRobot DFR0300 (kalibrasi dua titik, pustaka
mengenali larutan 1413 µS/cm dan 12,88 mS/cm). Konversi pH linear dari kalibrasi
dua/tiga titik (4,01 · 6,86 · 9,18), dengan koreksi suhu.

Tidak ada pompa koreksi pH. Kalau pH di luar rentang, software memberi peringatan dan
saran koreksi manual.

---

## 7. Daya

### 7.1 Sisi AC

```
PLN L ─ gland ─ RCBO 1 ──┐                         ┌─ T2 bawah → PZEM L / N
PLN N ─ gland ─ RCBO N ──┤ RCBO 1P+N 6 A 30 mA     ├─ T3 bawah → UPS DC L / N
                         └─ RCBO 2(L) ─(CT PZEM)─ T1 ─ jumper lantai L + N ─┼─ T4 bawah → SSR 1 (L) · pompa N
                                                                            └─ T4 atas  → SPD L / N
SPD T2 1P+N 275 V ─ PE ≤ 50 cm ─ earth bar
earth bar: 1 batang arde · 2 PE SPD · 3 body · 4 pintu · 5 PE UPS · 6 cadangan · 7 PE pompa
SSR 2 → L pompa (gland) · N pompa dari T4
```

Terminal UKK5 dua tingkat T1–T4 (+1 cadangan): lantai atas = L, lantai bawah = N.
PLN hanya memberi L + N; PE dibuat sendiri dari **batang arde copper-bonded 5/8" × 1,5 m**
dengan klem cincin, kabel **NYAF 4 mm²** di flexible conduit (sistem **TT**). SPD dipasang
paralel setelah RCBO, sehingga kalau varistornya rusak short, RCBO yang memutus.

Kabel PE memakai NYAF 4 kuning dengan selongsong hijau-kuning dan label "PE" di kedua
ujung. Pompa sebaiknya diberi PE ke bodinya (lubang 7) lewat kabel 3 inti atau kabel PE
terpisah; terminal arde di bodi pompa **belum terverifikasi**.

**0 V DC tidak dibonding ke PE** (SELV mengambang). Ini sengaja: probe EC/pH di larutan
dan sensor tanah di media basah akan membentuk ground loop kalau 0 V disambung ke arde.

### 7.2 Sisi DC

```
UPS DC 24 V 150 W (−V/+V = 27,2 V, trimpot +V ADJ)
   │  B+/B− ── MCB DC 2P 10 A ── 2× VRLA 12 V 9 Ah seri (direbahkan di lantai panel)
   ▼
LVD XH-M609 (putus 21 V, sambung 26 V) ── BUS 27,2 V (terminal UK2.5B +BUS / 0 V)
   ├─ F1 4 A ── buck 18,0 V ── TVS SMBJ18A ×2 + elco 1000 µF ≥ 25 V + 100 nF ── jack LB ── Jetson
   ├─ buck 12,0 V ─┬─ relay 20 (panel)
   │               └─ F3 4 A ─┬─ R2 → MY4N #1 → motor trolley (NYMHY 2×1,5, drag chain)
   │                          ├─ MY4N #2 → motor pan (Cat6 trolley)
   │                          └─ kotak tangki 12 V: relay 21 · ADC · DS18B20 (NYMHY 2×1,5 #2)
   ├─ buck 5,0 V ──┬─ PZEM-016 (sisi komunikasi)
   │               └─ kotak tangki 5 V: papan EC (lewat R5) · papan pH (NYMHY 2×0,75)
   └─ buck 24,0 V ─┬─ F2 4 A ── kotak tangki 24 V: peristaltik N/P/K · pompa aduk (NYMHY 2×1,5 #1)
                   ├─ F4 2 A ── sensor greenhouse via Cat6 oranye
                   └─ R0/R1/R3 COM → input SSR, koil MY4N #1 dan #2
```

UPS memberi **4 A untuk beban + 1,5 A untuk charge** pada 27,2–27,6 V. Trimpot disetel
**27,2 V**. Buck XL4016E1 bersifat non-isolasi; semua 0 V tersambung.

### 7.3 Anggaran daya

| Kondisi | Arus di bus 27 V (perkiraan) |
|---|---|
| Diam: Jetson mode 15 W + NVMe + kamera + modul + sensor | ±1,0–1,3 A |
| Dosing: + pompa aduk 0,9 A + satu peristaltik | ±2,3–2,6 A |
| Trolley jalan: + motor 12 V (start sesaat lebih tinggi) | ±1,4–1,8 A |
| Dosing + trolley bersamaan | dilarang ([§5.3](#53-aturan-switching)) |

Cadangan aki 2× 9 Ah pada beban diam: **±3,5–4 jam** sampai LVD memutus di 21 V.

### 7.4 Perilaku saat PLN padam

**Deteksi:** PLN dianggap padam kalau tegangan PZEM < 180 V, **atau** PZEM 30 tidak
menjawab sementara perangkat lain di bus (relay 20) tetap menjawab. Kalau relay 20 juga
diam, itu gangguan bus, bukan padam.

| Kejadian | Respons software |
|---|---|
| PLN padam | Aki mengambil alih tanpa jeda; bus turun ke ±25 V. **Hentikan dosing dan trolley**, matikan pompa distribusi, turunkan mode daya Jetson |
| ±2 jam di aki | Hentikan database dan semua layanan yang menulis ke disk. Jetson tetap menyala |
| Aki 21 V | LVD memutus bus. Aman, tidak ada yang sedang menulis |
| PLN kembali, Jetson masih hidup | Jalankan ulang layanan. Dosing baru boleh jalan lagi setelah tegangan bus normal |
| PLN kembali, bus sudah terputus | UPS 27,2 V → LVD menyambung di 26 V → Jetson menyala sendiri |

**Jetson tidak pernah di-shutdown oleh software.** Kalau Jetson shutdown saat di aki,
lalu PLN kembali sebelum LVD memutus, tegangan di jack tidak pernah hilang dan Jetson
tidak akan menyala sendiri.

Debit pompa peristaltik ikut tegangan; saat di aki, buck 24 V hanya bisa memberi
±22–23 V. Itu sebabnya dosing dilarang saat PLN padam.

### 7.5 Pemantauan pompa lewat PZEM

CT PZEM terpasang di jalur L utama, jadi membaca **arus total panel**, bukan arus pompa
saja.

| Bacaan | Arti |
|---|---|
| Arus saat R0 OFF | arus dasar panel (±0,3 A, **belum terverifikasi**) — simpan sebagai baseline |
| Kenaikan arus saat R0 ON | arus pompa (nominal 1,55 A) |
| Arus di atas baseline saat R0 OFF | **SSR rusak short** → alarm keras, minta RCBO dimatikan |
| Kenaikan arus jauh di bawah nominal saat R0 ON | pompa jalan kering atau tersumbat → matikan R0, alarm |
| kWh | catatan pemakaian energi |

---

## 8. Fertigasi dan irigasi

### 8.1 Perangkat

| Bagian | Isi |
|---|---|
| Tangki nutrisi | Drum HDPE 200 L, ditanam rebah. Diisi solenoid otomatis eksisting (tidak dikendalikan software). Volume kerja **belum terverifikasi** — ukur saat komisioning |
| Rakit probe | Styrofoam 3 cm mengapung: probe EC, probe pH, probe DS18B20 |
| Float LOW | Float vertikal di pipa PVC ¾" + dop, ke IN1 relay 21 |
| Stok pupuk | 3 galon 15 L dicat hitam: **N = Calnit** (N + Ca), **P = MKP** (P + K), **K = SOP** (K + S). Calnit tidak pernah dicampur pekat dengan MKP/SOP |
| Pompa dosing | 3 Kamoer 24 V di kotak pompa 20 × 12 × 8, selang silikon 3 × 5 mm, ujung selang di atas permukaan drum |
| Pengaduk | Pompa celup BLDC 24 V 22 W di dalam drum |
| Kotak tangki | MG 300 × 220 × 120 IP56: relay 21, ADC 10, papan EC, papan pH, modul DS18B20 |
| Irigasi | Pompa Panasonic GP-129JXK-P2 menyedot dari drum → filter → tee → PE 16 mm → drip tape/emitter/stik |

Kabel probe EC hanya 1 m, jadi kotak tangki dipasang dekat bibir drum.

### 8.2 Loop dosing

Output keputusan (fuzzy/AI) adalah **target EC dan rasio N:P:K per fase**, bukan kg/ha.

```
1.  Prasyarat: PLN normal, trolley diam, IN1 tertutup, pompa distribusi OFF.
2.  R4 ON (aduk). Baca pH (R5 OFF), lalu EC (R5 ON) dan suhu.
3.  EC25 = EC_ukur / (1 + 0,02 × (T − 25)).    ΔEC = EC_target(fase) − EC25
4.  ΔEC ≤ 0,05 mS/cm → selesai.
5.  Bagi ΔEC menurut rasio fase:
        vegetatif         N:P:K = 5,4 : 1 : 6,3
        buah muda         4,1 : 1 : 7,9
        pembesaran buah   5,4 : 1 : 4,7
6.  Dosis inkremental konservatif: hanya 30–50% ΔEC per iterasi.
        t_pompa = (V × Δc) / (debit_pompa × konsentrasi_stok)
7.  Jalankan N → P → K berurutan, satu pompa dalam satu waktu.
8.  Aduk 60–120 s, TUNGGU EC stabil (dEC/dt < ambang) sebelum baca ulang.
9.  Ulangi dari langkah 2. Maksimal 5–8 iterasi.
    Tidak konvergen → alarm: stok habis, pompa tersumbat, atau probe kotor.
10. R4 OFF. Catat setiap iterasi (dosis, EC, pH, suhu, durasi).
```

Nilai target EC per fase, konsentrasi stok, dan volume drum ada di konfigurasi, bukan
di kode. Larutan pekat tidak bisa ditarik kembali, jadi kesalahan arah selalu ke dosis
kurang.

### 8.3 Kalibrasi

| Apa | Cara | Jadwal |
|---|---|---|
| EC | Dua titik dengan larutan **1413 µS/cm** dan **12,88 mS/cm** (ikut di kit DFR0300) | awal, lalu bulanan |
| pH | Buffer **4,01 · 6,86 · 9,18** (bubuk, dilarutkan aquades) | awal, lalu 2–4 minggu |
| Debit pompa peristaltik | Gelas ukur + stopwatch, 3 kali ulang per pompa, pada tegangan PLN normal | awal, lalu bulanan |
| Baseline arus PZEM | Catat arus saat R0 OFF | awal, dan setelah ada perubahan beban |

Software sebaiknya menyediakan halaman kalibrasi yang menyimpan hasilnya ke konfigurasi.

---

## 9. Trolley dan kamera

### 9.1 Lintasan

| Hal | Nilai |
|---|---|
| Rel | V-slot 2040, 6 m satu batang, arah **utara–selatan**, sisi 40 mm menghadap bawah |
| HOME | ujung **selatan** (limit HOME) |
| END | ujung **utara** (limit END) |
| Stasiun | **10**, magnet tiap **600 mm**, S1 di 300 mm dari ujung selatan rel, S10 di 5700 mm |
| Langkah trolley | ±5.775 mm |
| Kecepatan | ±0,15 m/s (motor 24 V 89 rpm di 12 V, roda Ø±65 mm) |
| Tinggi | dasar rel ±2,96 m dari tanah; kamera di bawahnya |

Slip roda hanya memperlambat, **tidak menggeser posisi berhenti**, karena posisi
ditentukan magnet.

### 9.2 Kendali motor trolley

```
jalan(arah):   R2 OFF → R3 = arah → tunggu 100 ms → R2 ON
berhenti():    R2 OFF                (prioritas tertinggi di antrean bus)
```

Selama trolley bergerak, bus manager tidak menjalankan polling lain, supaya perintah
berhenti keluar dalam hitungan milidetik setelah tepi reed terdeteksi.

### 9.3 Homing dan menuju stasiun

```
homing():
    kalau HOME aktif → selesai, posisi = 0
    jalan(selatan); tunggu HOME aktif (timeout 60 s) → berhenti()
    posisi = 0

ke_stasiun(n):            # n = 1..10, selalu didekati dari arah selatan
    kalau posisi > n atau posisi tidak diketahui → homing()
    jalan(utara)
    setiap tepi turun reed: posisi += 1; kalau posisi == n → berhenti()
    batal & berhenti kalau: END aktif, timeout segmen 10 s tanpa pulsa reed,
                            atau total > 60 s
```

Stasiun selalu didekati dari arah yang sama (dari selatan) supaya titik berhentinya
berulang. Setelah berhenti, tunggu **2 s** sampai getaran hilang sebelum memotret.

### 9.4 Patroli kamera

```
patroli():                 # malam hari, lampu greenhouse menyala
    homing()
    untuk n = 1..10:
        ke_stasiun(n); tunggu 2 s
        pan timur (R1 OFF); tunggu 2 s; ambil foto
        pan barat (R1 ON);  tunggu 2 s; ambil foto
    R1 OFF; homing()
```

Satu patroli ±3 menit. Urutan pan bisa dibalik di stasiun genap (barat dulu) untuk
menghemat satu gerakan pan per stasiun.

### 9.5 Kamera

| Hal | Nilai |
|---|---|
| Sensor | IMX477 + lensa manual, fokus dikunci di jarak kerja |
| Antarmuka | Arducam B0278 CSI→USB 2.0 **UVC**, lewat kabel USB 2.0 aktif 15 m |
| Format & resolusi | cek `v4l2-ctl --list-formats-ext` (**belum terverifikasi**) |
| Pengaturan | exposure manual, white balance manual, tanpa autofocus; simpan di konfigurasi |
| Referensi warna | color patch + ArUco ikut terpotret di setiap stasiun |
| Kemiringan | 30–45° ke bawah lewat ball head; pan timur ↔ barat ±180° lewat motor |
| Waktu ambil | saat lampu greenhouse menyala (malam), supaya warna konsisten |

Nama berkas usulan: `YYYYMMDD-HHMM_S{n:02d}_{timur|barat}.jpg`, plus metadata
(stasiun, arah pan, eksposur, suhu, kelembapan) di database.

### 9.6 Kesalahan dan pemulihan

| Kejadian | Respons |
|---|---|
| END aktif saat menuju stasiun | berhenti, homing, tandai patroli gagal |
| Tidak ada pulsa reed dalam 10 s | berhenti, alarm (magnet lepas, reed rusak, roda selip parah) |
| HOME/END aktif terus padahal tidak di ujung | kabel limit putus → kunci trolley, alarm |
| Kamera tidak terbaca | lewati foto, catat, lanjut stasiun berikutnya |
| PLN padam di tengah patroli | berhenti di tempat, batalkan patroli |

---

## 10. Jaringan

LAN eksisting **langsung ke port Gigabit Jetson**, tanpa router dan tanpa switch.
Akses dari luar lewat **Cloudflare Tunnel** (outbound saja, tanpa port forwarding,
tetap jalan di belakang CGNAT) dan **Cloudflare Access** (One-time PIN ke email terdaftar).

```bash
sudo cloudflared --config /home/<USER>/.cloudflared/config.yml service install
sudo systemctl start cloudflared
```

Selalu beri `--config` eksplisit: dengan `sudo`, `$HOME` menunjuk ke `/root`.
Dashboard mengendalikan pompa — **jangan pernah diekspos tanpa Access**. Jam sistem dari
NTP; tanpa internet, jam bisa bergeser setelah mati total (baterai RTC dev kit
**belum terverifikasi**).

---

## 11. Berkas konfigurasi

Seluruh peta I/O di satu berkas. Contoh `fertimata.yaml`:

```yaml
bus:
  port: /dev/rs485
  baudrate: 9600
  parity: N
  stopbits: 1
  timeout_s: 0.3
  retries: 2
  gap_ms: 10

devices:
  soil:     {address: 1,  profile: soil_7in1,  timeout_s: 1.0}   # register belum terverifikasi
  air:      {address: 3,  profile: xy_md02}
  light:    {address: 4,  profile: lux_rs485}                    # register belum terverifikasi
  water_t:  {address: 6,  profile: ds18b20_mb}                   # register belum terverifikasi
  adc:      {address: 10, profile: adc_7ch}                      # register belum terverifikasi
  relay_p:  {address: 20, profile: relay_mb, channels: 4}        # profil A/B belum terverifikasi
  relay_t:  {address: 21, profile: relay_mb, channels: 8}
  pzem:     {address: 30, profile: pzem016}

outputs:
  pump_dist:   {device: relay_p, ch: 0, max_on_s: 900}
  pan_west:    {device: relay_p, ch: 1}            # OFF = timur, ON = barat
  trolley_run: {device: relay_p, ch: 2, max_on_s: 60}
  trolley_dir: {device: relay_p, ch: 3}            # OFF = selatan/HOME, ON = utara/END
  dose_n:      {device: relay_t, ch: 0, max_on_s: 120}
  dose_p:      {device: relay_t, ch: 1, max_on_s: 120}
  dose_k:      {device: relay_t, ch: 2, max_on_s: 120}
  stirrer:     {device: relay_t, ch: 4, max_on_s: 600}
  ec_power:    {device: relay_t, ch: 5}

inputs:
  float_low_ok: {device: relay_t, input: 1}        # tertutup = air cukup
  reed:  {gpio_line: null, active: low,  debounce_ms: 5}    # pin dari papan GPIO
  home:  {gpio_line: null, active: high, debounce_ms: 20}
  end:   {gpio_line: null, active: high, debounce_ms: 20}

analog:
  ec: {device: adc, ch: 1, k: 1.0}
  ph: {device: adc, ch: 2}

trolley:
  stations: 10
  station_pitch_mm: 600
  first_station_mm: 300
  speed_m_s: 0.15
  settle_s: 2.0
  pan_settle_s: 2.0
  segment_timeout_s: 10
  travel_timeout_s: 60

camera:
  device: /dev/cam-trolley
  exposure: manual
  white_balance: manual

power:
  outage_voltage_v: 180
  pump_baseline_a: 0.3          # ukur saat komisioning
  pump_nominal_a: 1.55

dosing:
  drum_volume_l: null           # ukur saat komisioning
  stir_s: 90
  max_iterations: 6
  fraction_per_iteration: 0.4
  ec_tolerance_ms_cm: 0.05
  ratio:
    vegetatif:       [5.4, 1, 6.3]
    buah_muda:       [4.1, 1, 7.9]
    pembesaran_buah: [5.4, 1, 4.7]
  pump_flow_ml_s: {n: null, p: null, k: null}     # hasil kalibrasi
  stock_g_l:      {n: null, p: null, k: null}     # ditentukan saat meracik stok
```

---

## 12. Aturan keselamatan untuk software

Aturan ini tidak boleh dilanggar oleh fitur apa pun:

1. Setelah boot, semua relay ditulis OFF lalu dibaca balik sebelum logika lain jalan.
2. Setiap output punya batas durasi; bus manager mematikannya sendiri.
3. Dosing dan perjalanan trolley tidak pernah bersamaan.
4. Dosing dan pompa distribusi dilarang saat PLN padam, saat IN1 terbuka, atau saat
   bacaan EC/pH tidak valid.
5. Arus pompa saat R0 OFF di atas baseline → alarm keras (SSR short).
6. HOME/END aktif → motor trolley berhenti; tidak ada perintah jalan ke arah limit yang
   aktif.
7. R3 tidak pernah diubah saat R2 ON.
8. Jetson tidak pernah di-shutdown oleh software.
9. Dashboard hanya lewat Cloudflare Access.

---

## 13. Pipeline citra

```
IMX477 via B0278 (fokus terkunci, exposure & WB manual, lampu greenhouse menyala)
   → gerbang kualitas: variance of Laplacian, tolak frame blur
   → normalisasi warna terhadap color patch + ArUco
   → STAGE 1 deteksi daun (model tahun lalu) → crop ROI per daun
   → STAGE 2 klasifikasi defisiensi, multi-label tiga sigmoid independen
   → Leaf_N_stress, Leaf_P_stress, Leaf_K_stress ∈ [0, 1]
   → keputusan dosing ← sensor tanah + EC/pH larutan + fase pertumbuhan (HST)
```

Dua foto per stasiun (timur dan barat), 10 stasiun, satu patroli per malam.

---

## 14. Perbedaan terhadap SimonKori

| Aspek | SimonKori | FERTIMATA Revisi A |
|---|---|---|
| Pemicu irigasi | Timer digital 7 hari | Sensor + keputusan software, dengan interlock |
| Kanal dosing | 2 (A/B pra-campur) | 3 terpisah (N, P, K) |
| Dosing | Volumetrik terjadwal | Loop tertutup berbasis EC |
| Fenotipe tanaman | Tidak ada | Kamera bergerak, 10 stasiun, pan timur/barat |
| Proteksi arus bocor | Tidak ada | RCBO 30 mA |
| Surja & pembumian | Tidak ada | SPD Tipe 2 + batang arde (TT) |
| Enclosure | Triplek terbuka | Panel baja tertutup IP55 |
| Cadangan daya | Tidak ada | UPS DC 24 V + 2× VRLA + LVD |
| Data | WordPress vendor | Lokal di Jetson + Cloudflare Tunnel |

---

## 15. Belum terverifikasi

| No. | Hal | Cara memastikan |
|---|---|---|
| 1 | Profil perintah relay 4-ch/8-ch (coil atau holding), register IN1, mode nyala-berwaktu | Manual penjual atau uji baca dengan `pymodbus` |
| 2 | Register ADC, DS18B20, cahaya, sensor tanah; baud bawaan sensor tanah | idem |
| 3 | VID:PID dan format/resolusi B0278 | `lsusb`, `v4l2-ctl --list-formats-ext` |
| 4 | Nomor pin GPIO reed/HOME/END | dari papan GPIO (Koko) |
| 5 | Lokasi panel (di dalam/luar greenhouse) dan ujung kabel LAN | cek lapangan |
| 6 | Volume kerja drum dan baseline arus PZEM | ukur saat komisioning |
| 7 | Diameter roda RC (target 60–70 mm) → kecepatan aktual | ukur saat barang datang |
| 8 | Jumlah pot per baris (39 atau 27–29) | hitung di lapangan; jumlah stasiun tetap 10 sampai ada keputusan baru |
| 9 | Terminal arde di bodi pompa | cek fisik pompa |
