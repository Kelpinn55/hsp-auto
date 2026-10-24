// POST /api/sync -> memicu workflow GitHub Actions "Sinkron data HSP".
const { cfg, gh, latestRun, RUNNING, WORKFLOW } = require("./_gh");
const COOLDOWN_MS = 10 * 60 * 1000;     // cegah penyalahgunaan tombol (halaman publik)

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ ok: false, pesan: "Gunakan POST" });
  const c = cfg();
  if (!c) return res.status(501).json({ ok: false, pesan: "GH_SYNC_TOKEN / GH_REPO belum diatur di Vercel" });
  try {
    const run = await latestRun(c);
    if (run && RUNNING.has(run.status)) return res.status(409).json({ ok: false, pesan: "Sinkronisasi sedang berjalan" });
    if (run && Date.now() - new Date(run.created_at).getTime() < COOLDOWN_MS)
      return res.status(409).json({ ok: false, pesan: "Baru saja disinkronkan, coba lagi beberapa menit lagi" });
    const r = await gh(c, `/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST", body: JSON.stringify({ ref: c.ref }),
    });
    if (r.status !== 204) return res.status(502).json({ ok: false, pesan: `GitHub menolak (${r.status})`, detail: await r.text() });
    return res.status(200).json({ ok: true });
  } catch (e) {
    return res.status(502).json({ ok: false, pesan: String(e.message || e) });
  }
};
