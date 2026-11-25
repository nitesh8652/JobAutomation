# Autonomous Agent v2 — implementation and operating guide

This document describes the v2 agent that ships with this repository. It is a complete,
phase-by-phase implementation: FastAPI backend, React dashboard, MySQL persistence, visible
Chrome/Playwright workers, local-first AI, weekly quota, duplicate protection, recovery, and
Excel reporting. The legacy `jobbot` CLI remains available.

**The agent is dry-run by default.** It fills forms in a visible browser but never clicks submit
until autonomous submission is explicitly authorized in Settings (UI confirmation `AUTHORIZE`).

## 1. What is implemented

| Phase | Delivered code | Behaviour |
|---|---|---|
| Discovery | `src/jobbot/v2/discovery.py`, `platforms/` | Adzuna/Jooble India feeds plus the configured ATS boards. Aggregator links that resolve to a supported ATS are re-homed to that ATS. |
| AI matching | `src/jobbot/v2/ai.py` | Ollama (`llama3.1:8b` by default) → optional OpenAI-compatible cloud → deterministic heuristic. Every job is stored with a score and reason. |
| Application | `orchestrator.py`, `forms.py`, `browser.py` | One visible tab per enabled ATS, shared weekly quota, form analysis, profile/standard answers, AI only for unfamiliar required fields, CAPTCHA/manual-review stops. |
| Persistence | `models.py`, `repositories.py`, `migrations/001_init.sql` | MySQL in production; SQLite fallback for tests/local dry runs. Quota and reservation rows survive restarts. |
| Dashboard | `frontend/`, `api.py`, `ws.py` | Overview, live tab monitor, profile, history, reports, settings, pause/resume/stop/emergency stop, WebSocket events. |
| Reporting | `reporting.py` | `.xlsx` with the required columns and success/pending/failure/weekly/platform sheets. |
| Operations | `cli.py`, `scripts/migrate.py`, `security.py` | Init, migration, recovery, report generation, rotating redacted logs. |

### Platform policy

The agent only auto-applies where the project already had a supported hosted ATS form:

- **Auto-apply:** Greenhouse, Lever, Ashby, Workable.
- **Dry-run only until reviewed:** Recruitee, Personio (present in the legacy code; not enabled by default).
- **Discovery only:** Adzuna, Jooble. Applications happen on the employer's site.
- **Manual only:** Naukri, Indeed, Wellfound, Hirist.tech, YC Work at a Startup. The agent never
  automates submission, CAPTCHA solving, stealth/fingerprint spoofing, proxy rotation, or account
  creation on these sites.

CAPTCHA, OTP, security checks, legal attestations, unknown mandatory questions, and form validation
errors stop the flow. The agent pauses all workers, leaves the form open in Chrome, and records
**Requires Manual Review** (or **Pending Verification** when a submit click may have landed). Finish
the form manually, then press Resume or Stop. The agent never guesses or bypasses a challenge.

## 2. Architecture

```
React dashboard (Vite) ──HTTP/WebSocket──> FastAPI (api.py)
                                             │
                         ┌───────────────────┼───────────────────┐
                         │                   │                   │
                  Orchestrator         Repositories          Report/AI
                  (workers/tabs)       (MySQL/SQLite)        (openpyxl/Ollama)
                         │
                 BrowserSession (visible Chrome, dedicated profile)
                         │
        Greenhouse / Lever / Ashby / Workable tabs (max 6)
                         │
             Discovery: Adzuna + Jooble + ATS board APIs
```

Deterministic code handles navigation, quota, duplicate checks, form validation, persistence, and
reports. The model only scores fit, answers unfamiliar required questions, and drafts cover letters.

## 3. Setup on Windows

### Python and browser

```powershell
cd C:\Users\pc\Desktop\Automation
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Google Chrome is preferred. If it is not installed, set `JOBBOT_BROWSER_CHANNEL` to empty and the
agent falls back to visible Playwright Chromium. The browser profile is
`data/chrome-jobbot-profile/` by default (override with `JOBBOT_BROWSER_PROFILE`). It is separate
from your normal Chrome profile; do not open that directory with your everyday Chrome.

### MySQL (recommended for production)

The MySQL 9.7 Windows service is already installed on this machine. Create the database and user
once, using your MySQL root password:

```sql
CREATE DATABASE jobbot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'jobbot'@'localhost' IDENTIFIED BY 'replace-with-a-strong-password';
GRANT ALL PRIVILEGES ON jobbot.* TO 'jobbot'@'localhost';
FLUSH PRIVILEGES;
```

Then copy `.env.example` to `.env` and set:

```dotenv
JOBBOT_DATABASE_URL=mysql+pymysql://jobbot:replace-with-a-strong-password@127.0.0.1:3306/jobbot?charset=utf8mb4
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1:8b
JOBBOT_API_TOKEN=choose-a-local-token
# Optional: OPENAI_API_KEY=... for cloud fallback
# Optional discovery keys: ADZUNA_APP_ID / ADZUNA_APP_KEY / JOOBLE_KEY
```

If `JOBBOT_DATABASE_URL` is omitted, the app uses `data/jobbot-v2.db` (SQLite). That is useful for
dry runs and tests, but MySQL is the production store.

Apply the explicit migration (or let the first API/CLI start create tables):

```powershell
.venv\Scripts\python scripts\migrate.py
.venv\Scripts\jobbot-v2 init
```

### Ollama (local AI)

```powershell
ollama pull llama3.1:8b
ollama serve
```

The app probes Ollama first. If it is down, cloud is used when `OPENAI_API_KEY` is set, otherwise
deterministic keyword scoring and template cover letters keep the agent working.

### Frontend

```powershell
cd frontend
npm install
npm run build
cd ..
```

The built `frontend/dist/` is served by FastAPI at `/`.

## 4. Running

```powershell
# from the repository root, with .venv active
jobbot-api
# or
.venv\Scripts\python -m uvicorn jobbot.v2.api:app --host 127.0.0.1 --port 8766
# or start the server and open the dashboard
powershell -ExecutionPolicy Bypass -File scripts\run_v2.ps1
```

Open <http://127.0.0.1:8766>. If `JOBBOT_API_TOKEN` is set, enter it in Settings; the dashboard
then sends `X-API-Token`, and WebSocket/report links append `?token=`.

Operational commands:

```powershell
jobbot-v2 platforms   # show configured platform policy
jobbot-v2 report      # write data/reports/jobbot-report-*.xlsx
jobbot-v2 recover     # mark stale in-flight submissions as pending verification
jobbot-v2 serve       # alternative API launcher
```

### Dry run (default)

1. Upload a PDF resume in **Candidate Profile**.
2. Check profile facts and standard answers.
3. Keep **Dry run** on and press **Start**.
4. The agent opens one visible Chrome tab per enabled ATS, discovers and scores jobs, fills forms,
   takes screenshots, and stops before submit. Filled forms stay visible for
   `dry_run_hold_seconds` (default 60s) or until you press Stop; review the form and history row.

### Authorizing live submission

Live submission requires both:

1. `auto_submit_authorized: true` in the stored settings, and
2. `dry_run: false`.

The dashboard only does this through the explicit **AUTHORIZE** confirmation. The API validates the
merged settings too, so a partial `PUT /api/settings` cannot disable dry run without authorization.
Startup refuses to launch when live mode is requested without authorization.

## 5. Weekly quota semantics

- Week = Monday 00:00 to Sunday 23:59 in `Asia/Kolkata` (`week_start_kolkata`).
- Default cap = 300; API validates 1–300.
- `quota_weeks` stores `used` and `reserved`; `submission_reservations` makes claims atomic.
- A reservation is created before submit, then:
  - success → `confirm_submission` moves `reserved` to `used`;
  - no confirmation → `mark_uncertain` keeps capacity reserved and records **Pending Verification**;
  - manual review/failure/closed → `release_submission` returns capacity.
- If the process dies during submit, startup recovery marks stale reservations **uncertain**, so
  the agent never assumes success and never exceeds the cap.
- A job is claimed with a conditional update, so two tabs cannot submit the same job.

Reports show submitted/used, successful, pending, failed, duplicate, and requires-review counts.

## 6. Duplicate protection

Each job gets a SHA-256 fingerprint of normalized company + title + location
(`repositories.fingerprint_for`). Before reserving a slot the repository checks:

- an existing application for the same job in a consuming state;
- an existing application for the same fingerprint under another job;
- an active/confirmed/uncertain reservation for the job or fingerprint;
- the optional `per_company_30d` company limit.

The database also has unique keys on `jobs(platform, platform_job_id)`, `jobs(url)`,
`applications(job_id)`, and reservations by job and fingerprint, so races fail closed.

## 7. Recovery and stopping

- **Pause** stops at the next safe checkpoint (after current field/page step).
- **Stop** finishes the current safe step and closes Chrome.
- **Emergency stop** cancels workers and closes Chrome immediately.
- On startup, `recover_stale`:
  - converts stale `reserved` rows to `uncertain` and the application to **Pending Verification**;
  - resets an interrupted dry-run/pre-submit job to **matched** so it can be retried;
  - keeps a job **pending** when an in-flight reservation may already have been submitted.
- If the browser/tab dies, the worker records the failure and the tab can acquire a replacement page
  when capacity allows.

## 8. Reports

`GET /api/reports/excel?start=YYYY-MM-DD&end=YYYY-MM-DD` (and `jobbot-v2 report`) creates an `.xlsx`
with:

- **All Applications** — 18 columns: serial, date/time, platform, company, job title, location,
  URL, status, confirmation, resume, cover letter, AI score, match reason, failure reason,
  next action, tab ID, dry run, application reference ID.
- **Successful**, **Pending**, **Failures**, **Weekly Statistics**, **Platform Summary**.

Screenshots of filled/submitted forms are in `data/screenshots/`, cover letters in `data/letters/`.

## 9. API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness and database ping |
| GET | `/api/overview` | Dashboard state, quota, counts, authorization |
| GET | `/api/platforms` | Catalog, policy, enabled state, configured company count |
| GET | `/api/jobs` | Paginated jobs with status/score/reason |
| GET | `/api/applications` | Application history |
| GET/PUT | `/api/profile` | Candidate profile |
| GET/PUT | `/api/settings` | Runtime settings (validated) |
| GET/POST/DELETE | `/api/resumes` | PDF upload/list/delete |
| POST | `/api/agent/start|pause|resume|stop|emergency-stop` | Agent controls |
| POST | `/api/automation/authorize` | Explicit `AUTHORIZE` confirmation |
| POST | `/api/applications/{job_id}/verify` | Resolve a pending submission |
| GET | `/api/quota` | Week snapshot |
| GET | `/api/reports/excel` | XLSX download |
| WS | `/ws` | Live snapshots, tab events, activity |

## 10. Background/minimized operation

Playwright launches Chrome with background-throttling flags, but Windows can still throttle a
minimized Chrome. The safe approach is to keep the dedicated Chrome window open on the desktop while
the agent runs; keep the machine awake and connected. A Windows Task Scheduler entry can start
`jobbot-api`, but you must still explicitly press Start (or add your own startup policy) and handle
the visible-browser review requirements.

## 11. Security and privacy

- No CAPTCHA bypass, stealth plugins, fingerprint spoofing, proxy rotation, or restricted-site
  automation.
- Credentials are never stored in the repo; `.env` is ignored. Do not commit it.
- The local API binds to `127.0.0.1` by default and supports an optional bearer token.
- Resume uploads are PDF-only, max 10 MB, random UUID filenames under `data/`, SHA-256 deduplicated,
  and served through authenticated endpoints only.
- Logs are written to `data/logs/jobbot-v2.log` with redaction of tokens/passwords/API keys.
- Use BitLocker/MySQL at-rest encryption for candidate data if the machine is shared. Answers and
  cover letters are candidate-visible files under `data/`.

## 12. Tests

```powershell
$env:PYTHONPATH="src"
.venv\Scripts\python -m pytest -q
```

The suite covers quota reservation/confirmation/uncertainty, atomic cap enforcement, duplicate
fingerprints, company limits, settings/profile round-trips, PDF upload rejection, Excel sheet/column
structure, API smoke tests, aggregator re-homing, and log redaction.

## 13. Troubleshooting

| Symptom | Fix |
|---|---|
| Chrome launch fails | Close Chrome windows using `data/chrome-jobbot-profile/`; confirm Chrome is installed; retry. |
| `database is locked` | You are on SQLite with a long-held transaction; use MySQL for production or stop the other process. |
| No jobs found | Add `ADZUNA_APP_ID/KEY` or `JOOBLE_KEY`, enable ATS platforms with India/Mumbai roles, or lower `match_threshold`. |
| AI unavailable | Start Ollama and pull the configured model, or set `OPENAI_API_KEY`; heuristic fallback still works. |
| Reports 401 | Set the same `JOBBOT_API_TOKEN` in the dashboard; downloads include `?token=`. |
| Pending verification persists | Verify the employer confirmation manually, then use the history page/API to mark it applied. |
