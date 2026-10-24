// Pembantu GitHub API untuk fungsi Vercel (berkas berawalan "_" tidak dijadikan route).
const WORKFLOW = process.env.GH_WORKFLOW || "sync-hsp.yml";

function cfg() {
  const token = process.env.GH_SYNC_TOKEN, repo = process.env.GH_REPO, ref = process.env.GH_BRANCH || "main";
  if (!token || !repo) return null;
  return { token, repo, ref };
}

async function gh(c, path, init = {}) {
  const r = await fetch(`https://api.github.com/repos/${c.repo}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${c.token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "rab-hsp-vercel",
      ...(init.headers || {}),
    },
  });
  return r;
}

const RUNNING = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

async function latestRun(c) {
  const r = await gh(c, `/actions/workflows/${WORKFLOW}/runs?per_page=1`);
  if (!r.ok) throw new Error(`GitHub API ${r.status}`);
  const j = await r.json();
  return (j.workflow_runs || [])[0] || null;
}

module.exports = { cfg, gh, latestRun, RUNNING, WORKFLOW };
