# Autonomous job-agent architecture and rollout

## Architecture

```text
Chrome extension / dashboard
            |
      localhost API
            |
   browser-agent controller
      /           \
fetch + matching   Playwright worker
      |             |
source adapters   dedicated persistent Chrome profile
      \             /
       application store and reports
```

The current code already provides Python fetch and ATS adapters, location filtering, resume-based
matching, application-form analysis, quota checks, and a local dashboard. This first rollout keeps
those modules and the JSON store as the source of truth. It adds a visible Chrome worker and control
path before a storage or dashboard migration.

The requested production target is Python 3.12+, FastAPI, React, MySQL, Playwright, an Ollama-first
model interface with optional cloud providers, and XLSX reporting. Migrate to that stack in later
phases behind repository and adapter interfaces so the existing source and form logic can be reused.

## Rollout

### Phase 1: visible browser and controls

- Start launches visible Google Chrome with `data/chrome-jobbot-profile/`, separate from the user's
  normal Chrome profile. The Jobbot extension remains installed in the user's Chrome and controls
  the local agent through its loopback API.
- The extension opens up to five user-operated search tabs in a new window. The agent reuses one
  application tab in its dedicated profile, keeping the workflow to at most six task tabs.
- The workflow runs fetch, role/location matching, then up to three supported ATS form dry-runs.
- Pause, Resume, Stop, and Emergency stop are available in the extension and dashboard. They take
  effect at browser-step boundaries; the current page action may finish first.
- Chrome stays open after a run so the user can inspect results and retain logins for the next run.

### Phase 2: source and platform adapters

Keep one adapter per source. Continue using supported APIs and ATS boards from the current
configuration. Add platform adapters only where a platform offers a permitted integration. Naukri,
Indeed, Wellfound, Work at a Startup, and Hirist remain user-operated searches in this rollout; the
extension captures listings only after an explicit user action.

### Phase 3: form interpretation and resume selection

Reuse the current label/accessibility-based form analyzer for supported ATS forms. Add multiple
resume versions and explicit factual profile fields before expanding uploads. Unknown required
questions, CAPTCHA, OTP, and legal declarations stay in review unless saved profile data answers
them truthfully.

### Phase 4: authorized submissions

The agent is dry-run by default. Live ATS submission requires the explicit
`automation.auto_submit_authorized: true` setting in `config/targets.yaml`. The ATS submitter must
continue to verify confirmation and queue uncertain outcomes instead of assuming success.

### Phase 5: durable reporting and quotas

Move the JSON repository to MySQL with migrations and atomic application reservations. Keep the
weekly boundary in `Asia/Kolkata`, count confirmed applications, reserve capacity for uncertain
submissions, and generate the requested multi-sheet XLSX report. Limit form submission concurrency
to one until account/session collision handling is proven.

### Phase 6: integration and hardening

Add adapter fixtures, browser dry-run scenarios, restart recovery, task limits, secret redaction,
and manual verification instructions. Validate on test listings before enabling live submission.

## Phase 1 local check

1. Confirm Google Chrome is installed and run `jobbot ui`.
2. Load `browser_extension` as an unpacked extension in the normal Chrome profile if it is not
   already installed there.
3. Click **Start Applying**. A new window should open with the target-role search tabs, and a
   separate Chrome window should open for supported ATS application dry-runs.
4. The agent should fetch and match eligible jobs, then fill up to three supported ATS forms without
   submitting. Inspect the Activity view and `data/screenshots/`.
5. Confirm Pause/Resume and Stop at the next between-form checkpoint.
6. Only after reviewing dry-runs, set `automation.auto_submit_authorized: true` in
   `config/targets.yaml` if live submission is desired.

The workflow can continue while the Chrome window is minimized as long as Windows remains awake and
the network is available. Sleep, hibernation, shutdown, browser crashes, expired sessions, and site
challenges can pause or stop progress. No CAPTCHA or anti-detection bypass is used.
