# Uji End-to-End di Laptop (TASKS F2.7)

Tujuan: edge agent (`hal: fake`) dan backend dev berbicara lewat **Mosquitto sungguhan**, tanpa
simulator backend. Dokumen ini dipakai ulang setiap fase menambah kemampuan.

## Apa yang bisa dibuktikan per fase

| Setelah | Yang terlihat di backend/dashboard |
|---|---|
| F2 | `JETSON-01` ONLINE (heartbeat 30 s). Perintah dari dashboard/API dibalas ACK → command berpindah dari SENT ke **FAILED** dengan pesan `NOT_READY` (eksekutor relay belum ada). Perintah kedaluwarsa → FAILED `EXPIRED` |
| F3.7 | 5 device ONLINE, angka sensor palsu tampil |
| **F4.6 (lulus 26 Okt)** | Perintah → ACK sukses → **EXECUTED**, state aktuator ikut berubah, durasi berhenti sendiri. Perintah yang melanggar interlock edge → FAILED dengan kode `INTERLOCK_*` |

F3.7 dan F4.6 sudah ada, jadi kriteria selesai F2.7 di TASKS (5 device ONLINE, angka tampil, EXECUTED)
bisa diuji penuh dengan langkah di bawah.

## Langkah

### 1. Broker

```powershell
docker ps --filter name=fertimata-mosquitto      # harus "Up", port 1883
```

Belum jalan → di folder backend: `docker compose up -d mosquitto`.

### 2. Matikan backend container (penting)

Container `fertimata-backend` di port 8080 berjalan dengan `MQTT_ENABLED=false` (dari `.env` lokal backend)
dan **simulator menyala**. Simulator itu menulis data untuk device yang sama ke database yang sama, jadi
akan ada dua sumber data. Untuk cara `mvnw` di langkah 3, hentikan dulu (cara container di langkah 3
menyalakannya ulang dengan setelan yang benar):

```powershell
docker stop fertimata-backend
```

### 3. Backend (terminal 1, folder backend)

**Cara singkat (sejak BE-5):** container backend dev tanpa simulator dan dengan broker sungguhan. Compose
membaca `.env` untuk `${MQTT_ENABLED:-true}`, dan variabel shell menang atas `.env`, jadi keduanya diset di
perintah. `.env` tidak masuk image (`.dockerignore`), sehingga topik di container memakai bawaan
`application.yml` (`fertimata/...`). Dikonfirmasi sesi backend 26 Okt:

```powershell
$env:DEV_SIMULATOR_ENABLED="false"; $env:MQTT_ENABLED="true"; docker compose up -d --build backend
```

Backend lalu ada di port 8080 (`SERVER_PORT` di compose); ganti `8090` di langkah berikutnya dengan 8080.

**Cara `mvnw` di port 8090** (bila perlu debug backend). Periksa dulu bahwa `MQTT_TOPIC_*` di `.env`
backend memakai `fertimata/{deviceId}/...`. `mvnw` membaca `.env` itu, dan sebelum 26 Okt isinya masih
`smartmelon/...`, sehingga backend tidak mendengar edge sama sekali.

Isi `DATABASE_*` sesuai `.env` backend. Postgres container dipublikasikan di port **55432**.

```powershell
$env:SPRING_PROFILES_ACTIVE="dev"
$env:MQTT_ENABLED="true"
$env:MQTT_BROKER_URL="tcp://localhost:1883"
$env:DEV_SIMULATOR_ENABLED="false"
$env:SERVER_PORT="8090"
$env:DATABASE_URL="jdbc:postgresql://localhost:55432/<nama-db-dari-.env>"
$env:DATABASE_USERNAME="<dari .env>"
$env:DATABASE_PASSWORD="<dari .env>"
.\mvnw.cmd spring-boot:run
```

Tunggu log `Started ...`. Login operator dev memakai nilai `BOOTSTRAP_OPERATOR_USERNAME`/`PASSWORD` di
`.env` backend (per 26 Okt `dev-operator-password` sudah tidak berlaku di database dev).

### 4. Intip broker (terminal 2)

```powershell
docker exec -it fertimata-mosquitto mosquitto_sub -t 'fertimata/#' -v
```

### 5. Edge agent (terminal 3, folder repo ini)

```powershell
.venv\Scripts\python.exe -m fertimata_edge --config config/fertimata.dev.yaml
```

Harus muncul `MQTT tersambung`, dan di terminal 2 `fertimata/JETSON-01/status {...agentVersion...}`.

### 6. Cek dari backend

```powershell
# token
$login = Invoke-RestMethod -Method Post -Uri http://localhost:8090/api/auth/login `
  -ContentType application/json -Body '{"username":"operator","password":"dev-operator-password"}'
$h = @{ Authorization = "Bearer $($login.accessToken)" }

Invoke-RestMethod -Uri http://localhost:8090/api/devices -Headers $h | Format-Table deviceCode, status, powerSource
Invoke-RestMethod -Uri http://localhost:8090/api/actuators -Headers $h | Format-Table id, code, deviceCode
```

- Kelima device harus ONLINE, `JETSON-01` dengan `powerSource` MAINS. Angka terbaru per metrik:
  `GET /api/sensors` lalu `GET /api/sensors/{id}/latest`. Di PowerShell 5.1 pakai
  `Invoke-WebRequest ... | ConvertFrom-Json`, karena `Invoke-RestMethod` membungkus array dengan aneh.

### 7. Perintah → ACK

Ambil `id` aktuator `STIRRER` dari langkah 6, lalu:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8090/api/actuators/<id>/commands -Headers $h `
  -ContentType application/json -Body '{"command":"ON","parameters":{"durationSeconds":10}}'
Invoke-RestMethod -Uri http://localhost:8090/api/actuators/<id>/commands -Headers $h
```

Yang diharapkan sejak F4.6:

1. Terminal 2: `fertimata/RIO-TANK-01/command {...}`, lalu `fertimata/RIO-TANK-01/status` dengan
   `"STIRRER":"ON"` dan `command/ack {"success":true,"actuatorState":"ON",...}`.
2. Log edge: `perintah diterima`, `output diubah ... cause=command limit_s=10.0`, `ACK dikirim`.
3. Riwayat command di backend: **EXECUTED**. 10 s kemudian log edge `output diubah ... cause=timer` dan
   status `"STIRRER":"OFF"`; state aktuator di dashboard ikut kembali OFF tanpa ACK baru.
4. Uji interlock edge: nyalakan `DOSING-N` 60 s, lalu `DOSING-P` → ACK gagal
   `INTERLOCK_EXCLUSIVE: ...` (kalau backend tidak menolaknya lebih dulu).

Catatan: backend mungkin menolak perintah sebelum dipublikasikan (interlock lapis 2). Edge sekarang
mengirim `tank.float_low_ok` tiap `polling.relays_s`, jadi interlock float backend seharusnya lolos
selama simulator `fake.float_low_ok: true`.

### 8. Selesai

Ctrl+C di terminal 3 dan 1. Matikan `java.exe` yang tersisa (Task Manager atau
`Get-Process java | Stop-Process`). Nyalakan lagi container bila perlu: `docker start fertimata-backend`.

## Uji otomatis tanpa backend

`pytest` sudah menjalankan `tests/test_mqtt_broker.py` melawan Mosquitto di `127.0.0.1:1883` (dilewati
bila broker mati): heartbeat, telemetri, ACK sukses, ACK `EXPIRED`, dan perintah ganda (QoS 1)
yang hanya dieksekusi dan di-ACK sekali. Topiknya memakai prefix acak sehingga tidak mengganggu
backend yang sedang jalan.
