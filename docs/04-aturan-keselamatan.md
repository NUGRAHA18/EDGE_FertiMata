# Aturan Keselamatan Edge Agent

Edge agent adalah **lapis pertama dan terakhir** sebelum relay. Backend punya interlock juga, tetapi
tidak berlaku kalau backend mati. Aturan di bawah ini **tidak boleh dilanggar oleh fitur apa pun**, dan
setiap aturan wajib punya test otomatis.

Sumber: Arsitektur §5.3, §5.4, §6, §7.4, §7.5, §9, §12, dan kebijakan interlock di katalog backend.

| # | Aturan | Implementasi di edge | Test |
|---|---|---|---|
| S1 | Saat boot, semua relay ditulis OFF lalu dibaca balik **sebelum** logika lain jalan | Langkah pertama `main`; poller/link belum mulai | boot dengan relay palsu yang ON |
| S2 | Setiap output punya batas durasi; edge mematikannya sendiri | Watchdog per output: `min(durationSeconds, max_on_s)` | timer habis → OFF + status + alert `OUTPUT_TIMEOUT` |
| S3 | `OFF`/STOP tidak pernah diblokir dan selalu didahulukan | Antrean bus berprioritas; STOP melompati antrean | STOP saat antrean penuh polling |
| S4 | Dosing (N/P/K) dan aduk **tidak bersamaan** dengan trolley jalan | `safety.exclusive` | ON dosing saat RUN ON → ditolak |
| S5 | Dosing dan aduk tidak bersamaan dengan pompa distribusi | idem | |
| S6 | Hanya satu pompa peristaltik ON dalam satu waktu | idem | |
| S7 | Saat `powerSource ≠ MAINS`: hentikan dan blokir dosing, aduk, trolley, pompa distribusi | `power` memicu `emergency_stop(group)` | simulasi PZEM padam |
| S8 | Float LOW terbuka (atau bacaan IN1 gagal/basi) → blokir pompa distribusi, dosing, aduk | `safety.float` | IN1 terbuka, IN1 timeout |
| S9 | Bacaan EC/pH tidak valid → dosing dilarang | `analog` menandai invalid (`AnalogReading.valid_for_dosing`), nilai invalid tidak dikirim di telemetri + `EC_PH_INVALID`. Sejak 28 Okt loop dosing milik layanan tim AI (lewat perintah mentah `DOSING-N/P/K`), jadi **layanan itu** yang wajib menahan dosis saat `tank.ec`/`tank.ph`/`tank.water_temperature` hilang atau basi (keputusan user 28 Okt: **sementara** S9 dijaga tim AI, lihat Q8 di TASKS). Edge tidak menolak perintah mentah berdasarkan bacaan larutan (belum pernah, juga sebelum F9 dihapus) | nilai di luar `analog.valid` → dibuang + `EC_PH_INVALID` |
| S10 | Arus di atas baseline saat R0 OFF → alert keras `SSR_SHORT` | `power` | |
| S11 | Arus jauh di bawah nominal saat R0 ON → matikan R0 + `PUMP_DRY_RUN` | `power` | |
| S12 | **DIR (R3) tidak pernah diubah saat RUN (R2) ON.** Jalan: R2 OFF → R3 → 100 ms → R2 ON. Berhenti: R2 OFF → 300 ms sebelum R3 boleh berubah | `trolley` + `safety` | perintah DIR saat RUN ON → ditolak |
| S13 | HOME/END aktif → motor berhenti; tidak ada jalan ke arah limit yang aktif | Event GPIO langsung memanggil STOP | |
| S14 | Selama trolley bergerak: polling lain ditunda, pan dilarang | bus manager mode "trolley" | |
| S15 | Batas perjalanan: segmen 10 s tanpa reed, total 60 s → berhenti + alert | `trolley` | |
| S16 | HOME dan END aktif bersamaan atau terus-menerus → kunci trolley (kabel putus) | `trolley` | |
| S17 | Perintah lewat `expiresAt` tidak dijalankan | `commands` | |
| S18 | **Jetson tidak pernah di-shutdown oleh software** | Tidak ada pemanggilan `shutdown`/`poweroff` di kode | grep di CI |
| S19 | Relay yang baca baliknya tidak cocok → coba OFF, alert `RELAY_MISMATCH`, tandai output rusak | `outputs` | |
| S20 | Proses edge mati → relay tidak boleh tertinggal ON tanpa batas | Mode nyala-berwaktu relay bila didukung (P-H4) + systemd restart → S1. Berhenti **normal** (SIGTERM, `systemctl stop`): semua relay OFF + baca balik sebelum keluar, dibatasi `agent.stop_off_timeout_s` (Q9) | berhenti dengan pompa ON → semua OFF; bus macet → berhenti tetap dalam batas waktu |

## Catatan penting

- Kalau modul relay **tidak** mendukung mode nyala-berwaktu (P-H4), S20 hanya bergantung pada
  restart systemd yang langsung menjalankan S1. Jetson hang total = pompa bisa tetap ON sampai hardware
  watchdog me-reboot. Risiko ini harus ditulis jujur di laporan.
- Tidak ada ambang agronomi (target pH/EC per fase) yang di-hardcode. Semuanya dari config atau dari
  perintah backend.
- Aturan yang hanya dicek di backend (role pengguna, `maxRunSeconds` yang diubah operator) tetap
  dicek ulang di edge dengan batas config edge. Pakai batas **yang lebih ketat** dari keduanya.
- **Pengecualian "OFF tidak pernah diblokir" (S3, Kontrak §5 aturan 2):** output arah, yaitu `TROLLEY-DIR` dan
  `CAMERA-PAN` (`safety.blocked_while_on`), tidak boleh berubah ON **maupun OFF** selama trolley jalan. OFF pada
  output ini berarti ganti arah, bukan berhenti, jadi S12/S14 yang berlaku. Untuk menghentikan trolley, kirim OFF ke
  `TROLLEY-RUN`, yang tidak pernah diblokir.
- Output yang state-nya tidak diketahui (relay tidak menjawab) dianggap **mungkin ON** saat memeriksa S4–S6 dan S12.
- Output yang baca baliknya pernah tidak cocok (S19) dikunci: ON ditolak `OUTPUT_FAULT`, OFF tetap boleh. Kunci
  dibuka dengan me-restart agent setelah panel diperiksa.
- Test per aturan: `tests/test_outputs.py` (S1, S2, S8 reaksi, S14 jeda polling, S19) dan `tests/test_safety.py`
  (S3–S6, S8, S12, S13, S14, S16). S7 dan S10/S11 di `tests/test_power.py`, S9 di `tests/test_analog.py`,
  S12/S13/S15/S16 untuk gerak trolley di `tests/test_trolley.py`.
  S17 di `tests/test_link.py`, S18 di `tests/test_codebase_rules.py`, S20 (berhenti normal, Q9) di
  `tests/test_app.py`.
