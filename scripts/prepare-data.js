// Menyalin data/*.json ke web/data agar disajikan sebagai berkas statis.
// Tidak gagal bila data/hsp.json belum ada (web akan memakai hsp.sample.json).
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const src = path.join(root, "data");
const dst = path.join(root, "web", "data");

fs.mkdirSync(dst, { recursive: true });

let copied = 0;
if (fs.existsSync(src)) {
  for (const f of fs.readdirSync(src)) {
    const full = path.join(src, f);
    if (f.endsWith(".json") && fs.statSync(full).isFile()) {
      fs.copyFileSync(full, path.join(dst, f));
      copied++;
      console.log("Disalin:", f);
    }
  }
}
if (!copied) console.warn("PERINGATAN: tidak ada data/*.json yang disalin.");
if (!fs.existsSync(path.join(dst, "hsp.json")))
  console.warn("Catatan: data/hsp.json belum ada, web memakai data contoh.");
console.log(`Selesai. ${copied} berkas JSON ada di web/data.`);
