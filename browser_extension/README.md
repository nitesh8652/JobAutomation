# Jobbot Search and Tracker extension

This Chrome extension connects to the local Jobbot UI. **Start Applying** opens up to five role searches in a new window for Naukri, Indeed, Wellfound, Y Combinator's Work at a Startup, and Hirist.tech, and launches a separate visible Google Chrome window with a persistent Jobbot profile. Jobbot fetches and scores India/Mumbai listings, then fills up to three eligible supported ATS forms in dry-run mode by default. It reuses one application tab and leaves your existing Chrome windows alone.

The extension does not imitate a human, bypass site controls, or evade automation restrictions. Naukri, Indeed, Wellfound, Work at a Startup, and Hirist searches and applications remain user-operated. Indeed's terms prohibit automated job application submissions outside its authorized vendors and tools. The existing ATS adapters fill Greenhouse, Lever, Ashby, and Workable forms; live submission stays disabled until `automation.auto_submit_authorized: true` is explicitly saved in `config/targets.yaml`. CAPTCHA and unanswered questions go to review.

## Install

1. Start the local app from the project folder: `jobbot ui`.
2. Open `chrome://extensions` and turn on **Developer mode**.
3. Choose **Load unpacked** and select this `browser_extension` folder.
4. Pin **Jobbot Search and Tracker** to the toolbar.

## Use

1. Start Jobbot with `jobbot ui`, click the extension, select a role, and choose **Start Applying**. A new window opens with user-operated job-board searches while the agent launches its persistent Chrome profile, fetches and scores jobs, and runs supported ATS forms in dry-run mode.
2. Use **Pause**, **Resume**, **Stop**, or **Emergency stop** in the extension or dashboard. Controls take effect at the next safe browser-step boundary; a page already loading or a form already being processed may finish that action first.
3. On a job-board result, open an individual listing and choose **Capture current job page**. Review its location, email, and skill comparison before tracking it.
4. Complete job-board applications yourself. On supported ATS boards, dry-run forms open in the separate Chrome profile; review their screenshots and logs. Only enable live submissions after reviewing dry runs and saving the explicit authorization setting.
5. After you apply to a job manually, choose **Mark applied**. The company, role, source, status, and any email found will appear in Jobbot's **Applied** table and the extension's recent application list.

The server must be running at `http://127.0.0.1:8765`. The extension can call that local server, open a new search window, and read the current tab only after you click **Capture current job page**.
