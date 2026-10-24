# RAB-HSP Otomatis

Web untuk menyusun RAB berdasarkan HSP (Harga Satuan Pekerjaan) DKI Jakarta. Data koefisien dan harga acuan
diunduh dari <https://dcktrp.jakarta.go.id/bangunjakarta/hsp>.

## Struktur repo

```
.
├── web/index.html              # aplikasi RAB (statis)
├── data/hsp.sample.json        # data contoh
├── data/hsp.json               # data asli (dibuat oleh sinkronisasi, di-commit ke repo)
├── api/sync.js, status.js      # fungsi Vercel: tombol "Sinkronkan" -> memicu GitHub Actions
├── .github/workflows/sync-hsp.yml   # yang benar-benar mengambil data (Playwright)
├── vercel.json, .vercelignore, .gitignore
├── sync_hsp.py, hsp_api.py     # scraper (jalan di GitHub Actions atau lokal)
├── server.py                   # HANYA untuk pemakaian lokal
├── hsp_console.js              # cadangan: jalankan di Console browser
└── requirements.txt
```

## Cara kerja di GitHub + Vercel

Vercel bersifat serverless (tidak ada server yang terus hidup dan tidak ada browser untuk Playwright), jadi
pengambilan data dilakukan di **GitHub Actions**:

1. Workflow `Sinkron data HSP` berjalan tiap Minggu (atau manual) -> menjalankan `sync_hsp.py`.
2. Hasilnya `data/hsp.json` di-commit ke repo.
3. Vercel mendeteksi commit baru -> deploy ulang -> web menyajikan data terbaru.

### Pengaturan sekali saja

**Vercel (Project Settings)**
- Framework Preset: **Other**. Build/Output sudah diatur oleh `vercel.json`
  (menyalin `data/*.json` ke `web/data` dan menyajikan folder `web`).

**GitHub**
- Settings -> Actions -> General -> Workflow permissions: **Read and write permissions**.
- Jalankan pertama kali: tab **Actions** -> *Sinkron data HSP* -> **Run workflow**.
- Jika gagal, unduh artifact `hsp-debug` dan kirimkan isinya.

### Mengaktifkan tombol "Sinkronkan data terbaru" di web (opsional)

Tombol memanggil `api/sync.js`, yang memicu workflow lewat GitHub API. Di Vercel -> Settings -> Environment Variables:

| Nama | Isi |
|---|---|
| `GH_SYNC_TOKEN` | Fine-grained personal access token untuk repo ini, izin **Actions: Read and write** |
| `GH_REPO` | `usernameAnda/nama-repo` |
| `GH_BRANCH` | (opsional) cabang, bawaan `main` |

Pengaman: tombol ditolak bila sinkron sedang berjalan atau baru selesai < 10 menit. Setelah workflow sukses,
tunggu ±1 menit sampai Vercel selesai deploy, lalu muat ulang halaman.

Opsional: buat *Deploy Hook* di Vercel dan simpan sebagai secret GitHub `VERCEL_DEPLOY_HOOK` agar deploy
dipicu eksplisit setelah commit.

### Catatan penting
- Situs pemerintah kadang membatasi akses dari luar Indonesia. Runner GitHub berada di luar negeri; jika workflow
  gagal membuka situs (`ERR_` / timeout), jalankan sinkron dari komputer Anda lalu push hasilnya:
  ```bash
  python sync_hsp.py
  git add data/hsp.json && git commit -m "data HSP" && git push
  ```
  atau gunakan `hsp_console.js` di browser lalu simpan hasilnya sebagai `data/hsp.json`.
- Jika Vercel menolak deploy dari commit bot (paket Hobby), workflow sudah memakai nama/email akun Anda
  (`github.actor`); atau pakai Deploy Hook di atas.

## Pemakaian lokal

```bash
pip install -r requirements.txt
playwright install chromium
python server.py --auto-sync-days 7      # http://localhost:8000
```

Opsi `sync_hsp.py`: `--mode auto|api|dom`, `--limit 5`, `--no-detail`, `--headed`, `--debug`, `--max-minutes N`.
Mode `auto` mencoba endpoint data situs lebih dulu (cepat), lalu membaca tabel dan modal bila perlu.

## Fitur web

- **Katalog HSP**: cari/filter uraian, kategori, satuan; lihat detail koefisien (upah, bahan, alat).
- **RAB**: tambah pekerjaan, volume, harga otomatis dari HSP (bisa diganti manual), PPN, total, pembulatan, terbilang.
- **Rekap Upah & Bahan**, **Harga Acuan**, ekspor Excel/CSV/cetak.

## Catatan
- Gunakan secukupnya dan patuhi ketentuan situs sumber. Periksa ulang harga terhadap dokumen resmi yang berlaku
  sebelum dipakai untuk dokumen pengadaan.
