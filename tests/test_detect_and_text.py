from jobbot.sources.ats_detect import detect, find_ats_links
from jobbot.text import best_option, html_to_text, slug_candidates


def test_detect_greenhouse():
    d = detect("https://job-boards.greenhouse.io/gitlab/jobs/7890123")
    assert (d.ats, d.slug, d.job_id) == ("greenhouse", "gitlab", "7890123")
    d = detect("https://boards.greenhouse.io/embed/job_app?for=acme&token=555")
    assert (d.ats, d.slug, d.job_id) == ("greenhouse", "acme", "555")


def test_detect_lever_ashby_workable():
    uid = "0f5a2b1c-1234-4abc-9def-0123456789ab"
    assert detect(f"https://jobs.lever.co/plaid/{uid}/apply").__dict__ == {"ats": "lever", "slug": "plaid", "job_id": uid}
    assert detect(f"https://jobs.ashbyhq.com/ramp/{uid}").__dict__ == {"ats": "ashby", "slug": "ramp", "job_id": uid}
    d = detect("https://apply.workable.com/acme-co/j/AB12CD34EF/")
    assert (d.ats, d.slug, d.job_id) == ("workable", "acme-co", "AB12CD34EF")


def test_detect_restricted_and_other():
    assert detect("https://www.linkedin.com/jobs/view/123").ats == "restricted"
    assert detect("https://www.naukri.com/job-listings-x").ats == "restricted"
    assert detect("https://in.indeed.com/viewjob?jk=1").ats == "restricted"
    assert detect("https://careers.example.com/jobs/1").ats == "other"


def test_find_ats_links():
    html = ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'
            '<a href="https://jobs.lever.co/widgets">x</a><a href="https://jobs.ashbyhq.com/foo">y</a>')
    assert find_ats_links(html) == {("greenhouse", "acme"), ("lever", "widgets"), ("ashby", "foo")}


def test_text_helpers():
    assert html_to_text("&lt;p&gt;Hello&lt;/p&gt;&lt;p&gt;World&lt;/p&gt;") == "Hello\nWorld"
    assert slug_candidates("Acme Labs, Inc.") == ["acme", "acmelabsinc", "acme-labs-inc"]
    assert slug_candidates("Post Hog") == ["posthog", "post-hog"]
    assert best_option("Yes", ["No", "Yes"]) == 1
    assert best_option("yes, I am", ["Yes", "No"]) == 0
    assert best_option("Prefer not to say", ["Male", "Female", "I prefer not to say"]) == 2
    assert best_option("banana", ["Yes", "No"]) is None
