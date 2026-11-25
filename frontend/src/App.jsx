import React, { useEffect, useMemo, useState } from "react";
import { api, downloadReport, getToken, setToken, websocketUrl } from "./api.js";

const NAV = [
  ["overview", "Overview"],
  ["monitor", "Live Agent Monitor"],
  ["profile", "Candidate Profile"],
  ["history", "Application History"],
  ["reports", "Excel Reports"],
  ["settings", "Settings"],
];

const STATUS_CLASS = {
  Applied: "ok",
  "Pending Verification": "warn",
  "Requires Manual Review": "review",
  Failed: "bad",
  Duplicate: "muted",
  Skipped: "muted",
  "Blocked by Platform": "blocked",
};

function cls(...parts) {
  return parts.filter(Boolean).join(" ");
}

function host(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url || "—";
  }
}

function when(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function Toast({ toast, onClose }) {
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(onClose, 4500);
    return () => clearTimeout(timer);
  }, [toast, onClose]);
  if (!toast) return null;
  return (
    <div className={cls("toast", toast.type)}>
      <span>{toast.message}</span>
      <button onClick={onClose} aria-label="Dismiss">×</button>
    </div>
  );
}

function StatCard({ label, value, sub, tone }) {
  return (
    <div className={cls("stat", tone)}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub ? <div className="stat-sub">{sub}</div> : null}
    </div>
  );
}

function StatusPill({ state }) {
  const tone = ["running", "completed"].includes(state) ? "ok"
    : ["failed", "stopped"].includes(state) ? "bad"
    : ["pausing", "stopping", "starting"].includes(state) ? "warn" : "muted";
  return <span className={cls("pill", tone)}>{state || "idle"}</span>;
}

function QuotaBar({ quota }) {
  if (!quota) return null;
  const pct = Math.min(100, Math.round((quota.used / Math.max(1, quota.cap)) * 100));
  return (
    <div className="quota">
      <div className="quota-head">
        <div>
          <strong>{quota.submitted_this_week} / {quota.cap}</strong>
          <span> applications this week</span>
        </div>
        <span className="muted">{quota.remaining} remaining · week of {quota.week_start}</span>
      </div>
      <div className="bar"><div className="fill" style={{ width: `${pct}%` }} /></div>
      <div className="quota-foot">
        <span>{quota.successful} successful</span>
        <span>{quota.pending_verification} pending verification</span>
        <span>{quota.failed} failed</span>
        <span>{quota.duplicate_skipped} duplicates skipped</span>
      </div>
    </div>
  );
}

function TabsTable({ tabs }) {
  if (!tabs?.length) return <div className="empty">No browser tabs are active.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Tab</th><th>Platform</th><th>Site</th><th>Job</th><th>Action</th><th>Result</th><th>State</th></tr>
        </thead>
        <tbody>
          {tabs.map((tab) => (
            <tr key={tab.id}>
              <td className="mono">{tab.id}</td>
              <td>{tab.platform || "—"}</td>
              <td className="mono">{host(tab.site)}</td>
              <td className="truncate" title={tab.job_title}>{tab.job_title || "—"}</td>
              <td>{tab.action || "—"}</td>
              <td className="truncate muted" title={tab.result}>{tab.result || "—"}</td>
              <td><span className={cls("pill", tab.state === "running" ? "ok" : tab.state === "error" ? "bad" : "muted")}>{tab.state}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ActivityFeed({ items }) {
  if (!items?.length) return <div className="empty">Activity will appear here when the agent starts.</div>;
  return (
    <div className="feed">
      {items.map((item, index) => (
        <div className="feed-item" key={item.id || `${item.ts}-${index}`}>
          <div className="feed-time">{new Date(item.ts).toLocaleTimeString()}</div>
          <div className="feed-body">
            <div className="feed-title">
              <span className={cls("dot", item.level === "error" ? "bad" : item.level === "warning" ? "warn" : "ok")} />
              {item.action}
              {item.platform ? <span className="tag">{item.platform}</span> : null}
            </div>
            {item.job_title ? <div className="feed-job">{item.job_title}</div> : null}
            {item.result ? <div className="feed-result">{item.result}</div> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function AgentControls({ agent, busy, onAction }) {
  const active = ["starting", "running", "pausing", "stopping"].includes(agent?.state);
  return (
    <div className="controls">
      <button className="btn primary" disabled={active || busy} onClick={() => onAction("start")}>Start Applying</button>
      <button className="btn" disabled={!active || agent?.paused || busy} onClick={() => onAction("pause")}>Pause</button>
      <button className="btn" disabled={!active || !agent?.paused || busy} onClick={() => onAction("resume")}>Resume</button>
      <button className="btn" disabled={!active || busy} onClick={() => onAction("stop")}>Stop</button>
      <button className="btn danger" disabled={busy} onClick={() => onAction("emergency")}>Emergency Stop</button>
    </div>
  );
}

function OverviewPage({ overview, liveEvents, busy, onAction }) {
  const agent = overview?.agent;
  const quota = overview?.quota;
  const tabs = agent?.tabs || [];
  const activity = liveEvents.length ? liveEvents : (overview?.activity || []);
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <div className="eyebrow">Autonomous application engine</div>
          <h1>Overview</h1>
          <p className="muted">Visible Chrome, dedicated profile, MySQL-backed quota and duplicate protection.</p>
        </div>
        <div className="head-status">
          <StatusPill state={agent?.state} />
          <span className="muted">{agent?.message}</span>
        </div>
      </header>
      <AgentControls agent={agent} busy={busy} onAction={onAction} />
      <QuotaBar quota={quota} />
      <div className="stats-grid">
        <StatCard label="Submitted this week" value={`${quota?.submitted_this_week ?? 0} / ${quota?.cap ?? 300}`} sub="Confirmed applications" tone="accent" />
        <StatCard label="Remaining capacity" value={quota?.remaining ?? "—"} sub="Includes reserved uncertain outcomes" />
        <StatCard label="Successful" value={quota?.successful ?? 0} sub="Verified confirmations" tone="ok" />
        <StatCard label="Pending verification" value={quota?.pending_verification ?? 0} sub="Capacity is held" tone="warn" />
        <StatCard label="Failed" value={quota?.failed ?? 0} sub="Not submitted or rejected" tone="bad" />
        <StatCard label="Needs review" value={quota?.requires_review ?? 0} sub="Manual answer or CAPTCHA" tone="review" />
        <StatCard label="Active tabs" value={tabs.length} sub={`${overview?.resume_count ?? 0} resume version(s)`} />
        <StatCard label="Matched jobs" value={overview?.job_counts?.matched ?? 0} sub={`${overview?.job_counts?.new ?? 0} new in queue`} />
      </div>
      <div className="two-col">
        <section className="panel">
          <div className="panel-head"><h2>Live agent monitor</h2><span className="muted">{agent?.settings?.ai_provider || "auto"} AI</span></div>
          <TabsTable tabs={tabs} />
        </section>
        <section className="panel">
          <div className="panel-head"><h2>Activity</h2><span className="muted">WebSocket</span></div>
          <ActivityFeed items={activity.slice(0, 30)} />
        </section>
      </div>
      <section className="panel">
        <div className="panel-head"><h2>Safety state</h2></div>
        <div className="safety-grid">
          <div><span className="muted">Mode</span><strong>{overview?.authorization?.dry_run ? "Dry run" : "Live submissions"}</strong></div>
          <div><span className="muted">Autonomous submission</span><strong>{overview?.authorization?.auto_submit_authorized ? "Authorized" : "Not authorized"}</strong></div>
          <div><span className="muted">Profile completeness</span><strong>{overview?.profile_completeness?.percent ?? 0}%</strong></div>
          <div><span className="muted">Platform policy</span><strong>Only supported ATS forms</strong></div>
        </div>
      </section>
    </div>
  );
}

function MonitorPage({ overview, liveEvents }) {
  const activity = liveEvents.length ? liveEvents : (overview?.activity || []);
  return (
    <div className="page">
      <header className="page-head">
        <div><div className="eyebrow">Real-time</div><h1>Live Agent Monitor</h1><p className="muted">Each tab owns one configured platform. Submission concurrency is capped centrally.</p></div>
        <StatusPill state={overview?.agent?.state} />
      </header>
      <section className="panel">
        <div className="panel-head"><h2>Browser tabs</h2><span className="muted">Max {overview?.agent?.settings?.max_tabs ?? 6} tabs</span></div>
        <TabsTable tabs={overview?.agent?.tabs || []} />
      </section>
      <section className="panel">
        <div className="panel-head"><h2>Event stream</h2><span className="muted">{activity.length} recent events</span></div>
        <ActivityFeed items={activity} />
      </section>
    </div>
  );
}

function Field({ label, value, onChange, type = "text", placeholder, hint, textarea }) {
  return (
    <label className="field">
      <span>{label}{hint ? <em>{hint}</em> : null}</span>
      {textarea ? (
        <textarea value={value ?? ""} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} rows={5} />
      ) : (
        <input type={type} value={value ?? ""} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
      )}
    </label>
  );
}

function ProfilePage({ profile, resumes, onSave, onUpload, onDefault, onDelete, busy, toast }) {
  const [form, setForm] = useState({});
  const [answersText, setAnswersText] = useState("[]");
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    setForm(profile || {});
    setAnswersText(JSON.stringify(profile?.answers || [], null, 2));
  }, [profile]);

  const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value }));

  async function save() {
    let answers;
    try {
      answers = JSON.parse(answersText || "[]");
    } catch {
      toast?.("Answers must be valid JSON", "error");
      return;
    }
    await onSave({ ...form, answers });
  }

  async function upload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await onUpload(file);
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <div><div className="eyebrow">Facts only</div><h1>Candidate Profile</h1><p className="muted">Only truthful saved facts are typed into forms. Unknown required questions go to manual review.</p></div>
        <button className="btn primary" disabled={busy} onClick={save}>Save profile</button>
      </header>
      <div className="two-col wide-left">
        <section className="panel">
          <div className="panel-head"><h2>Identity</h2></div>
          <div className="form-grid">
            <Field label="First name" value={form.first_name} onChange={set("first_name")} />
            <Field label="Last name" value={form.last_name} onChange={set("last_name")} />
            <Field label="Email" type="email" value={form.email} onChange={set("email")} />
            <Field label="Phone" value={form.phone} onChange={set("phone")} />
            <Field label="Location" value={form.location} onChange={set("location")} />
            <Field label="LinkedIn" value={form.linkedin} onChange={set("linkedin")} />
            <Field label="GitHub" value={form.github} onChange={set("github")} />
            <Field label="Portfolio" value={form.website} onChange={set("website")} />
            <Field label="Current company" value={form.current_company} onChange={set("current_company")} />
            <Field label="Current title" value={form.current_title} onChange={set("current_title")} />
            <Field label="Years of experience" value={form.years_experience} onChange={set("years_experience")} />
            <Field label="Notice period" value={form.notice_period} onChange={set("notice_period")} />
            <Field label="Current CTC" value={form.current_ctc} onChange={set("current_ctc")} />
            <Field label="Expected CTC" value={form.expected_ctc} onChange={set("expected_ctc")} />
            <Field label="Work authorization" value={form.work_authorization} onChange={set("work_authorization")} />
            <Field label="Graduation year" value={form.graduation_year} onChange={set("graduation_year")} />
          </div>
          <Field label="Skills" value={form.skills} onChange={set("skills")} hint="comma separated" />
          <Field label="Education" value={form.education} onChange={set("education")} />
          <Field label="Professional summary" value={form.summary} onChange={set("summary")} textarea />
          <Field label="Standard answers (JSON)" value={answersText} onChange={setAnswersText} textarea hint="regex → answer" />
        </section>
        <section className="panel">
          <div className="panel-head"><h2>Resumes</h2><span className="muted">{resumes.length} version(s)</span></div>
          <label className="upload">
            <input type="file" accept="application/pdf,.pdf" onChange={upload} disabled={uploading} />
            <span>{uploading ? "Uploading…" : "Upload PDF resume"}</span>
          </label>
          <div className="resume-list">
            {resumes.map((resume) => (
              <div className="resume" key={resume.id}>
                <div>
                  <strong>{resume.name}</strong>
                  <div className="muted small">{(resume.size_bytes / 1024).toFixed(0)} KB · {resume.has_text ? "text extracted" : "no extractable text"}</div>
                </div>
                <div className="resume-actions">
                  {resume.is_default ? <span className="pill ok">default</span> : <button className="link" onClick={() => onDefault(resume.id)}>make default</button>}
                  <a className="link" href={`/api/resumes/${resume.id}/download${getToken() ? `?token=${encodeURIComponent(getToken())}` : ""}`} target="_blank" rel="noreferrer">download</a>
                  <button className="link danger" onClick={() => onDelete(resume.id)}>delete</button>
                </div>
              </div>
            ))}
            {!resumes.length ? <div className="empty">Upload at least one PDF before starting.</div> : null}
          </div>
          <div className="notice">
            The most relevant resume is selected from extracted text for each job. The version used is stored with every application.
          </div>
        </section>
      </div>
    </div>
  );
}

function HistoryPage({ overview, toast }) {
  const [rows, setRows] = useState([]);
  const [filters, setFilters] = useState({ status: "", platform: "", search: "" });
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "500" });
      Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
      const data = await api(`/api/applications?${params}`);
      setRows(data.applications || []);
    } catch (error) {
      toast?.(error.message, "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function verify(jobId, confirmed) {
    try {
      await api(`/api/applications/${jobId}/verify`, {
        method: "POST",
        body: JSON.stringify({ confirmed, note: confirmed ? "confirmed in dashboard" : "not submitted in dashboard" }),
      });
      toast?.(confirmed ? "Marked as applied" : "Marked as failed", "success");
      load();
    } catch (error) {
      toast?.(error.message, "error");
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <div><div className="eyebrow">Durable record</div><h1>Application History</h1><p className="muted">Every attempt is stored with its reason, resume, screenshot and confirmation.</p></div>
      </header>
      <section className="panel">
        <div className="filters">
          <select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}>
            <option value="">All statuses</option>
            {["Applied", "Pending Verification", "Requires Manual Review", "Failed", "Duplicate", "Skipped", "Blocked by Platform"].map((status) => <option key={status}>{status}</option>)}
          </select>
          <select value={filters.platform} onChange={(event) => setFilters({ ...filters, platform: event.target.value })}>
            <option value="">All platforms</option>
            {(overview?.platforms || []).map((platform) => <option key={platform.id} value={platform.id}>{platform.label}</option>)}
          </select>
          <input placeholder="Search company, title, location" value={filters.search} onChange={(event) => setFilters({ ...filters, search: event.target.value })} onKeyDown={(event) => event.key === "Enter" && load()} />
          <button className="btn" onClick={load} disabled={loading}>{loading ? "Loading…" : "Apply filters"}</button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Date</th><th>Platform</th><th>Company</th><th>Role</th><th>Score</th><th>Status</th><th>Resume</th><th>Reference</th><th>Action</th></tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className="mono">{row.applied_at ? new Date(row.applied_at).toLocaleDateString() : "—"}</td>
                  <td>{row.platform}</td>
                  <td>{row.company}</td>
                  <td className="truncate" title={row.title}>{row.title}</td>
                  <td>{row.score ?? "—"}</td>
                  <td><span className={cls("pill", STATUS_CLASS[row.status] || "muted")}>{row.status}</span></td>
                  <td className="truncate muted" title={row.resume_name}>{row.resume_name || "—"}</td>
                  <td className="mono">{row.reference_id || "—"}</td>
                  <td>
                    {row.status === "Pending Verification" ? (
                      <span className="row-actions">
                        <button className="link" onClick={() => verify(row.job_id, true)}>confirm</button>
                        <button className="link danger" onClick={() => verify(row.job_id, false)}>not sent</button>
                      </span>
                    ) : row.url ? <a className="link" href={row.url} target="_blank" rel="noreferrer">open</a> : "—"}
                  </td>
                </tr>
              ))}
              {!rows.length ? <tr><td colSpan={9}><div className="empty">No applications match these filters.</div></td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function ReportsPage({ overview, toast }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const quota = overview?.quota;
  return (
    <div className="page">
      <header className="page-head">
        <div><div className="eyebrow">Downloadable</div><h1>Excel Reports</h1><p className="muted">All applications, successful, pending, failures, weekly statistics and platform summary.</p></div>
      </header>
      <div className="stats-grid">
        <StatCard label="This week" value={`${quota?.submitted_this_week ?? 0} / ${quota?.cap ?? 300}`} />
        <StatCard label="Successful" value={quota?.successful ?? 0} tone="ok" />
        <StatCard label="Pending" value={quota?.pending_verification ?? 0} tone="warn" />
        <StatCard label="Failed" value={quota?.failed ?? 0} tone="bad" />
      </div>
      <section className="panel">
        <div className="panel-head"><h2>Generate report</h2><span className="muted">Leave blank for the current Monday–Sunday week</span></div>
        <div className="filters">
          <label className="field inline"><span>From</span><input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label>
          <label className="field inline"><span>To</span><input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
          <button className="btn primary" onClick={() => { downloadReport(start, end); toast?.("Report download started", "success"); }}>Download .xlsx</button>
        </div>
        <div className="notice">
          Reports use Asia/Kolkata dates. Pending applications keep reserved weekly capacity until you verify them in Application History.
        </div>
      </section>
    </div>
  );
}

function SettingsPage({ settings, onSave, onAuthorize, busy, toast }) {
  const [form, setForm] = useState({});
  const [confirm, setConfirm] = useState("");
  const [token, setTokenValue] = useState(getToken());

  useEffect(() => { setForm(settings || {}); }, [settings]);

  const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value }));
  const setNumber = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value === "" ? "" : Number(value) }));
  const togglePlatform = (id) => {
    const current = new Set(form.enabled_platforms || []);
    if (current.has(id)) current.delete(id); else current.add(id);
    setForm((prev) => ({ ...prev, enabled_platforms: Array.from(current) }));
  };

  async function save() {
    if (!form.dry_run && !form.auto_submit_authorized) {
      toast?.("Authorize autonomous submission or keep dry run enabled", "error");
      return;
    }
    const wasLive = settings?.dry_run === false && Boolean(settings?.auto_submit_authorized);
    const willBeLive = form.dry_run === false && Boolean(form.auto_submit_authorized);
    if (willBeLive && !wasLive && confirm.trim().toUpperCase() !== "AUTHORIZE") {
      toast?.('Type "AUTHORIZE" to enable live submissions', "error");
      return;
    }
    await onSave({
      weekly_cap: Number(form.weekly_cap),
      daily_cap: Number(form.daily_cap),
      max_tabs: Number(form.max_tabs),
      max_concurrent_submissions: Number(form.max_concurrent_submissions),
      match_threshold: Number(form.match_threshold),
      per_company_30d: Number(form.per_company_30d),
      delay_seconds: form.delay_seconds,
      dry_run: Boolean(form.dry_run),
      auto_submit_authorized: Boolean(form.auto_submit_authorized),
      enabled_platforms: form.enabled_platforms,
      ai_provider: form.ai_provider,
      ai_model: form.ai_model,
      cloud_model: form.cloud_model,
      ollama_base_url: form.ollama_base_url,
      resume_selection: form.resume_selection,
      generate_cover_letter: Boolean(form.generate_cover_letter),
      aggregator_discovery: Boolean(form.aggregator_discovery),
      aggregator_limit: Number(form.aggregator_limit),
      schedule: form.schedule,
      confirm,
    });
  }

  async function authorize() {
    if (confirm.trim().toUpperCase() !== "AUTHORIZE") {
      toast?.('Type "AUTHORIZE" first', "error");
      return;
    }
    await onAuthorize(confirm);
    setConfirm("");
  }

  const ats = (settings?.platform_catalog || []).filter((platform) => platform.kind === "ats");
  const delay = form.delay_seconds || [30, 120];

  return (
    <div className="page">
      <header className="page-head">
        <div><div className="eyebrow">Guardrails</div><h1>Settings</h1><p className="muted">Limits are enforced in MySQL, not in the browser, so restarts cannot bypass them.</p></div>
        <button className="btn primary" disabled={busy} onClick={save}>Save settings</button>
      </header>
      <div className="two-col wide-left">
        <section className="panel">
          <div className="panel-head"><h2>Limits</h2></div>
          <div className="form-grid">
            <Field label="Weekly application cap" type="number" value={form.weekly_cap} onChange={setNumber("weekly_cap")} hint="1–300" />
            <Field label="Daily cap" type="number" value={form.daily_cap} onChange={setNumber("daily_cap")} />
            <Field label="Maximum browser tabs" type="number" value={form.max_tabs} onChange={setNumber("max_tabs")} hint="1–6" />
            <Field label="Concurrent submissions" type="number" value={form.max_concurrent_submissions} onChange={setNumber("max_concurrent_submissions")} hint="1–3" />
            <Field label="AI matching threshold" type="number" value={form.match_threshold} onChange={setNumber("match_threshold")} hint="0–100" />
            <Field label="Applications per company (30 days)" type="number" value={form.per_company_30d} onChange={setNumber("per_company_30d")} />
          </div>
          <div className="form-grid">
            <Field label="Minimum delay (seconds)" type="number" value={delay[0]} onChange={(value) => set("delay_seconds")([Number(value), Number(delay[1])])} />
            <Field label="Maximum delay (seconds)" type="number" value={delay[1]} onChange={(value) => set("delay_seconds")([Number(delay[0]), Number(value)])} />
            <Field label="Aggregator listings per source" type="number" value={form.aggregator_limit} onChange={setNumber("aggregator_limit")} hint="Adzuna/Jooble discovery cap" />
          </div>
          <label className="check"><input type="checkbox" checked={form.aggregator_discovery !== false} onChange={(event) => set("aggregator_discovery")(event.target.checked)} /> Discover India listings from Adzuna and Jooble</label>
        </section>
        <section className="panel">
          <div className="panel-head"><h2>AI model</h2></div>
          <label className="field">
            <span>Provider</span>
            <select value={form.ai_provider || "auto"} onChange={(event) => set("ai_provider")(event.target.value)}>
              <option value="auto">Auto: Ollama, then cloud, then heuristic</option>
              <option value="ollama">Ollama only</option>
              <option value="cloud">OpenAI-compatible cloud only</option>
              <option value="heuristic">Heuristic only (no model)</option>
            </select>
          </label>
          <Field label="Ollama model" value={form.ai_model} onChange={set("ai_model")} placeholder="llama3.1:8b" />
          <Field label="Ollama base URL" value={form.ollama_base_url} onChange={set("ollama_base_url")} />
          <Field label="Cloud model" value={form.cloud_model} onChange={set("cloud_model")} placeholder="gpt-4o-mini" />
          <label className="field">
            <span>Resume selection</span>
            <select value={form.resume_selection || "relevant"} onChange={(event) => set("resume_selection")(event.target.value)}>
              <option value="relevant">Most relevant by resume text</option>
              <option value="default">Always use default resume</option>
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={Boolean(form.generate_cover_letter)} onChange={(event) => set("generate_cover_letter")(event.target.checked)} /> Generate cover letters</label>
        </section>
      </div>

      <section className="panel">
        <div className="panel-head"><h2>Platforms</h2><span className="muted">Only platforms configured in this project</span></div>
        <div className="platform-grid">
          {ats.map((platform) => (
            <label className={cls("platform", form.enabled_platforms?.includes(platform.id) && "active")} key={platform.id}>
              <input type="checkbox" checked={form.enabled_platforms?.includes(platform.id) || false} onChange={() => togglePlatform(platform.id)} />
              <span>
                <strong>{platform.id}</strong>
                <em>{platform.policy}</em>
              </span>
            </label>
          ))}
        </div>
        <div className="notice">Naukri, Indeed, Wellfound, Hirist and YC remain manual browser searches. Adzuna and Jooble are discovery APIs only.</div>
      </section>

      <section className="panel danger-zone">
        <div className="panel-head"><h2>Autonomous submission authorization</h2></div>
        <div className="auth-grid">
          <label className="check"><input type="checkbox" checked={Boolean(form.dry_run)} onChange={(event) => { set("dry_run")(event.target.checked); if (event.target.checked) set("auto_submit_authorized")(false); }} /> Dry run (fill forms, never submit)</label>
          <label className="check"><input type="checkbox" checked={Boolean(form.auto_submit_authorized)} onChange={(event) => { set("auto_submit_authorized")(event.target.checked); if (event.target.checked) set("dry_run")(false); }} /> I authorize autonomous submission</label>
          {!form.dry_run ? (
            <div className="confirm-row">
              <input value={confirm} onChange={(event) => setConfirm(event.target.value)} placeholder='Type AUTHORIZE' />
              <button className="btn danger" onClick={authorize}>Authorize live submissions</button>
            </div>
          ) : null}
        </div>
        <p className="muted small">Submissions are verified after the click. Unknown outcomes hold weekly capacity as Pending Verification until you confirm or reject them.</p>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Operation schedule (Asia/Kolkata)</h2></div>
        <label className="check"><input type="checkbox" checked={Boolean(form.schedule?.enabled)} onChange={(event) => set("schedule")({ ...(form.schedule || {}), enabled: event.target.checked })} /> Enable schedule</label>
        <div className="form-grid">
          <Field label="Start" type="time" value={form.schedule?.start || "09:00"} onChange={(value) => set("schedule")({ ...(form.schedule || {}), start: value })} />
          <Field label="End" type="time" value={form.schedule?.end || "21:00"} onChange={(value) => set("schedule")({ ...(form.schedule || {}), end: value })} />
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Local API token</h2><span className="muted">Optional; stored in this browser only</span></div>
        <div className="filters">
          <input value={token} onChange={(event) => setTokenValue(event.target.value)} placeholder="JOBBOT_API_TOKEN" />
          <button className="btn" onClick={() => { setToken(token); toast?.("Token saved in this browser", "success"); }}>Save token</button>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState("overview");
  const [overview, setOverview] = useState(null);
  const [settings, setSettings] = useState(null);
  const [profile, setProfile] = useState(null);
  const [resumes, setResumes] = useState([]);
  const [liveEvents, setLiveEvents] = useState([]);
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState(null);
  const [connected, setConnected] = useState(false);

  function notify(message, type = "success") {
    setToast({ message, type });
  }

  async function loadOverview() {
    try {
      const data = await api("/api/overview");
      setOverview(data);
    } catch (error) {
      notify(error.message, "error");
    }
  }

  async function loadAll() {
    try {
      const [overviewData, settingsData, profileData, resumeData] = await Promise.all([
        api("/api/overview"), api("/api/settings"), api("/api/profile"), api("/api/resumes"),
      ]);
      setOverview(overviewData);
      setSettings(settingsData);
      setProfile(profileData.profile || {});
      setResumes(resumeData.resumes || []);
    } catch (error) {
      notify(error.message, "error");
    }
  }

  useEffect(() => {
    loadAll();
    const timer = setInterval(loadOverview, 5000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    let socket;
    let closed = false;
    let retry;
    function connect() {
      socket = new WebSocket(websocketUrl());
      socket.onopen = () => setConnected(true);
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === "snapshot") {
            setOverview((prev) => (prev ? { ...prev, agent: message.payload } : prev));
          } else {
            setLiveEvents((prev) => [message, ...prev].slice(0, 120));
            if (["submitted", "pending", "duplicate", "application_result", "quota_stop", "agent_done"].includes(message.action)) {
              loadOverview();
            }
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 3000);
      };
      socket.onerror = () => socket.close();
    }
    connect();
    return () => { closed = true; clearTimeout(retry); socket?.close(); };
  }, []);

  async function action(name) {
    const paths = { start: "/api/agent/start", pause: "/api/agent/pause", resume: "/api/agent/resume", stop: "/api/agent/stop", emergency: "/api/agent/emergency-stop" };
    if (name === "emergency" && !window.confirm("Emergency stop cancels all browser work immediately. Continue?")) return;
    if (name === "start" && settings && !settings.dry_run) {
      if (!window.confirm("Live submission is authorized. Start applying now?")) return;
    }
    setBusy(name);
    try {
      const data = await api(paths[name], { method: "POST", body: JSON.stringify({}) });
      setOverview((prev) => (prev ? { ...prev, agent: data } : prev));
      notify(name === "start" ? "Agent starting" : `Agent ${name} requested`, "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy("");
    }
  }

  async function saveProfile(data) {
    setBusy("profile");
    try {
      const result = await api("/api/profile", { method: "PUT", body: JSON.stringify(data) });
      setProfile(result.profile);
      notify("Profile saved", "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy("");
    }
  }

  async function uploadResume(file) {
    const form = new FormData();
    form.append("file", file);
    try {
      await api("/api/resumes", { method: "POST", body: form });
      const data = await api("/api/resumes");
      setResumes(data.resumes || []);
      notify("Resume uploaded", "success");
    } catch (error) {
      notify(error.message, "error");
    }
  }

  async function makeDefault(id) {
    try {
      await api(`/api/resumes/${id}/default`, { method: "POST" });
      const data = await api("/api/resumes");
      setResumes(data.resumes || []);
    } catch (error) {
      notify(error.message, "error");
    }
  }

  async function deleteResume(id) {
    if (!window.confirm("Delete this resume version?")) return;
    try {
      await api(`/api/resumes/${id}`, { method: "DELETE" });
      const data = await api("/api/resumes");
      setResumes(data.resumes || []);
    } catch (error) {
      notify(error.message, "error");
    }
  }

  async function saveSettings(data) {
    setBusy("settings");
    try {
      const result = await api("/api/settings", { method: "PUT", body: JSON.stringify(data) });
      setSettings((prev) => ({ ...prev, ...result }));
      notify("Settings saved", "success");
      loadOverview();
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy("");
    }
  }

  async function authorize(confirm) {
    try {
      await api("/api/automation/authorize", { method: "POST", body: JSON.stringify({ authorized: true, confirm }) });
      const data = await api("/api/settings");
      setSettings(data);
      notify("Live submissions authorized", "success");
    } catch (error) {
      notify(error.message, "error");
    }
  }

  const active = overview?.agent?.state;
  const page = useMemo(() => {
    switch (view) {
      case "monitor": return <MonitorPage overview={overview} liveEvents={liveEvents} />;
      case "profile": return <ProfilePage profile={profile} resumes={resumes} onSave={saveProfile} onUpload={uploadResume} onDefault={makeDefault} onDelete={deleteResume} busy={busy} toast={notify} />;
      case "history": return <HistoryPage overview={overview} toast={notify} />;
      case "reports": return <ReportsPage overview={overview} toast={notify} />;
      case "settings": return <SettingsPage settings={settings} onSave={saveSettings} onAuthorize={authorize} busy={busy} toast={notify} />;
      default: return <OverviewPage overview={overview} liveEvents={liveEvents} busy={busy} onAction={action} />;
    }
  }, [view, overview, liveEvents, profile, resumes, settings, busy]);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">J</div>
          <div><strong>Jobbot</strong><span>Autonomous Agent</span></div>
        </div>
        <nav>
          {NAV.map(([id, label]) => (
            <button key={id} className={cls("nav", view === id && "active")} onClick={() => setView(id)}>{label}</button>
          ))}
        </nav>
        <div className="side-foot">
          <div className={cls("conn", connected ? "ok" : "bad")}>{connected ? "Live socket" : "Reconnecting…"}</div>
          <div className="muted small">Agent: {active || "idle"}</div>
          <div className="muted small">Profile: {overview?.profile_completeness?.percent ?? 0}%</div>
        </div>
      </aside>
      <main>{page}</main>
      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}
