"""Assist mode: jobs we must not automate (LinkedIn, Naukri, Indeed, non-ATS company sites).
Writes a local HTML page with the tailored cover letter ready to copy; you apply by hand."""
from __future__ import annotations

import html
import logging
import webbrowser

from . import config, db, tailor

log = logging.getLogger(__name__)


def build(conn, limit: int = 30, open_browser: bool = True) -> str:
    rows = db.assist_jobs(conn, limit)
    cards = []
    for r in rows:
        job = dict(r)
        cover = job.get("cover_letter")
        if not cover:
            try:
                cover = tailor.cover_letter(job)
                db.save_application(conn, job["id"], "assist", "apply by hand", cover_letter=cover)
                conn.commit()
            except SystemExit:
                raise
            except Exception as exc:
                log.warning("cover letter for #%s failed: %s", job["id"], exc)
                cover = ""
        e = html.escape
        cards.append(f"""
<article>
  <header>
    <h2>{e(job['title'] or '')}</h2>
    <p class="meta">{e(job['company'] or '')} · {e(job['location'] or '')} · score {job['score'] if job['score'] is not None else '-'} · #{job['id']}</p>
    <p class="why">{e(job['score_reason'] or '')}</p>
  </header>
  <div class="actions">
    <a class="btn" href="{e(job['url'])}" target="_blank" rel="noopener">Open job ↗</a>
    <button class="btn ghost" data-copy="cl{job['id']}">Copy cover letter</button>
    <code>jobbot mark {job['id']} applied</code>
  </div>
  <textarea id="cl{job['id']}" rows="9">{e(cover)}</textarea>
</article>""")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Apply by hand</title>
<style>
:root{{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e3e2dc;--accent:#2f5d50}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--muted:#9a9993;--line:#33332f;--accent:#7fb8a6}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:860px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--muted);margin:0 0 20px}}
article{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 14px}}
h2{{font-size:17px;margin:0}} .meta,.why{{color:var(--muted);margin:2px 0;font-size:13px}}
.actions{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}}
.btn{{background:var(--accent);color:var(--bg);border:0;border-radius:6px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer;text-decoration:none}}
.btn.ghost{{background:transparent;color:var(--accent);border:1px solid var(--accent)}}
code{{font-size:12px;color:var(--muted)}}
textarea{{width:100%;box-sizing:border-box;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:8px;font:13px/1.45 ui-monospace,monospace}}
</style></head><body><main>
<h1>Apply by hand</h1>
<p class="sub">{len(rows)} job(s) on sites we don't automate. Open the job, paste the letter, apply, then run the <code>jobbot mark</code> command.</p>
{''.join(cards) or '<p>Nothing here yet.</p>'}
</main>
<script>
document.addEventListener('click', e => {{
  const id = e.target.getAttribute('data-copy'); if (!id) return;
  navigator.clipboard.writeText(document.getElementById(id).value).then(() => {{
    const t = e.target.textContent; e.target.textContent = 'Copied'; setTimeout(() => e.target.textContent = t, 1200); }});
}});
</script></body></html>"""
    config.ensure_dirs()
    out = config.DATA_DIR / "assist.html"
    out.write_text(page, encoding="utf-8")
    if open_browser:
        webbrowser.open(out.as_uri())
    return str(out)
