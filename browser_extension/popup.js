"use strict";

const API = "http://127.0.0.1:8765";
const $ = (id) => document.getElementById(id);
let trackedJobId = null;

function status(message, isError = false) {
  const el = $("status");
  el.textContent = message;
  el.classList.toggle("error", isError);
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Jobbot returned ${response.status}`);
  return data;
}

function isSupportedPage(url) {
  try {
    const host = new URL(url).hostname.toLowerCase().replace(/^www\./, "");
    return ["naukri.com", "indeed.com", "indeed.co.in", "wellfound.com",
      "workatastartup.com", "hirist.tech"].some(domain => host === domain || host.endsWith(`.${domain}`));
  } catch (_) { return false; }
}

async function startSearch() {
  const query = $("role-select").value || "software engineer";
  const data = await request("/api/agent/start", { method: "POST", body: { role: query } });
  updateAgent(data);
  status(`Starting ${query} workflow and opening search tabs…`);
  try {
    const result = await request(`/api/extension/searches?q=${encodeURIComponent(query)}`);
    const urls = (result.searches || []).slice(0, 5).map(item => item.url);
    if (urls.length) await chrome.windows.create({ url: urls, focused: true, type: "normal" });
    status(`Started ${query}. Search tabs are open for you; supported ATS forms run in the separate Jobbot profile.`);
  } catch (_) {
    status(`Started ${query}. Search tabs could not be opened; the Jobbot workflow is still running.`, true);
  }
}

function updateAgent(data) {
  $("agent-state").textContent = String(data.state || "idle").replaceAll("_", " ");
  const authorization = data.auto_submit_authorized === true ? "Live submission is enabled for supported ATS forms." : "Dry-run only; forms are filled for review and not submitted.";
  $("agent-stage").textContent = data.setup_error ? `Setup required: ${data.setup_error}` : `${data.stage || "idle"} · ${authorization}`;
  $("agent-tabs").textContent = (data.tabs || []).map(tab => `${tab.site}: ${tab.company} · ${tab.title} — ${tab.state}`).join("\n") || "Current application tab: idle.";
  $("start").disabled = Boolean(data.setup_error);
  $("agent-log").textContent = (data.logs || []).slice(-5).join("\n");
}

async function agentAction(action) {
  const data = await request(`/api/agent/${action}`, { method: "POST", body: {} });
  updateAgent(data);
  status(action === "emergency-stop" ? "Emergency stop requested." : `Agent ${action} requested.`);
}

async function loadAgent() {
  updateAgent(await request("/api/agent"));
}

async function loadRoles() {
  const data = await request("/api/extension/roles");
  const select = $("role-select");
  select.replaceChildren();
  for (const role of data.selected || []) {
    const option = document.createElement("option");
    option.value = role;
    option.textContent = role;
    select.append(option);
  }
  if (!select.options.length) throw new Error("Select target roles in Jobbot Settings first.");
  $("start").disabled = false;
}

async function showSkillMatch(description) {
  const data = await request("/api/extension/skills", { method: "POST", body: { description } });
  $("skill-results").hidden = false;
  $("skill-summary").textContent = data.resume_available
    ? `${data.match_pct}% of detected job skills also appear in your resume text.`
    : "No resume text is available to compare. Add a resume in Jobbot to see matches.";
  $("resume-skill-list").textContent = data.resume_skills_in_posting.length
    ? data.resume_skills_in_posting.join(", ") : "None detected";
  $("missing-skill-list").textContent = data.not_found_in_resume.length
    ? data.not_found_in_resume.join(", ") : "None detected";
  $("copy-resume-skills").dataset.text = data.resume_skills_in_posting.join(", ");
  $("copy-missing-skills").dataset.text = data.not_found_in_resume.join(", ");
}

async function captureCurrentJob() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id || !isSupportedPage(tab.url || "")) {
    throw new Error("Open an individual job page on Naukri, Indeed, Wellfound, Work at a Startup, or Hirist.tech first.");
  }
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const parse = (value) => {
        if (Array.isArray(value)) return value.flatMap(parse);
        if (!value || typeof value !== "object") return [];
        const here = String(value["@type"] || "").toLowerCase().includes("jobposting") ? [value] : [];
        return here.concat(Object.values(value).flatMap(parse));
      };
      let posting = null;
      for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
        try { posting ||= parse(JSON.parse(script.textContent || ""))[0] || null; } catch (_) { /* ignore malformed metadata */ }
      }
      const first = (v) => Array.isArray(v) ? v[0] : v;
      const place = first(posting?.jobLocation)?.address;
      const remote = String(posting?.jobLocationType || "").toUpperCase().includes("TELECOMMUTE")
        || /work[\s-]*from[\s-]*home|remote/i.test(document.querySelector("h1")?.innerText || "");
      const countryLabel = (v) => {
        const name = typeof v === "object" ? (v.name || v.addressCountry || "") : v;
        return String(name).toUpperCase() === "IN" ? "India" : name;
      };
      const locationParts = [place?.addressLocality, place?.addressRegion, place?.addressCountry]
        .filter(Boolean).map(countryLabel);
      const applicantPlace = first(posting?.applicantLocationRequirements);
      const applicantCountry = countryLabel(applicantPlace?.address?.addressCountry || applicantPlace?.name);
      if (remote && String(applicantCountry).toLowerCase() === "india" && !locationParts.includes("India")) {
        locationParts.push("India");
      }
      const bodyText = document.body?.innerText || "";
      const mailtoEmails = [...document.querySelectorAll('a[href^="mailto:"]')]
        .map(a => (a.getAttribute("href") || "").replace(/^mailto:/i, "").split(/[?;]/)[0]);
      const textEmails = bodyText.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/ig) || [];
      const emails = [...mailtoEmails, ...textEmails].filter((email, index, list) =>
        list.indexOf(email) === index && /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i.test(email)
        && !/(no-?reply|example\.com)/i.test(email));
      const contactHints = ["hr", "human resources", "recruit", "talent", "hiring", "career", "jobs", "apply"];
      const rankedEmails = emails.map(email => {
        const lower = email.toLowerCase();
        const local = lower.split("@")[0];
        const at = bodyText.toLowerCase().indexOf(lower);
        const context = at >= 0 ? bodyText.slice(Math.max(0, at - 100), at + lower.length + 100).toLowerCase() : "";
        const score = contactHints.reduce((sum, hint) => sum + (local.includes(hint) ? 2 : 0) + (context.includes(hint) ? 1 : 0), 0);
        return { email, score };
      }).filter(entry => entry.score > 0).sort((a, b) => b.score - a.score);
      return {
        url: location.href,
        title: posting?.title || document.querySelector("h1")?.innerText?.trim() || document.title,
        company: posting?.hiringOrganization?.name || "",
        location: locationParts.join(", ") || (remote ? "Remote" : ""),
        remote,
        description: bodyText.slice(0, 20000),
        hr_email: rankedEmails[0]?.email || "",
      };
    },
  });
  $("job-title").value = result.title || "";
  $("company").value = result.company || "";
  $("location").value = result.location || "";
  $("remote").checked = Boolean(result.remote);
  $("job-url").value = result.url || tab.url || "";
  $("description").value = result.description || "";
  $("hr-email").value = result.hr_email || "";
  $("job-form").hidden = false;
  $("tracked").hidden = true;
  trackedJobId = null;
  await showSkillMatch(result.description || "").catch(() => { $("skill-results").hidden = true; });
  status("Check the details and location before tracking.");
}

async function trackJob() {
  const data = await request("/api/extension/jobs", {
    method: "POST",
    body: {
      title: $("job-title").value,
      company: $("company").value,
      location: $("location").value,
      remote: $("remote").checked,
      url: $("job-url").value,
      description: $("description").value,
      hr_email: $("hr-email").value,
    },
  });
  trackedJobId = data.job.id;
  $("tracked-label").textContent = `${data.created ? "Tracked" : "Already tracked"}: ${data.job.title} · ${data.job.company} (#${trackedJobId})`;
  $("tracked").hidden = false;
  status(data.created ? "Added to Apply by hand." : "This job is already in Jobbot.");
  await loadApplied();
}

async function markApplied() {
  if (!trackedJobId) return;
  await request(`/api/jobs/${trackedJobId}/mark`, { method: "POST", body: { status: "applied" } });
  $("mark-applied").disabled = true;
  status("Application recorded. Company added to your Applied list.");
  await loadApplied();
}

async function loadApplied() {
  const data = await request("/api/extension/applied");
  const list = $("applied-list");
  list.replaceChildren();
  if (!data.applications.length) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = "No applications recorded yet.";
    list.append(li);
    return;
  }
  for (const application of data.applications.slice(0, 30)) {
    const li = document.createElement("li");
    const email = application.hr_email || "No email found";
    li.textContent = `${application.company || "Unknown company"} — ${application.title || "Role"} · ${application.status} · ${email}`;
    list.append(li);
  }
}

$("start").addEventListener("click", () => startSearch().catch(err => status(err.message, true)));
$("dashboard").addEventListener("click", () => chrome.tabs.create({ url: `${API}/#/applied` }));
$("agent-pause").addEventListener("click", () => agentAction("pause").catch(err => status(err.message, true)));
$("agent-resume").addEventListener("click", () => agentAction("resume").catch(err => status(err.message, true)));
$("agent-stop").addEventListener("click", () => agentAction("stop").catch(err => status(err.message, true)));
$("agent-emergency").addEventListener("click", () => agentAction("emergency-stop").catch(err => status(err.message, true)));
$("capture").addEventListener("click", () => captureCurrentJob().catch(err => status(err.message, true)));
$("track").addEventListener("click", () => trackJob().catch(err => status(err.message, true)));
$("mark-applied").addEventListener("click", () => markApplied().catch(err => status(err.message, true)));
$("refresh-applied").addEventListener("click", () => loadApplied().catch(err => status(err.message, true)));
$("description").addEventListener("change", () => showSkillMatch($("description").value).catch(err => status(err.message, true)));
$("copy-resume-skills").addEventListener("click", () => navigator.clipboard.writeText($("copy-resume-skills").dataset.text || "").then(() => status("Matched skills copied.")));
$("copy-missing-skills").addEventListener("click", () => navigator.clipboard.writeText($("copy-missing-skills").dataset.text || "").then(() => status("Terms to review copied.")));

Promise.all([loadRoles(), loadApplied()])
  .then(loadAgent)
  .catch(() => status("Start Jobbot with `jobbot ui` and initialize the workspace to connect this extension.", true));
setInterval(() => loadAgent().catch(() => {}), 1500);
