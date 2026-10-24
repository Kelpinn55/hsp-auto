#!/usr/bin/env python3
"""
server.py - server lokal untuk web RAB-HSP.

  python server.py            -> buka http://localhost:8000

Menyajikan web/ (atau folder ini bila index.html ada di sebelah server.py) dan data/, serta endpoint:
  POST /api/sync    jalankan sync_hsp.py di latar belakang  (opsi: ?debug=1&limit=5&nodetail=1&mode=auto|api|dom)
  POST /api/stop    hentikan sinkronisasi yang sedang berjalan
  GET  /api/status  status + log terakhir sinkronisasi

Perbaikan v2:
  * status "running" tidak bisa tersangkut lagi (try/finally), meskipun sync_hsp.py gagal dijalankan
  * batas waktu keras (SYNC_TIMEOUT) -> proses dimatikan bila macet
  * tombol/endpoint stop
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web" if (ROOT / "web" / "index.html").exists() else ROOT
SYNC_TIMEOUT = 100 * 60          # detik; sedikit di atas --max-minutes bawaan sync_hsp.py (90 menit)

STATE = {"running": False, "started": None, "finished": None, "exit_code": None, "log": []}
LOCK = threading.Lock()
PROC = {"p": None, "stopped": False}


def _log(line: str) -> None:
    with LOCK:
        STATE["log"].append(line.rstrip())
        STATE["log"] = STATE["log"][-300:]


def run_sync(extra_args):
    code = -1
    try:
        PROC["stopped"] = False
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "sync_hsp.py"), *extra_args], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        PROC["p"] = proc

        def watchdog():
            t_end = time.time() + SYNC_TIMEOUT
            while proc.poll() is None:
                if time.time() > t_end:
                    _log(f"!! Melewati batas waktu {SYNC_TIMEOUT // 60} menit, proses dihentikan.")
                    proc.kill()
                    return
                time.sleep(1)

        threading.Thread(target=watchdog, daemon=True).start()
        for line in proc.stdout:
            _log(line)
        code = proc.wait()
        if PROC["stopped"]:
            _log("Dihentikan oleh pengguna.")
    except Exception as e:  # noqa: BLE001  - jangan biarkan status macet di "running"
        _log(f"!! Gagal menjalankan sync_hsp.py: {type(e).__name__}: {e}")
    finally:
        PROC["p"] = None
        with LOCK:
            STATE.update(running=False, exit_code=code,
                         finished=datetime.now().isoformat(timespec="seconds"))


def start_sync(args) -> bool:
    with LOCK:
        if STATE["running"]:
            return False
        STATE.update(running=True, started=datetime.now().isoformat(timespec="seconds"),
                     finished=None, exit_code=None, log=[])
    threading.Thread(target=run_sync, args=(args,), daemon=True).start()
    return True


def data_age_days():
    f = ROOT / "data" / "hsp.json"
    return (time.time() - f.stat().st_mtime) / 86400 if f.exists() else None


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        p = urlparse(path).path
        if p.startswith("/data/"):
            return str(ROOT / p.lstrip("/"))
        if p in ("", "/"):
            p = "/index.html"
        return str(WEB / p.lstrip("/"))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):      # tenangkan log akses (polling status tiap 1,5 dtk)
        if "/api/status" not in str(args[0] if args else ""):
            super().log_message(fmt, *args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/api/status":
            with LOCK:
                return self._json(STATE)
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/sync":
            args = []
            if q.get("debug"):
                args.append("--debug")
            if q.get("nodetail"):
                args.append("--no-detail")
            if q.get("limit", [""])[0].isdigit():
                args += ["--limit", q["limit"][0]]
            if q.get("mode", [""])[0] in ("auto", "api", "dom"):
                args += ["--mode", q["mode"][0]]
            if not start_sync(args):
                return self._json({"ok": False, "pesan": "Sinkronisasi sedang berjalan"}, 409)
            return self._json({"ok": True})
        if u.path == "/api/stop":
            p = PROC.get("p")
            if p and p.poll() is None:
                PROC["stopped"] = True
                p.kill()
                return self._json({"ok": True})
            return self._json({"ok": False, "pesan": "Tidak ada sinkronisasi berjalan"}, 409)
        self._json({"ok": False}, 404)


if __name__ == "__main__":
    # python server.py [port] [--auto-sync-days N]   -> sinkron otomatis saat start bila data lebih tua dari N hari
    argv = sys.argv[1:]
    auto_days = None
    if "--auto-sync-days" in argv:
        k = argv.index("--auto-sync-days")
        auto_days = float(argv[k + 1])
        del argv[k:k + 2]
    port = int(argv[0]) if argv else 8000
    if auto_days is not None:
        age = data_age_days()
        if age is None or age > auto_days:
            print("Data HSP belum ada / sudah lama, sinkron otomatis dimulai...")
            start_sync([])
    print(f"RAB-HSP berjalan di http://localhost:{port}   (folder web: {WEB})")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
