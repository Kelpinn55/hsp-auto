// GET /api/status -> status workflow terakhir, dalam format yang sama dengan server.py lokal.
const { cfg, gh, latestRun, RUNNING } = require("./_gh");

const ICON = { completed: "✓", in_progress: "…", queued: "·", pending: "·" };

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  const c = cfg();
  if (!c) return res.status(501).json({ running: false, exit_code: 1, log: ["GH_SYNC_TOKEN / GH_REPO belum diatur di Vercel"] });
  try {
    const run = await latestRun(c);
    if (!run) return res.status(200).json({ running: false, started: null, finished: null, exit_code: null, log: ["Belum ada riwayat sinkronisasi."] });
    const running = RUNNING.has(run.status);
    const log = [`Workflow #${run.run_number}: ${run.status}${run.conclusion ? " / " + run.conclusion : ""}`];
    try {
      const jr = await gh(c, `/actions/runs/${run.id}/jobs`);
      if (jr.ok) for (const job of (await jr.json()).jobs || [])
        for (const s of job.steps || []) log.push(`${ICON[s.status] || "·"} ${s.name}${s.conclusion && s.conclusion !== "success" ? " (" + s.conclusion + ")" : ""}`);
    } catch (e) { /* abaikan */ }
    log.push(`Log lengkap: ${run.html_url}`);
    if (!running && run.conclusion === "success")
      log.push("Data baru sudah masuk ke repo. Vercel men-deploy ulang (±1 menit); muat ulang halaman setelah itu.");
    return res.status(200).json({
      running, started: run.run_started_at || run.created_at, finished: running ? null : run.updated_at,
      exit_code: running ? null : (run.conclusion === "success" ? 0 : 1), log,
    });
  } catch (e) {
    return res.status(502).json({ running: false, exit_code: 1, log: [String(e.message || e)] });
  }
};
