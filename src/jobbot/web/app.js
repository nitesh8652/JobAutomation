"use strict";

/* ------------------------------------------------------------------ utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

const STATUS_LABEL = {
  new: "New", matched: "Matched", assist: "Assist", queued: "Queued",
  failed: "Failed", submitted: "Submitted", applied_manual: "Applied",
  skipped: "Skipped", closed: "Closed", duplicate: "Duplicate",
};
const STATUS_TONE = {
  new: "", matched: "accent", assist: "info", queued: "warn", failed: "bad",
  submitted: "good", applied_manual: "good", skipped: "", closed: "", duplicate: "",
};
const STATUS_ORDER = ["new", "matched", "assist", "queued", "failed", "submitted", "applied_manual", "skipped", "closed", "duplicate"];

const statusBadge = (s) =>
  `<span class="badge dot ${STATUS_TONE[s] || ""}">${esc(STATUS_LABEL[s] || s || "unknown")}</span>`;

function scoreBadge(score) {
  if (score === null || score === undefined) return `<span class="score">—</span>`;
  const cls = score >= 80 ? "hi" : score >= 60 ? "mid" : "lo";
  return `<span class="score ${cls}">${esc(score)}</span>`;
}

function relTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "—";
  const secs = Math.round((Date.now() - then.getTime()) / 1000);
  if (secs < 45) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  if (secs < 86400 * 30) return `${Math.round(secs / 86400)}d ago`;
  return then.toLocaleDateString();
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? esc(iso) : d.toLocaleString();
}

function debounce(fn, ms = 260) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function toast(title, detail = "", tone = "") {
  const el = document.createElement("div");
  el.className = `toast ${tone}`;
  el.innerHTML = `<b>${esc(title)}</b>${detail ? `<span>${esc(detail)}</span>` : ""}`;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateY(6px)"; }, 4200);
  setTimeout(() => el.remove(), 4700);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

/* ------------------------------------------------------------------ state */
const state = {
  view: "dashboard",
  summary: null,
  settings: null,
  settingsTab: "targets",
  jobs: { status: "all", q: "", ats: "all", sort: "newest", limit: 100, offset: 0 },
  watchId: null,
  watchTimer: null,
  activityTimer: null,
  openLogs: new Set(),
  drawerJob: null,
};

/* --------------------------------------------------------------- routing */
const VIEW_META = {
  dashboard: ["Dashboard", "Your job search at a glance"],
  jobs: ["Jobs", "Everything jobbot has found, scored and applied to"],
  review: ["Needs review", "Forms waiting for you: CAPTCHAs and open questions"],
  assist: ["Apply by hand", "Sites we don't automate, with a tailored cover letter"],
  companies: ["Companies", "ATS boards jobbot watches for new listings"],
  "skill-gaps": ["Skill Gaps", "Skills most demanded by jobs vs what your resume shows"],
  activity: ["Activity", "Runs, output and errors"],
  settings: ["Settings", "Profile, targets, limits and paths"],
};

function currentView() {
  const m = location.hash.match(/^#\/(\w+)/);
  return m && VIEW_META[m[1]] ? m[1] : "dashboard";
}

function navigate(view) {
  if (location.hash === `#/${view}`) applyView(view);
  else location.hash = `#/${view}`;
}

function applyView(view) {
  state.view = view;
  if (state.activityTimer) { clearInterval(state.activityTimer); state.activityTimer = null; }
  $$("#nav .nav-item").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  const [title, sub] = VIEW_META[view];
  $("#view-title").textContent = title;
  $("#view-sub").textContent = sub;
  if (view !== "dashboard") $("#banner").innerHTML = "";
  const render = {
    dashboard: renderDashboard, jobs: renderJobs, review: renderReview,
    assist: renderAssist, companies: renderCompanies, "skill-gaps": renderSkillGaps,
    activity: renderActivity, settings: renderSettings,
  }[view];
  render().catch((err) => showError(err));
}

function showError(err) {
  console.error(err);
  $("#view").innerHTML = `<div class="empty"><div class="e-mark">!</div><b>Something went wrong</b><p>${esc(err.message || err)}</p>
    <button class="btn ghost small" data-action="refresh">Try again</button></div>`;
}

/* --------------------------------------------------------------- summary */
async function loadSummary() {
  try {
    const s = await api("/api/summary");
    state.summary = s;
    renderNavCounts(s);
    renderMiniQuota(s);
    setConn(true);
    return s;
  } catch (err) {
    setConn(false);
    return null;
  }
}

function setConn(ok) {
  const el = $("#conn");
  el.className = `conn ${ok ? "ok" : "bad"}`;
  el.querySelector("span").textContent = ok ? "connected" : "offline";
}

function renderNavCounts(s) {
  const set = (key, value) => {
    const el = $(`[data-count="${key}"]`);
    if (el) el.textContent = value ? String(value) : "";
  };
  set("jobs", s.total_jobs);
  set("review", s.review_count);
  set("assist", s.assist_count);
  set("companies", s.companies_active);
  const running = (s.tasks || []).filter((t) => t.status === "running" || t.status === "queued").length;
  set("running", running);
}

function renderMiniQuota(s) {
  const weekPct = Math.min(100, Math.round((s.week_applied / Math.max(1, s.limits.weekly_target)) * 100));
  const dayPct = Math.min(100, Math.round((s.today_attempts / Math.max(1, s.limits.daily_cap)) * 100));
  const tone = weekPct >= 100 ? "bad" : weekPct >= 80 ? "warn" : "";
  $("#mini-quota").innerHTML = `
    <div class="label"><span>This week</span><b>${s.week_applied}/${s.limits.weekly_target}</b></div>
    <div class="progress ${tone}"><i style="width:${weekPct}%"></i></div>
    <div class="label" style="margin-top:9px"><span>Today</span><b>${s.today_attempts}/${s.limits.daily_cap}</b></div>
    <div class="progress ${dayPct >= 100 ? "bad" : ""}"><i style="width:${dayPct}%"></i></div>`;
}

/* ------------------------------------------------------------ dashboard */
function setupBanner(s) {
  const missing = [];
  if (!s.has_profile) missing.push("config/profile.yaml is missing");
  if (!s.has_targets) missing.push("config/targets.yaml is missing");
  if (!s.has_resume) missing.push("no resume in resumes/");
  if (!s.has_api_key) missing.push("OPENAI_API_KEY is not set in .env");
  if (!missing.length) { $("#banner").innerHTML = ""; return; }
  const initBtn = s.config_ready ? "" :
    `<button class="btn small" data-task="init" data-label="Initialize workspace">Initialize</button>`;
  $("#banner").innerHTML = `
    <div class="banner">
      <div class="b-icon">!</div>
      <div class="b-text"><b>Setup needs a little attention</b>
        <ul>${missing.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>
      <button class="btn ghost small" data-action="open-settings">Open settings</button>
      ${initBtn}
    </div>`;
}

async function renderDashboard() {
  const [s, tasks] = await Promise.all([api("/api/summary"), api("/api/tasks?limit=6")]);
  state.summary = s;
  renderNavCounts(s);
  renderMiniQuota(s);
  setupBanner(s);
  const lim = s.limits;
  const weekPct = Math.min(100, Math.round((s.week_applied / Math.max(1, lim.weekly_target)) * 100));
  const dayPct = Math.min(100, Math.round((s.today_attempts / Math.max(1, lim.daily_cap)) * 100));
  const total = Math.max(1, ...Object.values(s.counts));
  const pipeline = STATUS_ORDER.filter((k) => s.counts[k])
    .map((k) => `<div class="pipeline-row ${k}">
        <span class="p-name">${esc(STATUS_LABEL[k])}</span>
        <span class="p-bar"><i style="width:${Math.round((s.counts[k] / total) * 100)}%"></i></span>
        <span class="p-n">${s.counts[k]}</span></div>`).join("") ||
    `<p class="note">No jobs yet. Run a fetch to pull listings.</p>`;

  const recent = (s.recent_applications || []).length
    ? s.recent_applications.map((a) => `<div class="mini-app">
        <div class="ma-main"><div class="ma-title">${esc(a.title || "Untitled")}</div>
        <div class="ma-sub">${esc(a.company || "?")} · ${esc(a.ats || "")} · ${esc(relTime(a.updated_at))}</div></div>
        ${statusBadge(a.status)}</div>`).join("")
    : `<p class="note">Nothing applied yet this week.</p>`;

  const taskList = (tasks.tasks || []).length
    ? tasks.tasks.slice(0, 5).map(taskRowMini).join("")
    : `<p class="note">No runs yet. Start with “Fetch jobs”.</p>`;

  $("#view").innerHTML = `
    <div class="grid stats">
      <div class="card stat">
        <span class="k">Applied this week</span>
        <span class="v">${s.week_applied} <small>/ ${lim.weekly_target}</small></span>
        <div class="progress ${weekPct >= 100 ? "bad" : weekPct >= 80 ? "warn" : ""}"><i style="width:${weekPct}%"></i></div>
        <span class="d">${Math.max(0, lim.weekly_target - s.week_applied)} to weekly target</span>
      </div>
      <div class="card stat">
        <span class="k">Attempts today</span>
        <span class="v">${s.today_attempts} <small>/ ${lim.daily_cap}</small></span>
        <div class="progress ${dayPct >= 100 ? "bad" : ""}"><i style="width:${dayPct}%"></i></div>
        <span class="d">${s.budget} form attempt(s) left in budget</span>
      </div>
      <div class="card stat ${s.matched_count ? "good" : ""}">
        <span class="k">Ready to apply</span>
        <span class="v">${s.matched_count}</span>
        <span class="d">matched jobs on supported ATS forms</span>
      </div>
      <div class="card stat ${s.review_count ? "warn" : ""}">
        <span class="k">Needs review</span>
        <span class="v">${s.review_count}</span>
        <span class="d">${s.assist_count} more to apply by hand</span>
      </div>
    </div>

    <div class="grid two">
      <div class="card">
        <div class="card-head"><div><h2>Run the pipeline</h2><div class="sub">Each step is rate-limited and recorded.</div></div></div>
        <div class="row">
          <button class="btn" data-task="fetch" data-label="Fetch new jobs">Fetch jobs</button>
          <button class="btn ghost" data-task="match" data-label="Score new jobs">Score new jobs</button>
          <button class="btn ghost" data-task="apply_dry" data-params='{"limit":3}' data-label="Dry-run applications">Dry-run 3 forms</button>
          <button class="btn ghost" data-task="apply" data-label="Apply to matched jobs" data-confirm="Submit applications automatically within today's limits?">Apply</button>
          <button class="btn ghost" data-task="report" data-label="Export weekly report">Weekly report</button>
          <button class="btn subtle" data-task="run_daily" data-label="Run daily pipeline" data-confirm="Run fetch + match + apply now?">Run daily</button>
        </div>
        <div class="card-foot">
          <div class="section-title"><h2>Pipeline</h2><span class="sub">${s.total_jobs} job(s) tracked</span></div>
          <div class="pipeline" style="margin-top:10px">${pipeline}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>This week</h2><a href="#/jobs" class="link-btn">All jobs</a></div>
        <div class="activity-mini">${recent}</div>
        <div class="card-foot">
          <div class="card-head"><h2>Recent runs</h2><a href="#/activity" class="link-btn">Activity</a></div>
          <div class="activity-mini">${taskList}</div>
        </div>
      </div>
    </div>`;
}

function taskRowMini(t) {
  const tone = t.status === "failed" ? "bad" : t.status === "running" ? "info" : t.status === "done" ? "good" : "";
  return `<div class="mini-app">
    <div class="ma-main"><div class="ma-title">${esc(t.name)}</div>
      <div class="ma-sub">${esc(t.command)} · ${esc(relTime(t.finished_at || t.started_at || t.created_at))}</div></div>
    <span class="badge ${tone}">${esc(t.status)}</span></div>`;
}

/* ------------------------------------------------------------------ jobs */
async function renderJobs() {
  const f = state.jobs;
  $("#view").innerHTML = `
    <div class="toolbar">
      <div class="search">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 2a8 8 0 1 0 4.9 14.3l5.4 5.4 1.4-1.4-5.4-5.4A8 8 0 0 0 10 2zm0 2a6 6 0 1 1 0 12 6 6 0 0 1 0-12z"/></svg>
        <input class="input" id="jobs-q" placeholder="Search title, company, location" value="${esc(f.q)}">
      </div>
      <select class="select" id="jobs-ats" aria-label="ATS">
        <option value="all">All sources</option>
      </select>
      <select class="select" id="jobs-sort" aria-label="Sort">
        <option value="newest">Newest</option>
        <option value="score">Best score</option>
        <option value="company">Company</option>
        <option value="status">Status</option>
      </select>
      <button class="btn ghost" data-action="open-add-job">Add job</button>
    </div>
    <div class="chips" id="jobs-chips"></div>
    <div id="jobs-results"><div class="empty"><b>Loading jobs…</b></div></div>`;

  $("#jobs-sort").value = f.sort;
  $("#jobs-q").addEventListener("input", debounce((e) => { f.q = e.target.value; f.offset = 0; loadJobs(); }, 260));
  $("#jobs-ats").addEventListener("change", (e) => { f.ats = e.target.value; f.offset = 0; loadJobs(); });
  $("#jobs-sort").addEventListener("change", (e) => { f.sort = e.target.value; f.offset = 0; loadJobs(); });
  await loadJobs();
}

async function loadJobs() {
  const f = state.jobs;
  const params = new URLSearchParams({ status: f.status, q: f.q, ats: f.ats, sort: f.sort, limit: f.limit, offset: f.offset });
  const data = await api(`/api/jobs?${params}`);
  const atsSelect = $("#jobs-ats");
  if (atsSelect) {
    const current = f.ats;
    atsSelect.innerHTML = `<option value="all">All sources</option>` +
      data.atses.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join("");
    atsSelect.value = current;
  }
  const counts = data.counts || {};
  const chips = [["all", "All", Object.values(counts).reduce((a, b) => a + b, 0)], ...STATUS_ORDER.map((s) => [s, STATUS_LABEL[s], counts[s] || 0])]
    .filter(([key, , n]) => key === "all" || n);
  $("#jobs-chips").innerHTML = chips.map(([key, label, n]) =>
    `<button class="chip ${f.status === key ? "active" : ""}" data-job-filter="${key}">${esc(label)}<span class="n">${n}</span></button>`).join("");

  if (!data.jobs.length) {
    $("#jobs-results").innerHTML = `<div class="empty"><div class="e-mark">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4h4a2 2 0 0 1 2 2v1h3a2 2 0 0 1 2 2v2H3V9a2 2 0 0 1 2-2h3V6a2 2 0 0 1 2-2zm0 2v1h4V6z"/></svg></div>
      <b>No jobs match</b><p>Try a different filter, or run a fetch to pull new listings.</p>
      <button class="btn small" data-task="fetch" data-label="Fetch new jobs">Fetch jobs</button></div>`;
    return;
  }

  const rows = data.jobs.map((j) => `
    <tr data-job-id="${j.id}">
      <td><div class="cell-title">${esc(j.title || "Untitled")}</div>
        <div class="cell-sub">${esc(j.company || "?")}${j.remote ? " · Remote" : ""}</div></td>
      <td class="cell-nowrap"><span class="ats-tag">${esc(j.ats || "other")}</span></td>
      <td class="cell-nowrap cell-sub">${esc(j.location || "—")}</td>
      <td>${scoreBadge(j.score)}</td>
      <td>${statusBadge(j.status)}</td>
      <td class="cell-nowrap cell-sub">${esc(relTime(j.posted_at || j.fetched_at))}</td>
      <td class="cell-actions"><button class="btn small ghost" data-action="open-job" data-id="${j.id}">Open</button></td>
    </tr>`).join("");

  $("#jobs-results").innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>Job</th><th>Source</th><th>Location</th><th>Score</th><th>Status</th><th>Posted</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="row" style="margin-top:10px;justify-content:space-between">
      <span class="note">Showing ${data.offset + 1}–${Math.min(data.offset + data.jobs.length, data.total)} of ${data.total}</span>
      <span class="row">
        <button class="btn ghost small" data-action="jobs-page" data-dir="-1" ${data.offset <= 0 ? "disabled" : ""}>Previous</button>
        <button class="btn ghost small" data-action="jobs-page" data-dir="1" ${data.offset + data.limit >= data.total ? "disabled" : ""}>Next</button>
      </span>
    </div>`;
}

/* ------------------------------------------------------------- job drawer */
async function openJob(id) {
  const job = await api(`/api/jobs/${id}`);
  state.drawerJob = job;
  const app = job.application;
  const badges = [
    statusBadge(job.status),
    scoreBadge(job.score),
    job.remote ? `<span class="badge">Remote</span>` : "",
    `<span class="ats-tag">${esc(job.ats || "other")}</span>`,
  ].join("");
  const cover = app && app.cover_letter
    ? `<div class="section"><h3>Cover letter</h3><textarea class="cover" readonly>${esc(app.cover_letter)}</textarea>
       <div class="cover-actions"><button class="btn small ghost" data-action="copy-el" data-target="drawer-cover">Copy</button></div></div>`
    : "";
  $("#drawer-body").innerHTML = `
    <div class="drawer-head">
      <div class="dh-main"><h2>${esc(job.title || "Untitled")}</h2>
        <div class="dh-meta">${esc(job.company || "?")} · ${esc(job.location || "—")}</div></div>
      <button class="icon-btn" data-close-drawer aria-label="Close">&times;</button>
    </div>
    <div class="badges">${badges}</div>
    <div class="actions">
      ${job.url ? `<a class="btn small" href="${esc(job.url)}" target="_blank" rel="noopener">Open job</a>` : ""}
      <button class="btn small ghost" data-task="apply_dry" data-params='{"job":${job.id}}' data-label="Dry-run this form">Dry-run form</button>
      <button class="btn small subtle" data-action="mark" data-id="${job.id}" data-status="matched">Requeue</button>
      <button class="btn small subtle" data-action="mark" data-id="${job.id}" data-status="submitted">Mark submitted</button>
      <button class="btn small subtle" data-action="mark" data-id="${job.id}" data-status="applied">Mark applied</button>
      <button class="btn small danger" data-action="mark" data-id="${job.id}" data-status="skip">Skip</button>
    </div>
    <div class="section"><h3>Details</h3>
      <dl class="kv">
        <dt>Status</dt><dd>${esc(STATUS_LABEL[job.status] || job.status || "—")}${job.status_reason ? ` · ${esc(job.status_reason)}` : ""}</dd>
        <dt>Fit score</dt><dd>${job.score ?? "—"}</dd>
        <dt>Source</dt><dd>${esc(job.source || job.ats || "—")}${job.source && job.ats && job.source !== job.ats ? ` · ${esc(job.ats)}` : ""}</dd>
        <dt>Posted</dt><dd>${esc(fmtDate(job.posted_at))}</dd>
        <dt>Fetched</dt><dd>${esc(fmtDate(job.fetched_at))}</dd>
        ${job.company_slug ? `<dt>Board</dt><dd>${esc(job.ats)}:${esc(job.company_slug)}</dd>` : ""}
      </dl>
    </div>
    ${job.score_reason ? `<div class="section"><h3>Why this score</h3><div class="why">${esc(job.score_reason)}</div></div>` : ""}
    ${app ? `<div class="section"><h3>Application</h3><dl class="kv">
        <dt>Status</dt><dd>${esc(app.status || "—")}</dd>
        <dt>Reason</dt><dd>${esc(app.reason || "—")}</dd>
        <dt>Updated</dt><dd>${esc(fmtDate(app.updated_at))}</dd>
        ${app.screenshot ? `<dt>Screenshot</dt><dd class="mono">${esc(app.screenshot)}</dd>` : ""}
      </dl></div>` : ""}
    ${cover}
    <div class="section"><h3>Description</h3><div class="desc" id="job-desc">${esc(job.description || "No description saved.")}</div></div>`;
  $("#drawer").hidden = false;
  $("#drawer").querySelector(".drawer").scrollTop = 0;
}

function closeDrawer() {
  $("#drawer").hidden = true;
  state.drawerJob = null;
}

/* ------------------------------------------------------------------ review */
async function renderReview() {
  const data = await api("/api/review");
  const jobs = data.jobs || [];
  $("#view").innerHTML = `
    <div class="row" style="justify-content:space-between">
      <div class="section-title"><div><h2>${jobs.length} form(s) need you</h2>
        <span class="sub">A browser opens with each form pre-filled. You solve the CAPTCHA or answer the open question, then submit.</span></div></div>
      <button class="btn" data-task="review" data-label="Review queued forms">Launch review console</button>
    </div>
    ${jobs.length ? `<div class="job-list">${jobs.map(reviewCard).join("")}</div>` : emptyState("All clear", "Nothing is waiting for you. New queued forms will show up here.")}`;
}

function reviewCard(j) {
  return `<article class="job-card">
    <div class="jc-head">
      <div style="flex:1;min-width:0">
        <div class="jc-title">${esc(j.title || "Untitled")}</div>
        <div class="jc-meta">${esc(j.company || "?")} · ${esc(j.location || "—")} · #${j.id} · ${esc(j.ats || "")}</div>
      </div>
      <div class="jc-badges">${statusBadge(j.status)}${scoreBadge(j.score)}</div>
    </div>
    <div class="why">${esc(j.app_reason || j.status_reason || "Needs a human.")}</div>
    <div class="jc-actions">
      ${j.url ? `<a class="btn small ghost" href="${esc(j.url)}" target="_blank" rel="noopener">Open job</a>` : ""}
      <button class="btn small ghost" data-task="apply_dry" data-params='{"job":${j.id}}' data-label="Dry-run this form">Dry-run form</button>
      <button class="btn small subtle" data-action="mark" data-id="${j.id}" data-status="submitted">Mark submitted</button>
      <button class="btn small subtle" data-action="mark" data-id="${j.id}" data-status="skip">Skip</button>
    </div>
  </article>`;
}

/* ------------------------------------------------------------------ assist */
async function renderAssist() {
  const data = await api("/api/assist");
  const jobs = data.jobs || [];
  $("#view").innerHTML = `
    <div class="row" style="justify-content:space-between">
      <div class="section-title"><div><h2>${jobs.length} job(s) to apply by hand</h2>
        <span class="sub">These sites don't allow automation. Open the job, copy the letter, apply, then mark it done.</span></div></div>
      <div class="row">
        <button class="btn ghost" data-task="assist" data-params='{"no_open":true}' data-label="Generate cover letters">Generate letters</button>
        <button class="btn" data-task="assist" data-label="Open assist page">Open assist page</button>
      </div>
    </div>
    ${jobs.length ? `<div class="job-list">${jobs.map(assistCard).join("")}</div>` : emptyState("Nothing queued", "Jobs that need a manual application will appear here after a match run.")}`;
}

function assistCard(j) {
  const cover = j.cover_letter || "";
  return `<article class="job-card">
    <div class="jc-head">
      <div style="flex:1;min-width:0">
        <div class="jc-title">${esc(j.title || "Untitled")}</div>
        <div class="jc-meta">${esc(j.company || "?")} · ${esc(j.location || "—")} · #${j.id} · ${esc(j.ats || "")}</div>
      </div>
      <div class="jc-badges">${statusBadge(j.status)}${scoreBadge(j.score)}</div>
    </div>
    ${j.score_reason ? `<div class="why">${esc(j.score_reason)}</div>` : ""}
    <textarea class="cover" id="cover-${j.id}" ${cover ? "" : "placeholder='No cover letter yet. Click “Generate letters” to write one.'"}>${esc(cover)}</textarea>
    <div class="cover-actions">
      ${j.url ? `<a class="btn small ghost" href="${esc(j.url)}" target="_blank" rel="noopener">Open job</a>` : ""}
      <button class="btn small ghost" data-action="copy-el" data-target="cover-${j.id}" ${cover ? "" : "disabled"}>Copy letter</button>
      <button class="btn small" data-action="save-cover" data-id="${j.id}">Save letter</button>
      <button class="btn small subtle" data-action="mark" data-id="${j.id}" data-status="applied">Mark applied</button>
      <button class="btn small subtle" data-action="mark" data-id="${j.id}" data-status="skip">Skip</button>
    </div>
  </article>`;
}

/* ------------------------------------------------------------ skill gaps */
async function renderSkillGaps() {
  $("#view").innerHTML = `<div class="empty"><b>Analysing jobs…</b></div>`;
  const data = await api("/api/skill-gaps");
  const gaps = data.gaps || [];
  const present = data.present || [];

  function bar(pct, cls) {
    return `<div class="sg-bar"><i class="${cls}" style="width:${pct}%"></i><span>${pct}%</span></div>`;
  }

  const gapRows = gaps.length
    ? gaps.map(s => `<tr>
        <td><b>${esc(s.skill)}</b></td>
        <td class="num">${s.jobs}</td>
        <td style="width:200px">${bar(s.pct, "gap")}</td>
        <td><span class="badge dot bad">Missing</span></td>
      </tr>`).join("")
    : `<tr><td colspan="4" class="note">No gaps found — great resume coverage!</td></tr>`;

  const presentRows = present.length
    ? present.map(s => `<tr>
        <td><b>${esc(s.skill)}</b></td>
        <td class="num">${s.jobs}</td>
        <td style="width:200px">${bar(s.pct, "ok")}</td>
        <td><span class="badge dot good">In resume</span></td>
      </tr>`).join("")
    : `<tr><td colspan="4" class="note">No matching skills found in resume yet.</td></tr>`;

  $("#view").innerHTML = `
    <div class="note" style="margin-bottom:14px">Based on <b>${data.total_jobs_analysed}</b> jobs in your pipeline.
      Skills are counted from job descriptions; resume coverage is checked against your PDF.
      <br>Location rule: <b>Remote → anywhere in India</b> · <b>On-site → Mumbai only</b>.
      Salary target: <b>25–35 LPA</b>.</div>
    <div class="grid two">
      <div class="card">
        <div class="card-head"><h2>⚠ Gaps — skills jobs want but your resume lacks</h2></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Skill</th><th>Jobs</th><th>Demand</th><th>Status</th></tr></thead>
          <tbody>${gapRows}</tbody>
        </table></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>✓ Covered — skills in both jobs and your resume</h2></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Skill</th><th>Jobs</th><th>Demand</th><th>Status</th></tr></thead>
          <tbody>${presentRows}</tbody>
        </table></div>
      </div>
    </div>`;
}

/* --------------------------------------------------------------- companies */
async function renderCompanies() {
  const data = await api("/api/companies");
  const rows = (data.companies || []).map((c) => `
    <tr>
      <td><div class="cell-title">${esc(c.name || c.slug)}</div><div class="cell-sub">${esc(c.ats)}:${esc(c.slug)}</div></td>
      <td><span class="ats-tag">${esc(c.ats)}</span></td>
      <td class="num">${c.open_jobs || 0}</td>
      <td class="cell-sub cell-nowrap">${esc(relTime(c.last_fetched))}</td>
      <td>${c.active === 1 ? `<span class="badge good dot">Watching</span>` : `<span class="badge">Paused</span>`}</td>
      <td class="cell-sub cell-nowrap">${esc(relTime(c.added_at))}</td>
    </tr>`).join("");
  $("#view").innerHTML = `
    <div class="grid two">
      <div class="card">
        <div class="card-head"><h2>Add a company board</h2></div>
        <form id="add-company-form" class="row" style="align-items:flex-end">
          <div class="field grow"><label for="company-target">ATS, board or careers URL</label>
            <input class="input" id="company-target" name="target" placeholder="greenhouse:acme or https://acme.com/careers" required></div>
          <div class="field" style="width:180px"><label for="company-name">Display name</label>
            <input class="input" id="company-name" name="name" placeholder="Acme"></div>
          <button class="btn" type="submit">Add board</button>
        </form>
        <p class="note" style="margin:10px 0 0">jobbot finds the Greenhouse, Lever, Ashby or Workable board behind a careers page. It fetches that board on the next run.</p>
      </div>
      <div class="card">
        <div class="card-head"><h2>Watch list</h2><span class="sub">${data.active} active · ${data.total} total</span></div>
        <div class="row">
          <button class="btn ghost" data-task="fetch" data-label="Fetch new jobs">Fetch boards now</button>
          <button class="btn ghost" data-task="fetch" data-params='{"force":true}' data-label="Force fetch">Force refresh</button>
        </div>
        <p class="note" style="margin-top:10px">Boards are fetched at most once every 20 hours unless forced.</p>
      </div>
    </div>
    ${data.total ? `<div class="table-wrap"><table>
      <thead><tr><th>Company</th><th>ATS</th><th>Open jobs</th><th>Last fetched</th><th>Status</th><th>Added</th></tr></thead>
      <tbody>${rows}</tbody></table></div>` : emptyState("No companies yet", "Add a board above, or run a fetch so jobbot can discover boards from aggregator listings.")}`;
}

/* ---------------------------------------------------------------- activity */
async function renderActivity() {
  if (state.activityTimer) { clearInterval(state.activityTimer); state.activityTimer = null; }
  const [data, logData] = await Promise.all([
    api("/api/tasks?limit=60"),
    api("/api/log?tail=300").catch(() => ({ lines: [], total: 0 })),
  ]);
  const tasks = data.tasks || [];
  state.lastTasks = tasks;
  const logLines = (logData.lines || []);
  const logHtml = logLines.length
    ? logLines.map(l => `<div class="${/error|failed|traceback|exception/i.test(l) ? "l-err" : /warning/i.test(l) ? "l-warn" : ""}">${esc(l)}</div>`).join("")
    : `<div class="log-empty">No log entries yet.</div>`;
  $("#view").innerHTML = `
    <div class="row" style="justify-content:space-between">
      <div class="section-title"><div><h2>Recent runs</h2><span class="sub">Output from every jobbot command started in the UI.</span></div></div>
      <button class="btn ghost" data-action="refresh">Refresh</button>
    </div>
    ${tasks.length ? `<div class="task-list">${tasks.map(taskRow).join("")}</div>` : emptyState("No runs yet", "Use the buttons on the dashboard, or add a job from the Jobs page.")}
    <div class="card" style="margin-top:18px">
      <div class="card-head"><h2>Full log</h2><span class="sub">Last ${logLines.length} of ${logData.total} lines from data/logs/jobbot.log</span></div>
      <div class="log" id="full-log" style="max-height:420px;overflow-y:auto">${logHtml}</div>
    </div>`;
  const fullLog = $("#full-log");
  if (fullLog) fullLog.scrollTop = fullLog.scrollHeight;
  for (const id of state.openLogs) loadLog(id, false);
  if (tasks.some((t) => t.status === "running" || t.status === "queued")) {
    state.activityTimer = setInterval(async () => {
      if (state.view !== "activity") return;
      const fresh = await api("/api/tasks?limit=60").catch(() => null);
      if (!fresh) return;
      if (JSON.stringify(fresh.tasks) !== JSON.stringify(state.lastTasks || [])) {
        state.lastTasks = fresh.tasks;
        renderActivity().catch(() => {});
      }
    }, 2500);
  }
}

function taskRow(t) {
  const tone = t.status === "failed" ? "bad" : t.status === "running" ? "info" : t.status === "done" ? "good" : t.status === "stopped" ? "warn" : "";
  const active = t.status === "running" || t.status === "queued";
  return `<div class="task" data-task-id="${t.id}">
    <div class="t-main">
      <div class="t-name">${esc(t.name)} <span class="badge ${tone}">${esc(t.status)}</span>
        ${t.returncode !== null && t.returncode !== undefined ? `<span class="note">exit ${esc(t.returncode)}</span>` : ""}</div>
      <div class="t-cmd">${esc(t.command)}</div>
      <div class="t-time">${esc(relTime(t.finished_at || t.started_at || t.created_at))} · ${t.line_count} line(s)</div>
    </div>
    <div class="t-actions">
      <button class="btn small ghost" data-action="toggle-log" data-id="${t.id}">${state.openLogs.has(t.id) ? "Hide log" : "View log"}</button>
      ${active ? `<button class="btn small danger" data-action="stop-task" data-id="${t.id}">Stop</button>` : ""}
    </div>
  </div>
  <div class="log hidden" id="log-${t.id}"></div>`;
}

async function loadLog(id, open = true) {
  const box = $(`#log-${id}`);
  if (!box) return;
  if (open) { state.openLogs.add(id); box.classList.remove("hidden"); }
  try {
    const t = await api(`/api/tasks/${id}`);
    box.innerHTML = t.lines && t.lines.length
      ? t.lines.map((l) => `<div class="${/error|failed|traceback|exception/i.test(l) ? "l-err" : ""}">${esc(l)}</div>`).join("")
      : `<div class="log-empty">No output yet.</div>`;
    box.scrollTop = box.scrollHeight;
  } catch (err) {
    box.innerHTML = `<div class="l-err">${esc(err.message)}</div>`;
  }
}

/* ---------------------------------------------------------------- settings */
async function renderSettings() {
  const data = await api("/api/config");
  state.settings = data;
  const lim = data.limits;
  const file = data.files[state.settingsTab] || { content: "", exists: false };
  $("#view").innerHTML = `
    <div class="grid two">
      <div class="card">
        <div class="card-head"><h2>Setup checklist</h2></div>
        <ul class="checklist">
          ${checkItem(data.files.profile.exists, "config/profile.yaml", "Your details and standard answers")}
          ${checkItem(data.files.targets.exists, "config/targets.yaml", "Titles, locations, limits, sources")}
          ${checkItem(data.has_resume, "resumes/base_resume.pdf", "Used for scoring and cover letters")}
          ${checkItem(data.has_api_key, "OPENAI_API_KEY", "Needed for scoring and cover letters")}
        </ul>
        <div class="card-foot row">
          <button class="btn ghost small" data-task="init" data-label="Initialize workspace">Initialize workspace</button>
          <span class="note">Creates the YAML files and data store from the examples.</span>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Pacing limits</h2><span class="sub">from targets.yaml</span></div>
        <dl class="kv">
          <dt>Weekly target</dt><dd>${lim.weekly_target} applications</dd>
          <dt>Daily cap</dt><dd>${lim.daily_cap} form attempts</dd>
          <dt>Per company</dt><dd>${lim.per_company_30d} in 30 days</dd>
          <dt>Delay</dt><dd>${esc(lim.delay_seconds[0])}–${esc(lim.delay_seconds[1])} s between forms</dd>
          <dt>Browser</dt><dd>${lim.headless ? "headless by default" : "visible by default"}</dd>
        </dl>
        <div class="card-foot"><dl class="kv">
          <dt>Config</dt><dd class="mono">${esc(data.paths.config)}</dd>
          <dt>Data</dt><dd class="mono">${esc(data.paths.data)}</dd>
        </dl></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Configuration</h2><span class="sub">Saved as YAML, validated before writing</span></div>
      <div class="settings-grid">
        <div class="file-tabs">
          <button class="file-tab ${state.settingsTab === "targets" ? "active" : ""}" data-file-tab="targets">
            <b>targets.yaml</b><span>titles · limits · sources</span></button>
          <button class="file-tab ${state.settingsTab === "profile" ? "active" : ""}" data-file-tab="profile">
            <b>profile.yaml</b><span>your details · answers</span></button>
        </div>
        <div class="editor">
          <div class="editor-bar">
            <span class="status" id="editor-status">${file.exists ? "Editing existing file" : "File does not exist yet — Initialize first"}</span>
            <div class="row">
              <button class="btn ghost small" data-action="reload-config">Reload</button>
              <button class="btn small" data-action="save-config">Save file</button>
            </div>
          </div>
          <textarea class="textarea" id="config-editor" spellcheck="false" ${file.exists ? "" : "disabled"}>${esc(file.content)}</textarea>
        </div>
      </div>
    </div>`;
}

function checkItem(ok, title, note) {
  return `<li class="${ok ? "ok" : "todo"}"><span class="ck">${ok ? "✓" : ""}</span>
    <span><b>${esc(title)}</b> <span class="muted">— ${esc(note)}</span></span></li>`;
}

function switchFileTab(name) {
  if (!state.settings) return;
  state.settingsTab = name;
  const file = state.settings.files[name] || { content: "", exists: false };
  $$(".file-tab").forEach((b) => b.classList.toggle("active", b.dataset.fileTab === name));
  const editor = $("#config-editor");
  editor.value = file.content;
  editor.disabled = !file.exists;
  $("#editor-status").textContent = file.exists ? "Editing existing file" : "File does not exist yet — Initialize first";
}

/* --------------------------------------------------------------- task flow */
function parseParams(el) {
  try { return el.dataset.params ? JSON.parse(el.dataset.params) : {}; }
  catch (_) { return {}; }
}

async function startTask(key, params = {}, label = "") {
  try {
    const data = await api("/api/tasks", { method: "POST", body: { task: key, params } });
    toast("Task queued", data.task.name);
    watchTask(data.task.id);
  } catch (err) {
    toast("Could not start task", err.message, "bad");
  }
}

async function watchTask(id) {
  if (state.watchTimer) clearInterval(state.watchTimer);
  state.watchId = id;
  const bar = $("#taskbar");
  bar.classList.add("show");
  const tick = async () => {
    let t;
    try { t = await api(`/api/tasks/${id}`); }
    catch (err) { clearInterval(state.watchTimer); state.watchTimer = null; bar.classList.remove("show"); return; }
    $("#taskbar-text").textContent = `${t.name} · ${t.status}`;
    if (t.status === "running" || t.status === "queued") return;
    clearInterval(state.watchTimer);
    state.watchTimer = null;
    setTimeout(() => bar.classList.remove("show"), 900);
    const ok = t.status === "done";
    toast(t.name, ok ? (t.lines && t.lines.length ? lastLine(t.lines) : "Finished") : `Failed${t.returncode !== null ? ` (exit ${t.returncode})` : ""}`,
      ok ? "" : t.status === "stopped" ? "warn" : "bad");
    await loadSummary();
    if (state.view === "activity") renderActivity().catch(() => {});
    else if (state.view === "jobs") loadJobs().catch(() => {});
    else applyView(state.view);
  };
  tick();
  state.watchTimer = setInterval(tick, 1200);
}

function lastLine(lines) {
  const tail = (lines || []).filter((l) => l.trim());
  return tail.length ? tail[tail.length - 1].slice(0, 140) : "Finished";
}

function stopTask(id) {
  api(`/api/tasks/${id}/stop`, { method: "POST", body: {} })
    .then(() => toast("Stopping", "The task will stop shortly."))
    .catch((err) => toast("Could not stop", err.message, "bad"));
}

/* ------------------------------------------------------------------ modal */
function openAddJob() {
  $("#modal-body").innerHTML = `
    <form id="add-job-form">
      <div class="form-grid">
        <div class="field span-2"><label for="aj-url">Job URL</label>
          <input class="input" id="aj-url" name="url" placeholder="https://boards.greenhouse.io/acme/jobs/123" required></div>
        <div class="field"><label for="aj-title">Title</label><input class="input" id="aj-title" name="title" placeholder="Backend Engineer"></div>
        <div class="field"><label for="aj-company">Company</label><input class="input" id="aj-company" name="company" placeholder="Acme"></div>
        <div class="field span-2"><label for="aj-location">Location</label><input class="input" id="aj-location" name="location" placeholder="Remote (India)"></div>
        <div class="field span-2"><label for="aj-desc">Description</label>
          <textarea class="textarea" id="aj-desc" name="desc" placeholder="Paste the job description so the cover letter is specific."></textarea>
          <span class="hint">ATS links go to the auto-apply queue; LinkedIn, Naukri, Indeed and other sites go to Apply by hand.</span></div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" type="button" data-close-modal>Cancel</button>
        <button class="btn" type="submit">Track job</button>
      </div>
    </form>`;
  $("#modal").hidden = false;
  setTimeout(() => $("#aj-url") && $("#aj-url").focus(), 30);
}

function closeModal() { $("#modal").hidden = true; }

/* ----------------------------------------------------------------- events */
document.addEventListener("click", async (e) => {
  const nav = e.target.closest("#nav .nav-item");
  if (nav) return; // handled by hashchange

  const filter = e.target.closest("[data-job-filter]");
  if (filter) { state.jobs.status = filter.dataset.jobFilter; state.jobs.offset = 0; loadJobs().catch(showError); return; }

  const fileTab = e.target.closest("[data-file-tab]");
  if (fileTab) { switchFileTab(fileTab.dataset.fileTab); return; }

  const jobRow = e.target.closest("tr[data-job-id]");
  if (jobRow && !e.target.closest("button, a")) { openJob(Number(jobRow.dataset.jobId)).catch((err) => toast("Could not open job", err.message, "bad")); return; }

  const taskBtn = e.target.closest("[data-task]");
  if (taskBtn) {
    const label = taskBtn.dataset.label || "";
    const confirmMsg = taskBtn.dataset.confirm;
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    startTask(taskBtn.dataset.task, parseParams(taskBtn), label);
    return;
  }

  const action = e.target.closest("[data-action]");
  if (action) {
    const a = action.dataset.action;
    const id = action.dataset.id ? Number(action.dataset.id) : null;
    try {
      if (a === "open-job") { await openJob(id); }
      else if (a === "mark") { await markJob(id, action.dataset.status); }
      else if (a === "refresh") { await refresh(); }
      else if (a === "open-add-job") { openAddJob(); }
      else if (a === "open-settings") { navigate("settings"); }
      else if (a === "jobs-page") {
        const dir = Number(action.dataset.dir);
        state.jobs.offset = Math.max(0, state.jobs.offset + dir * state.jobs.limit);
        await loadJobs();
      }
      else if (a === "copy-el") {
        const src = document.getElementById(action.dataset.target);
        if (src) await copyText(src.value !== undefined ? src.value : src.textContent);
      }
      else if (a === "toggle-log") {
        if (state.openLogs.has(id)) { state.openLogs.delete(id); const box = $(`#log-${id}`); if (box) box.classList.add("hidden"); action.textContent = "View log"; }
        else { action.textContent = "Hide log"; await loadLog(id); }
      }
      else if (a === "stop-task") { stopTask(id); }
      else if (a === "save-cover") { await saveCover(id); }
      else if (a === "reload-config") { state.settings = null; await renderSettings(); toast("Reloaded", "Configuration read from disk."); }
      else if (a === "save-config") { await saveConfig(); }
      else if (a === "copy-text") { await copyText(action.dataset.text || ""); }
    } catch (err) {
      toast("Action failed", err.message, "bad");
    }
  }
});

document.addEventListener("submit", async (e) => {
  if (e.target.id === "add-job-form") {
    e.preventDefault();
    const fd = new FormData(e.target);
    const params = {};
    for (const [k, v] of fd.entries()) if (String(v).trim()) params[k] = String(v).trim();
    closeModal();
    await startTask("add_job", params, "Track job URL");
  }
  if (e.target.id === "add-company-form") {
    e.preventDefault();
    const fd = new FormData(e.target);
    const params = {};
    for (const [k, v] of fd.entries()) if (String(v).trim()) params[k] = String(v).trim();
    await startTask("add_company", params, "Add company board");
    e.target.reset();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("#modal").hidden) closeModal();
    else if (!$("#drawer").hidden) closeDrawer();
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && state.view === "settings") {
    e.preventDefault();
    saveConfig().catch((err) => toast("Save failed", err.message, "bad"));
  }
});

async function markJob(id, status) {
  await api(`/api/jobs/${id}/mark`, { method: "POST", body: { status } });
  toast("Job updated", `#${id} → ${STATUS_LABEL[status === "applied" ? "applied_manual" : status === "skip" ? "skipped" : status] || status}`);
  await loadSummary();
  if (!$("#drawer").hidden) await openJob(id);
  if (state.view === "jobs") await loadJobs();
  else if (state.view === "review") await renderReview();
  else if (state.view === "assist") await renderAssist();
}

async function saveConfig() {
  const editor = $("#config-editor");
  if (!editor) return;
  const status = $("#editor-status");
  if (status) status.textContent = "Saving…";
  try {
    const res = await api("/api/config", { method: "POST", body: { name: state.settingsTab, content: editor.value } });
    if (state.settings) state.settings.files[state.settingsTab].content = editor.value;
    if (status) status.textContent = `Saved ${res.path}`;
    toast("Configuration saved", `${state.settingsTab}.yaml updated.`);
    await loadSummary();
  } catch (err) {
    if (status) status.textContent = "Save failed";
    toast("Save failed", err.message, "bad");
  }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied", "Copied to clipboard.");
  } catch (_) {
    toast("Copy failed", "Select the text and copy manually.", "warn");
  }
}

async function saveCover(id) {
  const el = document.getElementById(`cover-${id}`);
  if (!el) return;
  await api(`/api/jobs/${id}/cover-letter`, { method: "POST", body: { content: el.value } });
  toast("Cover letter saved", `#${id} updated.`);
}

function emptyState(title, note) {
  return `<div class="empty"><div class="e-mark">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16v3H4zm0 5h16v11H4zm2 2v7h12v-7z"/></svg></div>
    <b>${esc(title)}</b><p>${esc(note)}</p></div>`;
}

async function refresh() {
  await loadSummary();
  if (state.view === "jobs") await loadJobs();
  else applyView(state.view);
}

/* ------------------------------------------------------------------- boot */
$("#btn-refresh").addEventListener("click", () => refresh().catch(showError));
$("#btn-quick-fetch").addEventListener("click", () => startTask("fetch", {}, "Fetch new jobs"));
$("#btn-quick-apply").addEventListener("click", () => {
  if (window.confirm("Submit applications automatically within today's limits?")) startTask("apply", {}, "Apply to matched jobs");
});
$("#taskbar-view").addEventListener("click", () => navigate("activity"));
$("#taskbar-stop").addEventListener("click", () => state.watchId && stopTask(state.watchId));
$$("[data-close-drawer]").forEach((el) => el.addEventListener("click", closeDrawer));
$$("[data-close-modal]").forEach((el) => el.addEventListener("click", closeModal));

window.addEventListener("hashchange", () => applyView(currentView()));

loadSummary();
applyView(currentView());
