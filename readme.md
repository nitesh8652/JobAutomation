# jobbot: job applications that don't put your accounts at risk

Finds remote jobs, scores them against your resume with GPT, writes a cover letter, and fills and
submits application forms on **Greenhouse, Lever, Ashby and Workable**. Anything that needs a
person (a CAPTCHA, a question it can't answer truthfully, a submit it can't confirm) goes to a
review queue, where you finish it in a pre-filled browser window.

## What it does and doesn't touch

| Source | How it's used |
|---|---|
| Remotive, Arbeitnow APIs | Read job listings (Remotive: a few calls a day, as its terms ask) |
| Greenhouse / Lever / Ashby / Workable | Read the public job board APIs, then fill the hosted application form |
| A company's own careers page | `jobbot add-company <url>` finds the ATS board linked from the page |
| LinkedIn, Naukri, Indeed, WorkIndia, Instagram/Facebook reels | **Never automated.** `jobbot add-job <url>` tracks the job, and `jobbot assist` gives you a tailored cover letter to apply by hand |

There's no login anywhere, so there's no account that can be banned. It never solves or gets
around a CAPTCHA, and it never tries to hide that it's a script (no stealth plugins). It's
rate-limited: 40 form attempts a day, a random 30–120 s between them, and at most 3
applications per company per 30 days. All of these are set in `config/targets.yaml`.

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
jobbot apply                 # submit within the daily cap / weekly target
jobbot review                # finish queued forms yourself (CAPTCHA, open questions)
jobbot assist                # page of apply-by-hand jobs with cover letters
jobbot status                # quota + pipeline counts
jobbot report                # this week's applications -> data/reports/*.csv
```

Other commands:

```powershell
jobbot add-job <url>  [--title --company --desc]   # ATS link -> auto-apply queue; LinkedIn etc -> assist
jobbot add-company greenhouse:somecompany          # or an ATS URL, or a careers page URL
jobbot mark <job_id> applied|submitted|skip|closed
jobbot apply --dry-run --job <id>                  # test one specific form
```

### Run it automatically every day

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Time 10:30
# remove: schtasks /Delete /TN jobbot-daily /F
```

The task runs fetch, match and apply. Run `jobbot review` and `jobbot assist` yourself once or twice a week.

## How a form gets handled

1. Open the hosted form. A closed posting gets marked `closed`.
2. Read every field's label. Fill from `profile.yaml`, then from your `answers:` list. Voluntary
   self-ID questions get "prefer not to say". Privacy-consent boxes get ticked (`auto_consent`).
   Marketing and SMS opt-ins are left unticked. Optional unknown questions are left blank.
3. Required questions it doesn't know go to GPT along with your resume. If the answer isn't backed
   by your resume or profile, the field is marked **needs review** instead of guessed.
4. It submits only if nothing needs review and there's no visible CAPTCHA, then checks for a
   confirmation. Everything else lands in `jobbot review`.

Screenshots of every filled and submitted form are saved in `data/screenshots/`, and cover letters in `data/letters/`.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```
