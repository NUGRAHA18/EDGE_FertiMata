# Protokol Data — Perlakuan, Akuisisi Citra, dan Kalibrasi Sensor

> Dokumen ini berisi keputusan yang **tidak bisa ditunda**. Plot perlakuan defisiensi
> harus dipasang saat transplant. Kalau lewat, penelitian ini tidak akan pernah punya
> ground truth defisiensi dan tidak bisa dikejar belakangan.

---

## 1. Masalah inti yang harus dipecahkan

Penelitian ini ingin mengklasifikasi defisiensi N, P, dan K dari citra daun melon.
Untuk itu dibutuhkan gambar daun yang **benar-benar defisien** beserta labelnya.

Kondisi per 8 September 2026: galon masih kosong, bibit baru berdaun sejati 1–2 helai,
transplant diperkirakan 10–15 September. Gejala defisiensi hara butuh **3–5 minggu**
sejak perlakuan dimulai. Artinya:

| Tonggak | Tanggal | Umur tanaman | Gejala defisiensi? |
|---|---|---|---|
| Upload Laporan Antara | 25–29 Sep | 10–19 HST | **Belum muncul** |
| Review eksternal | 30 Sep–5 Okt | 15–25 HST | Mulai samar |
| Revisi & laporan lengkap | 6–31 Okt | 21–50 HST | **Jelas** |
| Upload laporan + draf artikel | 2–6 Nov | 48–57 HST | Jelas |

Konsekuensinya jelas: **BAB IV pada 25–29 September tidak boleh bergantung pada data
defisiensi tanaman sendiri.** Isi BAB IV dengan data commissioning (lihat §5).
Data defisiensi baru masuk pada revisi Oktober dan laporan lengkap November.

---

## 2. Plot perlakuan defisiensi — pasang saat transplant

### Rancangan

Mengikuti protokol induksi terkontrol dari `2005-Nitrogen deficiency effects...sorghum.pdf`
(3 level N: 100% / 20% / 0%) dan `2020-The reduction in leaf area precedes...potassium
deficiency.pdf` (3 level K, daun ke-1/3/5 ditandai untuk memisahkan umur daun).

Basis larutan hara mengikuti `2020-Quality Improvement of Netted Melon...pdf`:

| Unsur | Konsentrasi penuh (100%) |
|---|---|
| N | 168 mg/L |
| P | 31 mg/L |
| K | 195 mg/L |
| Rasio NO₃-N : NH₄-N | 5 : 1 |

### Perlakuan

| Kode | Perlakuan | N | P | K | Jumlah tanaman |
|---|---|---|---|---|---|
| **K0** | Kontrol lengkap | 100% | 100% | 100% | 12 |
| **N−** | Defisiensi nitrogen | **20%** | 100% | 100% | 12 |
| **P−** | Defisiensi fosfor | 100% | **20%** | 100% | 12 |
| **K−** | Defisiensi kalium | 100% | 100% | **20%** | 12 |

Pakai 20%, bukan 0%. Alasannya: pada 0% tanaman mati sebelum sempat menunjukkan
gejala visual yang stabil dan bisa difoto berulang kali. Paper sorgum memakai kedua
level dan level 20% yang paling produktif untuk pengamatan bertahap.

48 tanaman perlakuan dari ±156 total. Sisanya (±108) menjalankan protokol produksi
normal dan dipakai untuk perbandingan sistem (lihat §6).

### Penempatan

Sebar setiap perlakuan ke seluruh panjang greenhouse dalam blok acak, jangan
dikumpulkan di satu ujung. Gradien cahaya dan suhu di dalam greenhouse cukup besar
(rasio luminansi 4–9×), jadi perlakuan yang mengumpul akan tercampur dengan efek posisi.

Susun sebagai **4 blok × 4 perlakuan × 3 tanaman**, blok berurutan sepanjang lintasan rel.

### Penandaan daun — murah dan menaikkan kualitas data

Tandai **daun ke-1 (teratas yang sudah membuka penuh), ke-3, dan ke-5** pada setiap
tanaman perlakuan memakai label plastik bernomor. Ini memungkinkan pemisahan gejala
menurut umur daun, yang krusial karena:

| Unsur | Daun yang terkena lebih dulu |
|---|---|
| N, P, K, Mg | Daun **tua** (bawah) |
| Ca, Fe | Daun **muda** (atas) |

Tanpa penandaan, data Anda tidak bisa membedakan "daun ini kuning karena N" dari
"daun ini kuning karena memang sudah tua".

### Dokumentasi wajib per tanaman perlakuan

Setiap 3–4 hari, mengikuti irama pengukuran paper sorgum:

- Foto daun bertanda (dari rel kamera, otomatis)
- Tinggi tanaman, jumlah daun, panjang dan lebar daun ke-3
- Pembacaan sensor tanah di galon tersebut
- Catatan pengamatan visual dalam kalimat (kapan gejala pertama terlihat)

---

## 3. Aturan pelabelan citra

Ini bagian yang paling sering dikritik penguji. Tulis aturannya eksplisit, dan
konsisten. Pendekatan **penilaian ahli visual** sah secara metodologis — dipakai di
`2025-Nutrient deficiency...coffee leaves.pdf` (satu agronomist), `2023-Leaf Health
Identification on Melon Plants.pdf` (petani melon yang merawat harian), dan
`2022-Performance-Analysis...Raspberry Pi.pdf` (aturan tertulis eksplisit).

### Kelas yang realistis

| Kelas | Keterangan |
|---|---|
| `healthy` | Hijau merata, tidak ada klorosis atau nekrosis |
| `N-deficient` | Klorosis **seragam** pada daun tua, ukuran daun mengecil |
| `K-deficient` | Klorosis **marginal**, nekrosis/scorching di **tepi dan ujung** daun, sering disertai curling |
| `P-deficient` | Hijau gelap ke biru-hijau, parah menjadi ungu/violet, bercak mottled |
| `other-stress` | Gejala tidak jelas atau tercampur — buang dari training, jangan dipaksakan |

> **Peringatan:** N dan K sama-sama menyerang daun tua, jadi posisi daun tidak cukup
> untuk memisahkan keduanya. Pembedanya adalah **pola**: N seragam, K marginal.
> Ini harus ditulis di aturan label dan dipahami anotator.
>
> P paling lambat berkembang. Jujur saja di laporan bahwa kelas P kemungkinan
> underrepresented. `2025-Nutrient deficiency...coffee` pun hanya memakai 3 dari
> 10 kelas yang tersedia di CoLeaf-DB.
>
> **Jangan** masukkan Mg. Klorosis interveinal Mg diperparah intensitas cahaya tinggi,
> dan greenhouse ini punya variasi cahaya 4–9×. Confounder yang tidak bisa dikendalikan.

### Aturan tambahan

- Hanya daun pada galon **terdekat kamera** yang dilabel. Baris di belakangnya diabaikan.
- Bercak putih bekas semprot pestisida **tetap dilabel sesuai kondisi hara**, bukan sebagai gejala.
- Daun sobek karena mekanis dibuang, bukan dilabel `other-stress`.
- Label diberikan dua orang secara independen; ketidaksepakatan diselesaikan dengan
  merujuk ke perlakuan yang tercatat. Laporkan **Cohen's kappa** sebagai bukti reliabilitas.

---

## 4. Akuisisi citra

### Jadwal

| Parameter | Nilai | Alasan |
|---|---|---|
| Waktu | **10.00 dan 15.00 WIB**, setiap hari | `2026-Prediction of Optimal Harvest Timing` memakai 10.00; `2022-Performance-Analysis RPi` memakai 11.00 |
| Jumlah pass | 2× per hari | cukup untuk 2 kondisi cahaya, tidak membebani sistem |
| Stasiun per pass | 12 (tiap 60 cm sepanjang 7,2 m efektif) | |
| Frame per hari | 24 | |
| Akumulasi 12 Sep – 2 Nov (51 hari) | ±1.200 frame | cukup untuk fine-tuning |

### Setting kamera — kunci semuanya

Exposure, white balance, gain, dan fokus **dikunci manual**. Auto-exposure akan
menormalkan daun kuning menjadi terlihat hijau, dan itu menghancurkan sinyal yang
justru ingin dipelajari model.

| Parameter | Nilai |
|---|---|
| Format | PNG lossless (jangan JPEG) |
| Bit depth | 8-bit RGB |
| Resolusi | minimal 1280×720; IMX477 sanggup lebih tinggi |
| Penamaan | `rgb_YYYYmmdd_HHMMSS_st<NN>.png` |
| Struktur folder | `data/citra/YYYY-MM-DD/stasiun_NN/` |
| Shutter | secepat mungkin — motion blur menghilangkan tekstur daun |

Ambil gambar **saat trolley berhenti**, bukan sambil bergerak. Beri jeda 2 detik
setelah berhenti agar getaran mereda.

### Pencahayaan aktif — wajib, bukan opsional

Greenhouse ini punya rasio luminansi p95/p5 sebesar **8,9× saat cerah dan 9,5× saat
mendung**, dengan sebaran horizontal 69% dari rata-rata melintasi satu baris pot.
Tanpa pencahayaan terkendali, model akan belajar mengenali kondisi cahaya, bukan
status hara.

Pasang LED bar 12 V CRI ≥ 90 pada trolley, menyala hanya saat pengambilan gambar,
dengan shroud/naungan kecil untuk menekan kontribusi cahaya ambien.

### Target referensi warna dan skala

Tempel pada setiap stasiun, di batang bench sehingga selalu ikut terpotret:

1. **Patch warna** — putih, abu 18%, hitam. Dilaminasi. Dipakai untuk normalisasi
   per-frame sebelum training.
2. **Marker ArUco 100 × 100 mm.** Fungsinya ganda: menaikkan mAP deteksi (`2026-Prediction
   of Optimal Harvest Timing` mencatat 0,89 → 0,92), **dan** memungkinkan pengukuran
   luas daun dalam cm² sungguhan.

> Pengukuran luas daun absolut ini penting. `2020-The reduction in leaf area precedes
> that in photosynthesis under potassium deficiency` menemukan bahwa **penyusutan luas
> daun mendahului penurunan fotosintesis dan mendahului perubahan warna**. Artinya
> luas daun adalah indikator K paling awal, dan kamera RGB tanpa acuan skala akan
> selalu telat mendeteksi K.

### Batas fisik yang harus diakui di laporan

Pita spektral paling diagnostik untuk nitrogen adalah **715 nm, red-edge 730/740 nm,
dan 1075 nm** (`2005-Nitrogen deficiency...sorghum`, r² 0,73–0,82 pada turunan
red-edge). Semuanya di luar jangkauan sensor RGB ber-IR-cut. Yang tertangkap hanya
555 nm (hijau).

Konsekuensinya: **akurasi klasifikasi N dari RGB murni akan di bawah angka literatur
spektral**, dan itu harus dinyatakan sebagai keterbatasan, bukan disembunyikan.

Opsi murah kalau akurasi N mentok: pakai varian **NoIR** (IR-cut filter dilepas) untuk
mendapat kanal NIR kasar. Ini bisa jadi kontribusi tambahan yang tidak mahal.

---

## 5. Isi BAB IV untuk Laporan Antara (25–29 September)

Karena data defisiensi belum ada, BAB IV diisi data commissioning yang tetap menjawab
rumusan masalah:

| Rumusan masalah | Data yang bisa disajikan 25–29 Sep |
|---|---|
| RM1 — akuisisi data tanah | Log sensor 7-in-1 kontinu sejak instalasi + **hasil kalibrasi terhadap uji lab** (§7). Ini data kuat dan sudah siap. |
| RM2 — model citra daun | Baseline model dilatih pada dataset publik (CoLeaf-DB atau setara) + pipeline akuisisi yang sudah berjalan + contoh frame dari rel. Laporkan sebagai *baseline*, bukan hasil akhir. |
| RM3 — integrasi multimodal | Arsitektur, skema database, dan bukti data tanah + citra tersinkron waktu. |
| RM4 — inferensi fuzzy | Rule base lengkap + hasil pengujian **test case** (mengikuti pola 10 skenario draf tahun lalu). Simulasi sah untuk tahap ini. |
| RM5 — implementasi & evaluasi | Metrik teknis sistem: uptime, latensi, kelengkapan data, akurasi sensor, konsumsi daya, log aktuasi. |

Tambahkan satu bagian eksplisit: **"Rancangan pengujian lanjutan"** yang menjelaskan
plot perlakuan sudah terpasang sejak transplant dan data defisiensi akan tersedia pada
tahap revisi Oktober. Reviewer akan menilai ini sebagai perencanaan yang matang,
bukan kekurangan.

---

## 6. Perbandingan sistem baru vs SimonKori

### Yang membuat perbandingan ini adil dan tajam

SimonKori **menjadwalkan irigasi dengan timer digital EWIG TP8A16**, bukan dengan
sensor. Sensor tanah 7-in-1-nya terpasang tapi tidak dipakai untuk memicu penyiraman.
Draf artikel tahun lalu mengonfirmasi sendiri: *"waterpump akan menyala pada jam
tertentu sesuai dengan pengaturan yang telah ditetapkan sebelumnya."*

Jadi pembandingnya bukan "sistem canggih vs sistem canggih", melainkan
**kontrol berbasis sensor + citra vs kontrol berbasis jadwal**. Itu hipotesis yang
bersih dan bisa diuji.

Perbedaan fungsional lain yang terukur:

| Aspek | SimonKori | Sistem baru |
|---|---|---|
| Pemicu irigasi | Timer 7 hari | Sensor + fuzzy + citra |
| Kanal dosing | 2 (A/B pra-campur) | 3 terpisah (N, P, K) |
| Kontrol pH | **Loop terbuka** — ada elektroda, tidak ada aktuator | Kanal asam (opsional) |
| Sensor zona akar | Ada tapi tidak menggerakkan aktuator | Menggerakkan keputusan |
| Fenotipe tanaman | Tidak ada | Kamera bergerak |
| EC | PPM (0–1999) | mS/cm dengan kompensasi suhu |

### Rancangan eksperimen

Mengikuti pola *paired field comparison* dari `2023-Evaluating the Use of Intelligent
Irrigation Systems...Grain Corn.pdf` — dua sistem berdampingan di greenhouse yang sama,
dibandingkan dengan uji t.

Pembagian: **row 1 & 3 = sistem baru, row 2 & 4 = SimonKori.** Berpasangan menyilang
seperti ini mengendalikan gradien cahaya dan suhu di dalam greenhouse.

### Parameter yang dicatat

**Mingguan, per tanaman sampel:** tinggi tanaman, jumlah daun, panjang dan lebar daun ke-3.

**Kumulatif, otomatis:** volume air (flow meter), volume tiap larutan pupuk (log durasi
pompa × debit terkalibrasi), konsumsi daya.

**Saat panen:** bobot buah, **°Brix** (refraktometer), kekerasan, tebal daging, rasio
bahan kering.

**Uji rasa buta terpanel**, n ≥ 27, mengikuti `2020-Quality Improvement of Netted Melon`.
Murah, dan dampaknya pada laporan besar.

**Metrik teknis sistem:** uptime, latensi sensor→dashboard, kelengkapan data harian,
jumlah siklus aktuasi tanpa error.

> **Peringatan metodologis penting.** `2023-Evaluating...Grain Corn` menemukan sistem
> pintar hemat air 35,78% **tetapi produktivitas air turun dari 0,86 ke 0,66 kg/m³**
> dan diameter tongkol menyusut secara signifikan (p<0,01). **Hemat air saja bukan
> bukti keberhasilan.** Selalu laporkan efisiensi berpasangan dengan hasil panen.

---

## 7. Kalibrasi sensor NPK — ini yang akan ditanya penguji

### Masalahnya

Sensor tanah RS485 7-in-1 murah adalah **proxy berbasis konduktivitas**, bukan
pengukuran hara sejati. Akurasi menurut datasheet pabrikan (Hebei Ousu):

| Parameter | Akurasi |
|---|---|
| Suhu | ±1,0 °C |
| Kelembapan | ±5% |
| EC | ±5% |
| **N, P, K** | **±10%** |
| pH | ±10% |

Kritik yang bisa disitasi dan harus Anda antisipasi:

- `2025 Agriintel_Smart_Soil_Monitoring...pdf` (IEEE): sensor NPK murah *"function as
  proxies, which reduces their prediction accuracy compared to laboratory-grade
  instruments, requiring calibration and correction using regression-based models"*.
- `2025-Advanced_Technologies_for_Smart_Fertilizer_Management...pdf`: *"15-30%
  variability in nitrate detection under fluctuating soil moisture"*.
- `2023-Smart Farming Tool for Monitoring Nutrients...pdf` — **yang paling telak**:
  uji lapangan pada elektroda ion-selective (jauh lebih canggih daripada probe
  konduktivitas) memberi R² > 0,9 untuk pH tetapi hanya **R² = 0,6 untuk K** dan
  **R² = 0,5 untuk NO₃⁻**.

### Posisi yang aman untuk ditulis di laporan

> Sensor RS485 7-in-1 digunakan sebagai **indikator relatif dan tren kondisi zona akar
> dengan resolusi temporal tinggi**, bukan sebagai pengganti uji tanah laboratorium.
> Nilai absolut N/P/K dikalibrasi terhadap uji laboratorium melalui model regresi lokal.
> Keputusan fertigasi digerakkan oleh **EC dan kelembapan** (akurasi ±5%), dengan
> pembacaan NPK berperan sebagai koreksi rasio.

Alasan teknisnya kuat: EC dan kelembapan punya akurasi dua kali lebih baik, dan
pembacaan NPK secara fisik **diturunkan** dari konduktivitas — jadi merupakan besaran
turunan sekunder. Rancang loop kontrol pada besaran yang paling andal.

### Protokol kalibrasi

1. **Ambil 30–50 sampel tanah** dari galon, sengaja beragam: baru dipupuk, menjelang
   pemupukan, kering, basah, dari berbagai row. (`2025-Integrating IoT and ML...
   UG-AgroPlan` memakai 50 sampel; jangan hanya 14 seperti paper RS485 lain.)
2. **Ukur berpasangan dan simultan**: pembacaan sensor in-situ, lalu sampel komposit
   dari titik yang sama dikirim ke laboratorium.

   | Parameter | Metode rujukan lab |
   |---|---|
   | N total | Kjeldahl |
   | P tersedia | Olsen-P atau Bray-1 |
   | K tertukar | Ammonium asetat 1 N pH 7 + AAS/flame photometry |
   | pH | H₂O 1:2,5 |
   | EC | ekstrak 1:5 |

   Laboratorium di Yogyakarta: Fakultas Pertanian UGM atau BPTP DIY.

3. **Bangun model regresi koreksi per parameter.** Laporkan **R², RMSE, MAE, dan
   Bland-Altman** — bukan sekadar "akurasi %". Ini yang membuat hasil Anda lebih kuat
   daripada `UG-AgroPlan` yang hanya melaporkan persentase dan masih berstatus preprint.

4. **Masukkan kelembapan dan suhu tanah sebagai kovariat** dalam model. Ini menjawab
   langsung kritik "dependence on soil moisture" dan "temperature sensitivity".

5. **Uji drift mingguan.** Rendam probe di larutan EC standar 1413 µS/cm, catat
   penyimpangan. Kalibrasi pH dua titik dengan buffer 4,01 dan 6,86.

6. **Bersihkan probe berkala.** Flow cell EC SimonKori sudah penuh biofilm dan
   feedback-nya melenceng — jangan ulangi kesalahan itu.

Kalibrasi ini bisa dikerjakan dalam 17 hari dan **hasilnya langsung mengisi BAB IV**.
Ini pekerjaan dengan rasio hasil terhadap usaha tertinggi dalam seluruh rencana.

---

## 8. Kalibrasi pompa peristaltik

Ukur debit aktual (mL/detik) setiap pompa dengan gelas ukur dan stopwatch, minimal
3 pengulangan. Selang silikon **melar seiring waktu** sehingga debit menurun —
jadwalkan rekalibrasi bulanan dan catat sebagai bagian dari log sistem.

Tanpa ini, konversi dari dosis fuzzy ke durasi pompa tidak punya dasar, dan itu adalah
salah satu celah yang saat ini ada di proposal.
