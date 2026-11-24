import pytest

from jobbot import config
from jobbot.appliers import formfill
from jobbot.appliers.formfill import Question, _standard_answer, plan_answers

PROFILE = {
    "first_name": "Asha", "last_name": "Rao", "email": "a@example.com", "phone": "+91 1", "location": "Pune, India",
    "linkedin": "https://linkedin.com/in/x", "auto_consent": True, "eeo_decline": True,
    "answers": [{"match": "sponsor", "answer": "Yes"},
                {"match": "how did you (hear|learn|find)|^source$", "answer": "Company careers page"},
                {"match": "pronoun", "answer": ""}],
}


@pytest.fixture(autouse=True)
def profile(monkeypatch):
    monkeypatch.setattr(config, "profile", lambda: PROFILE)


def test_standard_answer_labels():
    assert _standard_answer("Phone") == ("phone", "+91 1")
    assert _standard_answer("On a scale of 1-10, how confident are you handling live phone calls with customers?") is None
    assert _standard_answer("Please choose the country in which you are located.") == ("country", "India")
    assert _standard_answer("Have you made any open source contributions?") is None
    assert _standard_answer("Will you require visa sponsorship?") == ("answers", "Yes")


def q(label, kind="text", required=True, options=None, hint=""):
    return Question(key=label + "|" + kind + (f"|{hint}" if hint else ""), label=label, kind=kind, required=required,
                    options=options or [], el_ids=["x"], hint=hint)


def test_plan_answers():
    asked = []

    def fake_llm(qs):
        asked.extend(x["label"] for x in qs)
        return {x["key"]: {"answer": "Yes", "confident": x["label"].startswith("Do you know")} for x in qs}

    qs = [
        q("Attach", "file", hint="resume"), q("Attach", "file", required=False, hint="cover_letter"),
        q("Autofill from resume", "file", required=False),
        q("Email"), q("Gender", "combobox", required=False, options=["Male", "Female", "Decline To Self Identify"]),
        q("I agree to the privacy policy", "checkbox_single", options=["I agree"]),
        q("Send me job alerts", "checkbox_single", required=False, options=["yes"]),
        q("Pronouns", required=False),
        q("Do you know Python?", "radio", options=["Yes", "No"]),
        q("Describe your favourite project", "textarea"),
        q("Optional unknown thing", required=False),
    ]
    plan = plan_answers(qs, {}, "letter", {}, "resume.pdf", "cover.txt", fake_llm)
    v = {k.split("|")[0] + "|" + k.split("|")[1]: val for k, val in plan.values.items()}
    assert plan.values["Attach|file|resume"] == "resume.pdf"
    assert plan.values["Attach|file|cover_letter"] == "cover.txt"
    assert "Autofill from resume|file" not in plan.values
    assert v["Email|text"] == "a@example.com"
    assert v["Gender|combobox"] == "Decline To Self Identify"
    assert v["I agree to the privacy policy|checkbox_single"] is True
    assert "Send me job alerts|checkbox_single" not in v and "Pronouns|text" not in v
    assert v["Do you know Python?|radio"] == "Yes"
    # the AI was only asked required unknowns, and an unconfident answer becomes "needs review"
    assert asked == ["Do you know Python?", "Describe your favourite project"]
    assert plan.unresolved == ["Describe your favourite project"]
