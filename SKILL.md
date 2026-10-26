---
name: SnaPeka
description: Checkpoint dan rollback untuk Hermes Agent. Buat save point sebelum perubahan berisiko, verifikasi sesudahnya, dan kembalikan proyek ke titik jalan terakhir (LKG) dalam satu perintah kalau perbaikan gagal 3 kali. Memakai git langsung (fase 1, tanpa plugin).
---

# SnaPeka

Skill checkpoint dan rollback untuk Hermes Agent. Prinsipnya satu kalimat:
**tidak ada kerja berisiko tanpa save point, tidak ada promosi tanpa verifikasi
lolos, dan rollback selalu menuju titik jalan terakhir (LKG).**

Fase 1 ini prosedural dan memakai perintah git langsung. Tidak ada tool plugin,
tidak ada server, tidak ada data yang dikirim ke pihak ketiga. Semua operasi
lokal di proyek pengguna.

## Kapan skill ini aktif

Aktif untuk tugas berisiko pada proyek apa pun:

- Refactor, restrukturisasi, migrasi, dan perubahan multi-file.
- Ganti dependensi, ganti framework, atau ubah konfigurasi build.
- Tugas yang menyentuh kode yang sedang jalan di produksi atau demo.

Tidak aktif untuk tugas baca saja (menjelajah kode, menjawab pertanyaan,
menulis dokumentasi tanpa mengubah kode) dan untuk perubahan yang pengguna
secara eksplisit tandai sebagai aman.

## Aturan keras

Aturan ini tidak boleh dilanggar dengan alasan apa pun, termasuk saat pengguna
mendesak agar cepat selesai.

1. **Tidak ada kerja tanpa snapshot.** Sebelum mengubah satu file pun dalam
   tugas berisiko, buat checkpoint dulu. Kalau snapshot gagal, berhenti dan
   laporkan alasannya. Jangan mulai kerja dengan janji snapshot menyusul.
2. **Tidak ada promosi tanpa verifikasi lolos.** Checkpoint hanya menjadi LKG
   setelah perintah verifikasi lolos. Verifikasi yang tidak dijalankan sama
   dengan verifikasi gagal.
3. **Rollback otomatis setelah 3 kali gagal.** Perbaikan yang gagal 3 kali
   berturut-turut memicu rollback ke LKG tanpa menunggu perintah pengguna.
   Batas ini bisa diubah di config (`max_fix_attempts`), default 3.
4. **Rollback hanya menuju LKG.** Jangan rollback ke checkpoint sembarang,
   kecuali pengguna secara eksplisit menyetujui targetnya lewat perintah
   `/rollback <label>` dan checkpoint itu adalah LKG atau disetujui pengguna.
5. **LKG tidak pernah dihapus.** Retensi boleh menghapus checkpoint lama,
   tetapi LKG selalu dipertahankan, bahkan saat rollback sudah terjadi.
6. **Rahasia tidak pernah masuk snapshot.** File `.env`, file kunci,
   kredensial, `node_modules/`, direktori build, dan pola `.gitignore` tidak
   boleh masuk snapshot, log, atau laporan. Kalau file rahasia terlihat di
   `git status` dan belum diabaikan `.gitignore`, batalkan snapshot dan minta
   pengguna menambahkannya ke `.gitignore` dulu.
7. **Jangan ubah file di luar cakupan tugas.** Kalau tugas tidak menyebut
   config atau lockfile, jangan sentuh file itu. Laporkan bila perubahan
   terpaksa melebar.
8. **Jangan menebak perintah verifikasi.** Pakai perintah dari
   `.hermes-checkpoints/config.json` bila ada. Bila tidak ada, deteksi otomatis
   (`package.json` test, `pytest`, `cargo test`, `go test`). Bila tidak ada
   yang dikenali, tanya pengguna, jangan mengarang perintah.
9. **Verifikasi punya batas waktu.** Default 300 detik (`verify_timeout_sec`).
   Timeout berarti gagal, bukan menggantung sesi.
10. **Retensi terbatas.** Simpan 10 checkpoint terakhir plus LKG. Checkpoint
    yang lebih tua dihapus dari catatan state (commit git-nya tetap ada di
    riwayat, tidak ditulis ulang).
11. **Dry-run tidak mengubah apa pun.** Mode kering hanya menampilkan apa yang
    akan terjadi.
12. **Keadaan darurat menghentikan semua kerja.** Bila rollback gagal (file
    terkunci, konflik), berhenti total, beri pesan jelas dan instruksi manual,
    dan jangan menyentuh proyek lagi sampai pengguna memutuskan.

## State dan config

Semua state tinggal di dalam proyek, di direktori `.hermes-checkpoints/`:

- `state.json`: daftar checkpoint. Satu record berisi `id` (format
  `cp-<tugas>-<nomor>`), `label`, `created_at` (ISO 8601), `backend`
  (`git` atau `archive`), `ref` (hash commit atau path arsip),
  `files_changed` (daftar path relatif, bukan isi file), `verify_command`,
  `verify_status` (`pending`, `passed`, `failed`), `is_lkg` (true hanya untuk
  satu checkpoint per proyek), `task`, dan `session`.
- `config.json`: `verify_command`, `verify_timeout_sec` (default 300),
  `max_fix_attempts` (default 3), `retention_count` (default 10),
  `exclude_patterns`, `auto_rollback` (default true),
  `require_confirm_for_manual_rollback` (default false). Bila file belum ada,
  buat dengan nilai default ini.
- `audit.jsonl`: satu baris JSON per kejadian (`snapshot`, `verify`,
  `promote`, `rollback`, `cleanup`) berisi waktu, sesi, checkpoint terkait,
  dan hasil. Tidak pernah berisi isi file atau diff penuh.

Checkpoint terikat ke proyeknya lewat direktori kerja. Jangan pernah memakai
checkpoint proyek lain, dan tolak argumen path yang keluar dari direktori
proyek (misalnya `../`).

## Urutan langkah

Ikuti state machine ini persis, tanpa lompatan:

```
IDLE -> SNAPSHOT -> KERJA -> VERIFIKASI -> PROMOSI -> IDLE
                                   |
                                gagal
                                   v
                        PERBAIKAN (maks 3 kali)
                           |             |
                        lolos        gagal ke-3
                           v             v
                       PROMOSI       ROLLBACK -> IDLE + laporan
                                          |
                                     gagal rollback
                                          v
                                       DARURAT
```

### Langkah 1: SNAPSHOT

1. Pastikan ini repo git: `git rev-parse --is-inside-work-tree`. Bila bukan
   repo git, berhenti dengan pesan jelas. Mode arsip hanya bila pengguna
   secara eksplisit mengizinkan (lihat "Proyek tanpa git" di bawah).
2. Catat perintah verifikasi yang berlaku (dari config atau hasil deteksi).
3. Periksa rahasia: jalankan `git status --porcelain`. Bila ada file rahasia
   (`.env`, kunci, kredensial) yang akan ikut ditambahkan dan belum diabaikan
   `.gitignore`, batalkan snapshot dan minta pengguna menambahkannya ke
   `.gitignore`.
4. Bila working tree bersih, snapshot adalah commit HEAD saat ini: catat
   hash-nya sebagai `ref`, tanpa membuat commit baru.
5. Bila working tree kotor, commit kondisi pra-tugas sebagai snapshot:
   `git add -A` (menghormati `.gitignore`), lalu
   `git commit -m "snapeka: cp-<tugas>-<nomor> <label>"`. Commit ini adalah
   penanda yang jelas, bukan commit kerja biasa.
6. Tulis record checkpoint ke `state.json` dengan `verify_status: pending`
   dan `is_lkg: false`. Tambah satu baris `snapshot` ke `audit.jsonl`.
7. Laporkan singkat ke pengguna: id checkpoint, jumlah file, dan perintah
   verifikasi yang berlaku.

### Langkah 2: KERJA

Kerjakan tugas seperti biasa, dengan batas: hanya file dalam cakupan tugas.
Bila cakupan terpaksa melebar, laporkan dulu sebelum melanjutkan.

### Langkah 3: VERIFIKASI

1. Jalankan perintah verifikasi di backend yang sama dengan tempat kode
   diubah (lokal, Docker, SSH, atau sandbox yang sama). Jangan pernah menguji
   salinan kode yang berbeda.
2. Terapkan timeout: `timeout <verify_timeout_sec> <perintah>`. Timeout
   berarti gagal.
3. Catat hasil terstruktur: lolos atau gagal, perintah yang dijalankan,
   durasi, dan ringkasan error (maks 20 baris terakhir output). Model tidak
   perlu membaca log penuh untuk memutuskan langkah berikut.
4. Bila lolos, lanjut ke PROMOSI. Bila gagal, lanjut ke PERBAIKAN.

### Langkah 4: PERBAIKAN (maks 3 kali)

Setiap percobaan: ubah kode untuk memperbaiki error, lalu jalankan VERIFIKASI
lagi. Hitung setiap percobaan. Bila lolos, lanjut ke PROMOSI. Bila gagal
ke-3, lanjut ke ROLLBACK otomatis (bila `auto_rollback` true; bila false,
laporkan dan tunggu perintah pengguna).

### Langkah 5: PROMOSI

1. Ubah record checkpoint di `state.json`: `verify_status: passed`,
   `is_lkg: true`. Checkpoint LKG sebelumnya menjadi `is_lkg: false`.
2. Tambah baris `promote` ke `audit.jsonl`.
3. Terapkan retensi: sisakan 10 checkpoint terakhir plus LKG, hapus record
   yang lebih tua dari `state.json` (tambah baris `cleanup` ke audit log).
4. Laporkan singkat: tugas selesai, checkpoint menjadi LKG.

### Langkah 6: ROLLBACK

1. Target default adalah LKG. Target lain hanya lewat `/rollback <label>`
   dengan persetujuan pengguna.
2. Kembalikan file terlacak ke kondisi LKG: `git reset --hard <ref-LKG>`.
   Perintah ini tidak menyentuh file tak terlacak; setelah reset, laporkan
   file tak terlacak yang tersisa dari `git status --porcelain` agar pengguna
   memeriksanya sendiri. Jangan menjalankan `git clean` tanpa perintah
   eksplisit pengguna.
3. Jalankan VERIFIKASI sekali lagi. Bila lolos, tulis record rollback ke
   `state.json` (bila perlu) dan baris `rollback` ke `audit.jsonl` berisi
   waktu, checkpoint tujuan, dan alasan (otomatis atau manual, plus ringkasan
   error pemicu). LKG tetap ada dan tetap bisa dipilih lagi.
4. Laporkan singkat: proyek kembali ke LKG, verifikasi lolos, dan alasan
   rollback.
5. Bila rollback gagal (file terkunci, konflik): masuk DARURAT. Berhenti
   total, beri pesan jelas dan instruksi manual, jangan menyentuh proyek
   lagi sampai pengguna memutuskan.

## Dry-run

- Dry-run snapshot: tampilkan daftar file dari `git status --porcelain` yang
  akan masuk snapshot, tanpa `git add` atau `git commit`.
- Dry-run rollback: tampilkan checkpoint LKG tujuan dan perintah yang akan
  dijalankan (`git reset --hard <ref>`), tanpa mengeksekusinya.

## Proyek tanpa git

Bila proyek bukan repo git dan pengguna mengizinkan mode arsip:

1. Buat arsip snapshot dengan pengecualian rahasia, misalnya
   `tar --exclude=.git --exclude=.env --exclude=node_modules -czf
   .hermes-checkpoints/archive-<id>.tgz .` dari dalam direktori proyek.
2. Catat `backend: archive` dan `ref` berupa path arsip di `state.json`.
3. Rollback arsip: ekstrak arsip ke direktori kosong, bandingkan, dan
   kembalikan file dengan persetujuan pengguna (jangan menimpa buta-buta).

Bila pengguna tidak mengizinkan mode arsip, snapshot gagal secara eksplisit
dengan pesan jelas. Jangan pernah membuat snapshot diam-diam yang kosong.

## Perintah chat

Perintah ini memanggil prosedur yang sama dengan yang dipakai model, sehingga
perilaku identik di semua permukaan:

| Perintah | Fungsi |
|---|---|
| `/checkpoint` | Buat snapshot manual untuk tugas yang sedang berjalan |
| `/checkpoint list` | Tampilkan daftar checkpoint dan LKG |
| `/checkpoint status` | Tampilkan status checkpoint aktif dan verifikasi terakhir |
| `/rollback` | Kembalikan proyek ke LKG |
| `/rollback <label>` | Kembalikan proyek ke checkpoint berlabel (hanya bila checkpoint itu LKG atau disetujui pengguna) |

## Contoh percakapan singkat

Pengguna: refactor modul auth, tapi jangan sampai merusak login yang jalan.

Agent: Membuat snapshot dulu: `cp-refactor-auth-001` (12 file, verifikasi
`npm test`). Lalu refactor. Verifikasi gagal 2 kali, perbaikan ketiga lolos.
Checkpoint dipromosikan menjadi LKG. Selesai.

Pengguna: kok login masih rusak setelah refactor kemarin?

Agent: Verifikasi gagal 3 kali, jadi rollback otomatis ke LKG
`cp-refactor-auth-001`. Proyek kembali ke kondisi jalan dan verifikasi lolos.
Perubahan refactor yang gagal tidak dipertahankan.

## Kesalahan umum

- Mengubah file di luar cakupan tugas tanpa melapor.
- Melewati verifikasi atau mempromosikan checkpoint yang verifikasinya
  tidak lolos.
- Menghapus atau menimpa LKG.
- Membiarkan file rahasia masuk snapshot, log, atau laporan.
- Mengulang perbaikan tanpa batas (lebih dari 3 kali) alih-alih rollback.
- Menguji salinan kode yang berbeda dari tempat kode diubah.
- Membuat snapshot kosong diam-diam saat git tidak tersedia.

## Instalasi (fase 1)

Salin direktori skill ini ke `~/.hermes/skills/snapeka/` sehingga file ini
berada di `~/.hermes/skills/snapeka/SKILL.md`. Tidak ada langkah lain.
Dokumen acuan lengkap (PRD) ada di `docs/` repo SnaPeka.
