/*
 * hsp_console.js - ambil data HSP LANGSUNG dari browser Anda (tanpa Playwright/Python).
 *
 * Cara pakai:
 *   1. Buka https://dcktrp.jakarta.go.id/bangunjakarta/hsp di Chrome/Edge, tunggu tabel terisi.
 *   2. Tekan F12 -> tab Console. (Jika Chrome meminta, ketik:  allow pasting  lalu Enter.)
 *   3. Tempel SELURUH isi berkas ini, tekan Enter.
 *   4. Panel kecil di kanan atas menampilkan progres. Setelah selesai, hsp.json terunduh otomatis.
 *   5. Di web RAB klik "Impor hsp.json" dan pilih berkas itu (atau taruh di folder data/).
 *
 * Uji coba cepat: sebelum menempel, ketik di Console   window.HSP_LIMIT = 5
 * Hanya daftar tanpa detail:                          window.HSP_NODETAIL = true
 * Menghentikan di tengah jalan:                        window.HSP_STOP = true  (hasil sebagian tetap diunduh)
 */
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const clean = s => (s || "").replace(/\s+/g, " ").trim();
  const LIMIT = window.HSP_LIMIT || 0, NODETAIL = !!window.HSP_NODETAIL;
  window.HSP_STOP = false;

  // ---------- panel progres ----------
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;top:10px;right:10px;z-index:2147483647;background:#111;color:#fff;" +
    "font:12px/1.4 monospace;padding:10px 12px;border-radius:8px;max-width:360px;box-shadow:0 4px 18px #0008";
  document.body.appendChild(box);
  const say = t => { box.textContent = t; console.log("[HSP]", t); };

  // ---------- parsing angka format Indonesia ----------
  function parseIdr(text) {
    if (text == null) return null;
    let s = String(text).trim().replace(/[^0-9,.\-]/g, "");
    if (!s || ["-", ".", ","].includes(s)) return null;
    if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
    else {
      const p = s.split(".");
      if (p.length > 2 || (p.length === 2 && p[1].length === 3 && !["0", ""].includes(p[0]))) s = s.replace(/\./g, "");
    }
    const v = parseFloat(s); return Number.isFinite(v) ? v : null;
  }
  function fixKoef(txt, k, h, j) {          // '1.250' = 1,25 atau 1250? pilih yang cocok dgn koef x harga = jumlah
    if (k == null || h == null || j == null) return k;
    const t = (txt || "").replace(/[^0-9.,]/g, "");
    if (t.includes(",") || (t.match(/\./g) || []).length !== 1) return k;
    const alt = parseFloat(t); if (!Number.isFinite(alt)) return k;
    const err = x => Math.abs(x * h - j) / Math.max(Math.abs(j), 1);
    return err(alt) < err(k) ? alt : k;
  }
  const groupOf = h => { h = h.toLowerCase();
    if (/tenaga|upah/.test(h)) return "upah"; if (/bahan|material/.test(h)) return "bahan";
    if (/alat|peralatan/.test(h)) return "alat"; return null; };

  function parseDetail(rows) {
    const out = []; let grup = null;
    for (const r of rows) {
      const c = r.map(clean); if (!c.some(Boolean)) continue;
      while (c.length < 7) c.push("");
      const [no, uraian, kode, satuan, koef, harga, jumlah] = c;
      const hv = parseIdr(harga), jv = parseIdr(jumlah);
      const kv = fixKoef(koef, parseIdr(koef), hv, jv);
      if (kv == null) {
        const judul = uraian || no, g = groupOf(judul);
        if (g && jv == null && hv == null) grup = g;
        out.push({ tipe: "baris", grup, no, uraian: judul, jumlah_harga: jv ?? hv });
        continue;
      }
      out.push({ tipe: "komponen", grup, no, uraian, kode, satuan, koefisien: kv, harga_satuan: hv, jumlah_harga: jv });
    }
    return out;
  }
  function hargaAcuan(items) {
    const seen = new Map();
    for (const it of items) for (const d of it.detail) {
      if (d.tipe !== "komponen") continue;
      const key = d.kode || d.uraian;
      if (!seen.has(key)) seen.set(key, { kode: d.kode || "", uraian: d.uraian, satuan: d.satuan || "", harga: d.harga_satuan, grup: d.grup });
    }
    return [...seen.values()].sort((a, b) => (a.grup || "z").localeCompare(b.grup || "z") ||
      (a.kode || "").localeCompare(b.kode || "") || a.uraian.localeCompare(b.uraian));
  }

  // ---------- DOM ----------
  const mainTable = () => {
    let best = null, n = -1;
    for (const t of document.querySelectorAll("table")) {
      if (t.closest(".modal")) continue;
      const c = t.querySelectorAll("tbody tr").length; if (c > n) { n = c; best = t; }
    }
    return best;
  };
  const EMPTY = /no data|tidak ada data|loading|memuat|processing|sedang/i;
  const rows = () => {
    const t = mainTable(); if (!t) return [];
    return [...t.querySelectorAll("tbody tr")].map(tr => ({ tr, cells: [...tr.children].map(td => clean(td.innerText)) }))
      .filter(r => r.cells.length >= 5 && !EMPTY.test(r.cells.join(" ")) && !r.tr.querySelector("td.dataTables_empty, td.dt-empty"));
  };
  const signature = () => {
    const t = mainTable(), trs = t ? [...t.querySelectorAll("tbody tr")] : [];
    const tx = tr => tr ? clean(tr.innerText) : "";
    const info = document.querySelector(".dataTables_info, .dt-info, [id$=_info]");
    return trs.length + "|" + tx(trs[0]) + "|" + tx(trs[trs.length - 1]) + "|" + (info ? info.innerText : "");
  };
  async function settle(max = 8000) {
    const end = Date.now() + max; let last = null, same = 0;
    while (Date.now() < end) { const s = signature(); same = s === last ? same + 1 : 0; if (same >= 2) return; last = s; await sleep(350); }
  }
  async function waitRows(max) { const end = Date.now() + max; while (Date.now() < end) { if (rows().length) return true; await sleep(300); } return false; }

  async function setLengthMax() {
    const s = document.querySelector("select[name$='_length'], .dataTables_length select, .dt-length select");
    if (!s) return;
    const vals = [...s.options].map(o => o.value), nums = vals.filter(v => /^-?\d+$/.test(v)).map(Number);
    if (!nums.length) return;
    const best = vals.includes("-1") ? "-1" : String(Math.max(...nums)), before = signature();
    s.value = best; s.dispatchEvent(new Event("change", { bubbles: true }));
    const end = Date.now() + 6000; while (Date.now() < end && signature() === before) await sleep(250);
    await settle();
  }

  async function goNext() {
    const before = signature();
    const sels = [".paginate_button.next", ".dt-paging-button.next", "li.next", "a.next", "button.next", "[aria-label='Next']", "[data-dt-idx='next']", "a[rel='next']"];
    let el = null; for (const s of sels) { el = document.querySelector(s); if (el) break; }
    if (!el) for (const c of document.querySelectorAll(".pagination a, .pagination button, .dataTables_paginate a, .dataTables_paginate button, .dt-paging button"))
      if (/^(next|selanjutnya|berikutnya|lanjut|›|»|>)$/i.test(clean(c.innerText))) { el = c; break; }
    if (!el) return false;
    const dis = n => !!n && (n.classList.contains("disabled") || n.hasAttribute("disabled") || n.getAttribute("aria-disabled") === "true");
    const holder = el.closest("li") || el, inner = el.matches("li") ? (el.querySelector("a,button") || el) : el;
    if (dis(el) || dis(holder) || dis(inner)) return false;
    inner.click();
    const end = Date.now() + 8000;
    while (Date.now() < end) { await sleep(200); if (signature() !== before) { await settle(4000); return true; } }
    return false;               // diklik tapi tidak berubah -> halaman terakhir
  }

  // ---------- modal detail ----------
  const visibleModal = () => [...document.querySelectorAll(".modal")].filter(m => {
    const r = m.getBoundingClientRect(), s = getComputedStyle(m);
    return s.display !== "none" && s.visibility !== "hidden" && r.height > 0; }).pop() || null;
  const modalRows = () => {
    const m = visibleModal(); if (!m) return null;
    const t = m.querySelector("table"); if (!t) return null;
    const r = [...t.querySelectorAll("tbody tr")].map(tr => [...tr.children].map(td => clean(td.innerText)))
      .filter(c => c.length >= 4 && !EMPTY.test(c.join(" ")));
    return r.length ? r : null;
  };
  async function closeModal() {
    const m = visibleModal();
    if (m) { const b = m.querySelector("[data-bs-dismiss='modal'],[data-dismiss='modal'],.btn-close,button.close,.btn-secondary"); if (b) b.click(); }
    const end = Date.now() + 1500; while (Date.now() < end && visibleModal()) await sleep(100);
    if (visibleModal() || document.querySelector(".modal-backdrop")) {
      try { if (window.jQuery && window.jQuery.fn.modal) window.jQuery(".modal").modal("hide"); } catch (e) {}
      document.querySelectorAll(".modal").forEach(x => { x.classList.remove("show", "in"); x.style.display = "none"; x.setAttribute("aria-hidden", "true"); });
      document.querySelectorAll(".modal-backdrop").forEach(b => b.remove());
      document.body.classList.remove("modal-open"); document.body.style.overflow = ""; document.body.style.paddingRight = "";
    }
  }
  let prevSig = "";
  async function fetchDetail(tr) {
    const b = tr.querySelector("td:last-child a, td:last-child button, td:last-child .btn, td:last-child [role=button]") ||
      tr.querySelector("a, button, .btn");
    if (!b) return null;
    b.click();
    const t0 = Date.now(); let stable = null, n = 0;
    while (Date.now() - t0 < 10000) {
      await sleep(150);
      const r = modalRows(); if (!r) continue;
      const sig = JSON.stringify(r);
      if (sig === prevSig && Date.now() - t0 < 1500) continue;
      if (sig === stable) n++; else { stable = sig; n = 0; }
      if (n >= 1 && Date.now() - t0 >= 300) { prevSig = sig; return r; }
    }
    return null;
  }

  // ---------- jalan ----------
  const items = [], seen = new Set(), failed = [];
  const full = () => LIMIT && items.length >= LIMIT;
  const t0 = Date.now();
  try {
    say("Menyiapkan tabel…");
    if (!(await waitRows(10000))) { say("Tabel kosong. Pilih kategori di halaman lalu jalankan ulang skrip."); return; }
    await setLengthMax();
    let pages = 0;
    outer: while (pages < 1000) {
      pages++;
      for (const r of rows()) {
        if (window.HSP_STOP || full()) break outer;
        const [no, uraian, satuan, kategori, harga] = r.cells, key = `${uraian}|${satuan}|${kategori}`;
        if (seen.has(key)) continue; seen.add(key);
        const it = { no, uraian, satuan, kategori, harga: parseIdr(harga), detail: [] };
        items.push(it);
        if (!NODETAIL) {
          let d = null;
          for (let a = 0; a < 2 && !d; a++) { try { d = await fetchDetail(r.tr); } catch (e) { d = null; } await closeModal(); }
          if (d) it.detail = parseDetail(d); else failed.push(uraian);
        }
        say(`Halaman ${pages} | HSP ${items.length} | gagal detail ${failed.length}\nKetik window.HSP_STOP=true untuk berhenti`);
      }
      if (!(await goNext())) break;
    }
  } catch (e) { console.error(e); say("Error: " + e.message + " — mengunduh hasil sebagian."); }

  const wib = new Date(Date.now() + 7 * 3600e3).toISOString().replace("Z", "+07:00").replace(/\.\d+/, "");
  const data = { meta: { sumber: location.href, diambil_pada: wib, jumlah_hsp: items.length,
    jumlah_hsp_dengan_detail: items.filter(i => i.detail.length).length }, items, harga_acuan: hargaAcuan(items) };
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 1)], { type: "application/json" }));
  a.download = "hsp.json"; document.body.appendChild(a); a.click(); a.remove();
  say(`Selesai: ${items.length} HSP (${data.meta.jumlah_hsp_dengan_detail} dengan detail) dalam ${Math.round((Date.now() - t0) / 1000)} dtk. hsp.json terunduh.`);
  window.HSP_RESULT = data;
})();
