# Laporan Progress FertiMata

**Per 31 Oktober 2026** · mencakup Backend (BE), Frontend (FE), dan Edge Agent (EDGE)

---

## Ringkasan

**Perangkat lunaknya hampir lengkap dan sudah terpasang di Jetson. Yang belum adalah uji dengan
hardware terpasang.**

Ketiga bagian sudah dibangun, diuji bersama di laptop (perintah dari aplikasi sampai ke "relay palsu",
lalu jawabannya kembali ke aplikasi), dan semua pekerjaan sudah disimpan ke GitHub. Backend sudah
berjalan di Jetson. Edge agent juga sudah dipasang di Jetson, tetapi **sengaja belum dinyalakan**
sampai komisioning bersama tim hardware, supaya pompa tidak bergerak tanpa ada orang di greenhouse.

Pada 29 Oktober diputuskan aplikasi **difokuskan untuk kontrol oleh petani**: halaman riset
(Riset, Kinerja, pengamatan, panen) dihapus, dan **jadwal siram** sekarang diatur oleh backend.
Pemberian pupuk otomatis (dosing dan campur pupuk) dikerjakan oleh layanan **tim AI**.

| Bagian | Fungsinya | Status | Hambatan utama |
|---|---|---|---|
| **Backend** | "Otak pusat": menyimpan data, mengatur akun, memeriksa perintah, menjalankan jadwal siram | Semua fitur selesai dan sudah berjalan di Jetson | Uji siram nyata; alamat https untuk notifikasi push |
| **Frontend** | Aplikasi yang dibuka petani di HP | Semua halaman selesai, termasuk jadwal siram, panduan, kalibrasi, dan notifikasi. Desain ulang tampilan sedang dikerjakan | Uji langsung dengan petani |
| **Edge Agent** | Program di Jetson yang menghubungkan aplikasi dengan alat di greenhouse | Logika selesai, diuji dengan simulator, terpasang di Jetson (belum dinyalakan) | Komisioning bersama tim hardware |
| AI (tim AI) | Membaca foto daun dan mengatur pemberian pupuk | Dikerjakan tim AI; backend sudah menyiapkan data untuk mereka | Serah terima ke tim AI |

---

## Gambaran sistem (untuk yang belum mengikuti)

```
  HP petani / laptop
          │  (internet / Wi-Fi)
          ▼
  ┌──────────────┐     MQTT      ┌──────────────┐   kabel RS485   ┌────────────────────────┐
  │   Backend    │ ◄───────────► │  Edge Agent  │ ◄─────────────► │ Sensor, relay pompa,   │
  │ + database   │  (pesan antar │  (di Jetson) │   GPIO, USB     │ trolley kamera, PZEM   │
  │ + jadwal     │    program)   └──────────────┘                 └────────────────────────┘
  │   siram      │                       │ foto patroli
  └──────────────┘                       ▼
         ▲                ┌──────────────────────────────────────────────┐
         └─── perintah ── │ Layanan AI (tim AI): baca foto, beri pupuk   │
              & data      └──────────────────────────────────────────────┘
```

- **Frontend** adalah yang dilihat dan disentuh pengguna.
- **Backend** menerima perintah dari aplikasi, memeriksa apakah boleh, menyimpan semua angka sensor,
  menjalankan jadwal siram, lalu meneruskan perintah ke Jetson.
- **Layanan AI** membaca foto dan data sensor, lalu meminta backend memberi pupuk. Perintahnya tetap
  diperiksa aturan keselamatan yang sama seperti perintah dari petani.
- **Edge Agent** adalah satu-satunya program yang benar-benar menyalakan/mematikan pompa dan membaca
  sensor. Ia juga **penjaga keselamatan terakhir**: kalau backend mati atau internet putus, ia tetap
  mematikan pompa tepat waktu dan menolak perintah berbahaya.
- Backend, Edge Agent, layanan AI, dan database semuanya berjalan di **satu Jetson** di greenhouse.

---

## 1. Backend (BE)

### Sudah selesai

- **Akun dan hak akses**: tiga peran, yaitu ADMIN (atur semua), OPERATOR (boleh menyalakan alat), dan
  VIEWER/Pelihat (hanya melihat). Login tetap tersimpan 7 hari, ganti sandi, kelola pengguna.
- **Data sensor**: menyimpan semua bacaan. Data rinci disimpan 30 hari lalu dibersihkan otomatis supaya
  disk Jetson tidak penuh, sedangkan ringkasan per jam (terendah, rata-rata, tertinggi) disimpan
  **tanpa batas waktu**, sehingga grafik satu musim penuh tetap bisa dibuka.
- **Perubahan langsung terlihat**: bila satu operator mengganti nama atau mematikan sensor/alat, layar
  operator lain ikut berubah tanpa memuat ulang.
- **Perintah alat**: setiap perintah dicatat (siapa, kapan, berhasil atau tidak). Bila ditolak, alasannya
  disimpan dengan kode (mis. "listrik PLN padam", "air di drum hampir habis"). Perintah yang tidak
  dijawab alat dalam 2 menit ditandai **"Alat tidak menjawab"**.
- **Aturan keselamatan di pusat (interlock)**: mis. pompa pupuk tidak boleh bersamaan dengan trolley
  jalan, semua aktivasi diblokir saat listrik dari aki.
- **Perintah tingkat tinggi**: "patroli kamera sekarang" dan "pindahkan kamera ke stasiun N".
- **Foto patroli** bisa dilihat dari aplikasi (dengan login).
- **Kalibrasi**: kalibrasi sensor dengan sampel lab, uji drift, dan takaran pompa pupuk. Angka yang sudah
  dikoreksi tersedia di samping angka mentah.
- **Baru (31 Okt), belum di Jetson** (keputusan pemilik proyek):
  - **Umur tanaman (HST) dihitung dari tanggal semai.** Hari benih disemai = hari ke-0.
  - **Lama siram ditentukan program fuzzy siram tim AI** saat jadwal tiba. Lama siram yang diisi petani
    menjadi cadangan bila AI tidak menjawab atau data sensor belum lengkap; petani mendapat pemberitahuan.
    Bila AI menilai tanah sudah cukup basah, pompa tidak dinyalakan.
- **Baru (29 Okt), sudah di GitHub dan sudah di Jetson:**
  - **Jadwal siram**: petani mengisi jam dan lama siram di aplikasi; backend menyalakan pompa siram
    tepat waktu. Semua aturan keselamatan tetap berlaku. Bila siram ditolak (mis. PLN padam), petani
    mendapat peringatan beserta alasannya. Bila backend mati lebih dari 15 menit, jadwal yang terlewat
    tidak dikejar, dan satu jadwal tidak pernah menyiram dua kali dalam sehari.
  - **Notifikasi push ke HP**: peringatan darurat (mis. listrik PLN padam) dikirim ke HP semua pengguna
    yang menyalakan notifikasi, walau aplikasinya sedang ditutup. Isinya terenkripsi. Sudah berhasil
    dicoba di laptop; untuk HP sungguhan butuh alamat **https** dan kunci notifikasi (VAPID) di Jetson
    (lihat bagian 4).
  - **Riwayat jangka panjang**: grafik 7/30/90 hari diambil dari ringkasan per jam, jadi tetap cepat.
  - **Data untuk tim AI**: `GET /api/ai/inputs` menyajikan angka sensor terbaru dengan nama yang sama
    seperti formulir di program tim AI, sudah dikoreksi kalibrasi, lengkap dengan umur data dan tanda
    "siap diberi pupuk" (aturan S9). Angka fase tanaman, target EC, dan batas pompa pupuk kini
    **mengikuti program fuzzy tim AI** (`FASE_MF`).
  - **Umur tanaman (HST)**: dihitung backend dari tanggal tanam. Tanggal tanam diisi saat menambah
    tanaman pertama; menghapus tanaman terakhir mengosongkan tanggal tanam.
  - **Pembersihan riset**: modul riset, metrik kinerja, pengamatan, panen, perlakuan K0/N-/P-/K-, dan
    aturan otomasi lama **dihapus** dari backend, sesuai keputusan "aplikasi untuk kontrol".
  - **Hemat penyimpanan**: data rinci 90 → 30 hari, log harian jauh lebih sedikit, meteran listrik
    (PZEM) dikirim tiap 30 detik, bukan tiap 3 detik.
- **Uji otomatis**: unit test dan integration test dengan database PostgreSQL sungguhan lulus.

### Sedang / belum

| Pekerjaan | Keterangan |
|---|---|
| **Uji jadwal siram nyata** | Buat satu jadwal 1 menit, pastikan pompa siram menyala dan mati sendiri, lalu hapus. Menunggu edge agent dinyalakan |
| Alamat https untuk dashboard | Syarat notifikasi push di HP. Setelah ada: daftarkan alamatnya, buat kunci VAPID di Jetson, nyalakan push |
| Alamat IP kampus bisa berubah | IP Jetson dari kampus bisa berganti; perlu diperbarui di pengaturan atau minta IP tetap. Akses lewat Tailscale tetap stabil |
| Akun `layanan-ai` untuk tim AI | Dibuat saat tim AI siap; sandinya diberikan lewat jalur aman |
| Isi data kalibrasi nyata (debit pompa pupuk, sampel lab NPK, uji drift) | Fiturnya siap, datanya belum ada. Setelah debit pompa diukur, batas waktu nyala pompa ditinjau ulang |
| Ukur pemakaian memori di Jetson | Terutama saat layanan AI (pembaca foto) ikut berjalan |
| Perawatan Jetson | Backup database berkala; **foto patroli tidak terhapus otomatis** (±2–3,5 GB/bulan), perlu dipindahkan berkala |
| Aturan peringatan berbasis ambang (mis. "beri tahu bila kelembapan < X") | Menunggu ambang dari tim agronomi |
| Flow meter air | Menunggu keputusan pemasangan sensor |

---

## 2. Frontend (FE)

### Sudah selesai

- **Aplikasi HP (PWA)**: bisa dipasang di layar utama HP seperti aplikasi biasa.
- **Bahasa petani**: "Peringatan", bukan "Alert"; "Kereta kamera", bukan "trolley"; "Nyalakan/Matikan".
  Istilah teknis disembunyikan di panel "Info teknis", dan ada **mode sederhana** yang menyembunyikannya
  sepenuhnya.
- **Beranda**: angka sensor dalam huruf besar, status alat, umur tanaman, kartu kamera dengan tombol
  **"Patroli sekarang"**, dan foto patroli.
- **Kontrol alat**: nyalakan/matikan pompa dan kamera, pindah stasiun 1–10. Tombol **Hentikan tidak
  pernah dinonaktifkan**. Bila perintah ditolak, alasannya ditampilkan dalam bahasa sederhana.
- **Tetap bisa dibuka saat sinyal putus**: menampilkan angka terakhir beserta jamnya.
- Halaman Peringatan, kelola pengguna, mode Pelihat yang hanya memantau, unduh CSV riwayat sensor.
- **Baru (29 Okt), sudah di GitHub:**
  - **Jadwal siram**: tombol **Siram** di tengah menu bawah; jam dan lama siram diketik langsung, dan
    hasil siram terakhir ditampilkan (berhasil, atau alasan bila dilewati).
  - **Panduan**: halaman cara pakai dengan tombol **"?"** yang langsung membuka bagian sesuai halaman
    yang sedang dibuka.
  - **Halaman Tanaman disederhanakan**: cukup umur tanaman (HST) dan nama fase. Tanggal tanam diisi
    saat menambah tanaman pertama.
  - **Kalibrasi** sensor dan pompa pupuk. Angka terkoreksi tampil di samping angka mentah.
  - **Grafik jangka panjang** 7/30/90 hari: garis rata-rata dengan pita terendah–tertinggi.
  - **Notifikasi push**: kartu "Notifikasi di HP ini" di halaman Profil, tombol kirim uji, petunjuk
    untuk iPhone (harus "Tambahkan ke Layar Utama"). Notifikasi berhenti saat pengguna keluar.
  - Pesan "Alat tidak menjawab" dan peringatan dalam bahasa sederhana; sensor/alat yang dimatikan
    hanya tampil di Pengaturan.
  - **Halaman riset dihapus** (Riset, Kinerja, pengamatan, panen) sesuai keputusan "aplikasi untuk
    kontrol".
- **Uji otomatis** lulus, build produksi lulus.

### Sedang / belum

| Pekerjaan | Keterangan |
|---|---|
| **Uji langsung dengan petani** di HP sungguhan (ukuran huruf, kontras di bawah matahari, pemahaman bahasa) | Prioritas tertinggi FE, menunggu jadwal ke lapangan |
| Desain ulang tampilan | Sedang dikerjakan |
| Coba notifikasi push di HP sungguhan | Sudah jalan di laptop; di HP menunggu alamat https |

---

## 3. Edge Agent (EDGE)

### Sudah selesai (diuji dengan simulator, tanpa hardware)

- **Hubungan dengan backend lewat MQTT**: kirim data sensor, status alat, jawaban perintah, dan
  peringatan. Kalau koneksi putus, pesan disimpan dulu lalu dikirim saat tersambung lagi. Peringatan
  penting tidak pernah dibuang.
- **Membaca semua sensor lewat RS485/Modbus**: suhu & kelembapan udara, meteran listrik (PZEM), relay.
  Sensor yang registernya belum dipastikan (tanah, cahaya, suhu larutan, pembaca EC/pH) **sengaja
  belum dibaca** supaya tidak mengirim angka salah.
- **20 aturan keselamatan (S1–S20)**, masing-masing punya uji otomatis. Contohnya:
  - Saat Jetson menyala, **semua relay dimatikan dulu** sebelum apa pun.
  - Setiap pompa punya **batas waktu nyala** dan dimatikan oleh Jetson sendiri walau backend mati.
  - Perintah **STOP/OFF tidak pernah ditolak**.
  - **Listrik PLN padam** → pompa dan trolley dihentikan dan diblokir sampai PLN kembali.
  - **Air drum hampir habis** → pompa siram dan pompa pupuk diblokir.
  - Relay yang "tidak menurut" (dibaca balik tidak sesuai) dikunci dan dilaporkan.
  - Trolley berhenti di ujung rel, dan dikunci bila kabel sakelar ujung putus.
  - Saat program dihentikan untuk servis, **semua relay dimatikan dulu**.
- **Daya listrik**: mendeteksi PLN/aki, korsleting SSR, dan pompa kering dari arus listrik. Meteran
  listrik tetap dibaca tiap 3 detik untuk keselamatan, tetapi angkanya dikirim ke backend tiap 30 detik.
- **EC/pH larutan**: urutan baca, konversi, kompensasi suhu, dan alat bantu kalibrasi sudah siap.
  EC tanah dikirim dalam satuan mS/cm, sama seperti program tim AI.
- **Pompa pupuk**: batas nyala 150 detik, mengikuti program pupuk tim AI. Loop pemberian pupuk
  otomatis **dipindah ke layanan tim AI**; edge agent hanya menjalankan perintahnya dengan aman.
- **Trolley kamera dan patroli malam**: homing, pindah stasiun, patroli terjadwal pukul 19.00 WIB atau
  manual dari aplikasi, foto PNG + data pendukung, cek foto buram, peringatan bila disk hampir penuh.
- **Pemasangan di Jetson**: skrip instal, layanan yang otomatis jalan dan restart sendiri, log.
  **Sudah terpasang di Jetson, sengaja belum dinyalakan** sampai komisioning.
- **Dokumen operasi** (cara start/stop, baca log, ganti register tanpa ubah kode) dan **lembar kerja
  komisioning** untuk uji di lapangan.
- **Uji bersama backend** (26 Okt): 5 perangkat tampil ONLINE di aplikasi, perintah dari aplikasi
  sampai ke relay simulator, jawabannya kembali dengan status berhasil.
- **Uji otomatis**: lebih dari 400 test lulus.

### Sedang / belum

Hampir semuanya butuh **barang fisik** dan dikerjakan bersama tim hardware saat komisioning:

| Pekerjaan | Butuh apa |
|---|---|
| Pastikan alamat dan register setiap modul (relay, sensor tanah, cahaya, suhu larutan, pembaca EC/pH) | Modul terpasang di bus; alat `modbus_probe` sudah siap |
| Pastikan profil modul relay | Modul relay terpasang |
| Program pembaca **GPIO asli** (sensor posisi trolley) | Nomor pin dari pembuat papan GPIO |
| Program **kamera asli** | Kamera terpasang (tipe, resolusi, pengaturan exposure) |
| Ukur arus pompa normal (untuk deteksi pompa kering/korsleting) | Pompa terpasang + PZEM |
| Kalibrasi EC/pH | Kit larutan kalibrasi DFR0300 + buffer pH |
| Nyalakan edge agent, uji pompa satu per satu | Ada orang yang mengawasi di greenhouse |
| **Komisioning**: uji 20 aturan keselamatan di alat asli, uji ketahanan 72 jam | Semua hardware terpasang |

---

## 4. Yang dibutuhkan dari tim

| Dari | Yang dibutuhkan | Untuk |
|---|---|---|
| **Pemilik proyek** | Alamat **https** untuk dashboard (domain sendiri atau HTTPS Tailscale). Domain yang dipakai untuk SSH ke Jetson milik teman dan tidak dipakai untuk akses umum | Notifikasi push di HP; akses dashboard dari luar greenhouse |
| **Tim hardware** | Waktu bersama untuk **komisioning** di greenhouse: alamat modul, profil relay, pin GPIO trolley, kamera, arus normal pompa | Membuka semua pekerjaan EDGE yang tertahan |
| **Tim hardware** | Jawaban: pompa aduk ("pompa akuarium") memakai **24 V atau 220 V**, dan dipasang di relay R4? Apakah toren yang ada pompa aduknya sama dengan drum yang ada sensor EC/pH dan pelampung? | Pengaturan relay pompa aduk |
| **Tim hardware** | Jawaban: apakah modul relay punya **mode nyala-berwaktu**? | Lapisan keselamatan tambahan bila Jetson hang total |
| **Tim lapangan** | **Kalibrasi debit pompa peristaltik** N/P/K (minimal 3 ulangan) lewat halaman Kalibrasi | Takaran pupuk yang tepat; batas nyala pompa |
| **Tim agronomi** | Ambang ideal pH/EC/kelembapan per fase | Aturan peringatan |
| **Tim AI** | Penjadwal pupuk yang memanggil API backend dengan urutan N → P → K → aduk, dan **berapa lama mengaduk** | Pemberian pupuk otomatis |
| **Tim AI** | Konfirmasi: layanan AI wajib **menolak memberi pupuk** bila bacaan EC/pH tidak ada atau tidak masuk akal (pakai tanda "siap diberi pupuk" dari `GET /api/ai/inputs`) | Aturan keselamatan S9 |
| **Tim AI** | Jawaban: kolom pH di formulir pupuk itu pH larutan atau pH tanah? Kabari bila angka fase (`FASE_MF`) berubah | Data yang dikirim backend tetap cocok |
| **Tim riset** | Konfirmasi format nama berkas foto (`YYYYMMDD-HHMM_Snn_timur.png`) | Pengolahan foto oleh tim AI/riset |
| **Tim lapangan** | Jadwal uji aplikasi dengan petani | Validasi tampilan dan bahasa |

catatan : keputusan tingkat argonomi bisa riset/bertanya ke ahlinya

---

## 5. Langkah berikutnya

1. **Komisioning bersama tim hardware:** cek alamat modul dan profil relay, pin GPIO trolley, kamera,
   arus normal pompa, lalu isi pengaturan dan nyalakan edge agent dengan diawasi orang di greenhouse.
   Ikuti `docs/komisioning.md` bagian F11.
2. **Uji pompa satu per satu**, lalu **uji jadwal siram nyata** (satu jadwal 1 menit, pompa harus
   menyala dan mati sendiri, lalu jadwalnya dihapus).
3. **Komisioning keselamatan:** uji S1–S20 di alat asli dengan pompa dan trolley yang sebenarnya.
4. **Pengukuran lapangan:** tegangan pompa aduk, kalibrasi debit pompa peristaltik N/P/K, volume drum,
   kalibrasi EC/pH.
5. **Uji ketahanan 72 jam:** semua jalan tanpa ditunggui.
6. **Alamat https untuk dashboard**, lalu nyalakan notifikasi push dan coba di HP sungguhan.
7. **Serah terima ke tim AI:** kirim dokumen perbandingan dan dokumentasi API, buat akun `layanan-ai`.
8. **Uji durasi siram dari AI** di Jetson: isi alamat layanan AI (`AI_SERVICE_URL`), jalankan satu jadwal, pastikan lama siram berasal dari AI.

---

## Istilah

| Istilah | Arti singkat |
|---|---|
| **Jetson** | Komputer kecil di greenhouse tempat semua program berjalan |
| **MQTT** | "Kurir pesan" antara backend dan edge agent |
| **RS485 / Modbus** | Kabel dan "bahasa" untuk berbicara dengan sensor dan relay |
| **Relay** | Saklar listrik yang dikendalikan komputer (menyalakan pompa, trolley) |
| **SSR** | Relay elektronik untuk pompa siram |
| **PZEM** | Meteran listrik: tegangan, arus, energi. Dipakai untuk tahu PLN padam atau pompa kering |
| **Trolley** | Kereta kamera yang berjalan di rel sepanjang bedengan |
| **Patroli** | Trolley berhenti di 10 stasiun dan memotret ke timur dan barat |
| **Interlock** | Aturan yang menolak kombinasi berbahaya, mis. pompa pupuk + trolley bersamaan |
| **EC / pH** | Kepekatan pupuk dan keasaman larutan nutrisi |
| **HST** | Hari Setelah Tanam, umur tanaman dalam hari |
| **Fuzzy / `FASE_MF`** | Program tim AI yang menentukan fase tanaman dan takaran pupuk; angka fase di aplikasi mengikuti program ini |
| **Pompa peristaltik** | Pompa kecil yang menakar pupuk N, P, K |
| **Kalibrasi** | Menyetel sensor dengan larutan standar, atau mengukur debit pompa, supaya angkanya benar |
| **Komisioning** | Uji menyeluruh di lokasi sebelum sistem dipakai sungguhan |
| **Register** | "Laci" di dalam sensor tempat angka bacaan disimpan; tiap merek berbeda letaknya |
| **Simulator / HAL palsu** | Hardware tiruan di laptop untuk menguji program tanpa barang asli |
| **Commit / push** | Menyimpan perubahan kode ke riwayat / mengunggahnya ke GitHub |
| **Notifikasi push** | Pemberitahuan yang muncul di HP walau aplikasinya tertutup |
| **https** | Alamat web terenkripsi; syarat browser untuk notifikasi push |
| **VAPID** | Kunci yang membuktikan notifikasi push benar-benar dari server FertiMata |

*Sumber: `BE-melonSmart/docs/status-pekerjaan.md` (bagian PENGINGAT dan 1e),
`FE-melonSmart/docs/status-pekerjaan.md`, `EDGE-melonSmart/TASKS.md`, dan riwayat git ketiga repo
per 31 Okt 2026.*
