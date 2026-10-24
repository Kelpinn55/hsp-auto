"""
hsp_api.py - mode cepat untuk sync_hsp.py: temukan endpoint data (XHR/fetch) yang dipakai halaman HSP,
lalu panggil langsung untuk seluruh data (tanpa klik tabel satu per satu).

Cara kerja (otomatis, tanpa perlu tahu URL API sebelumnya):
  1. Saat halaman dimuat, semua respons XHR/fetch direkam.
  2. Respons JSON dengan daftar baris terbanyak = daftar HSP. Permintaannya diputar ulang dengan
     parameter paging (start/length, page/per_page, dst.) diperbesar sampai semua data terambil.
  3. Tombol "Lihat Detail" baris pertama diklik sekali; permintaan yang muncul dipelajari
     (id baris ada di URL atau body) lalu diputar ulang untuk semua HSP.
  4. Jika salah satu langkah tidak bisa dipastikan, fungsi mengembalikan None dan sync_hsp.py
     otomatis memakai mode klik (DOM).
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SKIP_HEADERS = {"host", "content-length", "cookie", "connection", "accept-encoding", "origin", "referer"}


def _strip_tags(s) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", str(s)))).strip()


def _rows_of(obj, depth=0):
    """Cari daftar baris di dalam JSON (DataTables: {'data': [...]}, atau list langsung)."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and depth < 3:
        for k in ("data", "rows", "items", "result", "results", "records", "list"):
            if k in obj:
                r = _rows_of(obj[k], depth + 1)
                if r is not None:
                    return r
    return None


def _total_of(obj):
    if isinstance(obj, dict):
        for k in ("recordsFiltered", "recordsTotal", "total", "total_rows", "count"):
            if isinstance(obj.get(k), (int, float)):
                return int(obj[k])
        for v in obj.values():
            if isinstance(v, dict):
                t = _total_of(v)
                if t:
                    return t
    return None


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_table_rows(text: str) -> list[list[str]]:
    p = _TableParser()
    p.feed(text)
    return [r for r in p.rows if any(r)]


# --------------------------------------------------------------------------
# rekaman permintaan
# --------------------------------------------------------------------------
def _rec(resp):
    try:
        req = resp.request
        ct = resp.headers.get("content-type", "")
        if req.resource_type not in ("xhr", "fetch") and "json" not in ct:
            return None
        if resp.status >= 400:
            return None
        body = resp.text()
        if len(body) > 20_000_000:
            return None
        return {"url": resp.url, "method": req.method, "post": req.post_data, "headers": dict(req.headers),
                "ctype": ct, "body": body}
    except Exception:
        return None


def _json(body: str):
    try:
        return json.loads(body)
    except Exception:
        return None


def _pick_list_record(recs):
    best, score = None, 0
    for r in recs:
        obj = _json(r["body"])
        rows = _rows_of(obj) if obj is not None else None
        if not rows or not isinstance(rows[0], (dict, list)):
            continue
        width = len(rows[0])
        if width < 4:
            continue                      # daftar kategori/satuan (filter) biasanya <4 kolom
        sc = len(rows) * 10 + width
        if sc > score:
            best, score = r, sc
    return best


# --------------------------------------------------------------------------
# memutar ulang permintaan daftar
# --------------------------------------------------------------------------
_LEN_KEYS = ("length", "per_page", "perPage", "limit", "page_size", "pageSize", "size")
_START_KEYS = ("start", "offset")
_PAGE_KEYS = ("page", "pageNumber", "current_page")


def _mutate(rec, start: int, length: int, draw: int):
    """Kembalikan (url, method, headers, data) dengan parameter paging diganti."""
    url, method, post = rec["url"], rec["method"], rec["post"]
    headers = {k: v for k, v in rec["headers"].items()
               if k.lower() not in SKIP_HEADERS and not k.lower().startswith("sec-")}

    def patch(pairs):
        d = dict(pairs)
        lk = next((k for k in _LEN_KEYS if k in d), None)
        sk = next((k for k in _START_KEYS if k in d), None)
        pk = next((k for k in _PAGE_KEYS if k in d), None)
        if lk:
            d[lk] = str(length)
        if sk:
            d[sk] = str(start)
        elif pk and lk:
            d[pk] = str(start // max(length, 1) + 1)
        if "draw" in d:
            d["draw"] = str(draw)
        return d, bool(lk)

    parts = urlsplit(url)
    q, can_page = patch(parse_qsl(parts.query, keep_blank_values=True))
    # DataTables mengirim kolom sebagai columns[0][data]=... : tetap utuh karena memakai dict dari pasangan asli
    new_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))
    data = None
    if method.upper() != "GET" and post:
        if post.lstrip().startswith("{"):
            try:
                j = json.loads(post)
                lk = next((k for k in _LEN_KEYS if k in j), None)
                sk = next((k for k in _START_KEYS if k in j), None)
                if lk:
                    j[lk] = length
                    can_page = True
                if sk:
                    j[sk] = start
                if "draw" in j:
                    j["draw"] = draw
                data = json.dumps(j)
            except Exception:
                data = post
        else:
            d, cp = patch(parse_qsl(post, keep_blank_values=True))
            can_page = can_page or cp
            data = urlencode(d)
    return new_url, method, headers, data, can_page


def _fetch(ctx, url, method, headers, data):
    r = ctx.request.fetch(url, method=method, headers=headers, data=data, timeout=60000)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status} dari {url}")
    return r.text()


def fetch_all_rows(ctx, rec, log, limit=None):
    obj0 = _json(rec["body"])
    rows0 = _rows_of(obj0) or []
    total = _total_of(obj0)
    url, method, headers, data, can_page = _mutate(rec, 0, 1000, 1)
    if not can_page:
        log(f"  API daftar: tidak ada parameter paging, memakai {len(rows0)} baris apa adanya")
        return rows0
    out, seen, start, draw = [], set(), 0, 1
    for _ in range(400):
        url, method, headers, data, _cp = _mutate(rec, start, 1000, draw)
        obj = _json(_fetch(ctx, url, method, headers, data))
        rows = _rows_of(obj) or []
        total = _total_of(obj) or total
        new = 0
        for r in rows:
            k = json.dumps(r, sort_keys=True, ensure_ascii=False)
            if k not in seen:
                seen.add(k)
                out.append(r)
                new += 1
        log(f"  API daftar: {len(out)}" + (f"/{total}" if total else "") + " baris")
        if not new or not rows or (total and len(out) >= total) or (limit and len(out) >= limit):
            break
        start += len(rows)
        draw += 1
    return out


# --------------------------------------------------------------------------
# peta kolom
# --------------------------------------------------------------------------
def _pick(d: dict, *patterns):
    keys = list(d.keys())
    for pat in patterns:
        for k in keys:                                  # kecocokan persis dulu
            if k.lower() == pat:
                return d[k]
    for pat in patterns:
        for k in keys:
            kl = k.lower()
            if re.search(pat, kl) and not kl.endswith("_id") and kl != "id":
                return d[k]
    return None


def row_to_item(r, idx: int):
    if isinstance(r, dict):
        uraian = _strip_tags(_pick(r, "uraian", "uraian_pekerjaan", "nama_pekerjaan", r"uraian|pekerjaan|nama|deskripsi"))
        satuan = _strip_tags(_pick(r, "satuan", r"satuan|unit"))
        kategori = _strip_tags(_pick(r, "kategori", r"kateg"))
        harga = _pick(r, "harga", r"harga|price")
        no = _strip_tags(_pick(r, "no", "nomor")) or str(idx + 1)
    else:
        c = [_strip_tags(x) for x in r] + [""] * 6
        no, uraian, satuan, kategori, harga = c[0] or str(idx + 1), c[1], c[2], c[3], c[4]
    return {"no": no, "uraian": uraian, "satuan": satuan, "kategori": kategori, "harga": harga, "detail": []}


def _fix_koef(txt, k, h, j):
    """'1.250' = 1,25 atau 1250? pilih yang cocok dengan koef x harga = jumlah."""
    if k is None or h is None or j is None:
        return k
    t = re.sub(r"[^0-9.,]", "", txt or "")
    if "," in t or t.count(".") != 1:
        return k
    try:
        alt = float(t)
    except ValueError:
        return k
    err = lambda x: abs(x * h - j) / max(abs(j), 1.0)  # noqa: E731
    return alt if err(alt) < err(k) else k


def _group_from(kode: str, text: str, group_of):
    g = group_of(text or "") if text else None
    if g:
        return g
    k = (kode or "").strip().upper()
    if k.startswith("L"):
        return "upah"
    if k.startswith("M"):
        return "bahan"
    if k.startswith(("E", "A")):
        return "alat"
    return None


def detail_from_json(obj, group_of, parse_idr):
    rows = _rows_of(obj)
    comps = []

    def from_dict(d, forced=None):
        uraian = _strip_tags(_pick(d, "uraian", r"uraian|nama|komponen|item"))
        kode = _strip_tags(_pick(d, "kode", r"kode"))
        satuan = _strip_tags(_pick(d, "satuan", r"satuan|unit"))
        ktxt = _strip_tags(_pick(d, "koefisien", r"koef|indeks"))
        koef = parse_idr(ktxt)
        hs = parse_idr(_strip_tags(_pick(d, "harga_satuan", r"harga_satuan|harga")))
        jml = parse_idr(_strip_tags(_pick(d, "jumlah_harga", r"jumlah|total")))
        koef = _fix_koef(ktxt, koef, hs, jml)
        if koef is None:
            return None
        grp = forced or _group_from(kode, _strip_tags(_pick(d, r"grup|group|jenis|tipe|kelompok") or ""), group_of)
        return {"tipe": "komponen", "grup": grp, "no": _strip_tags(_pick(d, "no", "nomor")), "uraian": uraian,
                "kode": kode, "satuan": satuan, "koefisien": koef, "harga_satuan": hs, "jumlah_harga": jml}

    if rows is not None:
        for r in rows:
            if isinstance(r, dict):
                c = from_dict(r)
                if c:
                    comps.append(c)
    elif isinstance(obj, dict):                           # {"upah":[...], "bahan":[...], "alat":[...]}
        for k, v in obj.items():
            if isinstance(v, dict) and _rows_of(v) is not None:
                v = _rows_of(v)
            if isinstance(v, list):
                for r in v:
                    if isinstance(r, dict):
                        c = from_dict(r, group_of(k))
                        if c:
                            comps.append(c)
    return comps


# --------------------------------------------------------------------------
# template permintaan detail
# --------------------------------------------------------------------------
def build_detail_template(rec, first_row):
    """Cari field pada baris pertama yang nilainya muncul sebagai segmen path / parameter di permintaan detail."""
    parts = urlsplit(rec["url"])
    segs = parts.path.split("/")
    q = parse_qsl(parts.query, keep_blank_values=True)
    post_pairs = None
    post_json = None
    if rec["post"]:
        if rec["post"].lstrip().startswith("{"):
            try:
                post_json = json.loads(rec["post"])
            except Exception:
                pass
        else:
            post_pairs = parse_qsl(rec["post"], keep_blank_values=True)

    cands = []
    if isinstance(first_row, dict):
        for k, v in first_row.items():
            if isinstance(v, (str, int)) and str(v).strip() != "":
                cands.append((k, str(v)))
        cands.sort(key=lambda kv: (kv[0].lower() not in ("id", "id_hsp", "hsp_id", "kode", "uuid"), len(kv[1]) < 2))
    for k, val in cands:
        where = None
        if val in segs[1:]:
            where = ("path", segs.index(val))
        elif any(pv == val for _, pv in q):
            where = ("query", None)
        elif post_pairs and any(pv == val for pk, pv in post_pairs if pk != "_token"):
            where = ("form", None)
        elif post_json and any(str(pv) == val for pv in post_json.values()):
            where = ("json", None)
        if where:
            return {"key": k, "val": val, "where": where, "rec": rec}
    return None


def detail_request(tpl, row):
    rec, key, val = tpl["rec"], tpl["key"], tpl["val"]
    new = str(row.get(key, "")) if isinstance(row, dict) else ""
    parts = urlsplit(rec["url"])
    segs = parts.path.split("/")
    qs = [(k, new if v == val else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    segs = [new if (i > 0 and s == val) else s for i, s in enumerate(segs)]
    url = urlunsplit((parts.scheme, parts.netloc, "/".join(segs), urlencode(qs), ""))
    data = None
    if rec["post"]:
        if rec["post"].lstrip().startswith("{"):
            try:
                j = json.loads(rec["post"])
                data = json.dumps({k: (new if str(v) == val else v) for k, v in j.items()})
            except Exception:
                data = rec["post"]
        else:
            data = urlencode([(k, new if (v == val and k != "_token") else v)
                              for k, v in parse_qsl(rec["post"], keep_blank_values=True)])
    headers = {k: v for k, v in rec["headers"].items()
               if k.lower() not in SKIP_HEADERS and not k.lower().startswith("sec-")}
    return url, rec["method"], headers, data


# --------------------------------------------------------------------------
# alur utama
# --------------------------------------------------------------------------
def run(page, ctx, responses, *, log, limit, with_detail, parse_idr, parse_detail_rows, group_of,
        click_first_detail, close_modal):
    """Kembalikan list item (format sama seperti mode DOM) atau None jika perlu fallback ke DOM."""
    recs = [r for r in (_rec(x) for x in list(responses)) if r]
    log(f"Mode API: {len(recs)} permintaan XHR terekam")
    lst = _pick_list_record(recs)
    if not lst:
        log("Mode API: endpoint daftar HSP tidak ditemukan -> pakai mode klik")
        return None
    log(f"Mode API: endpoint daftar = {lst['method']} {urlsplit(lst['url']).path}")
    try:
        rows = fetch_all_rows(ctx, lst, log, limit)
    except Exception as e:  # noqa: BLE001
        log(f"Mode API: gagal memutar ulang daftar ({e}) -> pakai mode klik")
        return None
    if not rows:
        return None
    if limit:
        rows = rows[:limit]
    items = []
    for i, r in enumerate(rows):
        it = row_to_item(r, i)
        it["harga"] = parse_idr(_strip_tags(it["harga"])) if not isinstance(it["harga"], (int, float)) else float(it["harga"])
        items.append(it)
    if not items or not items[0]["uraian"]:
        log("Mode API: kolom uraian tidak terpetakan -> pakai mode klik")
        return None
    log(f"Mode API: {len(items)} HSP terbaca")
    if not with_detail:
        return items

    # --- pelajari permintaan detail dengan mengklik satu baris
    before = len(responses)
    if not click_first_detail():
        log("Mode API: tombol detail tidak ditemukan -> pakai mode klik")
        return None
    end = time.time() + 8
    new = []
    while time.time() < end and not new:
        page.wait_for_timeout(400)
        new = [r for r in (_rec(x) for x in list(responses)[before:]) if r]
    close_modal()
    if not new:
        log("Mode API: permintaan detail tidak terdeteksi -> pakai mode klik")
        return None
    tpl, drec = None, None
    for r in new:
        tpl = build_detail_template(r, rows[0] if isinstance(rows[0], dict) else None)
        if tpl:
            drec = r
            break
    if not tpl:
        log("Mode API: id baris pada permintaan detail tidak dapat dipastikan -> pakai mode klik")
        return None
    log(f"Mode API: endpoint detail = {drec['method']} {urlsplit(drec['url']).path} (id dari field '{tpl['key']}')")

    def parse_response(body: str):
        obj = _json(body)
        if obj is not None:
            return detail_from_json(obj, group_of, parse_idr)
        out = parse_detail_rows([{"cells": c} for c in html_table_rows(body)])
        for d in out:
            if d["tipe"] == "komponen" and not d.get("grup"):
                d["grup"] = _group_from(d.get("kode", ""), "", group_of)
        return out

    first = parse_response(drec["body"])
    if not any(d["tipe"] == "komponen" for d in first or []):
        log("Mode API: respons detail tidak bisa dipetakan -> pakai mode klik")
        return None

    failed = 0
    for n, (it, raw) in enumerate(zip(items, rows), 1):
        try:
            url, method, headers, data = detail_request(tpl, raw)
            it["detail"] = parse_response(_fetch(ctx, url, method, headers, data))
        except Exception as e:  # noqa: BLE001
            failed += 1
            if failed <= 3:
                log(f"  ! detail gagal: {it['uraian'][:50]} ({e})")
        if n % 50 == 0:
            log(f"  detail {n}/{len(items)}")
        if failed > max(10, len(items) // 5):
            log("Mode API: terlalu banyak detail gagal -> pakai mode klik")
            return None
    return items
