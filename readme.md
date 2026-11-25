# jobbot: job applications that don't put your accounts at risk

Finds jobs that fit the current location rule (remote work from India, or office/hybrid in Mumbai),
scores them against your resume, and writes cover letters. The browser agent launches a visible
Chrome window with a dedicated persistent profile, runs fetch and match, and fills supported ATS
forms in dry-run mode by default. The extension opens user-operated searches on Naukri, Indeed,
Wellfound, Y Combinator's Work at a Startup, and Hirist.tech. Live ATS submission requires
`automation.auto_submit_authorized: true` in `config/targets.yaml`.


## Autonomous v2 (FastAPI + React + MySQL)

A production-oriented agent lives alongside the legacy CLI. It discovers jobs from the configured
Adzuna/Jooble APIs and ATS boards, scores them with local Ollama (or cloud fallback), and fills
supported ATS forms in up to six visible Chrome tabs with a shared 300/week quota.

```powershell
# once: configure .env (MySQL/Ollama), apply schema, build the dashboard
.venv\Scripts\python scripts\migrate.py
.venv\Scripts\jobbot-v2 init
cd frontend; npm install; npm run build; cd ..

# run
jobbot-api          # http://127.0.0.1:8766
```

Dry run is the default. Live submission needs both `auto_submit_authorized=true` and the dashboard's
explicit `AUTHORIZE` confirmation. MySQL is used when `JOBBOT_DATABASE_URL` is set; otherwise the
agent falls back to SQLite for local dry runs. Full setup, architecture, quota semantics, recovery,
platform policy, API surface and troubleshooting: **[docs/AUTONOMOUS_AGENT.md](docs/AUTONOMOUS_AGENT.md)**.

## What it does and doesn't touch

| Source | How it's used |
|---|---|
| Adzuna, Jooble APIs | Read India-focused job listings |
| Greenhouse / Lever / Ashby / Workable | Read the public job board APIs, then fill the hosted application form |
| A company's own careers page | `jobbot add-company <url>` finds the ATS board linked from the page |
| Naukri, Indeed, Wellfound, Y Combinator / Work at a Startup, Hirist.tech | Open searches with the browser extension; capture and track jobs after reviewing the location |

The location filter runs before a job enters the queue and again before an ATS application starts.
Remote postings must confirm India (or an Indian city); office and hybrid postings must list Mumbai.
Global or unclear locations are excluded. The ATS applier never solves or gets around a CAPTCHA,
and it does not use stealth plugins. It is rate-limited by the settings in `config/targets.yaml`.
The extension does not scrape, fill, or submit forms on job-board websites. Its Pause, Resume, Stop,
and Emergency stop controls act at safe browser-step boundaries. Chrome stores login sessions in
`data/chrome-jobbot-profile/`, separate from your normal Chrome profile.

## Setup (once)

```powershell
cd C:\Users\pc\Desktop\Automation
.venv\Scripts\activate                  # already created and installed
jobbot init                             # creates config/*.yaml, .env, JSON data store (already done)
```

1. **`config/profile.yaml`**: your real details, plus the `answers:` list of standard answers
   (sponsorship, notice period, salary, nationality...). Everything here can get typed into forms.
2. **`resumes/base_resume.pdf`**: your resume.
3. **`.env`**: `OPENAI_API_KEY=sk-...`
4. **`config/targets.yaml`**: job titles, location rules, AI score threshold, limits, company list.

## Daily use

```powershell
jobbot fetch                 # pull new jobs (APIs + company boards; finds new companies automatically)
jobbot match                 # rule filter + GPT fit score -> 'matched' or 'assist'
jobbot apply --dry-run       # FIRST TIME: fills 3 forms in a visible browser, never submits
jobbot apply                 # blocked unless live submission is explicitly authorized
jobbot review                # finish queued forms yourself (CAPTCHA, open questions)
jobbot assist                # page of apply-by-hand jobs with cover letters
jobbot status                # quota + pipeline counts
jobbot report                # this week's applications -> data/reports/*.csv
jobbot ui                    # dashboard, skill gaps, application history, browser extension API
```

### Browser extension

Start `jobbot ui`, then load the `browser_extension` folder as an unpacked extension at
`chrome://extensions` (turn on Developer mode first). Click **Start Applying** to launch the dedicated
Chrome profile, open the search pages, and run fetch, match, and dry-run application steps. Search
tabs remain user-operated; captured job-board listings are location-checked before tracking. Full
steps are in `browser_extension/README.md`.
The **Skill Gaps** view counts requested technologies in eligible job descriptions, shows example
roles, and compares them with your resume text. If the resume file is missing or has no extractable
text, it shows demand only until you add a readable PDF or configure `resume_text_path`.

Indeed's terms prohibit automated submission without written permission. Wellfound also restricts
automated harvesting and spam; its listings are therefore searched by you in the browser. Naukri is
treated as a restricted source by this project. See the site's current terms before using any
automation beyond the supported ATS boards.

Other commands:

```powershell
jobbot add-job <url>  [--title --company --location --desc] # only India remote / Mumbai office jobs are tracked
jobbot add-company greenhouse:somecompany          # or an ATS URL, or a careers page URL
jobbot mark <job_id> applied|submitted|skip|closed
jobbot apply --dry-run --job <id>                  # test one specific form
```

### Run it automatically every day

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Time 10:30
# remove: schtasks /Delete /TN jobbot-daily /F
```

The task runs fetch and match. It submits supported ATS forms only when
`automation.auto_submit_authorized: true` is set; otherwise, run dry-runs and review forms yourself.

## How a form gets handled

1. Open the hosted form. A closed posting gets marked `closed`.
2. Read every field's label. Fill from `profile.yaml`, then from your `answers:` list. Voluntary
   self-ID questions get "prefer not to say". Privacy-consent boxes get ticked (`auto_consent`).
   Marketing and SMS opt-ins are left unticked. Optional unknown questions are left blank.
3. Required questions it doesn't know go to GPT along with your resume. If the answer isn't backed
   by your resume or profile, the field is marked **needs review** instead of guessed.
4. It submits only if nothing needs review, there is no visible CAPTCHA, and live submissions have
   been explicitly authorized in `config/targets.yaml`. It then checks for confirmation. Everything
   else lands in `jobbot review`.

Screenshots of every filled and submitted form are saved in `data/screenshots/`, and cover letters in `data/letters/`.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```
