# PRD: Skill Checkpoint dan Rollback untuk Hermes Agent

**Dokumen:** Analisis produk dan spesifikasi lengkap
**Tanggal:** 27 Oktober 2026
**Status:** Draft untuk review
**Produk:** Skill + plugin pendamping untuk Hermes Agent (Nous Research)
**Tujuan dokumen:** Menjadi satu-satunya acuan pembangunan skill checkpoint dan rollback yang sangat kompatibel dengan arsitektur Hermes, bukan sekadar skill prompt generik.

---

## 1. Ringkasan eksekutif

Vibe coding gagal bukan karena model tidak bisa menulis kode, melainkan karena tidak ada jaring pengaman saat model salah. Satu refactor yang gagal bisa menumpuk perbaikan di atas kode rusak sampai proyek tidak bisa kembali ke kondisi jalan. Skill ini memberi Hermes kemampuan save point: sebelum perubahan berisiko, kondisi proyek yang terakhir terbukti jalan disimpan. Setelah perubahan, build dan test diverifikasi. Kalau hijau, save point diperbarui. Kalau merah berulang, proyek dikembalikan ke save point terakhir dalam satu perintah.

Skill ini dirancang khusus untuk Hermes, bukan port mentah dari ekosistem lain. Ia memakai dua lapisan Hermes yang memang tersedia: SKILL.md prosedural berstandar agentskills.io untuk perilaku model, dan plugin tool deterministik lewat `ctx.register_tool()` untuk operasi yang tidak boleh salah (snapshot, verifikasi, rollback). State disimpan on-disk agar selamat dari hibernasi backend serverless Hermes.

## 2. Latar belakang dan masalah

### 2.1 Cara vibe coding gagal

1. **Perbaikan menumpuk.** Model memperbaiki error dengan mengubah kode yang sudah rusak, bukan kembali ke titik aman. Setiap iterasi menambah kerusakan.
2. **Tidak ada definisi selesai.** Tugas dianggap selesai karena kode "kelihatan jadi", padahal build atau test tidak pernah dijalankan.
3. **Kehilangan titik aman.** Pengguna tidak ingat commit mana yang terakhir jalan, sehingga pemulihan manual memakan waktu dan sering gagal.
4. **Perubahan diam-diam.** File di luar cakupan tugas ikut berubah (config, lockfile, file rahasia) tanpa disadari.
5. **Biaya loop tak terbatas.** Agent mengulang perbaikan tanpa batas sampai token dan waktu habis.

### 2.2 Bukti kelangkaan

Pencarian GitHub API pada 27 Oktober 2026 menunjukkan skill Hermes secara umum melimpah (6.466 repo untuk kata kunci "hermes skill"), tetapi tidak ada skill checkpoint dan rollback khusus Hermes (0 hasil untuk "hermes checkpoint rollback"). Skill loop otonom Hermes-native juga belum ada (13 hasil, mayoritas loop generik). Ini celah nyata, bukan sekadar variasi dari skill yang sudah ada.

### 2.3 Prinsip desain

1. **Biaya gagal harus mendekati nol.** Kalau rollback murah dan cepat, agent boleh ambisius.
2. **Keputusan kritis harus deterministik.** Snapshot, verifikasi, dan rollback dieksekusi kode, bukan dijanjikan prompt.
3. **Titik jalan terakhir adalah aset.** Last-known-good (LKG) tidak pernah dihapus otomatis.
4. **Transparan ke pengguna.** Setiap snapshot, verifikasi, dan rollback dilaporkan singkat ke chat atau dashboard.
5. **Aman secara default.** Rahasia tidak pernah masuk snapshot, log, atau konteks model.

## 3. Tujuan dan non-tujuan

### 3.1 Tujuan

1. Hermes bisa membuat checkpoint otomatis sebelum perubahan berisiko pada proyek apa pun.
2. Hermes memverifikasi hasil kerja dengan perintah build, test, atau lint yang bisa dikonfigurasi.
3. Hermes mengembalikan proyek ke checkpoint terakhir yang jalan dalam satu perintah, tanpa intervensi manual.
4. Pengguna selalu tahu checkpoint aktif, status verifikasi terakhir, dan riwayat rollback.
5. Skill berjalan di semua backend Hermes (lokal, Docker, SSH, Modal, Daytona, Vercel Sandbox) dan semua permukaan chat (Telegram, desktop, web).

### 3.2 Non-tujuan (versi 1)

1. Bukan sistem CI/CD dan bukan pengganti GitHub Actions.
2. Bukan backup cloud dan bukan sinkronisasi antar mesin.
3. Bukan visual review UI (skill terpisah, direncanakan setelah ini).
4. Bukan test generator (skill terpisah).
5. Bukan multi-repo monorepo orchestration. Satu proyek per checkpoint aktif.

## 4. Pengguna dan konteks pakai

### 4.1 Pengguna utama

Solo developer dan vibe coder yang menjalankan Hermes di VPS murah atau laptop, mengobrol lewat Telegram, dan mendelegasikan tugas coding berukuran sedang (satu fitur, satu refactor, satu perbaikan bug) tanpa mengawasi tiap langkah.

### 4.2 Skenario utama

1. **Refactor berisiko.** "Refactor modul auth." Hermes snapshot, refactor, test gagal tiga kali, rollback otomatis, lalu mencoba pendekatan berbeda dari titik aman.
2. **Fitur baru.** "Tambahkan halaman laporan." Hermes snapshot, implementasi, verifikasi hijau, snapshot diperbarui menjadi LKG baru.
3. **Perbaikan bug tengah malam.** Pengguna bangun dan melihat laporan: dua rollback terjadi, proyek tetap jalan, penyebab kegagalan tercatat.
4. **Eksperimen.** "Coba ganti library X." Kalau eksperimen gagal, satu perintah mengembalikan segalanya.

### 4.3 Skenario yang sengaja tidak didukung

1. Rollback sebagian file secara selektif (versi 1 hanya rollback penuh ke checkpoint).
2. Checkpoint lintas repo dalam satu perintah.
3. Pemulihan file yang belum pernah di-commit dan belum pernah masuk snapshot mana pun.

## 5. Analisis kompatibilitas Hermes

Bagian ini adalah inti PRD. Setiap keputusan desain dipetakan ke permukaan Hermes yang benar-benar ada, diverifikasi dari repo resmi pada 27 Oktober 2026.

### 5.1 Fakta arsitektur Hermes yang relevan

| Fakta | Sumber | Implikasi desain |
|---|---|---|
| Skills mengikuti standar terbuka agentskills.io, dikelola lewat Skills Hub, dan bisa dibuat otonom oleh agent | README hermes-agent | Skill ditulis sebagai SKILL.md standar, sehingga portabel dan bisa diimpor dari ekosistem lain |
| Agent punya learning loop: skill dibuat dari pengalaman dan membaik selama dipakai, plus memory antar sesi | README hermes-agent | Setiap rollback adalah data belajar: penyebab kegagalan dicatat ke memori agar tidak terulang |
| Plugin adalah direktori mandiri dengan `plugin.yaml` dan `register(ctx)`, dipasang ke `~/.hermes/plugins/`, diaktifkan dengan `hermes plugins enable` | hermes-example-plugins | Distribusi mengikuti pola resmi, tanpa fork Hermes |
| Tool model-callable didaftarkan lewat `ctx.register_tool()` dengan JSON Schema, validasi argumen, dan hasil terstruktur `tool_result()` / `tool_error()` | plugin-tool-example | Operasi kritis diekspos sebagai tool, bukan instruksi teks |
| Hook tersedia lewat `ctx.register_hook()`, contoh resmi `transform_llm_output` untuk masking data sensitif | plugin-hook-example | Masking rahasia dan audit bisa menempel di hook yang sama |
| Backend: lokal, Docker, SSH, Modal, Daytona, Vercel Sandbox; serverless bisa hibernasi saat idle | README hermes-agent | State harus on-disk di direktori proyek, bukan di memori proses |
| Permukaan chat: Telegram, desktop, web UI, plus dashboard plugin | README hermes-agent, example-dashboard | Status checkpoint harus bisa tampil di chat dan dashboard |
| Plugin inti yang sudah ada: memory provider, disk-cleanup hook, adapter platform | hermes-example-plugins README | Skill ini tidak perlu membangun ulang memory atau cleanup |

### 5.2 Pemetaan fitur ke permukaan Hermes

| Fitur skill | Permukaan Hermes | Alasan |
|---|---|---|
| Perilaku agent (kapan snapshot, kapan rollback, kapan berhenti) | SKILL.md (agentskills.io) di `~/.hermes/skills/` | Perilaku prosedural memang tempatnya di skill, dan bisa dibaca serta diperbaiki agent sendiri |
| Eksekusi snapshot, verifikasi, rollback | Plugin tool `ctx.register_tool()` | Operasi file dan git tidak boleh bergantung pada model mengetik perintah dengan benar |
| Validasi argumen dan hasil terstruktur | JSON Schema + `tool_result()` / `tool_error()` | Model menerima status mesin yang bisa dibaca, bukan teks bebas yang bisa disalahartikan |
| Masking rahasia di output | Hook `transform_llm_output` | Pola resmi yang sudah dicontohkan untuk SSN dan nomor kartu |
| Pencatatan kegagalan ke memori | Memory provider Hermes | Rollback menjadi bahan learning loop, bukan sekadar log mati |
| Status di dashboard | Dashboard plugin (manifest + slot) | Opsional versi 2, mengikuti pola example-dashboard |
| Instalasi | `~/.hermes/plugins/` + `hermes plugins enable` | Tanpa mengubah instalasi Hermes pengguna |

### 5.3 Keputusan arsitektur

**Keputusan 1: Dua lapisan, bukan satu.**
Lapisan skill (SKILL.md) mengatur keputusan: kapan snapshot wajib, berapa kali percobaan perbaikan, kapan rollback. Lapisan plugin mengatur eksekusi: perintah git, penyalinan file, dan pengujian. Alasannya: prompt bisa diabaikan model saat konteks panjang, sedangkan handler tool selalu berperilaku sama. Skill tanpa plugin akan gagal di perintah yang salah ketik. Plugin tanpa skill akan dipakai di waktu yang salah.

**Keputusan 2: Git sebagai backend snapshot utama.**
Git sudah ada di hampir semua proyek vibe coding, memberi diff, label, dan restore yang teruji. Checkpoint diimplementasikan sebagai commit khusus di branch atau ref terpisah (bukan branch kerja pengguna), sehingga riwayat kerja pengguna tidak tercemar. Untuk proyek tanpa git, plugin membuat snapshot arsip file (zip atau tar) di `.hermes-checkpoints/` dan tetap menolak bekerja tanpa konfirmasi eksplisit pengguna.

**Keputusan 3: State on-disk, bukan memori proses.**
Backend serverless Hermes bisa hibernasi. Semua state (checkpoint aktif, LKG, riwayat, lock) ditulis ke file JSON di direktori proyek (`.hermes-checkpoints/state.json`). Proses baru yang bangun dari hibernasi membaca state yang sama.

**Keputusan 4: Verifikasi sebagai gerbang, bukan saran.**
Promosi checkpoint menjadi LKG hanya terjadi setelah perintah verifikasi mengembalikan exit code 0. Tidak ada jalur "anggap saja lolos". Perintah verifikasi bisa dikonfigurasi per proyek (misalnya `npm test`, `pytest`, `cargo test`, atau gabungan build + lint + test).

**Keputusan 5: Satu checkpoint aktif per proyek, dengan lock.**
Tugas paralel pada proyek yang sama bisa saling menimpa snapshot. Plugin memakai file lock: tugas kedua yang mencoba snapshot saat lock aktif harus antre atau ditolak dengan pesan jelas, bukan diam-diam menimpa.

**Keputusan 6: Bahasa skill dalam Bahasa Inggris, laporan ke pengguna boleh Bahasa Indonesia.**
Model Hermes bekerja paling stabil dengan instruksi Bahasa Inggris. SKILL.md, nama tool, dan pesan error mesin ditulis Bahasa Inggris. Laporan ringkas ke pengguna (Telegram) boleh Bahasa Indonesia karena itu preferensi pengguna, dan teks laporan tidak memengaruhi logika.

### 5.4 Batasan Hermes yang memengaruhi desain

1. **Hibernasi backend.** Jangan menyimpan status di memori. Semua yang penting harus bisa dibaca ulang dari disk.
2. **Model bisa salah memanggil tool.** Handler harus memvalidasi argumen, menolak perintah berbahaya (misalnya rollback ke checkpoint milik proyek lain), dan mengembalikan error terstruktur.
3. **Konteks model terbatas.** Laporan tool harus ringkas: status, nama checkpoint, ringkasan diff (jumlah file, bukan isi penuh), dan langkah berikut. Diff penuh hanya dibuka atas permintaan eksplisit.
4. **Multi-permukaan.** Perintah chat harus bekerja identik di Telegram, desktop, dan web. Tidak ada fitur yang hanya ada di satu permukaan.
5. **Skill bisa dibuat agent sendiri.** SKILL.md harus ditulis agar mudah dibaca dan diperbaiki oleh Hermes sendiri (struktur jelas, contoh konkret, tanpa jargon yang tidak perlu).

## 6. Persyaratan fungsional

Setiap persyaratan disertai kriteria penerimaan yang bisa diuji.

### 6.1 Snapshot (FR-S)

- **FR-S1.** Sebelum perubahan berisiko (refactor, ganti dependensi, restrukturisasi), agent wajib memanggil snapshot. Kriteria: tidak ada perubahan file proyek tanpa checkpoint aktif, kecuali perubahan yang secara eksplisit ditandai pengguna sebagai aman.
- **FR-S2.** Snapshot berisi: label (format `cp-<tugas>-<nomor>`), waktu, daftar file berubah, hash commit atau arsip, dan perintah verifikasi yang berlaku. Kriteria: snapshot bisa diidentifikasi unik dan dijelajahi tanpa membuka isi file.
- **FR-S3.** File yang dikecualikan tidak pernah masuk snapshot: `.env`, file kunci, kredensial, `node_modules/`, direktori build, dan pola dari `.gitignore` plus `.checkpointignore` proyek. Kriteria: uji dengan file `.env` dummy membuktikan file tersebut tidak ada di snapshot mana pun.
- **FR-S4.** Snapshot gagal secara eksplisit bila git tidak tersedia dan mode arsip tidak diizinkan pengguna. Kriteria: pengguna menerima pesan jelas, bukan snapshot diam-diam yang kosong.
- **FR-S5.** Snapshot harus selesai dalam waktu wajar untuk proyek kecil-menengah (target di bawah 10 detik untuk proyek di bawah 500 file). Kriteria: diukur pada proyek uji.

### 6.2 Verifikasi (FR-V)

- **FR-V1.** Perintah verifikasi bisa dikonfigurasi per proyek di `.hermes-checkpoints/config.json` (default: deteksi otomatis `package.json` test, `pytest`, `cargo test`, atau `go test`). Kriteria: proyek tanpa perintah yang dikenali meminta pengguna menentukan perintah, bukan menebak.
- **FR-V2.** Verifikasi punya timeout default 300 detik dan bisa diubah. Kriteria: timeout mengembalikan status gagal yang jelas, bukan menggantung sesi.
- **FR-V3.** Hasil verifikasi terstruktur: lolos atau gagal, perintah yang dijalankan, durasi, dan ringkasan error (maks 20 baris terakhir output). Kriteria: model bisa memutuskan langkah berikut tanpa membaca log penuh.
- **FR-V4.** Verifikasi berjalan di backend yang sama dengan tempat kode diubah (lokal, Docker, SSH, atau sandbox). Kriteria: verifikasi tidak pernah menguji salinan kode yang berbeda.

### 6.3 Rollback (FR-R)

- **FR-R1.** Satu perintah mengembalikan proyek ke checkpoint terakhir yang jalan (LKG). Kriteria: setelah rollback, working tree sama dengan kondisi LKG dan verifikasi lolos.
- **FR-R2.** Rollback otomatis terjadi setelah percobaan perbaikan gagal sebanyak batas yang dikonfigurasi (default 3 kali). Kriteria: pada skenario uji, kegagalan ke-3 memicu rollback tanpa perintah pengguna.
- **FR-R3.** Rollback manual tersedia lewat perintah chat dan tool. Kriteria: pengguna bisa memicu rollback kapan pun, termasuk saat agent tidak sedang bekerja.
- **FR-R4.** Rollback tidak pernah menghapus checkpoint LKG. Kriteria: setelah rollback, LKG masih ada dan bisa dipilih lagi.
- **FR-R5.** Rollback yang gagal (misalnya file terkunci atau konflik) berhenti dengan pesan jelas dan tidak meninggalkan proyek dalam kondisi setengah. Kriteria: diuji dengan file terkunci buatan.

### 6.4 Riwayat dan retensi (FR-H)

- **FR-H1.** Pengguna bisa melihat daftar checkpoint: label, waktu, status verifikasi, dan apakah checkpoint tersebut LKG. Kriteria: daftar bisa dibaca dari chat maupun dashboard.
- **FR-H2.** Retensi default: simpan 10 checkpoint terakhir plus LKG. Checkpoint lebih tua dihapus otomatis. Kriteria: jumlah checkpoint tidak pernah tumbuh tanpa batas.
- **FR-H3.** Setiap rollback dicatat: waktu, checkpoint tujuan, dan alasan (otomatis atau manual, plus ringkasan error pemicu). Kriteria: laporan mingguan bisa menjawab "berapa kali rollback terjadi dan kenapa".

### 6.5 Keamanan dan privasi (FR-K)

- **FR-K1.** Snapshot, log, dan laporan tidak pernah berisi isi file rahasia. Kriteria: pemindaian snapshot dengan pola kunci API dan token tidak menemukan apa pun.
- **FR-K2.** Tool menolak argumen yang mengarah ke luar direktori proyek (path traversal). Kriteria: uji dengan `../` ditolak.
- **FR-K3.** Rollback tidak bisa menargetkan checkpoint milik proyek lain. Kriteria: setiap checkpoint terikat ke fingerprint direktori proyek.
- **FR-K4.** Audit log lokal mencatat siapa (sesi Hermes mana) dan kapan setiap snapshot, verifikasi, dan rollback terjadi. Kriteria: log bisa dibuka tanpa tool khusus (file JSON Lines).

### 6.6 Pelaporan dan konfigurasi (FR-P)

- **FR-P1.** Setiap kejadian penting dilaporkan singkat: snapshot dibuat, verifikasi lolos atau gagal, rollback terjadi. Kriteria: pengguna yang hanya membaca Telegram tetap tahu kondisi proyek.
- **FR-P2.** Konfigurasi per proyek: perintah verifikasi, batas percobaan, jumlah retensi, dan pola eksklusi tambahan. Kriteria: mengubah config tidak memerlukan instalasi ulang plugin.
- **FR-P3.** Mode kering (dry-run) tersedia: agent bisa melihat apa yang akan di-snapshot atau di-rollback tanpa mengeksekusi. Kriteria: dry-run tidak mengubah satu file pun.

## 7. Persyaratan non-fungsional

1. **Kinerja.** Snapshot proyek kecil-menengah di bawah 10 detik. Verifikasi tidak menambah overhead selain perintah itu sendiri.
2. **Reliabilitas.** Operasi snapshot dan rollback bersifat atomik sejauh yang dimungkinkan git: gagal di tengah jalan tidak merusak working tree di luar kondisi yang sudah dijelaskan di FR-R5.
3. **Portabilitas.** Berjalan di Linux, macOS, dan Windows sejauh git dan perintah verifikasi tersedia. Path ditangani lintas platform.
4. **Kompatibilitas.** Tidak memerlukan fork Hermes, tidak mengubah plugin inti, dan bisa dinonaktifkan penuh dengan `hermes plugins disable`.
5. **Observabilitas.** Semua kejadian tercatat di audit log lokal dengan format yang bisa dibaca mesin.
6. **Privasi.** Tidak ada data proyek yang dikirim ke server pihak ketiga oleh skill ini. Seluruh operasi lokal ke proyek pengguna.

## 8. Alur kerja dan state machine

```
IDLE (tidak ada checkpoint aktif)
  │  tugas berisiko dimulai
  ▼
SNAPSHOT ──gagal──▶ IDLE + pesan error jelas
  │  snapshot berhasil
  ▼
KERJA (agent mengubah kode)
  │  tugas selesai
  ▼
VERIFIKASI ──lolos──▶ PROMOSI (checkpoint menjadi LKG) ──▶ IDLE
  │  gagal
  ▼
PERBAIKAN (maks N kali, default 3)
  │  lolos ──▶ PROMOSI ──▶ IDLE
  │  gagal ke-N
  ▼
ROLLBACK ──berhasil──▶ IDLE + laporan (proyek kembali ke LKG)
  │  gagal ──▶ DARURAT (pesan jelas + instruksi manual, proyek tidak disentuh lagi)
```

Aturan transisi yang mengikat:

1. Tidak ada transisi KERJA tanpa SNAPSHOT.
2. PROMOSI hanya dari VERIFIKASI yang lolos.
3. ROLLBACK hanya menuju LKG, tidak menuju checkpoint sembarang.
4. DARURAT menghentikan agent mengubah proyek sampai pengguna memutuskan.

## 9. Model data

### 9.1 Record checkpoint (disimpan di `.hermes-checkpoints/state.json`)

| Field | Tipe | Keterangan |
|---|---|---|
| id | string | `cp-<tugas>-<nomor>`, unik per proyek |
| label | string | Label manusia, misalnya `sebelum-refactor-auth` |
| created_at | string (ISO 8601) | Waktu snapshot dibuat |
| backend | string | `git` atau `archive` |
| ref | string | Hash commit atau path arsip |
| files_changed | array string | Daftar path relatif yang berubah (bukan isi) |
| verify_command | string | Perintah verifikasi yang berlaku saat itu |
| verify_status | string | `pending`, `passed`, `failed` |
| is_lkg | boolean | True hanya untuk satu checkpoint per proyek |
| task | string | Deskripsi singkat tugas yang memicu snapshot |
| session | string | ID sesi Hermes pemicu |

### 9.2 Konfigurasi proyek (`.hermes-checkpoints/config.json`)

| Field | Default | Keterangan |
|---|---|---|
| verify_command | deteksi otomatis | Perintah verifikasi, bisa lebih dari satu dipisah `&&` |
| verify_timeout_sec | 300 | Batas waktu verifikasi |
| max_fix_attempts | 3 | Percobaan perbaikan sebelum rollback otomatis |
| retention_count | 10 | Checkpoint non-LKG yang disimpan |
| exclude_patterns | daftar bawaan + `.gitignore` | Pola tambahan yang dikecualikan dari snapshot |
| auto_rollback | true | Rollback otomatis saat batas percobaan tercapai |
| require_confirm_for_manual_rollback | false | Minta konfirmasi untuk rollback manual |

### 9.3 Audit log (`.hermes-checkpoints/audit.jsonl`)

Satu baris JSON per kejadian: waktu, sesi, jenis kejadian (`snapshot`, `verify`, `promote`, `rollback`, `cleanup`), checkpoint terkait, dan hasil. Tidak pernah berisi isi file atau diff penuh.

## 10. Antarmuka

### 10.1 Tool plugin (JSON Schema ringkas)

Tool-tool ini didaftarkan lewat `ctx.register_tool()` dalam satu toolset, misalnya `checkpoint`:

1. **checkpoint_create**: argumen: `task` (string, wajib), `label` (string, opsional). Hasil: id checkpoint, jumlah file, dan status.
2. **checkpoint_verify**: argumen: `command` (string, opsional, default dari config). Hasil: lolos atau gagal, durasi, ringkasan error.
3. **checkpoint_rollback**: argumen: `target` (string, opsional, default LKG). Hasil: status, checkpoint tujuan, dan daftar file dikembalikan (jumlah saja).
4. **checkpoint_list**: argumen: kosong. Hasil: daftar checkpoint dengan status dan penanda LKG.
5. **checkpoint_status**: argumen: kosong. Hasil: checkpoint aktif, LKG, verifikasi terakhir, dan jumlah rollback yang pernah terjadi.
6. **checkpoint_dry_run**: argumen: `action` (`snapshot` atau `rollback`). Hasil: apa yang akan terjadi tanpa eksekusi.

Semua handler memvalidasi argumen, menolak path di luar proyek, dan mengembalikan `tool_error()` terstruktur untuk kegagalan, bukan teks bebas.

### 10.2 Perintah chat

| Perintah | Fungsi |
|---|---|
| `/checkpoint` | Buat snapshot manual untuk tugas yang sedang berjalan |
| `/checkpoint list` | Tampilkan daftar checkpoint dan LKG |
| `/checkpoint status` | Tampilkan status checkpoint aktif dan verifikasi terakhir |
| `/rollback` | Kembalikan proyek ke LKG |
| `/rollback <label>` | Kembalikan proyek ke checkpoint berlabel (hanya bila checkpoint itu LKG atau disetujui pengguna) |

Perintah chat memanggil tool yang sama dengan yang dipakai model, sehingga perilaku identik di semua permukaan.

### 10.3 Kerangka SKILL.md

SKILL.md mengikuti standar agentskills.io dan berisi, secara berurutan: kapan skill aktif (tugas berisiko), aturan keras (tidak ada kerja tanpa snapshot, tidak ada promosi tanpa verifikasi lolos, rollback setelah N gagal), urutan langkah (snapshot, kerja, verifikasi, perbaikan, rollback), contoh percakapan singkat, dan daftar kesalahan umum (mengubah file di luar cakupan, melewati verifikasi, menghapus LKG). Panjang target: cukup lengkap untuk diikuti model tanpa membaca ulang, sekitar 150 sampai 250 baris.

## 11. Skenario uji dan kriteria penerimaan

| ID | Skenario | Hasil yang diharapkan |
|---|---|---|
| UT-01 | Tugas refactor, test hijau | Snapshot dibuat, promosi ke LKG, laporan singkat |
| UT-02 | Test gagal 3 kali | Rollback otomatis ke LKG, proyek lolos verifikasi, laporan berisi alasan |
| UT-03 | Rollback manual lewat chat | Proyek kembali ke LKG dalam satu perintah |
| UT-04 | Proyek tanpa git | Plugin menolak dengan pesan jelas, atau membuat arsip bila pengguna mengizinkan |
| UT-05 | File `.env` dummy di proyek | File tidak ada di snapshot mana pun |
| UT-06 | Dua tugas paralel di proyek yang sama | Tugas kedua antre atau ditolak, tidak menimpa snapshot tugas pertama |
| UT-07 | Verifikasi timeout | Status gagal yang jelas, percobaan dihitung, tidak menggantung sesi |
| UT-08 | Rollback dengan file terkunci | Berhenti aman, pesan jelas, proyek tidak setengah |
| UT-09 | Retensi | Setelah 12 checkpoint, hanya 10 terakhir plus LKG yang tersisa |
| UT-10 | Hibernasi backend | Sesi baru membaca state dari disk dan melanjutkan dari checkpoint yang benar |
| UT-11 | Path traversal | Argumen `../` ditolak oleh handler |
| UT-12 | Nonaktifkan plugin | `hermes plugins disable` menghentikan semua tool tanpa sisa proses |

## 12. Rencana rilis bertahap

**Fase 1, skill saja (minggu 1).** SKILL.md prosedural yang memakai perintah git langsung. Target: membuktikan alur snapshot, verifikasi, rollback pada proyek uji. Keterbatasan yang diakui: perintah masih bisa salah ketik model.

**Fase 2, plugin tool (minggu 2 sampai 3).** Plugin `checkpoint` dengan enam tool di atas, validasi argumen, audit log, dan lock konkurensi. Target: semua skenario UT-01 sampai UT-12 lolos.

**Fase 3, integrasi Hermes (minggu 4).** Pencatatan rollback ke memory Hermes agar learning loop menghindari pola kegagalan yang sama, laporan status ke dashboard, dan pengujian di backend Docker, SSH, dan Daytona. Target: perilaku identik di semua backend dan permukaan.

**Fase 4, pengerasan (minggu 5).** Uji proyek besar (di atas 2.000 file), uji Windows, dan dokumentasi pengguna Bahasa Indonesia. Target: tidak ada regresi kinerja dan tidak ada kebocoran rahasia.

## 13. Metrik keberhasilan

1. **Tingkat pemulihan.** Persentase tugas gagal yang kembali ke kondisi jalan tanpa intervensi manual. Target versi 1: di atas 95 persen pada proyek uji.
2. **Waktu rollback.** Median waktu dari perintah rollback sampai verifikasi lolos. Target: di bawah 30 detik untuk proyek kecil-menengah.
3. **Rasio rollback otomatis.** Jumlah rollback otomatis dibanding total tugas berisiko. Angka ini mengukur seberapa sering jaring pengaman benar-benar bekerja.
4. **Adopsi.** Jumlah instalasi plugin dan skill di Skills Hub dalam 30 hari pertama.
5. **Nol insiden rahasia.** Jumlah laporan kebocoran file rahasia lewat snapshot atau log. Target: nol, tanpa toleransi.

## 14. Risiko dan mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Model melewati snapshot dan langsung mengubah kode | Jaring pengaman tidak ada | SKILL.md menjadikan snapshot langkah pertama yang wajib; plugin menolak perubahan? Tidak bisa dipaksa di level file, jadi mitigasi kedua adalah hook pra-tugas bila tersedia dan pengingat keras di SKILL.md |
| Snapshot membesar pada proyek besar | Lambat dan boros disk | Pakai git sebagai backend utama; arsip hanya untuk proyek tanpa git; retensi otomatis |
| Verifikasi lama atau flaky | Rollback palsu atas test yang tidak stabil | Timeout bisa dikonfigurasi; perintah verifikasi dipisah antara build, lint, dan test agar sumber gagal jelas |
| Rollback menghapus pekerjaan pengguna yang tidak terkait | Kehilangan kerja | Rollback hanya mengembalikan file yang tercatat di checkpoint; file baru yang tidak terkait dilaporkan, bukan dihapus diam-diam |
| Perbedaan perilaku antar backend | Snapshot tidak portabel | State on-disk dan backend git; pengujian fase 3 di semua backend |
| Format skill agentskills.io berubah | SKILL.md usang | Ikuti spesifikasi terbaru saat implementasi; catat versi spesifikasi di header SKILL.md |
| Pengguna mengira skill ini backup | Kehilangan data di luar proyek | Dokumentasi menegaskan: ini save point proyek, bukan backup. Retensi terbatas dan lokal |

## 15. Pertanyaan terbuka (TBD sebelum implementasi)

1. Daftar lengkap hook Hermes yang tersedia (apakah ada hook pra-eksekusi tool atau hanya `transform_llm_output` yang sudah dicontohkan). Bila hanya transformasi output, enforcement snapshot tetap di SKILL.md.
2. Field frontmatter SKILL.md terbaru menurut spesifikasi agentskills.io (nama, deskripsi, dan field wajib lain) agar skill lolos validasi Skills Hub.
3. Perilaku hibernasi Daytona dan Modal terhadap file di `.hermes-checkpoints/` (apakah direktori proyek bertahan penuh saat bangun).
4. Apakah Hermes mengizinkan plugin menulis ke direktori proyek pengguna tanpa konfirmasi tambahan, atau perlu izin eksplisit di manifest.
5. Batas ukuran snapshot arsip yang wajar untuk proyek tanpa git sebelum plugin wajib menolak.

## 16. Sumber yang diverifikasi

1. Repo `NousResearch/hermes-agent` (README, struktur direktori, 27 Oktober 2026): skills agentskills.io, learning loop, backend lokal, Docker, SSH, Modal, Daytona, Vercel Sandbox, permukaan Telegram, desktop, web.
2. Repo `NousResearch/hermes-example-plugins` (README dan `plugin-tool-example`, 27 Oktober 2026): `plugin.yaml`, `register(ctx)`, `ctx.register_tool()`, JSON Schema, `tool_result()` dan `tool_error()`, `ctx.register_hook()`, instalasi `~/.hermes/plugins/` dan `hermes plugins enable`.
3. Pencarian GitHub API (27 Oktober 2026): "hermes skill" 6.466 repo, "hermes checkpoint rollback" 0 repo, "hermes ralph" 13 repo, "hermes visual review" 1 repo, "hermes test generation" 7 repo.

---

*Dokumen ini adalah draft. Bagian 15 harus ditutup sebelum fase 2 dimulai. Perubahan apa pun pada keputusan arsitektur di bagian 5 harus diperbarui di dokumen ini, bukan hanya di kode.*
