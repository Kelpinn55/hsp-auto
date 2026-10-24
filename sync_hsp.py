#!/usr/bin/env python3
"""
sync_hsp.py - Unduh data HSP (Harga Satuan Pekerjaan) terbaru dari Bangun Jakarta
https://dcktrp.jakarta.go.id/bangunjakarta/hsp

Yang diambil:
  - daftar HSP        : uraian pekerjaan, satuan, kategori, harga
  - detail analisa    : uraian, kode, satuan, koefisien, harga satuan, jumlah harga
  - harga acuan       : daftar unik upah / bahan / alat (diturunkan dari detail analisa)

Hasil disimpan ke data/hsp.json (dipakai oleh web RAB).

Cara pakai:
  pip install -r requirements.txt
  playwright install chromium
  python sync_hsp.py                 # sinkron penuh
  python sync_hsp.py --debug         # simpan screenshot, HTML, dan daftar URL XHR ke data/debug/
  python sync_hsp.py --headed        # tampilkan jendela browser (untuk melihat prosesnya)
  python sync_hsp.py --limit 5       # hanya 5 HSP pertama (untuk uji coba)
  python sync_hsp.py --no-detail     # hanya daftar HSP (tanpa koefisien), sangat cepat

Perbaikan v2 (penyebab "stuck" di tahap mengambil data):
  * Pagination tidak lagi bisa berputar selamanya: tombol "Next" dianggap habis jika
    disabled (class / atribut / aria) ATAU isi tabel tidak berubah setelah diklik.
  * Semua klik memakai JavaScript langsung, sehingga backdrop modal yang menutupi
    layar tidak membuat Playwright menunggu 20 detik per baris.
  * Modal Detail ditutup dengan paksa bila tombol Close tidak bekerja.
  * Ada batas waktu total (--max-minutes) dan log progres di setiap tahap.
  * Satu kali jalan: baris dibaca dan detailnya langsung diambil (tanpa pencocokan ulang).
"""
from __future__ import annotations

import argparse
import os
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_URL = "https://dcktrp.jakarta.go.id/bangunjakarta/hsp"
ROOT = Path(__file__).resolve().parent
OUT_FILE = ROOT / "data" / "hsp.json"
DEBUG_DIR = ROOT / "data" / "debug"
WIB = timezone(timedelta(hours=7))
MAX_PAGES = 1000          # pengaman: maksimum halaman pagination per filter
EMPTY_RE = re.compile(r"no data|tidak ada data|loading|memuat|processing|sedang", re.I)


# --------------------------------------------------------------------------
# util
# --------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[{datetime.now(WIB):%H:%M:%S}] {msg}", flush=True)


def parse_idr(text: str | None):
    """'1.234.567,89' -> 1234567.89 ; '0,0250' -> 0.025 ; '' -> None"""
    if text is None:
        return None
    s = re.sub(r"[^0-9,.\-]", "", str(text).strip())
    if not s or s in {"-", ".", ","}:
        return None
    if "," in s:                       # format Indonesia: titik ribuan, koma desimal
        s = s.replace(".", "").replace(",", ".")
    else:
        parts = s.split(".")
        # '1.234.567' = ribuan; '0.025' = desimal
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and parts[0] not in ("0", "")):
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def fix_koef(koef_text: str, koef_v, harga_v, jumlah_v):
    """Koefisien '1.250' bisa berarti 1250 atau 1,25. Pilih yang cocok dengan koef x harga = jumlah."""
    if koef_v is None or harga_v is None or jumlah_v is None:
        return koef_v
    t = re.sub(r"[^0-9.,]", "", koef_text or "")
    if "," in t or t.count(".") != 1:
        return koef_v
    alt = None
    try:
        alt = float(t)                  # baca titik sebagai desimal
    except ValueError:
        return koef_v
    def err(k):
        return abs(k * harga_v - jumlah_v) / max(abs(jumlah_v), 1.0)
    return alt if err(alt) < err(koef_v) else koef_v


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def group_of(heading: str) -> str | None:
    h = heading.lower()
    if "tenaga" in h or "upah" in h:
        return "upah"
    if "bahan" in h or "material" in h:
        return "bahan"
    if "alat" in h or "peralatan" in h:
        return "alat"
    return None


# --------------------------------------------------------------------------
# JavaScript yang dijalankan di halaman
# --------------------------------------------------------------------------
# Tabel utama = tabel di luar modal yang punya baris tbody terbanyak.
_JS_PICK_TABLE = """
const pickTable = () => {
  const ts = [...document.querySelectorAll('table')].filter(t => !t.closest('.modal'));
  let best = null, n = -1;
  for (const t of ts) { const c = t.querySelectorAll('tbody tr').length; if (c > n) { n = c; best = t; } }
  return best;
};
"""

JS_MAIN_ROWS = "() => {" + _JS_PICK_TABLE + """
  const t = pickTable(); if (!t) return [];
  return [...t.querySelectorAll('tbody tr')].map((tr, idx) => ({
    idx,
    cells: [...tr.children].map(td => (td.innerText || '').trim()),
    empty: !!tr.querySelector('td.dataTables_empty, td.dt-empty'),
    hasButton: !!tr.querySelector('button, a, .btn, input[type=button]')
  }));
}"""

JS_CLICK_DETAIL = "(i) => {" + _JS_PICK_TABLE + """
  const t = pickTable(); if (!t) return false;
  const tr = t.querySelectorAll('tbody tr')[i]; if (!tr) return false;
  const b = tr.querySelector('td:last-child button, td:last-child a, td:last-child .btn, td:last-child [role=button]')
         || tr.querySelector('button, a.btn, .btn, a[data-bs-toggle], a[data-toggle], a');
  if (!b) return false;
  b.click();
  return true;
}"""

JS_MODAL_STATE = """() => {
  const vis = [...document.querySelectorAll('.modal')].filter(m => {
    const r = m.getBoundingClientRect(), s = getComputedStyle(m);
    return s.display !== 'none' && s.visibility !== 'hidden' && r.height > 0;
  });
  if (!vis.length) return null;
  const m = vis[vis.length - 1];
  const t = m.querySelector('table');
  const rows = t ? [...t.querySelectorAll('tbody tr')].map(tr => ({
    cells: [...tr.children].map(td => (td.innerText || '').trim()), hasButton: false
  })) : [];
  return { rows };
}"""

JS_CLOSE_MODAL = """() => {
  const vis = [...document.querySelectorAll('.modal')].filter(m => {
    const r = m.getBoundingClientRect(), s = getComputedStyle(m);
    return s.display !== 'none' && s.visibility !== 'hidden' && r.height > 0;
  });
  for (const m of vis) {
    const b = m.querySelector("[data-bs-dismiss='modal'], [data-dismiss='modal'], .btn-close, button.close, .btn-secondary");
    if (b) b.click();
  }
  return vis.length;
}"""

JS_FORCE_CLOSE = """() => {
  try { if (window.jQuery && window.jQuery.fn.modal) window.jQuery('.modal').modal('hide'); } catch (e) {}
  document.querySelectorAll('.modal').forEach(m => {
    m.classList.remove('show', 'in'); m.style.display = 'none';
    m.setAttribute('aria-hidden', 'true'); m.removeAttribute('aria-modal');
  });
  document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
  document.body.classList.remove('modal-open');
  document.body.style.overflow = ''; document.body.style.paddingRight = '';
}"""

# Info "Showing 1 to 10 of 300 entries" + baris pertama/terakhir -> tanda tangan halaman.
JS_SIGNATURE = "() => {" + _JS_PICK_TABLE + """
  const t = pickTable();
  const trs = t ? [...t.querySelectorAll('tbody tr')] : [];
  const txt = tr => tr ? (tr.innerText || '').replace(/\\s+/g, ' ').trim() : '';
  const info = document.querySelector('.dataTables_info, .dt-info, [id$=_info]');
  return trs.length + '|' + txt(trs[0]) + '|' + txt(trs[trs.length - 1]) + '|' + (info ? info.innerText : '');
}"""

# Klik tombol "Next". Hasil: 'none' (tidak ada), 'disabled' (sudah halaman terakhir), 'clicked'.
JS_NEXT = """() => {
  const sels = ['.paginate_button.next', '.dt-paging-button.next', 'li.next', 'a.next', 'button.next',
                '[aria-label="Next"]', '[aria-label="Next page"]', '[data-dt-idx="next"]', 'a[rel="next"]'];
  let el = null;
  for (const s of sels) { el = document.querySelector(s); if (el) break; }
  if (!el) {
    const re = /^(next|selanjutnya|berikutnya|lanjut|›|»|>)$/i;
    const pool = document.querySelectorAll('.pagination a, .pagination button, .dataTables_paginate a, .dataTables_paginate button, .dt-paging button, .dt-paging a');
    for (const c of pool) { if (re.test((c.innerText || '').trim())) { el = c; break; } }
  }
  if (!el) return 'none';
  const holder = el.closest('li') || el;
  const dis = n => !!n && (n.classList.contains('disabled') || n.hasAttribute('disabled') ||
                           n.getAttribute('aria-disabled') === 'true');
  if (dis(el) || dis(holder)) return 'disabled';
  const inner = el.matches('li') ? (el.querySelector('a, button') || el) : el;
  if (dis(inner)) return 'disabled';
  inner.click();
  return 'clicked';
}"""

JS_SELECTS = """() => [...document.querySelectorAll('select')]
  .filter(s => !/_length$/.test(s.name || '') && !s.closest('.dataTables_length, .dt-length'))
  .map((s, i) => ({
    i, id: s.id || '', name: s.name || '',
    label: ((s.id && document.querySelector('label[for="' + s.id + '"]')) || {}).innerText || '',
    options: [...s.options].map(o => ({ v: o.value, t: (o.text || '').trim() }))
  }))"""

JS_SET_SELECT = """([i, v]) => {
  const sel = [...document.querySelectorAll('select')]
    .filter(s => !/_length$/.test(s.name || '') && !s.closest('.dataTables_length, .dt-length'))[i];
  if (!sel) return false;
  sel.value = v;
  sel.dispatchEvent(new Event('input', { bubbles: true }));
  sel.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}"""

JS_LENGTH_OPTIONS = """() => {
  const s = document.querySelector("select[name$='_length'], .dataTables_length select, .dt-length select");
  return s ? [...s.options].map(o => o.value) : null;
}"""

JS_SET_LENGTH = """(v) => {
  const s = document.querySelector("select[name$='_length'], .dataTables_length select, .dt-length select");
  if (!s) return false;
  s.value = v;
  s.dispatchEvent(new Event('input', { bubbles: true }));
  s.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}"""


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def parse_detail_rows(raw_rows: list[dict]) -> list[dict]:
    """Ubah baris tabel modal Detail HSP menjadi komponen terstruktur."""
    out, grup = [], None
    for r in raw_rows:
        c = [clean(x) for x in r["cells"]]
        if not any(c):
            continue
        # urutan kolom: No, Uraian, Kode, Satuan, Koefisien, Harga Satuan, Jumlah Harga
        c += [""] * (7 - len(c))
        no, uraian, kode, satuan, koef, harga, jumlah = c[:7]
        koef_v, harga_v, jumlah_v = parse_idr(koef), parse_idr(harga), parse_idr(jumlah)
        koef_v = fix_koef(koef, koef_v, harga_v, jumlah_v)

        # baris judul grup / subtotal (tanpa koefisien)
        if koef_v is None:
            judul = uraian or no
            g = group_of(judul)
            if g and jumlah_v is None and harga_v is None:
                grup = g
            out.append({
                "tipe": "baris", "grup": grup, "no": no, "uraian": judul,
                "jumlah_harga": jumlah_v if jumlah_v is not None else harga_v,
            })
            continue

        out.append({
            "tipe": "komponen", "grup": grup, "no": no, "uraian": uraian,
            "kode": kode, "satuan": satuan, "koefisien": koef_v,
            "harga_satuan": harga_v, "jumlah_harga": jumlah_v,
        })
    return out


def build_harga_acuan(items: list[dict]) -> list[dict]:
    """Daftar unik upah/bahan/alat dari seluruh analisa -> tabel harga acuan."""
    seen: dict[str, dict] = {}
    for it in items:
        for d in it.get("detail", []):
            if d.get("tipe") != "komponen":
                continue
            key = d.get("kode") or d["uraian"]
            if key not in seen:
                seen[key] = {
                    "kode": d.get("kode", ""), "uraian": d["uraian"], "satuan": d.get("satuan", ""),
                    "harga": d.get("harga_satuan"), "grup": d.get("grup"),
                }
    return sorted(seen.values(), key=lambda x: (x["grup"] or "z", x["kode"] or "", x["uraian"]))


# --------------------------------------------------------------------------
# scraper
# --------------------------------------------------------------------------
class Scraper:
    def __init__(self, page, *, limit, delay, with_detail, deadline, debug):
        self.page = page
        self.limit = limit
        self.delay = delay
        self.with_detail = with_detail
        self.deadline = deadline
        self.debug = debug
        self.items: list[dict] = []
        self.seen: set[str] = set()
        self.detail_failed: list[str] = []
        self.stopped_early = False
        self.close_fail = 0          # berapa kali tombol Close gagal berturut-turut

    # ---- status
    def out_of_time(self) -> bool:
        if time.time() > self.deadline:
            self.stopped_early = True
            return True
        return False

    def limit_reached(self) -> bool:
        return bool(self.limit and len(self.items) >= self.limit)

    # ---- baca tabel utama
    def real_rows(self) -> list[dict]:
        rows = self.page.evaluate(JS_MAIN_ROWS)
        out = []
        for r in rows:
            cells = [clean(x) for x in r["cells"]]
            if r.get("empty") or len(cells) < 5 or EMPTY_RE.search(" ".join(cells)):
                continue
            r["cells"] = cells
            out.append(r)
        return out

    def signature(self) -> str:
        try:
            return self.page.evaluate(JS_SIGNATURE)
        except Exception:
            return ""

    def wait_rows(self, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.real_rows():
                return True
            self.page.wait_for_timeout(300)
        return False

    def settle(self, timeout: float = 8.0) -> None:
        """Tunggu sampai isi tabel berhenti berubah (dua pembacaan sama berturut-turut)."""
        end, last, same = time.time() + timeout, None, 0
        while time.time() < end:
            sig = self.signature()
            same = same + 1 if sig == last else 0
            if same >= 2:
                return
            last = sig
            self.page.wait_for_timeout(350)

    # ---- panjang halaman
    def set_page_length_max(self) -> None:
        opts = self.page.evaluate(JS_LENGTH_OPTIONS)
        if not opts:
            return
        nums = [int(v) for v in opts if re.fullmatch(r"-?\d+", v)]
        if not nums:
            return
        best = "-1" if "-1" in opts else str(max(nums))
        before = self.signature()
        self.page.evaluate(JS_SET_LENGTH, best)
        end = time.time() + 6
        while time.time() < end and self.signature() == before:
            self.page.wait_for_timeout(250)
        self.settle()

    # ---- pagination yang pasti berhenti
    def go_next(self) -> bool:
        before = self.signature()
        res = self.page.evaluate(JS_NEXT)
        if res != "clicked":
            return False
        end = time.time() + 8
        while time.time() < end:
            self.page.wait_for_timeout(200)
            if self.signature() != before:
                self.settle(4.0)
                return True
        return False  # diklik tetapi isi tidak berubah -> sudah halaman terakhir

    # ---- modal detail
    def read_modal(self):
        st = self.page.evaluate(JS_MODAL_STATE)
        if not st:
            return None
        rows = [r for r in st["rows"]
                if len(r["cells"]) >= 4 and not EMPTY_RE.search(" ".join(r["cells"]))]
        return rows or None

    def close_modal(self) -> None:
        try:
            if self.close_fail < 2:                      # tombol Close normal
                if self.page.evaluate(JS_CLOSE_MODAL):
                    end = time.time() + 1.5
                    while time.time() < end and self.page.evaluate(JS_MODAL_STATE):
                        self.page.wait_for_timeout(100)
                if self.page.evaluate(JS_MODAL_STATE):
                    self.close_fail += 1
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(200)
                else:
                    self.close_fail = 0
            if self.page.evaluate(JS_MODAL_STATE) or self.page.locator(".modal-backdrop").count():
                self.page.evaluate(JS_FORCE_CLOSE)       # tutup paksa + buang backdrop
        except Exception:
            self.page.evaluate(JS_FORCE_CLOSE)

    def fetch_detail(self, idx: int, prev_sig: str, timeout: float = 10.0):
        if not self.page.evaluate(JS_CLICK_DETAIL, idx):
            return None, prev_sig
        start = time.time()
        stable_rows, stable_n = None, 0
        while time.time() - start < timeout:
            self.page.wait_for_timeout(150)
            rows = self.read_modal()
            if not rows:
                continue
            sig = json.dumps(rows, ensure_ascii=False)
            # isi modal harus baru (beda dari item sebelumnya), kecuali sudah lewat 1,5 dtk
            if sig == prev_sig and time.time() - start < 1.5:
                continue
            if sig == stable_rows:
                stable_n += 1
            else:
                stable_rows, stable_n = sig, 0
            if stable_n >= 1 and time.time() - start >= self.delay:
                return rows, sig
        return None, prev_sig

    # ---- proses satu filter (semua halaman)
    def process_pages(self, label: str = "") -> None:
        pages = 0
        prev_sig = ""
        while True:
            if self.out_of_time():
                log("Batas waktu tercapai; berhenti dan menyimpan hasil sebagian.")
                return
            rows = self.real_rows()
            pages += 1
            log(f"Halaman {pages}{label}: {len(rows)} baris (total terkumpul {len(self.items)})")
            for r in rows:
                if self.limit_reached() or self.out_of_time():
                    return
                cells = r["cells"]
                # No | Uraian Pekerjaan | Satuan | Kategori | Harga | Detail
                no, uraian, satuan, kategori, harga = cells[:5]
                key = f"{uraian}|{satuan}|{kategori}"
                if key in self.seen:
                    continue
                self.seen.add(key)
                item = {"no": no, "uraian": uraian, "satuan": satuan, "kategori": kategori,
                        "harga": parse_idr(harga), "detail": []}
                self.items.append(item)

                if self.with_detail and r["hasButton"]:
                    rows_d = None
                    for attempt in (1, 2):
                        try:
                            rows_d, prev_sig = self.fetch_detail(r["idx"], prev_sig)
                        except Exception as e:  # noqa: BLE001
                            log(f"  ! error detail ({type(e).__name__}): {uraian[:50]}")
                            rows_d = None
                        finally:
                            self.close_modal()
                        if rows_d:
                            break
                    if rows_d:
                        item["detail"] = parse_detail_rows(rows_d)
                    else:
                        self.detail_failed.append(uraian)
                        log(f"  ! detail tidak terambil: {uraian[:60]}")
                    n = len(self.items)
                    if n % 10 == 0:
                        log(f"  detail {n} HSP diproses")
            if self.limit_reached():
                return
            if pages >= MAX_PAGES:
                log(f"Peringatan: mencapai batas {MAX_PAGES} halaman, pagination dihentikan.")
                return
            if not self.go_next():
                return

    # ---- mode API: panggil endpoint data situs langsung (cepat)
    def run_api(self, ctx, responses) -> bool:
        try:
            for extra in (ROOT, ROOT / "data"):          # toleran jika hsp_api.py salah taruh di data/
                if str(extra) not in sys.path:
                    sys.path.append(str(extra))
            import hsp_api
        except ImportError as e:
            log(f"Mode API dilewati: hsp_api.py tidak ditemukan di {ROOT} ({e}). "
                "Pindahkan hsp_api.py ke folder yang sama dengan sync_hsp.py.")
            return False
        self.wait_rows(12)                      # pastikan permintaan data awal sudah terjadi
        self.page.wait_for_timeout(800)

        def click_first():
            rows = self.real_rows()
            return bool(rows) and bool(self.page.evaluate(JS_CLICK_DETAIL, rows[0]["idx"]))

        try:
            items = hsp_api.run(self.page, ctx, responses, log=log, limit=self.limit,
                                with_detail=self.with_detail, parse_idr=parse_idr,
                                parse_detail_rows=parse_detail_rows, group_of=group_of,
                                click_first_detail=click_first, close_modal=self.close_modal)
        except Exception as e:  # noqa: BLE001
            log(f"Mode API error: {type(e).__name__}: {str(e)[:150]}")
            items = None
        if not items:
            return False
        self.items = items
        return True

    # ---- filter kategori (bila tabel kosong sampai filter dipilih)
    def find_filter_values(self):
        end = time.time() + 8
        sels = []
        while time.time() < end:
            sels = [s for s in self.page.evaluate(JS_SELECTS) if len(s["options"]) > 1]
            if sels:
                break
            self.page.wait_for_timeout(400)
        if not sels:
            return None
        def is_kat(s):
            return "kateg" in f"{s['id']} {s['name']} {s['label']}".lower()
        sel = next((s for s in sels if is_kat(s)), sels[0])
        vals = [o["v"] for o in sel["options"] if o["v"] not in ("", None)]
        log(f"Filter ditemukan ({sel['id'] or sel['name'] or 'select'}): {len(vals)} pilihan")
        return sel["i"], vals

    def run(self) -> None:
        self.set_page_length_max()
        if self.wait_rows(12):
            log("Tabel terisi tanpa filter.")
            self.process_pages()
            return

        log("Tabel kosong di awal; mencoba iterasi lewat filter kategori...")
        flt = self.find_filter_values()
        if not flt:
            log("Tidak menemukan filter maupun baris. Struktur halaman mungkin berubah.")
            return
        idx, vals = flt
        for n, v in enumerate(vals, 1):
            if self.limit_reached() or self.out_of_time():
                break
            self.page.evaluate(JS_SET_SELECT, [idx, v])
            self.wait_rows(8)
            self.settle()
            self.set_page_length_max()
            log(f"Filter {n}/{len(vals)}: {v}")
            self.process_pages(label=f" [{v[:30]}]")


def scrape(url: str, headed: bool, debug: bool, limit: int | None, delay: float,
           with_detail: bool, max_minutes: float, mode: str = "auto"):
    from playwright.sync_api import sync_playwright

    xhr_log: list[dict] = []
    deadline = time.time() + max_minutes * 60

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        try:
            ctx = browser.new_context(locale="id-ID", viewport={"width": 1400, "height": 900})
            # gambar/font tidak diperlukan -> lebih cepat dan tidak membuat "networkidle" menggantung
            ctx.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ("image", "media", "font") else route.continue_())
            page = ctx.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(45000)

            def on_response(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    if resp.request.resource_type in ("xhr", "fetch") or "json" in ct:
                        xhr_log.append({"url": resp.url, "status": resp.status,
                                        "method": resp.request.method, "type": ct})
                except Exception:
                    pass

            page.on("response", on_response)
            responses: list = []
            page.on("response", lambda r: responses.append(r))

            log(f"Membuka {url}")
            page.goto(url, wait_until="domcontentloaded")   # bukan "networkidle" (bisa menggantung)
            page.wait_for_selector("table", state="attached", timeout=20000)
            page.wait_for_timeout(1500)

            if debug:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(DEBUG_DIR / "hsp_awal.png"), full_page=True)
                (DEBUG_DIR / "hsp_awal.html").write_text(page.content(), encoding="utf-8")

            sc = Scraper(page, limit=limit, delay=delay, with_detail=with_detail,
                         deadline=deadline, debug=debug)
            try:
                done = False
                if mode in ("auto", "api"):
                    done = sc.run_api(ctx, responses)
                if not done and mode != "api":
                    if mode == "auto":
                        log("Beralih ke mode klik (DOM)...")
                    sc.run()
            finally:
                if debug or not sc.items:
                    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                    try:
                        page.screenshot(path=str(DEBUG_DIR / "hsp_akhir.png"), full_page=True)
                        (DEBUG_DIR / "hsp_akhir.html").write_text(page.content(), encoding="utf-8")
                    except Exception:
                        pass
                    (DEBUG_DIR / "xhr_urls.json").write_text(
                        json.dumps(xhr_log, indent=2, ensure_ascii=False), encoding="utf-8")
                    log(f"Berkas debug disimpan di {DEBUG_DIR}")
        finally:
            browser.close()

    return sc


def main() -> int:
    ap = argparse.ArgumentParser(description="Sinkron data HSP Bangun Jakarta")
    ap.add_argument("--url", default=os.environ.get("HSP_URL", DEFAULT_URL))
    ap.add_argument("--out", default=str(OUT_FILE))
    ap.add_argument("--headed", action="store_true", help="tampilkan browser")
    ap.add_argument("--debug", action="store_true", help="simpan screenshot/HTML/URL XHR")
    ap.add_argument("--limit", type=int, default=None, help="batasi jumlah HSP (uji coba)")
    ap.add_argument("--delay", type=float, default=0.3, help="jeda minimum (detik) setelah modal terbuka")
    ap.add_argument("--no-detail", action="store_true", help="lewati pengambilan detail koefisien")
    ap.add_argument("--mode", choices=["auto", "api", "dom"], default="auto",
                    help="auto = coba API situs dulu, lalu klik tabel; api = hanya API; dom = hanya klik tabel")
    ap.add_argument("--max-minutes", type=float, default=90, help="batas waktu total (menit)")
    a = ap.parse_args()

    url = a.url.strip()
    if re.search(r"/hsb/?($|\?)", url):
        log("Peringatan: /hsb adalah Harga Satuan BANGUNAN (per m2, kolom berbeda). "
            "Web RAB ini memakai HSP (analisa koefisien). Gunakan /hsp.")
        return 2

    t0 = time.time()
    try:
        sc = scrape(url, a.headed, a.debug, a.limit, a.delay, not a.no_detail, a.max_minutes, a.mode)
    except Exception as e:  # jaringan, browser belum terpasang, dsb.
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        log(f"GAGAL: {msg}")
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            log("Browser belum terpasang. Jalankan: playwright install chromium")
        elif "ERR_" in str(e):
            log("Tidak bisa membuka situs. Periksa koneksi internet / akses ke dcktrp.jakarta.go.id.")
        elif "Timeout" in str(e):
            log("Halaman/tabel tidak muncul tepat waktu. Coba lagi dengan --headed --debug.")
        return 1

    items = sc.items
    if not items:
        log("GAGAL: tidak ada baris HSP yang terbaca. Cek data/debug/hsp_akhir.html dan xhr_urls.json "
            "(struktur halaman mungkin berubah).")
        return 1

    n_detail = sum(1 for i in items if i["detail"])
    data = {
        "meta": {
            "sumber": url,
            "diambil_pada": datetime.now(WIB).isoformat(timespec="seconds"),
            "jumlah_hsp": len(items),
            "jumlah_hsp_dengan_detail": n_detail,
        },
        "items": items,
        "harga_acuan": build_harga_acuan(items),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    log(f"Selesai: {len(items)} HSP ({n_detail} dengan detail), {len(data['harga_acuan'])} harga acuan "
        f"-> {out}  [{time.time() - t0:.0f} dtk]")
    if sc.stopped_early:
        log("Peringatan: dihentikan oleh batas waktu, data HSP belum lengkap. Naikkan --max-minutes.")
    if not a.no_detail and n_detail < len(items):
        log(f"Peringatan: {len(items) - n_detail} HSP belum punya detail koefisien. Coba ulang dengan --delay 1.0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
