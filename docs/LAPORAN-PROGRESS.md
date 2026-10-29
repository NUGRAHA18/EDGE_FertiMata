# Laporan Progress FertiMata

**Per 29 Oktober 2026** · mencakup Backend (BE), Frontend (FE), dan Edge Agent (EDGE)

---

## Ringkasan

**Perangkat lunaknya hampir lengkap. Yang belum adalah uji dengan hardware terpasang.**

Ketiga bagian sudah dibangun dan sudah diuji bersama di laptop: perintah dari aplikasi sampai ke
"relay palsu", lalu jawabannya kembali ke aplikasi. Hampir semua pekerjaan yang tersisa menunggu
hardware: memastikan alamat dan register setiap sensor, mengukur arus pompa, kalibrasi EC/pH, dan
memasang semuanya di Jetson.

| Bagian | Fungsinya | Status | Hambatan utama |
|---|---|---|---|
| **Backend** | "Otak pusat": menyimpan data, mengatur akun, memeriksa perintah | Semua fitur yang direncanakan selesai, termasuk notifikasi push | Data kalibrasi dari lapangan; uji di Jetson; alamat https untuk dashboard |
| **Frontend** | Aplikasi yang dibuka petani/peneliti di HP | Semua halaman selesai, termasuk riset, kalibrasi, dan notifikasi | Uji langsung dengan petani |
| **Edge Agent** | Program di Jetson yang menghubungkan aplikasi dengan alat di greenhouse | Logika selesai dan diuji dengan simulator | Pengecekan hardware, uji di Jetson dan di rel |
| AI (tim AI) | Membaca foto daun dan mengatur jadwal pupuk/siram | Dikerjakan tim AI | Dokumentasi API dari tim AI |

---

## Gambaran sistem (untuk yang belum mengikuti)

```
  HP petani / laptop peneliti
          │  (internet / Wi-Fi)
          ▼
  ┌──────────────┐     MQTT      ┌──────────────┐   kabel RS485   ┌────────────────────────┐
  │   Backend    │ ◄───────────► │  Edge Agent  │ ◄─────────────► │ Sensor, relay pompa,   │
  │ + database   │  (pesan antar │  (di Jetson) │   GPIO, USB     │ trolley kamera, PZEM   │
  └──────────────┘    program)   └──────────────┘                 └────────────────────────┘
         ▲                               │ foto patroli
         │ hasil AI                      ▼
  ┌──────────────────────────────────────────────┐
  │ Layanan AI (tim AI): baca foto, jadwal pupuk │
  └──────────────────────────────────────────────┘
```

- **Frontend** adalah yang dilihat dan disentuh pengguna.
- **Backend** menerima perintah dari aplikasi, memeriksa apakah boleh, menyimpan semua angka sensor,
  lalu meneruskan perintah ke Jetson.
- **Edge Agent** adalah satu-satunya program yang benar-benar menyalakan/mematikan pompa dan membaca
  sensor. Ia juga **penjaga keselamatan terakhir**: kalau backend mati atau internet putus, ia tetap
  mematikan pompa tepat waktu dan menolak perintah berbahaya.
- Backend, Edge Agent, dan database semuanya berjalan di **satu Jetson** di greenhouse.

---

## 1. Backend (BE)

### Sudah selesai

- **Akun dan hak akses**: tiga peran, yaitu ADMIN (atur semua), OPERATOR (boleh menyalakan alat), dan
  VIEWER/Pelihat (hanya melihat). Login tetap tersimpan 7 hari, ganti sandi, kelola pengguna.
- **Data sensor**: menyimpan semua bacaan, data lama dibersihkan otomatis supaya database tidak penuh,
  dan ringkasan per jam (terendah, rata-rata, tertinggi) disimpan **tanpa batas waktu**, sehingga grafik
  satu musim penuh tetap bisa dibuka walau data mentahnya sudah dihapus.
- **Perubahan langsung terlihat**: bila satu operator mengganti nama atau mematikan sensor/alat, layar
  operator lain ikut berubah tanpa memuat ulang.
- **Perintah alat**: setiap perintah dicatat (siapa, kapan, berhasil atau tidak). Bila ditolak, alasannya
  disimpan dengan kode (mis. "listrik PLN padam", "air di drum hampir habis").
- **Aturan keselamatan di pusat (interlock)**: mis. pompa pupuk tidak boleh bersamaan dengan trolley
  jalan, semua aktivasi diblokir saat listrik dari aki.
- **Perintah tingkat tinggi**: "patroli kamera sekarang" dan "pindahkan kamera ke stasiun N".
- **Foto patroli** bisa dilihat dari aplikasi (dengan login).
- **Data riset**: siklus tanam dan umur tanaman (HST), pengamatan per tanaman, kalibrasi sensor dan
  pompa, uji drift, data panen (bobot, °Brix, dll.), ekspor CSV, dan laporan kesehatan sistem
  (kelengkapan data, uptime, energi listrik). **Baru (29 Okt):** semua pengamatan satu siklus bisa
  diambil sekaligus untuk grafik pertumbuhan per perlakuan, dan HST pengamatan yang dicatat belakangan
  kini dihitung dari tanggal pengamatan, bukan tanggal input.
- **Notifikasi push ke HP (baru, 29 Okt)**: peringatan darurat (mis. listrik PLN padam) dikirim ke HP
  semua pengguna yang menyalakan notifikasi, walau aplikasinya sedang ditutup. Isinya terenkripsi;
  hanya HP penerima yang bisa membukanya. Sudah aktif di laptop untuk dicoba; untuk HP sungguhan
  butuh alamat **https** untuk dashboard (lihat bagian 4).
- **Data contoh satu musim tanam** di database pengembangan (75 hari riwayat sensor, 30 tanaman dengan
  perlakuan K0/N-/P-/K- dan SimonKori, 300 pengamatan, 30 panen) supaya grafik dan halaman riset bisa
  dikerjakan dan didemokan sebelum musim sungguhan.
- **Uji otomatis**: 167 unit test dan 10/10 integration test lulus dengan database PostgreSQL sungguhan.

### Sedang / belum

| Pekerjaan | Keterangan |
|---|---|
| Simpan ke GitHub | Semua pekerjaan 29 Okt (grafik jangka panjang, notifikasi perubahan data, notifikasi push, data riset) belum di-commit, dan satu commit 28 Okt belum di-push |
| Alamat https untuk dashboard | Syarat notifikasi push di HP. Domain sendiri atau HTTPS Tailscale; sedang disiapkan |
| Isi batas waktu nyala pompa dari hasil kalibrasi | Menunggu pengukuran di lapangan |
| Isi data kalibrasi nyata (debit pompa, sampel lab NPK, uji drift) | Fiturnya siap, datanya belum ada |
| Ukur pemakaian memori backend di Jetson | Saat ini masih perkiraan |
| Aturan peringatan berbasis ambang (mis. "beri tahu bila kelembapan < X") | Menunggu ambang keputusan tim |
| Flow meter air | Menunggu keputusan pemasangan sensor |
| Permintaan kecil dari FE | Koreksi data panen/uji drift yang salah ketik, satu satuan kekerasan per siklus, uji statistik (p-value) di server; menunggu keputusan tim riset |

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
- **Petunjuk untuk pengguna baru**, halaman Tanaman (siklus, fase, pengamatan), halaman Peringatan,
  kelola pengguna, mode Pelihat yang hanya memantau.
- **Unduh CSV** untuk riwayat sensor, pengamatan tanaman, panen, dan sampel kalibrasi.
- **Baru (29 Okt), belum di GitHub:**
  - **Grafik jangka panjang** 7/30/90 hari: garis rata-rata dengan pita terendah–tertinggi; bagian saat
    alat mati tampil sebagai celah.
  - **Kalibrasi**: sampel lab, pratinjau dan simpan hasil kalibrasi sensor, uji drift, takaran pompa
    pupuk. Angka terkoreksi tampil di samping angka mentah.
  - **Form panen** per buah dan **halaman Riset**: kurva tinggi dan jumlah daun per perlakuan, rekap
    panen per perlakuan (mis. "N− 22% lebih ringan dari K0"), FertiMata vs SimonKori, catatan gejala.
  - **Halaman Kinerja** (metrik sistem): kelengkapan data, uptime, energi, takaran pupuk.
  - **Notifikasi push**: saklar "Notifikasi di HP ini" di halaman Akun, tombol kirim uji, petunjuk untuk
    iPhone (harus "Tambahkan ke Layar Utama").
  - Daftar alat/sensor ikut berubah saat operator lain mengubahnya.
- **Uji otomatis**: 108 test lulus, build produksi lulus.

### Sedang / belum

| Pekerjaan | Keterangan |
|---|---|
| **Uji langsung dengan petani** di HP sungguhan (ukuran huruf, kontras di bawah matahari, pemahaman bahasa) | Prioritas tertinggi FE, menunggu jadwal ke lapangan |
| Simpan pekerjaan 29 Okt ke GitHub | Belum di-commit |
| Coba notifikasi push sungguhan | Siap dicoba di laptop (localhost); di HP menunggu alamat https |
| Coba halaman baru di browser dengan data contoh | Grafik, kalibrasi, riset, dan kinerja baru diuji otomatis |
| Angka hasil AI per stasiun (skor N/P/K, luas daun) | Menunggu format hasil dari tim AI |

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
  - **Baru (29 Okt):** saat program dihentikan untuk servis, semua relay dimatikan dulu.
- **Daya listrik**: mendeteksi PLN/aki, korsleting SSR, dan pompa kering dari arus listrik.
- **EC/pH larutan**: urutan baca, konversi, kompensasi suhu, dan alat bantu kalibrasi sudah siap.
- **Trolley kamera dan patroli malam**: homing, pindah stasiun, patroli terjadwal pukul 19.00 WIB atau
  manual dari aplikasi, foto PNG + data pendukung, cek foto buram, peringatan bila disk hampir penuh.
- **Pemasangan di Jetson**: skrip instal, layanan yang otomatis jalan dan restart sendiri, log.
- **Dokumen operasi** (cara start/stop, baca log, ganti register tanpa ubah kode) dan **lembar kerja
  komisioning** untuk uji di lapangan.
- **Uji bersama backend** (26 Okt): 5 perangkat tampil ONLINE di aplikasi, perintah dari aplikasi
  sampai ke relay simulator, jawabannya kembali dengan status berhasil.
- **Uji otomatis**: 415 test lulus.

### Sedang / belum

Hampir semuanya butuh **barang fisik**:

| Pekerjaan | Butuh apa |
|---|---|
| Pastikan alamat dan register setiap modul (relay, sensor tanah, cahaya, suhu larutan, pembaca EC/pH) | Akses ke Jetson + modul terpasang di bus, alat `modbus_probe` sudah siap |
| Program pembaca **GPIO asli** (sensor posisi trolley) | Nomor pin dari pembuat papan GPIO |
| Program **kamera asli** | Kamera terpasang (tipe, resolusi, pengaturan exposure) |
| Ukur arus pompa normal (untuk deteksi pompa kering/korsleting) | Pompa terpasang + PZEM |
| Kalibrasi EC/pH | Kit larutan kalibrasi DFR0300 + buffer pH |
| Pasang di Jetson dan uji restart/watchdog | Akses SSH ke Jetson |
| **Komisioning**: uji 20 aturan keselamatan di alat asli, uji ketahanan 72 jam | Semua hardware terpasang |

---

## 4. Yang dibutuhkan dari tim

| Dari | Yang dibutuhkan | Untuk |
|---|---|---|
| **Pemilik proyek** | Alamat **https** untuk dashboard (domain sendiri atau HTTPS Tailscale). Domain yang dipakai untuk SSH ke Jetson milik teman dan tidak dipakai untuk akses umum | Notifikasi push di HP; akses dashboard dari luar greenhouse |
| **Tim riset** | Keputusan: cara mengoreksi data panen yang salah ketik, satuan kekerasan buah, perlu tidaknya uji statistik di aplikasi | Menutup permintaan FE ke BE |
| **Tim hardware** | Akses Jetson; manual atau hasil uji modul relay, sensor tanah, cahaya, DS18B20, ADC; nomor pin GPIO; kamera terpasang | Membuka semua pekerjaan EDGE yang tertahan |
| **Tim hardware** | Jawaban: apakah modul relay punya **mode nyala-berwaktu**? | Lapisan keselamatan tambahan bila Jetson hang total |
| **Tim agronomi** | Ambang ideal pH/EC/kelembapan per fase | Badge "ideal" di aplikasi, aturan peringatan |
| **Tim AI** | Dokumentasi API layanan AI dan format hasil final | Menampilkan skor N/P/K di aplikasi; integrasi foto |
| **Tim AI** | Konfirmasi: layanan AI wajib **menolak memberi pupuk** bila bacaan EC/pH tidak ada atau tidak masuk akal | Aturan keselamatan S9, yang untuk sementara dijaga tim AI |
| **Tim riset** | Konfirmasi format nama berkas foto (`YYYYMMDD-HHMM_Snn_timur.png`) | Pengolahan foto oleh tim riset |
| **Tim lapangan** | Jadwal uji aplikasi dengan petani | Validasi tampilan dan bahasa |
| **Semua** | Keputusan: backend menyajikan folder foto langsung (D7, sudah dibuat, belum disetujui resmi) | Menutup keputusan terbuka |

catatan : keputusan tingkat argonomi bisa riset/bertanya ke ahlinya

---

## 5. Langkah berikutnya

1. **Begitu Jetson bisa diakses:** pasang edge agent dan backend, cek semua alamat sensor dan relay.
   Ikuti `docs/komisioning.md` bagian F11.1.
2. **Komisioning keselamatan:** uji S1–S20 di alat asli dengan pompa dan trolley yang sebenarnya.
3. **Pengukuran lapangan:** arus pompa, debit pompa pupuk, volume drum, kalibrasi EC/pH.
4. **Uji ketahanan 72 jam:** semua jalan tanpa ditunggui.
5. Sambil menunggu: coba notifikasi push dan halaman baru di laptop dengan data contoh, simpan
   pekerjaan BE dan FE 29 Okt ke GitHub, dan siapkan alamat https untuk dashboard.

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
| **Kalibrasi** | Menyetel sensor dengan larutan standar supaya angkanya benar |
| **Komisioning** | Uji menyeluruh di lokasi sebelum sistem dipakai sungguhan |
| **Register** | "Laci" di dalam sensor tempat angka bacaan disimpan; tiap merek berbeda letaknya |
| **Simulator / HAL palsu** | Hardware tiruan di laptop untuk menguji program tanpa barang asli |
| **Commit / push** | Menyimpan perubahan kode ke riwayat / mengunggahnya ke GitHub |
| **Notifikasi push** | Pemberitahuan yang muncul di HP walau aplikasinya tertutup |
| **https** | Alamat web terenkripsi; syarat browser untuk notifikasi push |

*Sumber: `BE-melonSmart/docs/status-pekerjaan.md`, `FE-melonSmart/docs/status-pekerjaan.md`,
`EDGE-melonSmart/TASKS.md`, dan riwayat git ketiga repo per 29 Okt 2026.*
