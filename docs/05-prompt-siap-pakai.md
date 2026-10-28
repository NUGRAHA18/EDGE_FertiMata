# Prompt Siap Pakai untuk Claude di Repo Ini

Buka Claude Code di folder repo `EDGE_FertiMata`. `CLAUDE.md` otomatis terbaca, jadi prompt cukup singkat.
Jalankan satu prompt per sesi atau per fase. Setelah tiap fase selesai, periksa `TASKS.md` dan hasil test.

---

### 0. Orientasi (sekali di awal)

```
Baca CLAUDE.md, TASKS.md, dan semua berkas di docs/. Ringkas pemahamanmu tentang edge agent ini
dalam 10 poin, lalu sebutkan hal yang menurutmu kurang jelas atau bertentangan antar dokumen.
Jangan menulis kode dulu.
```

### 1. Kerangka proyek (F1)

```
Kerjakan fase F1 di TASKS.md. Buat struktur paket, pyproject.toml, model config pydantic yang
memvalidasi config/fertimata.example.yaml, HAL beserta implementasi fake, logging, entry point,
dan test. Buat juga config/fertimata.dev.yaml (hal: fake). Pastikan pytest lulus di Windows.
Perbarui TASKS.md setelah selesai.
```

### 2. Link MQTT (F2)

```
Kerjakan fase F2. Ikuti docs/02-kontrak-mqtt.md persis. Setelah unit test lulus, bantu aku
menjalankan uji end-to-end F2.7 dengan Mosquitto dan backend dev (tuliskan langkahnya). Perbarui
TASKS.md.
```

### 3. Alat verifikasi register (F3.1) — sebelum ke lapangan

```
Kerjakan F3.1: tools/modbus_probe.py untuk membaca/menulis register satu perangkat Modbus dari
command line (port, alamat, fungsi, register, jumlah). Tambahkan petunjuk pemakaian untuk mengecek
P-H3 sampai P-H8 di docs/03-persiapan.md.
```

### 4. Bus manager dan sensor (F3)

```
Kerjakan F3.2–F3.8. Profil yang registernya belum terverifikasi tetap dibuat, tapi peta registernya
hanya diambil dari config, dan beri tanda ⛔ di TASKS.md. Perbarui TASKS.md.
```

### 5. Output, perintah, keselamatan (F4)

```
Kerjakan F4. Setiap aturan di docs/04-aturan-keselamatan.md yang tersentuh fase ini harus punya
test bernama sesuai nomornya (mis. test_s12_dir_not_changed_while_running). Profil relay A dan B
keduanya dibuat, dipilih lewat config. Perbarui TASKS.md.
```

### 6. Daya (F5)

```
Kerjakan F5 dengan hardware palsu: simulasikan PLN padam saat pompa distribusi jalan, PZEM diam
tapi relay 20 menjawab, dan keduanya diam. Perbarui TASKS.md.
```

### 7. Fase berikutnya (F6–F11)

```
Lanjutkan tugas berikutnya di TASKS.md yang tidak terhambat. Kalau semua tugas tersisa terhambat
oleh P-H* atau D*, tampilkan daftar hambatannya dan apa yang perlu aku cek di lapangan.
```

### Saat hasil cek lapangan sudah ada

```
Hasil cek P-H4: relay 4-ch dan 8-ch memakai profil <A/B>, IN1 dibaca dengan FC <..> register <..>,
mode nyala-berwaktu <ada/tidak, register ..>. Masukkan ke config, buka hambatan terkait di
TASKS.md, dan lanjutkan tugas yang tadinya terhambat.
```

### Saat kontrak backend berubah

```
Backend berubah: <jelaskan / tempel diff payload>. Perbarui docs/02-kontrak-mqtt.md dulu, lalu
sesuaikan kode dan test. Catat di Log perkembangan TASKS.md.
```
